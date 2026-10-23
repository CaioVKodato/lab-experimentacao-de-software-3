"""Testes da etapa que monta o dataset.csv a partir dos caches (fecha a Issue #21).

Os caches são gravados pelos COLETORES REAIS (releases, compare e workflow runs) com um
cliente falso, não escritos à mão: se a leitura do consolidador divergir do formato que os
coletores gravam, o teste da linha completa falha.
"""
import csv
from pathlib import Path

import pytest

from metricas.janela import parse_janela
from pipeline.cache import JsonCache
from pipeline.compare import collect_commits_between
from pipeline.consolidar import (
    Resumo,
    consolidar,
    construir_linha,
    ler_estratos,
    ler_metadados,
    main,
)
from pipeline.dataset import nomes_colunas
from pipeline.github_client import GitHubAPIError
from pipeline.releases import collect_repo_releases
from pipeline.workflow_runs import coletar_workflow_runs

JANELA_STR = ("2026-01-01", "2026-12-31")
JANELA = parse_janela(*JANELA_STR)
AGORA = "2026-10-24T12:00:00Z"


def rel(tag, data, prerelease=False, draft=False):
    return {"tag_name": tag, "name": tag, "published_at": f"{data}T12:00:00Z",
            "draft": draft, "prerelease": prerelease, "html_url": f"https://x/{tag}"}


def commit(sha, data, msg="chore: x"):
    return {"sha": sha, "commit": {"author": {"date": f"{data}T12:00:00Z"}, "message": msg}}


def run(i, dia, hora, conclusion, fim=None):
    inicio = f"{dia}T{hora}:00Z"
    return {"id": i, "workflow_id": 1, "name": "CI", "event": "push", "head_branch": "main",
            "head_sha": "abc", "status": "completed", "conclusion": conclusion,
            "created_at": inicio, "run_started_at": inicio, "updated_at": f"{dia}T{fim or hora}:00Z"}


class Mundo:
    """Cliente falso que atende os três coletores reais."""

    def __init__(self, releases, comparacoes, runs=()):
        self.releases, self.comparacoes, self.runs = releases, comparacoes, list(runs)

    def paginate(self, path, params=None, *, items_key=None, max_items=None):
        if path.endswith("/releases"):
            return list(self.releases)
        if path.endswith("/tags"):
            return []
        if "/compare/" in path:
            base, head = path.split("/compare/")[1].split("...")
            resposta = self.comparacoes.get((base, head), 404)
            if isinstance(resposta, int):
                raise GitHubAPIError(resposta, "simulado")
            return resposta
        if path.endswith("/actions/runs"):
            ini, fim = params["created"].split("..")
            return [r for r in self.runs if ini <= r["created_at"][:10] <= fim]
        raise AssertionError(f"chamada inesperada: {path}")


# --- o repositório "o/r" completo: 4 releases (uma antes da janela) e runs só em março ---

RELEASES = [rel("v0.9", "2025-12-20"), rel("v1.0", "2026-02-01"), rel("v1.1", "2026-03-15"), rel("v1.2", "2026-04-10")]
COMPARACOES = {
    ("v0.9", "v1.0"): [commit("a1", "2026-01-20", "feat: nova coisa")],
    ("v1.0", "v1.1"): [commit("b1", "2026-03-02"), commit("b2", "2026-03-10"), commit("b3", "2026-03-14")],
    ("v1.1", "v1.2"): [commit("c1", "2026-04-05"), commit("c2", "2026-04-09")],
}
RUNS = [run(1, "2026-03-01", "09", "success"), run(2, "2026-03-01", "10", "failure"),
        run(3, "2026-03-01", "11", "success", fim="12")]
PARES = list(COMPARACOES)


@pytest.fixture
def cache(tmp_path):
    return JsonCache(tmp_path / "cache")


def coletar(cache, *, releases=RELEASES, comparacoes=COMPARACOES, runs=RUNS, pares=None,
            janela_runs=JANELA, owner="o", name="r"):
    """Roda os coletores reais, deixando os caches como a coleta de verdade deixaria."""
    mundo = Mundo(releases, comparacoes, runs)
    collect_repo_releases(mundo, owner, name, cache=cache, window_start=JANELA_STR[0], window_end=JANELA_STR[1])
    for base, head in (PARES if pares is None else pares):
        collect_commits_between(mundo, owner, name, base, head, cache=cache)
    coletar_workflow_runs(mundo, cache, owner, name, "main", janela_runs)


META = {"estrelas": 2003, "linguagem_principal": "Python", "criado_em": "2015-06-01T00:00:00Z", "n_contribuidores": 120}


def linha(cache, *, metadados=META, faixa="1000-2000", owner="o", name="r"):
    return construir_linha(owner=owner, name=name, default_branch="main", cache=cache, janela=JANELA,
                           janela_str=JANELA_STR, metadados=metadados, faixa_estrelas=faixa, coletado_em=AGORA)


# ---------- linha completa ----------

def test_linha_completa_bate_com_o_que_os_coletores_gravaram(cache):
    coletar(cache)
    l = linha(cache)
    assert l["lacunas_de_coleta"] == 0
    assert l["repo"] == "o/r" and l["default_branch"] == "main"
    assert l["n_releases_janela"] == 3                      # v0.9 é anterior à janela
    assert l["lead_time_a_horas"] == pytest.approx(288)     # mediana de 12d, 13d e 5d
    assert l["lead_time_b_horas"] == pytest.approx(120)
    assert l["cfr_a"] == pytest.approx(1 / 3)
    assert l["recuperacao_mediana_horas"] == pytest.approx(2.0)  # falha às 10h, sucesso termina às 12h
    assert l["n_runs_validas"] == 3 and l["meses_sem_runs"] == 11
    assert l["coletado_em"] == AGORA


def test_a_primeira_release_da_janela_usa_a_base_anterior_a_janela(cache):
    coletar(cache)
    l = linha(cache)
    assert l["lt_n_releases_avaliadas"] == 3 and l["lt_n_ignoradas_compare"] == 0


def test_metadados_e_estrato_vem_dos_arquivos_e_o_estrato_nao_e_recalculado(cache):
    coletar(cache)
    l = linha(cache)  # 2003 estrelas hoje, mas sorteado em 1000-2000
    assert l["estrelas"] == 2003 and l["faixa_estrelas"] == "1000-2000"
    assert l["linguagem_principal"] == "Python" and l["n_contribuidores"] == 120


def test_colunas_da_linha_sao_exatamente_as_do_esquema(cache):
    coletar(cache)
    assert set(linha(cache)) == set(nomes_colunas())


# ---------- lacunas: dado ausente não pode se passar por zero ----------

def test_par_de_compare_que_nao_foi_coletado_e_uma_lacuna(cache):
    coletar(cache, pares=PARES[:2])  # falta ("v1.1", "v1.2")
    l = linha(cache)
    assert l["lacunas_de_coleta"] == 1 and l["lt_n_ignoradas_compare"] == 1


def test_par_404_ja_registrado_nao_e_lacuna_mas_fica_ignorado(cache):
    comparacoes = {**COMPARACOES, ("v1.1", "v1.2"): 404}
    coletar(cache, comparacoes=comparacoes)
    l = linha(cache)
    assert l["lacunas_de_coleta"] == 0 and l["lt_n_ignoradas_compare"] == 1


def test_par_que_falhou_com_5xx_nao_foi_gravado_e_conta_como_lacuna(cache):
    coletar(cache, comparacoes={**COMPARACOES, ("v1.1", "v1.2"): 502})
    assert linha(cache)["lacunas_de_coleta"] == 1


def test_cache_de_compare_na_versao_antiga_conta_como_lacuna(cache):
    coletar(cache)
    chave = "compare/o/r/v1.0...v1.1.json"
    antigo = cache.load(chave)
    antigo.pop("versao")
    cache.save(chave, antigo)
    assert linha(cache)["lacunas_de_coleta"] == 1


def test_meses_de_workflow_runs_nao_coletados_sao_lacunas(cache):
    coletar(cache, janela_runs=parse_janela("2026-01-01", "2026-06-30"))  # faltam jul..dez
    l = linha(cache)
    assert l["lacunas_de_coleta"] == 6


def test_releases_nao_coletadas_geram_linha_vazia_com_lacuna_e_sem_quebrar(cache):
    l = linha(cache)  # nada coletado
    assert l["lacunas_de_coleta"] >= 1
    assert l["n_releases_janela"] == 0 and l["lead_time_a_horas"] is None


def test_cache_de_releases_de_outra_janela_conta_como_lacuna(cache):
    coletar(cache)
    outra = construir_linha(owner="o", name="r", default_branch="main", cache=cache, janela=JANELA,
                            janela_str=("2025-11-01", "2026-10-31"), metadados=META,
                            faixa_estrelas="1000-2000", coletado_em=AGORA)
    assert outra["lacunas_de_coleta"] >= 1 and outra["n_releases_janela"] == 0


def test_metadados_ausentes_ficam_vazios_e_contam_como_lacuna(cache):
    coletar(cache)
    l = linha(cache, metadados=None)
    assert l["estrelas"] is None and l["lacunas_de_coleta"] == 1


def test_estrato_ausente_fica_vazio_e_conta_como_lacuna(cache):
    coletar(cache)
    l = linha(cache, faixa=None)
    assert l["faixa_estrelas"] is None and l["lacunas_de_coleta"] == 1


# ---------- variante com pré-releases ----------

def test_variante_com_prerelease_le_os_pares_proprios_da_variante(cache):
    releases = [rel("v1.0", "2026-02-01"), rel("v1.1-rc1", "2026-03-01", prerelease=True), rel("v1.1", "2026-04-01")]
    comparacoes = {
        ("v1.0", "v1.1"): [commit("x1", "2026-03-10")],          # variante padrão
        ("v1.0", "v1.1-rc1"): [commit("y1", "2026-02-20")],       # variante com pré-releases
        ("v1.1-rc1", "v1.1"): [commit("z1", "2026-03-25")],
    }
    coletar(cache, releases=releases, comparacoes=comparacoes, runs=[], pares=list(comparacoes))
    l = linha(cache)
    assert l["n_releases_janela"] == 2 and l["n_releases_janela_pre"] == 3
    assert l["lead_time_a_horas"] == pytest.approx(22 * 24)       # v1.0 -> v1.1 pelo par padrão
    assert l["lead_time_a_horas_pre"] == pytest.approx(8 * 24)    # mediana de 9d e 7d
    assert l["lacunas_de_coleta"] == 0 and l["meses_sem_runs"] == 12  # meses coletados e vazios: não são lacuna


# ---------- leitura dos arquivos de metadados e de estrato ----------

def _csv(caminho: Path, cabecalho, linhas):
    with caminho.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cabecalho)
        w.writeheader()
        w.writerows(linhas)


CAB_META = ["full_name", "owner", "name", "html_url", "stargazers_count", "language", "default_branch",
            "created_at", "age_days", "contributors_count", "forks_count", "open_issues_count", "fetched_at", "from_cache"]


def test_ler_metadados_converte_tipos_e_trata_vazio(tmp_path):
    _csv(tmp_path / "m.csv", CAB_META, [
        {"full_name": "o/r", "stargazers_count": "2003", "language": "Python", "created_at": "2015-06-01T00:00:00Z", "contributors_count": "120"},
        {"full_name": "o/s", "stargazers_count": "9", "language": "", "created_at": "", "contributors_count": ""},
    ])
    meta = ler_metadados(tmp_path / "m.csv")
    assert meta["o/r"] == {"estrelas": 2003, "linguagem_principal": "Python", "criado_em": "2015-06-01T00:00:00Z", "n_contribuidores": 120}
    assert meta["o/s"]["linguagem_principal"] is None and meta["o/s"]["n_contribuidores"] is None


def test_ler_estratos_so_considera_os_incluidos_na_amostra(tmp_path):
    _csv(tmp_path / "e.csv", ["full_name", "faixa", "included"], [
        {"full_name": "o/r", "faixa": "1000-2000", "included": "True"},
        {"full_name": "o/descartado", "faixa": "1000-2000", "included": "False"},
    ])
    assert ler_estratos(tmp_path / "e.csv") == {"o/r": "1000-2000"}


# ---------- etapa completa e linha de comando ----------

CAB_AMOSTRA = ["full_name", "owner", "name", "html_url", "stargazers_count", "language", "default_branch", "created_at", "forks_count"]


def _montar_projeto(tmp_path: Path):
    saida = tmp_path / "output"
    saida.mkdir()
    cache = JsonCache(tmp_path / "cache")
    coletar(cache)  # o/r completo; o/vazio não é coletado
    _csv(saida / "sample_repos.csv", CAB_AMOSTRA, [
        {"full_name": "o/r", "owner": "o", "name": "r", "default_branch": "main"},
        {"full_name": "o/vazio", "owner": "o", "name": "vazio", "default_branch": "master"},
    ])
    _csv(saida / "repo_metadata.csv", CAB_META, [
        {"full_name": "o/r", "stargazers_count": "2003", "language": "Python", "created_at": "2015-06-01T00:00:00Z", "contributors_count": "120"},
    ])
    _csv(saida / "selection_evaluated.csv", ["full_name", "faixa", "included"], [
        {"full_name": "o/r", "faixa": "1000-2000", "included": "True"},
        {"full_name": "o/vazio", "faixa": "2001-5000", "included": "True"},
    ])
    config = {
        "observation_window": {"start": JANELA_STR[0], "end": JANELA_STR[1]},
        "paths": {"cache_dir": str(tmp_path / "cache"), "output_dir": str(saida)},
    }
    return config, saida


def test_consolidar_gera_o_csv_com_uma_linha_por_repositorio_da_amostra(tmp_path):
    config, saida = _montar_projeto(tmp_path)
    resumo = consolidar(config, coletado_em=AGORA)
    assert isinstance(resumo, Resumo)
    assert (resumo.n_repos, resumo.n_completos, resumo.n_incompletos) == (2, 1, 1)
    with (saida / "dataset.csv").open(encoding="utf-8", newline="") as fh:
        leitor = csv.DictReader(fh)
        linhas = list(leitor)
        assert leitor.fieldnames == nomes_colunas()
    assert [l["repo"] for l in linhas] == ["o/r", "o/vazio"]
    assert linhas[0]["lacunas_de_coleta"] == "0" and int(linhas[1]["lacunas_de_coleta"]) >= 1
    assert linhas[1]["faixa_estrelas"] == "2001-5000"  # o estrato vem do sorteio mesmo sem coleta


def test_consolidar_nao_precisa_de_token_nem_de_rede(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config, _ = _montar_projeto(tmp_path)
    assert consolidar(config, coletado_em=AGORA).n_repos == 2


def test_linha_de_comando_grava_o_dataset_e_resume(tmp_path, capsys):
    import yaml
    config, saida = _montar_projeto(tmp_path)
    arq = tmp_path / "config.yaml"
    arq.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert main(["--config", str(arq)]) == 0
    texto = capsys.readouterr().out
    assert (saida / "dataset.csv").is_file()
    assert "2 repositórios" in texto and "1 completos" in texto and "1 incompletos" in texto


def test_arquivo_de_amostra_alternativo_e_respeitado(tmp_path):
    config, saida = _montar_projeto(tmp_path)
    _csv(saida / "so_um.csv", CAB_AMOSTRA, [{"full_name": "o/r", "owner": "o", "name": "r", "default_branch": "main"}])
    resumo = consolidar(config, sample_csv=saida / "so_um.csv", coletado_em=AGORA)
    assert resumo.n_repos == 1 and resumo.n_completos == 1

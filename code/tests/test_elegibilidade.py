"""Testes da fase 1 da #20 (elegibilidade), com cliente falso — sem rede."""
import csv

import pytest

from metricas.janela import parse_janela
from pipeline.cache import JsonCache
from pipeline.elegibilidade import (
    STATUS_VALIDOS,
    avaliar_candidatos,
    avaliar_repositorio,
    escrever_csv,
    funil,
    ler_candidatos,
)
from pipeline.github_client import GitHubAPIError

JANELA = parse_janela("2025-10-01", "2026-09-30")


def rel(i, data="2026-03-01", draft=False, prerelease=False):
    return {"tag_name": f"v{i}", "published_at": f"{data}T12:00:00Z", "draft": draft, "prerelease": prerelease}


class ClienteFalso:
    def __init__(self, workflows=3, runs=None, releases=None, branch="main", erro=None):
        self.workflows = workflows
        self.runs = runs if runs is not None else {"completed": 120, "success": 80, "failure": 10, "timed_out": 0}
        self.releases = releases if releases is not None else [rel(i) for i in range(8)]
        self.branch = branch
        self.erro = erro
        self.chamadas = []

    def get(self, path, params=None):
        self.chamadas.append((path, dict(params or {})))
        if self.erro:
            raise self.erro
        if path.endswith("/actions/workflows"):
            return {"total_count": self.workflows}
        if path.endswith("/actions/runs"):
            return {"total_count": self.runs.get(params.get("status"), 0)}
        return {"default_branch": self.branch}

    def paginate(self, path, params=None, *, items_key=None, max_items=None):
        self.chamadas.append((path, dict(params or {})))
        if self.erro:
            raise self.erro
        return list(self.releases)


@pytest.fixture
def cache(tmp_path):
    return JsonCache(tmp_path / "cache")


def avaliar(cliente, cache, repo="o/r", **kw):
    return avaliar_repositorio(cliente, cache, repo, JANELA, **kw)


# ---------- caminho feliz e cada motivo de descarte ----------

def test_repositorio_que_atende_a_tudo_e_elegivel(cache):
    res = avaliar(ClienteFalso(), cache)
    assert res.elegivel and res.motivo == "elegivel"
    assert res.default_branch == "main"
    assert res.n_runs_validas == 90 and res.n_releases_janela == 8


def test_sem_actions_descarta_logo_e_nao_gasta_outras_chamadas(cache):
    cliente = ClienteFalso(workflows=0)
    res = avaliar(cliente, cache)
    assert not res.elegivel and res.motivo == "sem_actions"
    assert len(cliente.chamadas) == 1


def test_poucas_runs_completas_descartam_antes_de_olhar_releases(cache):
    cliente = ClienteFalso(runs={"completed": 30})
    res = avaliar(cliente, cache)
    assert res.motivo == "runs_insuficientes" and res.n_runs_validas is None
    assert not any(c[0].endswith("/releases") for c in cliente.chamadas)


def test_menos_de_5_releases_na_janela_descarta(cache):
    cliente = ClienteFalso(releases=[rel(i) for i in range(4)])
    res = avaliar(cliente, cache)
    assert res.motivo == "releases_insuficientes" and res.n_releases_janela == 4
    assert res.n_runs_validas is None  # nem chegou a contar as runs válidas


def test_releases_draft_prerelease_e_fora_da_janela_nao_contam(cache):
    releases = [rel(1), rel(2), rel(3), rel(4),
                rel(5, draft=True), rel(6, prerelease=True), rel(7, data="2025-09-30"), rel(8, data="2026-10-01")]
    res = avaliar(ClienteFalso(releases=releases), cache)
    assert res.n_releases_janela == 4 and res.motivo == "releases_insuficientes"


def test_runs_validas_exatas_abaixo_do_minimo_descartam_mesmo_com_completas_altas(cache):
    # 120 completas, mas boa parte cancelada/skipped: só 40 válidas.
    cliente = ClienteFalso(runs={"completed": 120, "success": 35, "failure": 5, "timed_out": 0})
    res = avaliar(cliente, cache)
    assert res.motivo == "runs_insuficientes" and res.n_runs_validas == 40


# ---------- parâmetros das consultas ----------

def test_consultas_de_runs_usam_branch_push_e_a_janela(cache):
    cliente = ClienteFalso()
    avaliar(cliente, cache)
    runs = [p for path, p in cliente.chamadas if path.endswith("/actions/runs")]
    assert runs and all(p["branch"] == "main" and p["event"] == "push" and p["per_page"] == 1 and
                        p["created"] == "2025-10-01..2026-09-30" for p in runs)
    assert [p["status"] for p in runs] == ["completed", *STATUS_VALIDOS]


def test_default_branch_informado_evita_a_chamada_extra(cache):
    informado = ClienteFalso()
    avaliar(informado, cache, default_branch="develop")
    assert not any(path == "/repos/o/r" for path, _ in informado.chamadas)
    assert all(p.get("branch") == "develop" for path, p in informado.chamadas if path.endswith("/actions/runs"))
    buscado = ClienteFalso(branch="master")
    assert avaliar(buscado, JsonCache(cache.cache_dir.parent / "outro")).default_branch == "master"


# ---------- cache e erros ----------

def test_segunda_avaliacao_vem_do_cache(cache):
    avaliar(ClienteFalso(), cache)
    segundo = ClienteFalso()
    res = avaliar(segundo, cache)
    assert segundo.chamadas == [] and res.elegivel


def test_cache_e_separado_por_janela(cache):
    avaliar(ClienteFalso(), cache)
    outro = ClienteFalso()
    avaliar_repositorio(outro, cache, "o/r", parse_janela("2025-11-01", "2026-10-31"))
    assert outro.chamadas  # janela diferente não reaproveita o resultado


def test_erro_da_api_vira_motivo_e_nao_e_cacheado(cache):
    res = avaliar(ClienteFalso(erro=GitHubAPIError(404, "nao achei")), cache)
    assert not res.elegivel and res.motivo == "erro_api"
    ok = ClienteFalso()
    assert avaliar(ok, cache).elegivel  # tentou de novo, porque erro não fica em cache


# ---------- lote, funil e arquivos ----------

def test_lote_avalia_todos_e_o_funil_conta_cada_motivo(cache):
    por_repo = {"o/ok": ClienteFalso(), "o/sem": ClienteFalso(workflows=0), "o/poucas": ClienteFalso(runs={"completed": 3})}

    class Roteador:
        def __init__(self):
            self.atual = None
        def _c(self, path):
            return por_repo["/".join(path.split("/")[2:4])]
        def get(self, path, params=None):
            return self._c(path).get(path, params)
        def paginate(self, path, params=None, **kw):
            return self._c(path).paginate(path, params, **kw)

    vistos = []
    resultados = avaliar_candidatos(Roteador(), cache, [("o/ok", None), ("o/sem", None), ("o/poucas", None)],
                                    JANELA, progresso=lambda i, n, r: vistos.append((i, n, r.repo)))
    assert funil(resultados) == {"elegivel": 1, "sem_actions": 1, "runs_insuficientes": 1}
    assert vistos == [(1, 3, "o/ok"), (2, 3, "o/sem"), (3, 3, "o/poucas")]


def test_csv_de_elegibilidade_tem_uma_linha_por_repositorio(cache, tmp_path):
    resultados = [avaliar(ClienteFalso(), cache, repo="o/a"), avaliar(ClienteFalso(workflows=0), cache, repo="o/b")]
    destino = escrever_csv(resultados, tmp_path / "saida" / "elegibilidade.csv")
    with destino.open(encoding="utf-8", newline="") as fh:
        linhas = list(csv.DictReader(fh))
    assert [l["repo"] for l in linhas] == ["o/a", "o/b"]
    assert [l["elegivel"] for l in linhas] == ["True", "False"]
    assert linhas[1]["motivo"] == "sem_actions" and linhas[1]["n_runs_validas"] == ""


def test_ler_candidatos_reconhece_colunas_comuns_e_branch_opcional(tmp_path):
    arq = tmp_path / "c.csv"
    arq.write_text("full_name,default_branch,stars\ndono/a,main,10\ndono/b,,20\n", encoding="utf-8")
    assert ler_candidatos(arq) == [("dono/a", "main"), ("dono/b", None)]


def test_ler_candidatos_sem_coluna_conhecida_mostra_o_cabecalho(tmp_path):
    arq = tmp_path / "c.csv"
    arq.write_text("nome_estranho,x\na,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nome_estranho"):
        ler_candidatos(arq)

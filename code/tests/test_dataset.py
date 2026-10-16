"""Testes do esquema do CSV, do consolidador por repositório e do dicionário (Issues #21/#22)."""
import csv
from datetime import datetime, timezone

import pytest

from metricas.janela import parse_janela
from pipeline.dataset import (
    COLUNAS,
    contar_runs_validas,
    deploys_por_semana,
    escrever_csv,
    escrever_dicionario,
    gerar_dicionario_md,
    linha_repositorio,
    meses_sem_runs,
    nomes_colunas,
    releases_na_janela,
    semanas_da_janela,
)

JANELA = parse_janela("2026-01-01", "2026-12-31")
DIA = 24


def rel(tag, mes, dia, draft=False, prerelease=False, ano=2026):
    return {"tag_name": tag, "published_at": f"{ano}-{mes:02d}-{dia:02d}T12:00:00Z",
            "draft": draft, "prerelease": prerelease}


def commits(*datas):
    return [{"commit": {"author": {"date": d}}} for d in datas]


def c(mes, dia):
    return f"2026-{mes:02d}-{dia:02d}T12:00:00Z"


def run(wid, dia, hora, conclusion, fim=None):
    t = f"{dia}T{hora}:00Z"
    return {"id": 0, "workflow_id": wid, "conclusion": conclusion,
            "run_started_at": t, "updated_at": f"{dia}T{fim or hora}:00Z"}


# ---------- esquema ----------

def test_nomes_de_colunas_sao_unicos_e_nao_vazios():
    nomes = nomes_colunas()
    assert len(nomes) == len(set(nomes)) and all(nomes)


def test_toda_coluna_tem_tipo_valido_unidade_e_origem():
    for col in COLUNAS:
        assert col.tipo in {"texto", "inteiro", "decimal"}, col.nome
        assert col.unidade.strip(), col.nome
        assert col.origem.strip(), col.nome
        assert col.rq.strip(), col.nome


def test_colunas_cobrem_todas_as_metricas_obrigatorias():
    nomes = set(nomes_colunas())
    esperadas = {"deploys_por_semana", "lead_time_a_horas", "lead_time_b_horas", "cfr_a", "cfr_b",
                 "recuperacao_mediana_horas", "recuperacao_prop_censurados"}
    assert esperadas <= nomes


# ---------- funções auxiliares ----------

def test_semanas_da_janela_de_12_meses_e_52_ponto_1():
    assert semanas_da_janela(parse_janela("2025-10-01", "2026-09-30")) == pytest.approx(365 / 7)


def test_deploys_por_semana():
    assert deploys_por_semana(52, parse_janela("2025-10-01", "2026-09-30")) == pytest.approx(52 * 7 / 365)
    assert deploys_por_semana(0, JANELA) == 0.0


def test_releases_na_janela_exclui_draft_prerelease_e_fora_da_janela():
    todas = [rel("v0", 12, 1, ano=2025), rel("v1", 2, 1), rel("v2-rc", 3, 1, prerelease=True),
             rel("v3", 4, 1, draft=True), rel("v4", 5, 1)]
    assert [r["tag_name"] for r in releases_na_janela(todas, JANELA)] == ["v1", "v4"]
    assert [r["tag_name"] for r in releases_na_janela(todas, JANELA, incluir_prerelease=True)] == ["v1", "v2-rc", "v4"]


def test_primeira_release_da_historia_conta_para_a_frequencia_de_deploy():
    assert len(releases_na_janela([rel("v1", 2, 1)], JANELA)) == 1


def test_contar_runs_validas_ignora_cancelled_skipped_e_em_andamento():
    runs = [run(1, "2026-03-01", "09", c_) for c_ in
            ["success", "failure", "timed_out", "cancelled", "skipped", None, "neutral"]]
    assert contar_runs_validas(runs) == 3


def test_meses_sem_runs_conta_meses_da_janela_sem_nenhuma_run():
    j = parse_janela("2026-01-01", "2026-03-31")
    runs = [run(1, "2026-01-10", "09", "success"), run(1, "2026-03-05", "09", "success")]
    assert meses_sem_runs(runs, j) == 1  # fevereiro


def test_meses_sem_runs_ignora_runs_fora_da_janela():
    j = parse_janela("2026-01-01", "2026-02-28")
    runs = [run(1, "2025-12-31", "09", "success"), run(1, "2026-03-01", "09", "success")]
    assert meses_sem_runs(runs, j) == 2


def test_historico_totalmente_ausente_conta_todos_os_meses():
    assert meses_sem_runs([], parse_janela("2025-10-01", "2026-09-30")) == 12


# ---------- linha do repositório ----------

def _entrada():
    releases = [rel("v1.0", 2, 1), rel("v1.1", 3, 15), rel("v1.2", 4, 10)]
    por_tag = {"v1.1": commits(c(3, 2), c(3, 10), c(3, 14)), "v1.2": commits(c(4, 5), c(4, 9))}
    runs = [run(1, "2026-03-01", "09", "success"), run(1, "2026-03-01", "10", "failure"),
            run(1, "2026-03-01", "11", "success", fim="12")]
    meta = {"estrelas": 5000, "linguagem_principal": "Python",
            "criado_em": "2015-06-01T00:00:00Z", "n_contribuidores": 120}
    return dict(repo="dono/proj", default_branch="main", metadados=meta, releases=releases,
                commits_por_tag=por_tag, runs=runs, janela=JANELA,
                coletado_em="2026-10-10T12:00:00Z")


def test_linha_tem_exatamente_as_colunas_do_esquema():
    assert set(linha_repositorio(**_entrada())) == set(nomes_colunas())


def test_linha_valores_calculados_com_os_exemplos_do_enunciado():
    linha = linha_repositorio(**_entrada())
    assert linha["n_releases_janela"] == 3
    assert linha["deploys_por_semana"] == pytest.approx(3 * 7 / 365)
    assert linha["lead_time_a_horas"] == pytest.approx(9 * DIA)
    assert linha["lead_time_b_horas"] == pytest.approx(5 * DIA)
    assert linha["lt_n_releases_avaliadas"] == 2
    assert linha["lt_n_commits"] == 5
    assert linha["n_runs_validas"] == 3
    assert linha["cfr_a"] == pytest.approx(1 / 3)
    assert linha["recuperacao_mediana_horas"] == pytest.approx(2.0)  # falha 10:00 -> sucesso termina 12:00
    assert linha["recuperacao_n_episodios"] == 1
    assert linha["recuperacao_prop_censurados"] == 0.0
    assert linha["meses_sem_runs"] == 11
    assert linha["estrelas"] == 5000 and linha["linguagem_principal"] == "Python"


def test_cfr_b_fica_vazio_ate_a_heuristica_existir_e_aceita_valor_depois():
    assert linha_repositorio(**_entrada())["cfr_b"] is None
    linha = linha_repositorio(**_entrada(), cfr_b=(0.25, 8))
    assert linha["cfr_b"] == 0.25 and linha["cfr_b_n_avaliadas"] == 8


def test_metadado_ausente_vira_vazio_e_nao_quebra():
    entrada = _entrada()
    entrada["metadados"] = {}
    linha = linha_repositorio(**entrada)
    assert linha["estrelas"] is None and linha["n_contribuidores"] is None


def test_repositorio_sem_runs_nem_releases_nao_quebra():
    entrada = _entrada()
    entrada.update(releases=[], commits_por_tag={}, runs=[])
    linha = linha_repositorio(**entrada)
    assert linha["deploys_por_semana"] == 0.0
    assert linha["lead_time_a_horas"] is None and linha["cfr_a"] is None
    assert linha["recuperacao_mediana_horas"] is None and linha["meses_sem_runs"] == 12


# ---------- CSV ----------

def test_csv_tem_cabecalho_do_esquema_e_celulas_vazias_para_ausentes(tmp_path):
    destino = tmp_path / "saida" / "dataset.csv"
    escrever_csv([linha_repositorio(**_entrada())], destino)
    with destino.open(encoding="utf-8", newline="") as fh:
        leitor = csv.DictReader(fh)
        linhas = list(leitor)
        assert leitor.fieldnames == nomes_colunas()
    assert len(linhas) == 1
    assert linhas[0]["repo"] == "dono/proj"
    assert linhas[0]["cfr_b"] == ""          # ausente = célula vazia
    assert float(linhas[0]["cfr_a"]) == pytest.approx(1 / 3)


def test_csv_rejeita_coluna_fora_do_esquema(tmp_path):
    linha = linha_repositorio(**_entrada())
    linha["coluna_inventada"] = 1
    with pytest.raises(ValueError):
        escrever_csv([linha], tmp_path / "x.csv")


def test_csv_sem_linhas_escreve_so_o_cabecalho(tmp_path):
    destino = tmp_path / "vazio.csv"
    escrever_csv([], destino)
    assert destino.read_text(encoding="utf-8").strip().split(",") == nomes_colunas()


# ---------- dicionário ----------

def test_dicionario_documenta_toda_coluna_do_csv_e_so_elas():
    md = gerar_dicionario_md()
    linhas_tabela = [l for l in md.splitlines() if l.startswith("| `")]
    assert [l.split("`")[1] for l in linhas_tabela] == nomes_colunas()


def test_dicionario_explica_convencoes_de_unidade_e_ausencia():
    md = gerar_dicionario_md()
    assert "célula vazia" in md.lower()
    assert "horas" in md and "proporção" in md.lower()


def test_dicionario_e_gravado_em_utf8_com_caracteres_especiais(tmp_path):
    destino = escrever_dicionario(tmp_path / "docs" / "dicionario.md")
    bruto = destino.read_bytes()
    assert b"\r" not in bruto  # quebras de linha iguais em qualquer sistema operacional
    texto = bruto.decode("utf-8")
    assert "≈" in texto and "÷" in texto and "−" in texto
    assert texto == gerar_dicionario_md()


def test_meses_sem_runs_usa_created_at_das_runs_reais():
    j = parse_janela("2026-01-01", "2026-03-31")
    reais = [{"created_at": "2026-01-10T09:00:00Z", "run_started_at": "2026-01-10T09:00:05Z"},
             {"created_at": "2026-02-10T09:00:00Z", "run_started_at": "2026-02-10T09:00:05Z"}]
    assert meses_sem_runs(reais, j) == 1  # março

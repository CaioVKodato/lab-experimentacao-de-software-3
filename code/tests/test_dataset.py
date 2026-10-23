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


def _entrada_cfr_b(releases, commits_por_tag):
    entrada = _entrada()
    entrada.update(releases=releases, commits_por_tag=commits_por_tag)
    return entrada


def commit_com_msg(msg):
    return {"commit": {"message": msg, "author": {"date": c(5, 10)}}}


def test_cfr_b_liga_a_heuristica_do_grupo_com_o_exemplo_do_enunciado():
    # v2.3.0 falhou (v2.3.1 só muda o patch e traz um fix); v2.4.0 não falhou.
    releases = [rel("v2.3.0", 5, 10), rel("v2.3.1", 5, 12), rel("v2.4.0", 6, 1), rel("v2.5.0", 6, 20)]
    por_tag = {"v2.3.1": [commit_com_msg("fix: crash ao abrir arquivo")]}
    linha = linha_repositorio(**_entrada_cfr_b(releases, por_tag))
    assert linha["cfr_b"] == pytest.approx(0.25)
    assert linha["cfr_b_n_avaliadas"] == 4
    assert linha["cfr_b_n_falhas"] == 1
    assert linha["cfr_b_n_censuradas"] == 0
    assert linha["cfr_b_n_sem_compare"] == 0
    assert linha["cfr_b_heuristica"] == "v1"


def test_cfr_b_sem_falhas_vale_zero_e_nao_vazio():
    linha = linha_repositorio(**_entrada())  # releases espaçadas em semanas
    assert linha["cfr_b"] == 0.0 and linha["cfr_b_n_avaliadas"] == 3


def test_cfr_b_conta_releases_censuradas_nos_7_dias_finais_da_janela():
    releases = [rel("v1.0", 3, 1), rel("v1.1", 12, 28)]
    linha = linha_repositorio(**_entrada_cfr_b(releases, {}))
    assert linha["cfr_b_n_censuradas"] == 1
    assert linha["cfr_b_n_avaliadas"] == 1


def test_cfr_b_sem_compare_aplica_so_semver_e_e_contado():
    releases = [rel("v1.0.0", 3, 1), rel("v1.0.1", 3, 3)]
    linha = linha_repositorio(**_entrada_cfr_b(releases, {"v1.0.1": None}))
    assert linha["cfr_b_n_sem_compare"] == 1
    assert linha["cfr_b_n_falhas"] == 1  # patch mudou, então o critério SemVer basta


def test_versao_da_heuristica_e_registrada_no_dataset():
    linha = linha_repositorio(**_entrada(), versao_heuristica="v2")
    assert linha["cfr_b_heuristica"] == "v2"


def test_cfr_b_vazio_quando_nao_ha_releases():
    entrada = _entrada()
    entrada.update(releases=[], commits_por_tag={})
    linha = linha_repositorio(**entrada)
    assert linha["cfr_b"] is None and linha["cfr_b_n_avaliadas"] == 0


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
    entrada = _entrada()
    entrada["metadados"] = {}  # repositório sem metadados: as colunas ficam ausentes
    escrever_csv([linha_repositorio(**entrada)], destino)
    with destino.open(encoding="utf-8", newline="") as fh:
        leitor = csv.DictReader(fh)
        linhas = list(leitor)
        assert leitor.fieldnames == nomes_colunas()
    assert len(linhas) == 1
    assert linhas[0]["repo"] == "dono/proj"
    assert linhas[0]["estrelas"] == ""       # ausente = célula vazia
    assert linhas[0]["cfr_b"] == "0.0"       # calculado e zero: NÃO é ausente
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


# ---------- variante com pré-releases (insumo da RQ07) ----------

COLUNAS_PRE = {
    "n_releases_janela_pre", "deploys_por_semana_pre", "lead_time_a_horas_pre", "lead_time_b_horas_pre",
    "lt_n_releases_avaliadas_pre", "lt_n_ignoradas_compare_pre", "cfr_b_pre", "cfr_b_n_avaliadas_pre",
}


def _entrada_com_prerelease():
    releases = [rel("v1.0", 2, 1), rel("v1.1-rc1", 3, 1, prerelease=True), rel("v1.1", 4, 1)]
    # Variante padrão: compare(v1.0...v1.1). Com pré-releases a base de v1.1 passa a ser o rc1.
    padrao = {"v1.1": commits(c(3, 10))}
    com_pre = {"v1.1-rc1": commits(c(2, 20)), "v1.1": commits(c(3, 25))}
    entrada = _entrada()
    entrada.update(releases=releases, commits_por_tag=padrao, commits_por_tag_pre=com_pre)
    return entrada


def test_esquema_tem_as_colunas_da_variante_com_prerelease():
    assert COLUNAS_PRE <= set(nomes_colunas())


def test_variante_padrao_ignora_prerelease_e_a_variante_pre_a_inclui():
    linha = linha_repositorio(**_entrada_com_prerelease())
    assert linha["n_releases_janela"] == 2 and linha["n_releases_janela_pre"] == 3
    assert linha["deploys_por_semana_pre"] > linha["deploys_por_semana"]
    assert linha["deploys_por_semana_pre"] == pytest.approx(3 * 7 / 365)


def test_lead_time_pre_usa_a_base_correta_de_cada_variante():
    linha = linha_repositorio(**_entrada_com_prerelease())
    assert linha["lead_time_a_horas"] == pytest.approx(22 * DIA)       # v1.0 -> v1.1, commit de 10/03
    assert linha["lead_time_a_horas_pre"] == pytest.approx(8 * DIA)    # mediana de 9d (rc1) e 7d (v1.1)
    assert linha["lead_time_b_horas_pre"] == pytest.approx(8 * DIA)
    assert linha["lt_n_releases_avaliadas_pre"] == 2 and linha["lt_n_ignoradas_compare_pre"] == 0


def test_cfr_b_pre_tem_denominador_proprio():
    linha = linha_repositorio(**_entrada_com_prerelease())
    assert linha["cfr_b_n_avaliadas"] == 2 and linha["cfr_b_n_avaliadas_pre"] == 3
    assert linha["cfr_b_pre"] == 0.0


def test_sem_prerelease_na_amostra_as_duas_variantes_coincidem():
    entrada = _entrada()
    entrada["commits_por_tag_pre"] = entrada["commits_por_tag"]
    linha = linha_repositorio(**entrada)
    assert linha["lead_time_a_horas_pre"] == linha["lead_time_a_horas"]
    assert linha["lead_time_b_horas_pre"] == linha["lead_time_b_horas"]
    assert linha["cfr_b_pre"] == linha["cfr_b"]
    assert linha["deploys_por_semana_pre"] == linha["deploys_por_semana"]


def test_sem_mapa_da_variante_pre_so_as_colunas_de_releases_sao_calculadas():
    linha = linha_repositorio(**_entrada_com_prerelease() | {"commits_por_tag_pre": None})
    assert linha["n_releases_janela_pre"] == 3 and linha["deploys_por_semana_pre"] is not None
    for nome in ("lead_time_a_horas_pre", "lead_time_b_horas_pre", "lt_n_releases_avaliadas_pre",
                 "lt_n_ignoradas_compare_pre", "cfr_b_pre", "cfr_b_n_avaliadas_pre"):
        assert linha[nome] is None, nome


# ---------- estrato da amostragem (insumo da RQ06) ----------

def test_esquema_tem_a_coluna_do_estrato():
    assert "faixa_estrelas" in nomes_colunas()


def test_estrato_informado_passa_para_a_linha_sem_ser_recalculado():
    # Sorteado em 1000-2000, mas com 2003 estrelas hoje: o estrato é o do sorteio.
    entrada = _entrada()
    entrada["metadados"] = {"estrelas": 2003}
    linha = linha_repositorio(**entrada, faixa_estrelas="1000-2000")
    assert linha["faixa_estrelas"] == "1000-2000" and linha["estrelas"] == 2003


def test_estrato_nao_informado_fica_vazio_e_nao_quebra():
    assert linha_repositorio(**_entrada())["faixa_estrelas"] is None


# ---------- lacunas de coleta: dado AUSENTE não pode se passar por dado ZERO ----------

def test_esquema_tem_a_coluna_de_lacunas_de_coleta():
    assert "lacunas_de_coleta" in nomes_colunas()


def test_lacunas_informadas_passam_para_a_linha():
    assert linha_repositorio(**_entrada(), lacunas_de_coleta=3)["lacunas_de_coleta"] == 3
    assert linha_repositorio(**_entrada(), lacunas_de_coleta=0)["lacunas_de_coleta"] == 0


def test_lacunas_nao_informadas_ficam_vazias():
    assert linha_repositorio(**_entrada())["lacunas_de_coleta"] is None

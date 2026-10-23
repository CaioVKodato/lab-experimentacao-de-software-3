"""Testes da RQ05 (Spearman entre deploys e CFR)."""
import pandas as pd
import pytest

from analise.dados_exemplo import gerar_linhas
from analise.rq05 import (
    VARIANTES_PADRAO,
    Variante,
    _parse_variantes,
    analisar,
    calcular_spearman,
    filtrar_completos,
    gravar_csv,
    main,
    plotar,
    relatorio_md,
)
from pipeline.dataset import escrever_csv


def _df(deploys, cfr_a=None, cfr_b=None):
    linhas = {"deploys_por_semana": list(deploys)}
    if cfr_a is not None:
        linhas["cfr_a"] = list(cfr_a)
    if cfr_b is not None:
        linhas["cfr_b"] = list(cfr_b)
    return pd.DataFrame(linhas)


# ---------- calcular_spearman: sinal e magnitude conhecidos ----------

def test_correlacao_negativa_perfeita_entre_rankings():
    df = _df(deploys=[1, 2, 3, 4, 5], cfr_a=[0.5, 0.4, 0.3, 0.2, 0.1])
    r = calcular_spearman(df, VARIANTES_PADRAO[0])
    assert r.rho == pytest.approx(-1.0)
    assert r.p_valor < 0.05
    assert r.n == 5 and r.n_excluidos == 0


def test_correlacao_positiva_perfeita_entre_rankings():
    df = _df(deploys=[1, 2, 3, 4, 5], cfr_a=[0.1, 0.2, 0.3, 0.4, 0.5])
    r = calcular_spearman(df, VARIANTES_PADRAO[0])
    assert r.rho == pytest.approx(1.0)


def test_sem_correlacao_quando_cfr_e_constante():
    df = _df(deploys=[1, 2, 3, 4, 5], cfr_a=[0.1, 0.1, 0.1, 0.1, 0.1])
    r = calcular_spearman(df, VARIANTES_PADRAO[0])
    assert r.rho is None and r.p_valor is None
    assert "variância" in r.nota


def test_cfr_ausente_e_excluido_e_contado():
    df = _df(deploys=[1.0, 2.0, 3.0, 4.0, 5.0], cfr_a=[0.5, None, 0.3, None, 0.1])
    r = calcular_spearman(df, VARIANTES_PADRAO[0])
    assert r.n == 3 and r.n_excluidos == 2
    assert r.rho == pytest.approx(-1.0)


def test_poucas_linhas_apos_exclusao_dao_rho_none_com_nota():
    df = _df(deploys=[1.0, 2.0], cfr_a=[0.1, 0.2])
    r = calcular_spearman(df, VARIANTES_PADRAO[0])
    assert r.rho is None and "n < 3" in r.nota


def test_coluna_inexistente_falha_com_nome_da_coluna():
    df = _df(deploys=[1, 2, 3])  # sem cfr_a
    with pytest.raises(KeyError, match="cfr_a"):
        calcular_spearman(df, VARIANTES_PADRAO[0])


def test_variante_pre_pode_apontar_para_colunas_diferentes():
    df = pd.DataFrame({"d_pre": [1, 2, 3, 4], "c_pre": [0.4, 0.3, 0.2, 0.1]})
    var = Variante("b_pre", "c_pre", "d_pre")
    r = calcular_spearman(df, var)
    assert r.rho == pytest.approx(-1.0)


# ---------- analisar: as duas variantes de uma vez ----------

def _df_exemplo(n=120, seed=3):
    return pd.DataFrame(gerar_linhas(n=n, seed=seed))


def test_duas_variantes_dao_duas_correlacoes():
    res = analisar(_df_exemplo())
    assert [c.variante for c in res.correlacoes] == ["a", "b"]
    assert res.n_total == 120


def test_dados_exemplo_tem_sinal_negativo_entre_deploys_e_cfr_a():
    """`dados_exemplo.py` embute cfr_a = beta - 0.03*log(deploys): esperamos rho negativo."""
    res = analisar(_df_exemplo(n=200, seed=1))
    rho_a = next(c for c in res.correlacoes if c.variante == "a").rho
    assert rho_a is not None and rho_a < 0


def test_subconjunto_de_variantes_e_respeitado():
    res = analisar(_df_exemplo(40), variantes=(VARIANTES_PADRAO[0],))
    assert len(res.correlacoes) == 1 and res.correlacoes[0].variante == "a"


# ---------- parser de variantes ----------

def test_parse_ambas_devolve_as_duas():
    assert _parse_variantes("ambas") == VARIANTES_PADRAO


def test_parse_variante_isolada():
    assert _parse_variantes("a") == (VARIANTES_PADRAO[0],)
    assert _parse_variantes("b") == (VARIANTES_PADRAO[1],)


def test_parse_variante_desconhecida_falha():
    with pytest.raises(ValueError, match="variante desconhecida"):
        _parse_variantes("c")


# ---------- filtrar_completos ----------

def test_filtrar_completos_mantem_so_lacunas_zero():
    df = pd.DataFrame({"repo": list("abcd"), "lacunas_de_coleta": [0, 2, 0, None]})
    completos, excluidos = filtrar_completos(df)
    assert list(completos["repo"]) == ["a", "c"] and excluidos == 2


def test_filtrar_completos_sem_coluna_nao_filtra():
    df = pd.DataFrame({"repo": list("ab")})
    completos, excluidos = filtrar_completos(df)
    assert len(completos) == 2 and excluidos == 0


# ---------- relatório, CSV, figura, CLI ----------

def test_relatorio_md_cita_cada_variante_e_disclaimer_causal():
    res = analisar(_df_exemplo(60, seed=4))
    md = relatorio_md(res)
    assert "variante" in md and "causalidade" in md
    for v in ("a", "b"):
        assert f"| {v} |" in md


def test_gravar_csv_tem_colunas_e_valores(tmp_path):
    res = analisar(_df_exemplo(40, seed=4))
    destino = gravar_csv(res, tmp_path / "rq05.csv")
    conteudo = destino.read_text(encoding="utf-8")
    assert "variante,n,n_excluidos,rho_spearman,p_valor,nota" in conteudo
    assert conteudo.count("\n") == 1 + len(res.correlacoes)  # header + 1 por variante (sem linha extra)


def test_plotar_grava_figura(tmp_path):
    pytest.importorskip("matplotlib")
    df = _df_exemplo(80, seed=4)
    res = analisar(df)
    arquivos = plotar(df, res, tmp_path)
    assert len(arquivos) == 1 and arquivos[0].is_file() and arquivos[0].stat().st_size > 0


def test_linha_de_comando_de_ponta_a_ponta(tmp_path, capsys):
    csv = escrever_csv(gerar_linhas(n=80, seed=5), tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras"]) == 0
    assert (tmp_path / "docs" / "rq05.md").is_file()
    assert (tmp_path / "docs" / "rq05_correlacoes.csv").is_file()
    saida = capsys.readouterr().out
    assert "Spearman" in saida and "variante" in saida


def test_cli_variante_isolada_grava_so_essa(tmp_path):
    csv = escrever_csv(gerar_linhas(n=40, seed=5), tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras", "--variante", "a"]) == 0
    conteudo = (tmp_path / "docs" / "rq05_correlacoes.csv").read_text(encoding="utf-8")
    assert conteudo.count("\n") == 2  # header + 1 variante


def test_cli_sem_linhas_completas_sai_com_erro(tmp_path, capsys):
    linhas = gerar_linhas(n=10, seed=8)
    for l in linhas:
        l["lacunas_de_coleta"] = 1
    csv = escrever_csv(linhas, tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras"]) == 2
    assert "nenhuma linha completa" in capsys.readouterr().out
    assert not (tmp_path / "docs" / "rq05.md").exists()


def test_cli_incluir_incompletos_mantem_todos_e_avisa(tmp_path):
    linhas = gerar_linhas(n=30, seed=7)
    for l in linhas[:5]:
        l["lacunas_de_coleta"] = 3
    csv = escrever_csv(linhas, tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras",
                 "--incluir-incompletos"]) == 0
    texto = (tmp_path / "docs" / "rq05.md").read_text(encoding="utf-8")
    assert "Repositórios no CSV: **30**" in texto and "ATENÇÃO" in texto

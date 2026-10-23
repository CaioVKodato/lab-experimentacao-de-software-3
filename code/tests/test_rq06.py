"""Testes da RQ06 (Kruskal-Wallis + Holm + tamanho de efeito)."""
import numpy as np
import pandas as pd
import pytest

from analise.dados_exemplo import gerar_linhas
from analise.rq06 import (
    AGRUPADOR_OUTROS,
    METRICAS,
    Fator,
    _linguagem,
    _quartis,
    analisar,
    classe_cliff,
    classe_epsilon2,
    cliff_delta,
    epsilon_squared,
    fatores_padrao,
    filtrar_completos,
    gravar_csv,
    main,
    relatorio_md,
    um_teste,
)
from pipeline.dataset import escrever_csv


# ---------- quartis e agrupamento de linguagem ----------

def test_quartis_distribuem_valores_em_quatro_categorias():
    valores = pd.Series(list(range(20)))
    q = _quartis(valores)
    assert set(q.dropna().unique()) == {"Q1", "Q2", "Q3", "Q4"}


def test_quartis_preservam_na():
    valores = pd.Series([1.0, None, 2.0, None, 3.0, 4.0, 5.0, 6.0, 7.0])
    q = _quartis(valores)
    assert q.isna().sum() == 2


def test_quartis_com_coluna_constante_dao_tudo_nan():
    q = _quartis(pd.Series([1.0, 1.0, 1.0, 1.0]))
    assert q.isna().all()


def test_linguagem_com_poucas_entradas_vira_outros():
    df = pd.DataFrame({"linguagem_principal": ["Python"] * 5 + ["Elm"] + ["Rust"] * 5})
    g = _linguagem(df, minimo=3)
    assert (g == AGRUPADOR_OUTROS).sum() == 1
    assert set(g.dropna().unique()) == {"Python", "Rust", AGRUPADOR_OUTROS}


# ---------- tamanho de efeito ----------

def test_epsilon2_eh_h_sobre_n_menos_1():
    assert epsilon_squared(h=10.0, n=51) == pytest.approx(10 / 50)


def test_epsilon2_com_n_1_e_none():
    assert epsilon_squared(h=1.0, n=1) is None


def test_cliff_delta_mais_1_quando_x_sempre_maior():
    assert cliff_delta([5, 6, 7], [1, 2, 3]) == pytest.approx(1.0)


def test_cliff_delta_menos_1_quando_x_sempre_menor():
    assert cliff_delta([1, 2, 3], [5, 6, 7]) == pytest.approx(-1.0)


def test_cliff_delta_zero_quando_distribuicoes_iguais():
    assert cliff_delta([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)


def test_classe_epsilon2_segue_corte_de_tomczak():
    assert classe_epsilon2(0.005) == "trivial"
    assert classe_epsilon2(0.03) == "pequeno"
    assert classe_epsilon2(0.10) == "moderado"
    assert classe_epsilon2(0.20) == "grande"
    assert classe_epsilon2(None) == ""


def test_classe_cliff_segue_corte_de_romano():
    assert classe_cliff(0.05) == "trivial"
    assert classe_cliff(0.20) == "pequeno"
    assert classe_cliff(0.40) == "moderado"
    assert classe_cliff(0.50) == "grande"


# ---------- um_teste: diferença conhecida vs sem diferença ----------

def _df_com_grupos(valores_por_grupo: dict[str, list[float]], coluna="metrica"):
    linhas = []
    for g, vs in valores_por_grupo.items():
        for v in vs:
            linhas.append({"grupo": g, coluna: v})
    return pd.DataFrame(linhas)


def _fator_grupo():
    return Fator("grupo", lambda df: df["grupo"].astype(object))


def test_tres_grupos_com_diferenca_grande_detectam_kruskal_e_efeito_grande():
    rng = np.random.default_rng(42)
    df = _df_com_grupos({
        "A": list(rng.normal(1, 0.1, 20)),
        "B": list(rng.normal(5, 0.1, 20)),
        "C": list(rng.normal(10, 0.1, 20)),
    })
    t = um_teste(df, _fator_grupo(), "metrica")
    assert t.teste == "kruskal-wallis" and t.k_grupos == 3
    assert t.p_bruto < 0.001
    assert t.efeito_tipo == "epsilon2" and t.efeito_classe == "grande"


def test_tres_grupos_sem_diferenca_nao_detectam():
    rng = np.random.default_rng(42)
    df = _df_com_grupos({
        "A": list(rng.normal(5, 1, 30)),
        "B": list(rng.normal(5, 1, 30)),
        "C": list(rng.normal(5, 1, 30)),
    })
    t = um_teste(df, _fator_grupo(), "metrica")
    assert t.teste == "kruskal-wallis"
    assert t.p_bruto > 0.1
    assert t.efeito_classe in {"trivial", "pequeno"}


def test_dois_grupos_usam_mann_whitney_e_cliff():
    rng = np.random.default_rng(1)
    df = _df_com_grupos({
        "A": list(rng.normal(1, 0.1, 20)),
        "B": list(rng.normal(5, 0.1, 20)),
    })
    t = um_teste(df, _fator_grupo(), "metrica")
    assert t.teste == "mann-whitney" and t.k_grupos == 2
    assert t.p_bruto < 0.001
    assert t.efeito_tipo == "cliff_delta" and abs(t.efeito_valor) > 0.9
    assert t.efeito_classe == "grande"


def test_grupo_abaixo_do_minimo_e_descartado_e_pode_sobrar_so_1():
    df = _df_com_grupos({"A": [1.0, 2.0, 3.0, 4.0], "B": [10.0]})
    t = um_teste(df, _fator_grupo(), "metrica", minimo=3)
    assert t.teste == "indeterminado" and "menos de 2 grupos" in t.nota


def test_coluna_metrica_ausente_marca_indeterminado():
    df = _df_com_grupos({"A": [1.0, 2.0, 3.0]})
    t = um_teste(df, _fator_grupo(), "nao_existe")
    assert t.teste == "indeterminado" and "nao_existe" in t.nota


def test_sem_variancia_em_grupos_marca_sem_calculo():
    df = _df_com_grupos({"A": [1.0, 1.0, 1.0], "B": [1.0, 1.0, 1.0]})
    t = um_teste(df, _fator_grupo(), "metrica")
    assert t.p_bruto is None and "variância" in t.nota


# ---------- analisar: Holm aplicado sobre o conjunto ----------

def _df_exemplo(n=120, seed=3):
    return pd.DataFrame(gerar_linhas(n=n, seed=seed))


def test_quatro_fatores_quatro_metricas_dao_16_testes():
    res = analisar(_df_exemplo())
    assert len(res.testes) == 4 * 4
    assert set(res.fatores) >= {"linguagem_principal", "estrelas_q", "contribuidores_q", "idade_anos_q"}
    assert set(res.metricas) == set(METRICAS)


def test_holm_eh_sempre_maior_ou_igual_ao_bruto():
    res = analisar(_df_exemplo(n=150, seed=5))
    for t in res.testes:
        if t.p_bruto is not None and t.p_holm is not None:
            assert t.p_holm + 1e-12 >= t.p_bruto


def test_holm_so_corrige_entre_testes_com_p_valor():
    res = analisar(_df_exemplo(n=60, seed=7))
    com_p = [t for t in res.testes if t.p_bruto is not None]
    sem_p = [t for t in res.testes if t.p_bruto is None]
    assert all(t.p_holm is not None for t in com_p)
    assert all(t.p_holm is None for t in sem_p)


# ---------- filtrar_completos ----------

def test_filtrar_completos_remove_lacunas():
    df = pd.DataFrame({"repo": list("abcd"), "lacunas_de_coleta": [0, 1, 0, 2]})
    completos, excluidos = filtrar_completos(df)
    assert list(completos["repo"]) == ["a", "c"] and excluidos == 2


def test_filtrar_completos_sem_coluna_nao_filtra():
    df = pd.DataFrame({"repo": list("ab")})
    completos, excluidos = filtrar_completos(df)
    assert len(completos) == 2 and excluidos == 0


# ---------- relatório + CSV + CLI ----------

def test_relatorio_cita_fatores_metricas_e_corrige_holm():
    res = analisar(_df_exemplo(80, seed=4))
    md = relatorio_md(res)
    assert "Kruskal-Wallis" in md and "Holm" in md
    for f in res.fatores:
        assert f in md


def test_gravar_csv_tem_todas_as_linhas(tmp_path):
    res = analisar(_df_exemplo(50, seed=4))
    destino = gravar_csv(res, tmp_path / "rq06.csv")
    conteudo = destino.read_text(encoding="utf-8")
    assert "p_holm" in conteudo
    assert conteudo.count("\n") == 1 + len(res.testes)


def test_linha_de_comando_de_ponta_a_ponta(tmp_path, capsys):
    csv = escrever_csv(gerar_linhas(n=80, seed=5), tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs")]) == 0
    assert (tmp_path / "docs" / "rq06.md").is_file()
    assert (tmp_path / "docs" / "rq06_testes.csv").is_file()
    assert "Holm" in capsys.readouterr().out


def test_cli_sem_linhas_completas_sai_com_erro(tmp_path, capsys):
    linhas = gerar_linhas(n=10, seed=8)
    for l in linhas:
        l["lacunas_de_coleta"] = 1
    csv = escrever_csv(linhas, tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs")]) == 2
    assert "nenhuma linha completa" in capsys.readouterr().out


def test_cli_minimo_por_grupo_customizado(tmp_path):
    csv = escrever_csv(gerar_linhas(n=60, seed=5), tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--minimo-por-grupo", "5"]) == 0
    texto = (tmp_path / "docs" / "rq06.md").read_text(encoding="utf-8")
    assert "n < 5" in texto


# ---------- fatores_padrao com referência fixa (idade reprodutível) ----------

def test_idade_quartis_sao_estaveis_quando_referencia_e_fixa():
    import datetime as dt
    ref = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
    fs = fatores_padrao(referencia=ref)
    nomes = [f.nome for f in fs]
    assert "idade_anos_q" in nomes

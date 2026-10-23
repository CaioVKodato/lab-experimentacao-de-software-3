"""Testes da RQ07 (sensibilidade da classificação DORA), sem depender do módulo do Caio."""
import statistics
import sys

import pandas as pd
import pytest

from analise.dados_exemplo import gerar_linhas
from analise.rq07 import (
    CATEGORIAS,
    COMBINACOES_PADRAO,
    Combinacao,
    analisar,
    classificar_combinacao,
    comparar_par,
    main,
    plotar,
    relatorio_md,
)
from pipeline.dataset import escrever_csv


def so_por_frequencia(deploys, lead_time, cfr, recuperacao):
    """Classificador de teste: olha só a frequência de deploy (isola a lógica da análise)."""
    return "Elite" if deploys >= 7 else "High" if deploys >= 1 else "Medium" if deploys >= 0.23 else "Low"


def tabela_de_referencia(deploys, lead_time, cfr, recuperacao):
    """Reimplementação de teste da tabela do enunciado (oráculo para o teste de ponta a ponta)."""
    notas = [
        4 if deploys >= 7 else 3 if deploys >= 1 else 2 if deploys >= 12 / 52.1429 else 1,
        4 if lead_time < 24 else 3 if lead_time < 168 else 2 if lead_time < 720 else 1,
        4 if cfr <= 0.15 else 3 if cfr <= 0.30 else 2 if cfr <= 0.45 else 1,
        4 if recuperacao < 1 else 3 if recuperacao < 24 else 2 if recuperacao < 168 else 1,
    ]
    return {4: "Elite", 3: "High", 2: "Medium", 1: "Low"}[int(statistics.median(notas))]


def serie(*valores):
    return pd.Series(list(valores), dtype=object)


# ---------- classificar uma combinação ----------

def test_metrica_ausente_impede_a_classificacao_do_repositorio():
    df = pd.DataFrame({"d": [8.0, 8.0], "l": [10.0, None], "c": [0.1, 0.1], "r": [0.5, 0.5]})
    comb = Combinacao("X", "d", "l", "c", "r")
    resultado = classificar_combinacao(df, comb, lambda *a: "Elite")
    assert resultado.iloc[0] == "Elite" and pd.isna(resultado.iloc[1])


def test_categoria_invalida_do_classificador_falha_com_mensagem_clara():
    df = pd.DataFrame({"d": [1.0], "l": [1.0], "c": [0.1], "r": [1.0]})
    with pytest.raises(ValueError, match="elite"):
        classificar_combinacao(df, Combinacao("X", "d", "l", "c", "r"), lambda *a: "elite")


# ---------- comparação de um par ----------

def test_classificacoes_identicas_dao_zero_mudanca_e_kappa_um():
    a = serie("Elite", "High", "Low", "Medium")
    res = comparar_par(a, a.copy(), "C1", "C2")
    assert res.n == 4 and res.pct_mudou == 0.0 and res.kappa_ponderado == pytest.approx(1.0)


def test_discordancia_total_entre_extremos_da_kappa_menos_um():
    a = serie("Elite", "Elite", "Low", "Low")
    b = serie("Low", "Low", "Elite", "Elite")
    res = comparar_par(a, b, "C1", "C2")
    assert res.pct_mudou == 1.0 and res.kappa_ponderado == pytest.approx(-1.0)


def test_ponderacao_linear_pune_mais_a_troca_elite_low_do_que_elite_high():
    base = ["Elite"] * 5 + ["Low"] * 5
    perto = serie(*(["High"] + ["Elite"] * 4 + ["Low"] * 5))
    longe = serie(*(["Low"] + ["Elite"] * 4 + ["Low"] * 5))
    k_perto = comparar_par(serie(*base), perto, "C1", "C2").kappa_ponderado
    k_longe = comparar_par(serie(*base), longe, "C1", "C2").kappa_ponderado
    assert k_perto > k_longe


def test_repositorio_nao_classificavel_em_alguma_das_duas_combinacoes_fica_de_fora_do_par():
    a = serie("Elite", None, "Low", "High")
    b = serie("Elite", "Low", None, "Elite")
    res = comparar_par(a, b, "C1", "C2")
    assert res.n == 2 and res.pct_mudou == pytest.approx(0.5)


def test_sem_variancia_o_kappa_e_indefinido_e_isso_fica_explicito():
    a = serie("High", "High", "High")
    res = comparar_par(a, a.copy(), "C1", "C2")
    assert res.pct_mudou == 0.0 and res.kappa_ponderado is None and "variância" in res.nota


def test_par_sem_nenhum_repositorio_comum_nao_quebra():
    res = comparar_par(serie(None, None), serie("Elite", "Low"), "C1", "C2")
    assert res.n == 0 and res.pct_mudou is None and res.kappa_ponderado is None


def test_matriz_de_confusao_conta_cada_transicao():
    a = serie("Elite", "Elite", "Low")
    b = serie("Elite", "High", "Low")
    m = comparar_par(a, b, "C1", "C2").matriz
    assert m["Elite"]["Elite"] == 1 and m["Elite"]["High"] == 1 and m["Low"]["Low"] == 1
    assert sum(sum(linha.values()) for linha in m.values()) == 3


# ---------- análise completa ----------

def _df_exemplo(n=120, seed=3):
    return pd.DataFrame(gerar_linhas(n=n, seed=seed))


def test_tres_combinacoes_geram_tres_pares():
    res = analisar(_df_exemplo(), classificador=tabela_de_referencia)
    assert [c for c in res.combinacoes] == ["C1", "C2", "C3"]
    assert [(p.a, p.b) for p in res.pares] == [("C1", "C2"), ("C1", "C3"), ("C2", "C3")]


def test_distribuicao_e_contagens_batem_com_o_total():
    res = analisar(_df_exemplo(), classificador=tabela_de_referencia)
    for nome in res.combinacoes:
        assert sum(res.distribuicao[nome].values()) == res.n_classificados[nome]
        assert res.n_classificados[nome] <= res.n_total
        assert set(res.distribuicao[nome]) == set(CATEGORIAS)


def test_valores_estao_nos_intervalos_validos():
    for par in analisar(_df_exemplo(), classificador=tabela_de_referencia).pares:
        assert 0.0 <= par.pct_mudou <= 1.0
        assert par.kappa_ponderado is None or -1.0 <= par.kappa_ponderado <= 1.0


def test_combinacoes_podem_ser_trocadas_por_configuracao():
    df = _df_exemplo(40)
    duas = (COMBINACOES_PADRAO[0], COMBINACOES_PADRAO[1])
    res = analisar(df, combinacoes=duas, classificador=so_por_frequencia)
    assert res.combinacoes == ("C1", "C2") and len(res.pares) == 1


def test_combinacao_com_coluna_inexistente_avisa_qual_coluna_falta():
    df = _df_exemplo(10).drop(columns=["cfr_b_pre"])
    with pytest.raises(KeyError, match="cfr_b_pre"):
        analisar(df, classificador=so_por_frequencia)


def test_sem_o_modulo_do_caio_a_mensagem_diz_o_que_fazer(monkeypatch):
    monkeypatch.setitem(sys.modules, "metricas.classificacao_dora", None)
    with pytest.raises(RuntimeError, match="classificacao_dora"):
        analisar(_df_exemplo(10))


# ---------- relatório, figuras e linha de comando ----------

def test_relatorio_mostra_combinacoes_pares_e_exclusoes():
    res = analisar(_df_exemplo(), classificador=tabela_de_referencia)
    md = relatorio_md(res)
    for trecho in ("C1", "C2", "C3", "C1 × C2", "kappa", "% que mudou", "classificáveis"):
        assert trecho in md


def test_figuras_sao_gravadas(tmp_path):
    pytest.importorskip("matplotlib")
    res = analisar(_df_exemplo(), classificador=tabela_de_referencia)
    arquivos = plotar(res, tmp_path)
    assert len(arquivos) == 2 and all(a.is_file() and a.stat().st_size > 0 for a in arquivos)


def test_linha_de_comando_de_ponta_a_ponta(tmp_path, monkeypatch, capsys):
    import types
    modulo = types.ModuleType("metricas.classificacao_dora")
    modulo.classificar_dora = tabela_de_referencia
    monkeypatch.setitem(sys.modules, "metricas.classificacao_dora", modulo)
    csv = escrever_csv(gerar_linhas(n=80, seed=5), tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras"]) == 0
    assert (tmp_path / "docs" / "rq07.md").is_file()
    assert "C1 × C2" in capsys.readouterr().out


def test_sem_matplotlib_o_script_avisa_e_ainda_grava_o_relatorio(tmp_path, monkeypatch, capsys):
    import types
    modulo = types.ModuleType("metricas.classificacao_dora")
    modulo.classificar_dora = tabela_de_referencia
    monkeypatch.setitem(sys.modules, "metricas.classificacao_dora", modulo)
    monkeypatch.setitem(sys.modules, "matplotlib", None)  # simula pacote não instalado
    csv = escrever_csv(gerar_linhas(n=30, seed=6), tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs")]) == 0
    assert (tmp_path / "docs" / "rq07.md").is_file()
    assert "matplotlib não instalado" in capsys.readouterr().out


# ---------- só linhas completas entram: dado cortado não pode se passar por dado inteiro ----------

from analise.rq07 import filtrar_completos  # noqa: E402


def _sem_classificador_real(monkeypatch):
    import types
    modulo = types.ModuleType("metricas.classificacao_dora")
    modulo.classificar_dora = tabela_de_referencia
    monkeypatch.setitem(sys.modules, "metricas.classificacao_dora", modulo)


def test_filtrar_completos_mantem_so_lacunas_zero_e_conta_as_excluidas():
    import pandas as pd
    df = pd.DataFrame({"repo": list("abcd"), "lacunas_de_coleta": [0, 2, 0, None]})
    completos, excluidos = filtrar_completos(df)
    assert list(completos["repo"]) == ["a", "c"] and excluidos == 2  # vazio = desconhecido = excluído


def test_filtrar_completos_sem_a_coluna_nao_filtra_nada():
    import pandas as pd
    df = pd.DataFrame({"repo": list("ab")})
    completos, excluidos = filtrar_completos(df)
    assert len(completos) == 2 and excluidos == 0


def test_linha_de_comando_exclui_incompletos_e_o_relatorio_diz_quantos(tmp_path, monkeypatch, capsys):
    _sem_classificador_real(monkeypatch)
    linhas = gerar_linhas(n=40, seed=7)
    for l in linhas[:10]:
        l["lacunas_de_coleta"] = 4
    csv = escrever_csv(linhas, tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras"]) == 0
    texto = (tmp_path / "docs" / "rq07.md").read_text(encoding="utf-8")
    assert "Repositórios no CSV: **30**" in texto
    assert "40" in texto and "excluídas por coleta incompleta" in texto and "10" in texto


def test_incluir_incompletos_mantem_todos_e_avisa_no_relatorio(tmp_path, monkeypatch):
    _sem_classificador_real(monkeypatch)
    linhas = gerar_linhas(n=40, seed=7)
    for l in linhas[:10]:
        l["lacunas_de_coleta"] = 4
    csv = escrever_csv(linhas, tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras", "--incluir-incompletos"]) == 0
    texto = (tmp_path / "docs" / "rq07.md").read_text(encoding="utf-8")
    assert "Repositórios no CSV: **40**" in texto and "ATENÇÃO" in texto


def test_sem_nenhuma_linha_completa_falha_com_mensagem_clara(tmp_path, monkeypatch, capsys):
    _sem_classificador_real(monkeypatch)
    linhas = gerar_linhas(n=10, seed=8)
    for l in linhas:
        l["lacunas_de_coleta"] = 1
    csv = escrever_csv(linhas, tmp_path / "ex.csv")
    assert main([str(csv), "--saida", str(tmp_path / "docs"), "--sem-figuras"]) == 2
    assert "nenhuma linha completa" in capsys.readouterr().out
    assert not (tmp_path / "docs" / "rq07.md").exists()

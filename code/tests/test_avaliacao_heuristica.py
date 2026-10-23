"""Testes da avaliação da heurística corretiva contra o consenso."""
import pandas as pd
import pytest

from analise.avaliacao_heuristica import (
    F1_MINIMO_RECOMENDADO,
    avaliar,
    gravar_csv_cumulativo,
    ler_csv_cumulativo,
    main,
    relatorio_md,
)


def _df(pares):
    """[(full, tag, rotulo), ...] -> DataFrame com as 3 colunas canônicas."""
    return pd.DataFrame(pares, columns=["full_name", "release_tag", "eh_corretiva"])


# ---------- caso perfeito e caso ruim conhecido ----------

def test_concordancia_total_da_f1_um():
    dados = [("x/a", "v1.0.1", "sim"), ("x/a", "v1.0.2", "nao"),
             ("y/b", "v2.0.0", "sim"), ("y/b", "v2.0.1", "nao")]
    res = avaliar(_df(dados), _df(dados))
    assert res.precisao == 1.0 and res.revocacao == 1.0 and res.f1 == 1.0
    assert res.acuracia == 1.0 and res.vp == 2 and res.vn == 2 and res.fp == 0 and res.fn == 0


def test_todos_errados_dao_f1_zero():
    cons = _df([("x/a", "v1", "sim"), ("x/a", "v2", "nao")])
    heur = _df([("x/a", "v1", "nao"), ("x/a", "v2", "sim")])
    res = avaliar(heur, cons)
    assert res.f1 == 0.0 and res.precisao == 0.0 and res.revocacao == 0.0


def test_precisao_e_revocacao_coincidem_com_formula():
    """4 verdadeiros positivos, 2 falsos positivos, 1 falso negativo, 3 verdadeiros negativos."""
    cons = _df([("x/a", f"v{i}", r) for i, r in enumerate([
        "sim", "sim", "sim", "sim", "sim",   # 5 positivos reais
        "nao", "nao", "nao", "nao", "nao",   # 5 negativos reais
    ])])
    heur = _df([("x/a", f"v{i}", r) for i, r in enumerate([
        "sim", "sim", "sim", "sim", "nao",   # 4 VP, 1 FN
        "sim", "sim", "nao", "nao", "nao",   # 2 FP, 3 VN
    ])])
    res = avaliar(heur, cons)
    assert res.vp == 4 and res.fn == 1 and res.fp == 2 and res.vn == 3
    assert res.precisao == pytest.approx(4 / 6)
    assert res.revocacao == pytest.approx(4 / 5)
    assert res.f1 == pytest.approx(2 * (4/6) * (4/5) / ((4/6) + (4/5)))


# ---------- cobertura parcial, labels inválidos ----------

def test_releases_sem_heuristica_vao_para_n_sem_heuristica():
    cons = _df([("x/a", "v1", "sim"), ("x/a", "v2", "nao"), ("y/b", "v3", "sim")])
    heur = _df([("x/a", "v1", "sim")])  # cobre só uma
    res = avaliar(heur, cons)
    assert res.n_total == 3 and res.n_pareadas == 1 and res.n_sem_heuristica == 2
    assert res.vp == 1


def test_sem_pareamento_nenhum_dá_metricas_none_com_nota():
    cons = _df([("x/a", "v1", "sim")])
    heur = _df([("outro/repo", "v9", "nao")])
    res = avaliar(heur, cons)
    assert res.n_pareadas == 0 and res.f1 is None and "nenhuma release" in res.nota


def test_rotulos_fora_do_vocabulario_falham_com_mensagem_clara():
    cons = _df([("x/a", "v1", "talvez")])
    heur = _df([("x/a", "v1", "sim")])
    with pytest.raises(ValueError, match="talvez"):
        avaliar(heur, cons)


def test_rotulos_case_insensitive_e_com_espaco():
    cons = _df([("x/a", "v1", " SIM "), ("x/a", "v2", "Nao")])
    heur = _df([("x/a", "v1", "sim"), ("x/a", "v2", "NAO")])
    res = avaliar(heur, cons)
    assert res.f1 == 1.0


def test_coluna_obrigatoria_faltando_falha():
    cons = _df([("x/a", "v1", "sim")])
    heur = cons.drop(columns=["eh_corretiva"])
    with pytest.raises(KeyError, match="eh_corretiva"):
        avaliar(heur, cons)


# ---------- nota de F1 abaixo do alvo ----------

def test_f1_abaixo_do_alvo_gera_nota_para_refinar():
    cons = _df([("x/a", f"v{i}", "sim") for i in range(5)] +
               [("x/a", f"w{i}", "nao") for i in range(5)])
    heur = _df([("x/a", f"v{i}", "nao") for i in range(5)] +   # tudo errado nos sim
               [("x/a", f"w{i}", "nao") for i in range(5)])
    res = avaliar(heur, cons)
    assert res.f1 < F1_MINIMO_RECOMENDADO and "refinar" in res.nota


def test_f1_acima_do_alvo_nao_gera_nota():
    cons = _df([("x/a", f"v{i}", "sim") for i in range(9)] + [("x/a", "w1", "nao")])
    heur = _df([("x/a", f"v{i}", "sim") for i in range(9)] + [("x/a", "w1", "nao")])
    res = avaliar(heur, cons)
    assert res.f1 == 1.0 and res.nota == ""


def test_f1_minimo_customizado_é_respeitado():
    cons = _df([("x/a", f"v{i}", "sim") for i in range(10)])
    heur = _df([("x/a", f"v{i}", "sim" if i < 9 else "nao") for i in range(10)])
    res_padrao = avaliar(heur, cons)
    assert res_padrao.nota == ""  # F1 ~0.947 passa
    res_rigoroso = avaliar(heur, cons, f1_minimo=0.99)
    assert "refinar" in res_rigoroso.nota


# ---------- CSV cumulativo ----------

def test_csv_cumulativo_grava_header_na_primeira_e_só_linha_nas_proximas(tmp_path):
    destino = tmp_path / "aval.csv"
    cons = _df([("x/a", "v1", "sim")])
    heur_v1 = _df([("x/a", "v1", "sim")])
    heur_v2 = _df([("x/a", "v1", "nao")])
    gravar_csv_cumulativo(avaliar(heur_v1, cons, "v1"), destino)
    gravar_csv_cumulativo(avaliar(heur_v2, cons, "v2"), destino)
    linhas = destino.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 3  # header + v1 + v2
    assert linhas[0].startswith("heuristica,")
    assert linhas[1].startswith("v1,") and linhas[2].startswith("v2,")


def test_ler_csv_cumulativo_devolve_dicts():
    import io
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        destino = Path(d) / "x.csv"
        cons = _df([("x/a", "v1", "sim")])
        gravar_csv_cumulativo(avaliar(cons.copy(), cons, "v1"), destino)
        linhas = ler_csv_cumulativo(destino)
        assert len(linhas) == 1 and linhas[0]["heuristica"] == "v1" and linhas[0]["f1"]


def test_ler_csv_inexistente_devolve_lista_vazia(tmp_path):
    assert ler_csv_cumulativo(tmp_path / "nope.csv") == []


# ---------- relatório ----------

def test_relatorio_md_cita_metricas_e_matriz():
    cons = _df([("x/a", "v1", "sim"), ("x/a", "v2", "nao")])
    md = relatorio_md(avaliar(cons.copy(), cons, "v1"))
    for trecho in ("precisão", "revocação", "F1", "Matriz de confusão", "VP", "FN"):
        assert trecho in md


def test_relatorio_md_mostra_historico_se_fornecido():
    cons = _df([("x/a", "v1", "sim")])
    res = avaliar(cons.copy(), cons, "v2")
    historico = [{"heuristica": "v1", "n_pareadas": "1", "precisao": "0.80",
                  "revocacao": "0.75", "f1": "0.77", "acuracia": "0.90"}]
    md = relatorio_md(res, historico)
    assert "Histórico" in md and "v1" in md and "0.77" in md


# ---------- CLI de ponta a ponta ----------

def test_cli_completa_grava_md_e_csv_cumulativo(tmp_path, capsys):
    cons = _df([("x/a", "v1", "sim"), ("x/a", "v2", "nao"), ("y/b", "v3", "sim")])
    heur = _df([("x/a", "v1", "sim"), ("x/a", "v2", "nao"), ("y/b", "v3", "nao")])
    cons_csv = tmp_path / "cons.csv"
    heur_csv = tmp_path / "heur.csv"
    cons.to_csv(cons_csv, index=False)
    heur.to_csv(heur_csv, index=False)
    saida = tmp_path / "docs"
    rc = main(["--consenso", str(cons_csv), "--heuristica", str(heur_csv),
               "--nome", "v1", "--saida", str(saida)])
    assert rc == 0
    assert (saida / "avaliacao_heuristica.md").is_file()
    assert (saida / "avaliacao_heuristica.csv").is_file()
    assert "F1" in capsys.readouterr().out


def test_cli_segunda_rodada_acrescenta_linha(tmp_path):
    cons = _df([("x/a", "v1", "sim"), ("x/a", "v2", "nao")])
    heur_v1 = _df([("x/a", "v1", "sim"), ("x/a", "v2", "nao")])
    heur_v2 = _df([("x/a", "v1", "nao"), ("x/a", "v2", "nao")])
    cons_csv = tmp_path / "cons.csv"
    cons.to_csv(cons_csv, index=False)
    heur_v1_csv = tmp_path / "h1.csv"; heur_v1.to_csv(heur_v1_csv, index=False)
    heur_v2_csv = tmp_path / "h2.csv"; heur_v2.to_csv(heur_v2_csv, index=False)
    saida = tmp_path / "docs"
    main(["--consenso", str(cons_csv), "--heuristica", str(heur_v1_csv), "--nome", "v1", "--saida", str(saida)])
    main(["--consenso", str(cons_csv), "--heuristica", str(heur_v2_csv), "--nome", "v2", "--saida", str(saida)])
    linhas = (saida / "avaliacao_heuristica.csv").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 3 and linhas[1].startswith("v1,") and linhas[2].startswith("v2,")
    md = (saida / "avaliacao_heuristica.md").read_text(encoding="utf-8")
    assert "Histórico" in md and "v1" in md and "v2" in md

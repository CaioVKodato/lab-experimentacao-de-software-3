"""Testes do gerador de CSV sintético (para desenvolver as análises antes dos dados reais)."""
import csv

import pytest

from analise.dados_exemplo import gerar_linhas, main
from pipeline.dataset import COLUNAS, escrever_csv, nomes_colunas


def test_toda_linha_tem_exatamente_as_colunas_do_esquema():
    for linha in gerar_linhas(n=40, seed=1):
        assert set(linha) == set(nomes_colunas())


def test_mesma_semente_gera_o_mesmo_arquivo_e_sementes_diferentes_divergem():
    assert gerar_linhas(n=20, seed=7) == gerar_linhas(n=20, seed=7)
    assert gerar_linhas(n=20, seed=7) != gerar_linhas(n=20, seed=8)


def test_repositorios_sao_visivelmente_sinteticos_e_unicos():
    repos = [l["repo"] for l in gerar_linhas(n=60, seed=3)]
    assert all(r.startswith("exemplo/") for r in repos)
    assert len(set(repos)) == 60


def test_valores_respeitam_tipos_e_limites_do_dicionario():
    for linha in gerar_linhas(n=200, seed=5):
        for col in COLUNAS:
            v = linha[col.nome]
            if v is None:
                continue
            if col.tipo == "inteiro":
                assert isinstance(v, int) and v >= 0, (col.nome, v)
            elif col.tipo == "decimal":
                assert isinstance(v, float) and v >= 0, (col.nome, v)
            if col.unidade.startswith("proporção"):
                assert 0.0 <= v <= 1.0, (col.nome, v)


def test_criterios_de_inclusao_do_enunciado_valem_na_amostra():
    for linha in gerar_linhas(n=200, seed=9):
        assert linha["n_runs_validas"] >= 50
        assert linha["n_releases_janela"] >= 5
        assert linha["cfr_b_n_avaliadas"] <= linha["n_releases_janela"]
        assert linha["recuperacao_n_censurados"] <= (linha["recuperacao_n_episodios"] or 0)
        assert 0 <= linha["meses_sem_runs"] <= 12


def test_ha_ausentes_realistas_mas_nao_demais():
    linhas = gerar_linhas(n=300, seed=11)
    sem_recuperacao = sum(l["recuperacao_mediana_horas"] is None for l in linhas) / len(linhas)
    assert 0.03 < sem_recuperacao < 0.35  # repositórios sem episódio de falha existem, mas são minoria


def test_variante_com_prerelease_nunca_tem_menos_releases_que_a_padrao():
    for linha in gerar_linhas(n=100, seed=13):
        assert linha["n_releases_janela_pre"] >= linha["n_releases_janela"]


def test_ha_relacao_fraca_entre_frequencia_e_cfr_para_a_rq05_ter_o_que_medir():
    from scipy.stats import spearmanr
    linhas = gerar_linhas(n=400, seed=21)
    rho = spearmanr([l["deploys_por_semana"] for l in linhas], [l["cfr_a"] for l in linhas]).statistic
    assert rho < -0.05  # leve tendência inversa, para não ser ruído puro


def test_escreve_csv_valido_com_o_cabecalho_do_esquema(tmp_path):
    destino = escrever_csv(gerar_linhas(n=10, seed=2), tmp_path / "ex.csv")
    with destino.open(encoding="utf-8", newline="") as fh:
        leitor = csv.DictReader(fh)
        linhas = list(leitor)
        assert leitor.fieldnames == nomes_colunas()
    assert len(linhas) == 10


def test_linha_de_comando_grava_o_arquivo(tmp_path, capsys):
    destino = tmp_path / "saida" / "dataset_exemplo.csv"
    assert main([str(destino), "--n", "15", "--seed", "4"]) == 0
    assert destino.is_file()
    assert "15 repositórios" in capsys.readouterr().out


# ---------- estratos (desenho da amostra real: alocação igual por faixa de estrelas) ----------

from pipeline.selection import DEFAULT_STAR_RANGES, faixa_de, rotulo_faixa  # noqa: E402


def test_exemplo_tem_os_sete_estratos_em_partes_iguais():
    linhas = gerar_linhas(n=70, seed=1)
    rotulos = [rotulo_faixa(a, b) for a, b in DEFAULT_STAR_RANGES]
    contagem = {r: sum(l["faixa_estrelas"] == r for l in linhas) for r in rotulos}
    assert set(contagem.values()) == {10}


def test_estrelas_do_exemplo_caem_dentro_do_proprio_estrato():
    for l in gerar_linhas(n=140, seed=2):
        i = faixa_de(l["estrelas"], DEFAULT_STAR_RANGES)
        assert i is not None and rotulo_faixa(*DEFAULT_STAR_RANGES[i]) == l["faixa_estrelas"]

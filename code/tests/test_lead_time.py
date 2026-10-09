"""Testes de lead time for changes (RQ 02, variantes a e b).

Casos numéricos do exemplo da seção 5: release v1.1 publicada em 15/03 com
commits de 02/03, 10/03 e 14/03 -> (a) 13 dias; (b) 13, 5 e 1 dias.
"""
from datetime import datetime, timezone

import pytest

from metricas.janela import parse_janela
from metricas.lead_time import (
    lead_time_por_release_horas,
    lead_time_repositorio,
    lead_times_por_commit_horas,
    selecionar_pares,
)

JANELA = parse_janela("2026-01-01", "2026-12-31")
DIA = 24  # horas


def dt(mes, dia, hora=12):
    return datetime(2026, mes, dia, hora, tzinfo=timezone.utc)


def rel(tag, mes, dia, draft=False, prerelease=False, ano=2026):
    return {
        "tag_name": tag,
        "published_at": f"{ano}-{mes:02d}-{dia:02d}T12:00:00Z",
        "draft": draft,
        "prerelease": prerelease,
    }


def commits(*datas):
    """Formato cru de `compare`: commits[].commit.author.date."""
    return [{"commit": {"author": {"date": d}}} for d in datas]


def c(mes, dia):
    return f"2026-{mes:02d}-{dia:02d}T12:00:00Z"


def pares_tags(pares):
    return [(a["tag_name"], r["tag_name"]) for a, r in pares]


# ---------- cálculo puro ----------

def test_variante_a_exemplo_do_enunciado_13_dias():
    horas = lead_time_por_release_horas(dt(3, 15), [dt(3, 2), dt(3, 10), dt(3, 14)])
    assert horas == pytest.approx(13 * DIA)


def test_variante_b_exemplo_do_enunciado_13_5_1_dias():
    horas = lead_times_por_commit_horas(dt(3, 15), [dt(3, 2), dt(3, 10), dt(3, 14)])
    assert horas == pytest.approx([13 * DIA, 5 * DIA, 1 * DIA])


def test_release_sem_commits_nao_tem_lead_time():
    assert lead_time_por_release_horas(dt(3, 15), []) is None
    assert lead_times_por_commit_horas(dt(3, 15), []) == []


# ---------- seleção de pares (anterior, atual) ----------

def test_primeira_release_da_historia_e_ignorada():
    pares = selecionar_pares([rel("v1.0", 2, 1), rel("v1.1", 3, 15)], JANELA)
    assert pares_tags(pares) == [("v1.0", "v1.1")]


def test_release_anterior_pode_estar_fora_da_janela():
    antiga = rel("v0.9", 12, 20, ano=2025)
    pares = selecionar_pares([antiga, rel("v1.0", 1, 10)], JANELA)
    assert pares_tags(pares) == [("v0.9", "v1.0")]


def test_releases_fora_da_janela_nao_sao_avaliadas():
    todas = [rel("v0.8", 11, 1, ano=2025), rel("v0.9", 12, 20, ano=2025), rel("v1.0", 3, 1)]
    assert [r["tag_name"] for _, r in selecionar_pares(todas, JANELA)] == ["v1.0"]


def test_limites_da_janela_sao_inclusivos():
    j = parse_janela("2026-03-01", "2026-03-31")
    todas = [rel("v1.0", 2, 1), rel("v1.1", 3, 1), rel("v1.2", 3, 31), rel("v1.3", 4, 1)]
    assert [r["tag_name"] for _, r in selecionar_pares(todas, j)] == ["v1.1", "v1.2"]


def test_draft_nunca_conta_como_deploy():
    todas = [rel("v1.0", 2, 1), rel("v1.1", 3, 1, draft=True), rel("v1.2", 4, 1)]
    assert pares_tags(selecionar_pares(todas, JANELA)) == [("v1.0", "v1.2")]


def test_release_sem_published_at_e_ignorada():
    sem_data = {"tag_name": "v1.1", "published_at": None, "draft": False, "prerelease": False}
    todas = [rel("v1.0", 2, 1), sem_data, rel("v1.2", 4, 1)]
    assert pares_tags(selecionar_pares(todas, JANELA)) == [("v1.0", "v1.2")]


def test_prerelease_fica_de_fora_por_padrao_e_entra_com_a_flag():
    todas = [rel("v1.0", 2, 1), rel("v1.1-rc1", 3, 1, prerelease=True), rel("v1.1", 4, 1)]
    assert pares_tags(selecionar_pares(todas, JANELA)) == [("v1.0", "v1.1")]
    com_pre = selecionar_pares(todas, JANELA, incluir_prerelease=True)
    assert pares_tags(com_pre) == [("v1.0", "v1.1-rc1"), ("v1.1-rc1", "v1.1")]


def test_ordem_de_entrada_nao_importa():
    todas = [rel("v1.2", 4, 10), rel("v1.0", 2, 1), rel("v1.1", 3, 15)]
    assert pares_tags(selecionar_pares(todas, JANELA)) == [("v1.0", "v1.1"), ("v1.1", "v1.2")]


def test_repositorio_com_uma_unica_release_nao_gera_pares():
    assert selecionar_pares([rel("v1.0", 3, 1)], JANELA) == []


# ---------- agregação por repositório ----------

def _repo_exemplo():
    releases = [rel("v1.0", 2, 1), rel("v1.1", 3, 15), rel("v1.2", 4, 10)]
    por_tag = {
        "v1.1": commits(c(3, 2), c(3, 10), c(3, 14)),   # 13d, 5d, 1d
        "v1.2": commits(c(4, 5), c(4, 9)),               # 5d, 1d
    }
    return releases, por_tag


def test_repositorio_medianas_a_e_b_divergem_como_esperado():
    releases, por_tag = _repo_exemplo()
    res = lead_time_repositorio(releases, por_tag, JANELA)
    assert res.mediana_a_horas == pytest.approx(9 * DIA)   # mediana de 13d e 5d
    assert res.mediana_b_horas == pytest.approx(5 * DIA)   # mediana de 13,5,1,5,1
    assert res.n_releases_avaliadas == 2
    assert res.n_commits == 5


def test_commit_antigo_esquecido_explode_a_variante_a_mas_pouco_a_b():
    releases = [rel("v1.0", 2, 1), rel("v1.1", 6, 1)]
    datas = [c(2, 21)] + [c(5, 31)] * 9   # 1 commit de ~100 dias + 9 de 1 dia
    res = lead_time_repositorio(releases, {"v1.1": commits(*datas)}, JANELA)
    assert res.mediana_a_horas == pytest.approx(100 * DIA, rel=0.02)
    assert res.mediana_b_horas == pytest.approx(1 * DIA)


def test_release_sem_commits_novos_e_contada_e_nao_entra_no_calculo():
    releases = [rel("v1.0", 2, 1), rel("v1.1", 3, 15), rel("v1.2", 4, 10)]
    por_tag = {"v1.1": [], "v1.2": commits(c(4, 5))}
    res = lead_time_repositorio(releases, por_tag, JANELA)
    assert res.n_sem_commits == 1
    assert res.n_releases_avaliadas == 1
    assert res.mediana_a_horas == pytest.approx(5 * DIA)


def test_compare_com_erro_404_e_ignorado_e_contado():
    releases = [rel("v1.0", 2, 1), rel("v1.1", 3, 15), rel("v1.2", 4, 10)]
    por_tag = {"v1.1": None, "v1.2": commits(c(4, 5))}
    res = lead_time_repositorio(releases, por_tag, JANELA)
    assert res.n_ignoradas_compare == 1
    assert res.n_releases_avaliadas == 1


def test_release_sem_entrada_no_mapa_tambem_conta_como_ignorada():
    res = lead_time_repositorio([rel("v1.0", 2, 1), rel("v1.1", 3, 15)], {}, JANELA)
    assert res.n_ignoradas_compare == 1
    assert res.mediana_a_horas is None and res.mediana_b_horas is None


def test_repositorio_sem_pares_devolve_none_e_zeros():
    res = lead_time_repositorio([rel("v1.0", 3, 1)], {}, JANELA)
    assert res.mediana_a_horas is None and res.mediana_b_horas is None
    assert (res.n_releases_avaliadas, res.n_ignoradas_compare,
            res.n_sem_commits, res.n_commits) == (0, 0, 0, 0)


def test_flag_de_prerelease_muda_o_conjunto_avaliado():
    releases = [rel("v1.0", 2, 1), rel("v1.1-rc1", 3, 1, prerelease=True), rel("v1.1", 4, 1)]
    por_tag = {"v1.1": commits(c(3, 25)), "v1.1-rc1": commits(c(2, 20))}
    padrao = lead_time_repositorio(releases, por_tag, JANELA)
    com_pre = lead_time_repositorio(releases, por_tag, JANELA, incluir_prerelease=True)
    assert padrao.n_releases_avaliadas == 1
    assert com_pre.n_releases_avaliadas == 2


# ---------- janela ----------

def test_parse_janela_dia_final_e_inclusivo():
    j = parse_janela("2025-10-01", "2026-09-30")
    assert j.inicio == datetime(2025, 10, 1, 0, 0, tzinfo=timezone.utc)
    assert j.contem(datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc))
    assert not j.contem(datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc))
    assert not j.contem(datetime(2025, 9, 30, 23, 59, tzinfo=timezone.utc))
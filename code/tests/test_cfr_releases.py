"""Testes de CFR variante (b) — heurística automática de release corretiva.

Os casos dos exemplos do enunciado:
  - v2.3.0 → v2.3.1 (patch-only dentro de 7 dias) → v2.3.0 classificada como falha
  - v2.4.0 → v2.5.0 (minor mudou) → v2.4.0 NÃO é falha
"""
import pytest

from metricas.cfr_releases import (
    ResultadoCFRReleases,
    change_failure_rate_releases,
    eh_release_corretiva,
    so_patch_mudou,
    tem_keyword_corretiva,
)
from metricas.janela import parse_janela

JANELA = parse_janela("2026-01-01", "2026-06-30")


def release(tag, date, draft=False, prerelease=False, rid=0):
    """Fixture mínima de release no formato da API do GitHub."""
    return {
        "id": rid,
        "tag_name": tag,
        "published_at": f"{date}T12:00:00Z",
        "draft": draft,
        "prerelease": prerelease,
    }


def commit(message):
    return {"commit": {"message": message, "author": {"date": "2026-01-01T10:00:00Z"}}}


# ---------- so_patch_mudou ----------

def test_patch_mudou():
    assert so_patch_mudou("v2.3.0", "v2.3.1") is True


def test_patch_mudou_sem_prefixo_v():
    assert so_patch_mudou("2.3.0", "2.3.1") is True


def test_minor_mudou_nao_e_so_patch():
    assert so_patch_mudou("v2.3.0", "v2.4.0") is False


def test_major_mudou_nao_e_so_patch():
    assert so_patch_mudou("v2.3.0", "v3.0.0") is False


def test_minor_e_patch_mudaram_nao_e_so_patch():
    assert so_patch_mudou("v2.3.0", "v2.4.1") is False


def test_versoes_iguais_retorna_false():
    assert so_patch_mudou("v2.3.0", "v2.3.0") is False


def test_semver_invalido_ambos_retorna_false():
    assert so_patch_mudou("release-2024", "release-2025") is False


def test_semver_invalido_um_lado_retorna_false():
    assert so_patch_mudou("v1.0.0", "release-2024") is False


# ---------- tem_keyword_corretiva ----------

def test_fix_detectado():
    assert tem_keyword_corretiva([commit("fix: corrige login")]) is True


def test_hotfix_detectado():
    assert tem_keyword_corretiva([commit("hotfix: corrige crash em produção")]) is True


def test_revert_detectado():
    assert tem_keyword_corretiva([commit('revert "feat: nova feature"')]) is True


def test_keywords_case_insensitive():
    assert tem_keyword_corretiva([commit("Fix: corrige login")]) is True
    assert tem_keyword_corretiva([commit("HOTFIX: emergência")]) is True


def test_mensagem_sem_keyword():
    assert tem_keyword_corretiva([commit("feat: nova funcionalidade")]) is False


def test_lista_vazia():
    assert tem_keyword_corretiva([]) is False


def test_keyword_em_um_commit_da_lista():
    commits = [
        commit("feat: nova feature"),
        commit("fix: corrige bug"),
        commit("docs: atualiza readme"),
    ]
    assert tem_keyword_corretiva(commits) is True


def test_todos_commits_sem_keyword():
    commits = [commit("feat: A"), commit("chore: B"), commit("docs: C")]
    assert tem_keyword_corretiva(commits) is False


# ---------- eh_release_corretiva ----------

def test_corretiva_por_patch_semver():
    assert eh_release_corretiva("v2.3.0", "v2.3.1", []) is True


def test_corretiva_por_keyword_nos_commits():
    assert eh_release_corretiva("v2.3.0", "v2.4.0", [commit("fix: corrige bug")]) is True


def test_nao_corretiva_minor_e_sem_keyword():
    assert eh_release_corretiva("v2.4.0", "v2.5.0", [commit("feat: nova feature")]) is False


def test_corretiva_por_patch_sem_commits_disponiveis():
    assert eh_release_corretiva("v2.3.0", "v2.3.1", None) is True


def test_sem_commits_e_sem_patch_nao_e_corretiva():
    assert eh_release_corretiva("v2.3.0", "v2.4.0", None) is False


# ---------- change_failure_rate_releases ----------

def test_exemplo_enunciado_v2_3_0_falha():
    """v2.3.0 → v2.3.1 (patch-only, 2 dias): v2.3.0 deve ser classificada como falha.

    Ambas as releases entram no denominador; apenas v2.3.0 é marcada como falha.
    """
    rels = [
        release("v2.3.0", "2026-02-01", rid=1),
        release("v2.3.1", "2026-02-03", rid=2),
    ]
    res = change_failure_rate_releases(rels, {"v2.3.1": [commit("chore: bump")]}, JANELA)
    assert res.n_releases_falhas == 1
    assert res.n_releases_denominador == 2
    assert res.cfr_b == pytest.approx(0.5)


def test_exemplo_enunciado_v2_4_0_nao_falha():
    """v2.4.0 → v2.5.0 (minor mudou, 2 dias): v2.4.0 NÃO deve ser falha."""
    rels = [
        release("v2.4.0", "2026-02-01", rid=1),
        release("v2.5.0", "2026-02-03", rid=2),
    ]
    res = change_failure_rate_releases(rels, {"v2.5.0": [commit("feat: nova feature")]}, JANELA)
    assert res.n_releases_falhas == 0
    assert res.cfr_b == pytest.approx(0.0)


def test_sem_releases_retorna_none():
    res = change_failure_rate_releases([], {}, JANELA)
    assert res.cfr_b is None
    assert res.n_releases_denominador == 0


def test_release_unica_nao_e_falha():
    """Uma única release sem próxima não pode ser seguida de corretiva."""
    rels = [release("v1.0.0", "2026-02-01")]
    res = change_failure_rate_releases(rels, {}, JANELA)
    assert res.n_releases_falhas == 0
    assert res.n_releases_denominador == 1
    assert res.cfr_b == pytest.approx(0.0)


def test_proxima_fora_de_7_dias_nao_conta():
    rels = [
        release("v2.3.0", "2026-02-01", rid=1),
        release("v2.3.1", "2026-02-10", rid=2),  # 9 dias depois
    ]
    res = change_failure_rate_releases(rels, {"v2.3.1": []}, JANELA)
    assert res.n_releases_falhas == 0
    assert res.cfr_b == pytest.approx(0.0)


def test_proxima_exatamente_7_dias_conta():
    """Exatamente 7 dias está dentro da janela corretiva (não é > 7)."""
    rels = [
        release("v2.3.0", "2026-02-01", rid=1),
        release("v2.3.1", "2026-02-08", rid=2),  # exatamente 7 dias depois
    ]
    res = change_failure_rate_releases(rels, {"v2.3.1": []}, JANELA)
    assert res.n_releases_falhas == 1


def test_censura_exclui_releases_nos_ultimos_7_dias():
    """Releases nos 7 dias finais da janela são censuradas e ficam fora do denominador."""
    rels = [release("v1.0.0", "2026-06-26", rid=1)]  # 4 dias antes do fim
    res = change_failure_rate_releases(rels, {}, JANELA)
    assert res.n_releases_censuradas == 1
    assert res.n_releases_denominador == 0
    assert res.cfr_b is None


def test_release_fora_do_corte_nao_censurada():
    """Release com mais de 7 dias antes do fim da janela entra no denominador."""
    rels = [release("v1.0.0", "2026-06-20", rid=1)]  # 10 dias antes do fim
    res = change_failure_rate_releases(rels, {}, JANELA)
    assert res.n_releases_censuradas == 0
    assert res.n_releases_denominador == 1


def test_draft_excluido():
    rels = [
        release("v1.0.0", "2026-02-01", draft=True, rid=1),
        release("v1.1.0", "2026-02-02", rid=2),
    ]
    res = change_failure_rate_releases(rels, {}, JANELA)
    assert res.n_releases_denominador == 1


def test_prerelease_excluida_por_padrao():
    rels = [
        release("v1.0.0-rc1", "2026-02-01", prerelease=True, rid=1),
        release("v1.1.0", "2026-02-02", rid=2),
    ]
    res = change_failure_rate_releases(rels, {}, JANELA)
    assert res.n_releases_denominador == 1


def test_prerelease_incluida_com_flag():
    """Com incluir_prerelease=True, pré-releases entram no denominador normalmente."""
    rels = [
        release("v1.0.0-rc1", "2026-02-01", prerelease=True, rid=1),
        release("v1.0.0-rc2", "2026-02-02", prerelease=True, rid=2),
    ]
    res = change_failure_rate_releases(rels, {"v1.0.0-rc2": []}, JANELA, incluir_prerelease=True)
    assert res.n_releases_denominador == 2  # ambas elegíveis com a flag


def test_tag_nao_semver_sem_keyword_nao_e_corretiva():
    """Tags sem SemVer e commits sem keywords não são corretivas."""
    rels = [
        release("release-2026-01", "2026-02-01", rid=1),
        release("release-2026-02", "2026-02-03", rid=2),
    ]
    commits_por_tag = {"release-2026-02": [commit("chore: release mensal")]}
    res = change_failure_rate_releases(rels, commits_por_tag, JANELA)
    assert res.n_releases_falhas == 0


def test_tag_nao_semver_com_keyword_e_corretiva():
    """Tags sem SemVer, mas com keyword nos commits, são corretivas."""
    rels = [
        release("release-2026-01", "2026-02-01", rid=1),
        release("release-2026-02", "2026-02-03", rid=2),
    ]
    commits_por_tag = {"release-2026-02": [commit("fix: corrige bug crítico")]}
    res = change_failure_rate_releases(rels, commits_por_tag, JANELA)
    assert res.n_releases_falhas == 1


def test_sem_compare_conta_n_sem_compare_e_usa_semver():
    """Compare indisponível: semver ainda é avaliado; tag ausente incrementa n_sem_compare."""
    rels = [
        release("v2.3.0", "2026-02-01", rid=1),
        release("v2.4.0", "2026-02-03", rid=2),  # minor mudou
    ]
    res = change_failure_rate_releases(rels, {}, JANELA)  # v2.4.0 ausente do mapa
    assert res.n_sem_compare == 1
    assert res.n_releases_falhas == 0  # SemVer diz não-corretiva


def test_sem_compare_patch_only_ainda_falha():
    """Se o SemVer indica patch-only, a release falha mesmo sem commits disponíveis."""
    rels = [
        release("v2.3.0", "2026-02-01", rid=1),
        release("v2.3.1", "2026-02-03", rid=2),
    ]
    res = change_failure_rate_releases(rels, {}, JANELA)  # v2.3.1 ausente do mapa
    assert res.n_sem_compare == 1
    assert res.n_releases_falhas == 1


def test_cfr_b_mistura_de_releases():
    rels = [
        release("v1.0.0", "2026-01-01", rid=1),
        release("v1.0.1", "2026-01-03", rid=2),  # patch-only em 2 dias → v1.0.0 falha
        release("v1.1.0", "2026-01-10", rid=3),  # minor em 7 dias → v1.0.1 não falha
        release("v1.2.0", "2026-01-20", rid=4),  # 10 dias depois → fora da janela corretiva
    ]
    commits_por_tag = {"v1.0.1": [], "v1.1.0": [], "v1.2.0": []}
    res = change_failure_rate_releases(rels, commits_por_tag, JANELA)
    assert res.n_releases_denominador == 4
    assert res.n_releases_falhas == 1
    assert res.cfr_b == pytest.approx(0.25)

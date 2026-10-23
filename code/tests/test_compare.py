"""Testes da coleta de commits entre releases (Issue #6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.cache import JsonCache
from pipeline.compare import (
    CommitEntry,
    CompareResult,
    _cache_key,
    _extract_commit,
    collect_commits_between,
    collect_repo_commits_between_releases,
    count_not_found,
    write_commits_csv,
)
from pipeline.github_client import GitHubAPIError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_commit(sha: str, date: str) -> dict:
    return {
        "sha": sha,
        "commit": {"author": {"date": date, "name": "dev"}, "message": "msg"},
    }


class FakeClient:
    """Simula paginate() retornando listas de commits ou levantando GitHubAPIError."""

    def __init__(self, responses: dict[str, list | int]) -> None:
        # responses: {path: lista_de_commits} ou {path: status_code_int}
        self.responses = responses
        self.calls: list[str] = []

    def paginate(self, path: str, params=None, *, items_key=None, max_items=None) -> list:
        self.calls.append(path)
        response = self.responses.get(path)
        if isinstance(response, int):
            raise GitHubAPIError(response, f"Simulated {response}")
        return response or []


@pytest.fixture
def cache(tmp_path: Path) -> JsonCache:
    return JsonCache(tmp_path / "cache")


# ---------------------------------------------------------------------------
# _extract_commit
# ---------------------------------------------------------------------------

def test_extract_commit_extrai_sha_e_data():
    raw = _raw_commit("abc123", "2026-01-15T10:00:00Z")
    entry = _extract_commit(raw)
    assert entry.sha == "abc123"
    assert entry.author_date == "2026-01-15T10:00:00Z"


def test_extract_commit_campos_ausentes():
    entry = _extract_commit({})
    assert entry.sha == ""
    assert entry.author_date == ""


def test_extract_commit_author_ausente():
    raw = {"sha": "abc", "commit": {}}
    entry = _extract_commit(raw)
    assert entry.sha == "abc"
    assert entry.author_date == ""


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------

def test_cache_key_formato():
    key = _cache_key("owner", "repo", "v1.0", "v2.0")
    assert key == "compare/owner/repo/v1.0...v2.0.json"


# ---------------------------------------------------------------------------
# collect_commits_between
# ---------------------------------------------------------------------------

def test_coleta_commits_basico(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [_raw_commit("abc", "2026-01-15T10:00:00Z")]})
    result = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert isinstance(result, CompareResult)
    assert result.full_name == "o/r"
    assert result.base_tag == "v1.0"
    assert result.head_tag == "v2.0"
    assert len(result.commits) == 1
    assert result.commits[0].sha == "abc"
    assert result.not_found is False
    assert result.from_cache is False


def test_coleta_commits_multiplos(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [
        _raw_commit("aaa", "2026-01-10T00:00:00Z"),
        _raw_commit("bbb", "2026-01-12T00:00:00Z"),
        _raw_commit("ccc", "2026-01-14T00:00:00Z"),
    ]})
    result = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert len(result.commits) == 3
    shas = [c.sha for c in result.commits]
    assert "aaa" in shas and "ccc" in shas


def test_404_retorna_not_found_sem_propagar(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: 404})
    result = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert result.not_found is True
    assert result.commits == []
    assert result.from_cache is False


def test_outros_erros_propagam(cache: JsonCache):
    # Issue #20: 5xx e 422 deixaram de derrubar a etapa (viram falha registrada e refeita
    # depois; veja os testes de "falha persistente"). Erros inesperados, como permissão
    # negada, continuam propagando.
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: 403})
    with pytest.raises(GitHubAPIError) as exc:
        collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert exc.value.status_code == 403


def test_segunda_chamada_retorna_do_cache(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [_raw_commit("abc", "2026-01-15T10:00:00Z")]})
    collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    chamadas_antes = len(client.calls)

    result2 = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert result2.from_cache is True
    assert result2.commits[0].sha == "abc"
    assert len(client.calls) == chamadas_antes  # nenhuma chamada nova


def test_404_tambem_fica_em_cache(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: 404})
    collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    chamadas_antes = len(client.calls)

    result2 = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert result2.from_cache is True
    assert result2.not_found is True
    assert len(client.calls) == chamadas_antes


def test_force_ignora_cache(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [_raw_commit("abc", "2026-01-15T10:00:00Z")]})
    collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache, force=True)
    assert len(client.calls) == 2  # segunda chamada foi forçada


def test_pares_diferentes_nao_compartilham_cache(cache: JsonCache):
    path_a = "/repos/o/r/compare/v1.0...v2.0"
    path_b = "/repos/o/r/compare/v2.0...v3.0"
    client = FakeClient({
        path_a: [_raw_commit("aaa", "2026-01-01T00:00:00Z")],
        path_b: [_raw_commit("bbb", "2026-02-01T00:00:00Z")],
    })
    r1 = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    r2 = collect_commits_between(client, "o", "r", "v2.0", "v3.0", cache=cache)
    assert r1.commits[0].sha == "aaa"
    assert r2.commits[0].sha == "bbb"


# ---------------------------------------------------------------------------
# collect_repo_commits_between_releases
# ---------------------------------------------------------------------------

def test_collect_repo_commits_multiplos_pares(cache: JsonCache):
    client = FakeClient({
        "/repos/o/r/compare/v1.0...v2.0": [_raw_commit("aaa", "2026-01-15T10:00:00Z")],
        "/repos/o/r/compare/v2.0...v3.0": [_raw_commit("bbb", "2026-02-15T10:00:00Z")],
    })
    pairs = [("v1.0", "v2.0"), ("v2.0", "v3.0")]
    results = collect_repo_commits_between_releases(client, "o", "r", pairs, cache=cache)
    assert len(results) == 2
    assert results[0].head_tag == "v2.0"
    assert results[1].head_tag == "v3.0"


def test_collect_repo_commits_lista_vazia(cache: JsonCache):
    client = FakeClient({})
    results = collect_repo_commits_between_releases(client, "o", "r", [], cache=cache)
    assert results == []


# ---------------------------------------------------------------------------
# count_not_found
# ---------------------------------------------------------------------------

def test_count_not_found_zero():
    results = [
        CompareResult(full_name="o/r", base_tag="v1", head_tag="v2", commits=[]),
        CompareResult(full_name="o/r", base_tag="v2", head_tag="v3", commits=[]),
    ]
    assert count_not_found(results) == 0


def test_count_not_found_alguns():
    results = [
        CompareResult(full_name="o/r", base_tag="v1", head_tag="v2", commits=[], not_found=True),
        CompareResult(full_name="o/r", base_tag="v2", head_tag="v3", commits=[]),
        CompareResult(full_name="o/r", base_tag="v3", head_tag="v4", commits=[], not_found=True),
    ]
    assert count_not_found(results) == 2


# ---------------------------------------------------------------------------
# write_commits_csv
# ---------------------------------------------------------------------------

def test_write_commits_csv_exclui_not_found(tmp_path: Path):
    results = [
        CompareResult(
            full_name="o/r", base_tag="v1.0", head_tag="v2.0",
            commits=[CommitEntry(sha="abc", author_date="2026-01-15T10:00:00Z")],
        ),
        CompareResult(
            full_name="o/r", base_tag="v2.0", head_tag="v3.0",
            commits=[], not_found=True,
        ),
    ]
    path = write_commits_csv(results, tmp_path / "commits.csv")
    text = path.read_text(encoding="utf-8")
    assert "abc" in text
    assert "v3.0" not in text  # par com 404 excluído


def test_write_commits_csv_cabecalho(tmp_path: Path):
    path = write_commits_csv([], tmp_path / "commits.csv")
    text = path.read_text(encoding="utf-8")
    assert "sha" in text
    assert "author_date" in text
    assert "full_name" in text


def test_write_commits_csv_multiplos_commits(tmp_path: Path):
    results = [
        CompareResult(
            full_name="o/r", base_tag="v1.0", head_tag="v2.0",
            commits=[
                CommitEntry(sha="aaa", author_date="2026-01-10T00:00:00Z"),
                CommitEntry(sha="bbb", author_date="2026-01-12T00:00:00Z"),
            ],
        ),
    ]
    path = write_commits_csv(results, tmp_path / "commits.csv")
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3  # header + 2 commits


# ---------------------------------------------------------------------------
# Mensagem do commit e versão do cache (Issue #20: a heurística do CFR (b) precisa dela)
# ---------------------------------------------------------------------------

from pipeline.compare import CACHE_VERSAO, MAX_MENSAGEM  # noqa: E402


def _raw_commit_msg(sha: str, date: str, message: str) -> dict:
    return {"sha": sha, "commit": {"author": {"date": date}, "message": message}}


def test_extract_commit_preserva_a_mensagem():
    raw = _raw_commit_msg("abc", "2026-01-15T10:00:00Z", "fix: crash ao abrir arquivo")
    assert _extract_commit(raw).message == "fix: crash ao abrir arquivo"


def test_extract_commit_sem_mensagem_vira_vazia():
    assert _extract_commit({"sha": "abc", "commit": {}}).message == ""
    assert _extract_commit({}).message == ""


def test_extract_commit_trunca_mensagens_muito_longas():
    raw = _raw_commit_msg("abc", "2026-01-15T10:00:00Z", "x" * (MAX_MENSAGEM + 500))
    assert len(_extract_commit(raw).message) == MAX_MENSAGEM


def test_mensagem_sobrevive_ao_cache(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [_raw_commit_msg("abc", "2026-01-15T10:00:00Z", "revert: desfaz mudança")]})
    collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    segundo = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert segundo.from_cache is True
    assert segundo.commits[0].message == "revert: desfaz mudança"


def test_cache_novo_grava_a_versao(cache: JsonCache):
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [_raw_commit("abc", "2026-01-15T10:00:00Z")]})
    collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert cache.load(_cache_key("o", "r", "v1.0", "v2.0"))["versao"] == CACHE_VERSAO


def test_cache_antigo_sem_versao_e_ignorado_em_vez_de_lido_com_mensagem_vazia(cache: JsonCache):
    # Formato anterior: os commits só tinham sha e data. Lê-lo "com sucesso" daria
    # mensagens vazias e a heurística de CFR (b) ficaria degradada sem ninguém notar.
    key = _cache_key("o", "r", "v1.0", "v2.0")
    cache.save(key, {"full_name": "o/r", "not_found": False,
                     "commits": [{"sha": "abc", "author_date": "2026-01-15T10:00:00Z"}]})
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [_raw_commit_msg("abc", "2026-01-15T10:00:00Z", "fix: x")]})
    result = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert result.from_cache is False and len(client.calls) == 1
    assert result.commits[0].message == "fix: x"
    again = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert again.from_cache is True and len(client.calls) == 1


def test_404_antigo_sem_versao_tambem_e_refeito(cache: JsonCache):
    key = _cache_key("o", "r", "v1.0", "v2.0")
    cache.save(key, {"full_name": "o/r", "commits": [], "not_found": True})
    path = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({path: [_raw_commit("abc", "2026-01-15T10:00:00Z")]})
    result = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert result.from_cache is False and result.not_found is False


def test_as_api_reproduz_o_formato_cru_consumido_pelas_metricas():
    entry = CommitEntry(sha="abc", author_date="2026-01-15T10:00:00Z", message="fix: x")
    assert entry.as_api() == {
        "sha": "abc",
        "commit": {"message": "fix: x", "author": {"date": "2026-01-15T10:00:00Z"}},
    }


def test_commits_coletados_alimentam_a_heuristica_e_o_lead_time_do_grupo():
    from metricas.cfr_releases import tem_keyword_corretiva
    from metricas.lead_time import data_commit

    com_fix = CommitEntry(sha="a", author_date="2026-05-12T10:00:00Z", message="fix: crash ao abrir arquivo")
    assert tem_keyword_corretiva([com_fix.as_api()]) is True
    assert data_commit(com_fix.as_api()).year == 2026
    # Contraponto: sem a mensagem (o comportamento antigo), o critério nunca dispararia.
    sem_msg = CommitEntry(sha="a", author_date="2026-05-12T10:00:00Z")
    assert tem_keyword_corretiva([sem_msg.as_api()]) is False


def test_csv_de_commits_continua_sem_a_coluna_de_mensagem(tmp_path: Path):
    path = write_commits_csv([], tmp_path / "commits.csv")
    assert path.read_text(encoding="utf-8").strip() == "full_name,base_tag,head_tag,sha,author_date"


# ---------------------------------------------------------------------------
# Falha persistente de um par não pode derrubar a etapa inteira (Issue #20)
# ---------------------------------------------------------------------------

from pipeline.compare import MAX_FALHAS_SEGUIDAS, count_erros  # noqa: E402


def _par(i: int) -> tuple[str, str]:
    return (f"v{i}", f"v{i + 1}")


def _caminho(par: tuple[str, str]) -> str:
    return f"/repos/o/r/compare/{par[0]}...{par[1]}"


@pytest.mark.parametrize("status", [500, 502, 504, 422])
def test_erro_persistente_vira_falha_registrada_sem_cache_e_sem_derrubar(cache: JsonCache, status: int):
    caminho = "/repos/o/r/compare/v1.0...v2.0"
    client = FakeClient({caminho: status})
    r = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert r.erro == status and r.not_found is False and r.commits == []
    assert cache.load(_cache_key("o", "r", "v1.0", "v2.0")) is None  # não grava: será refeito

    client.responses[caminho] = [_raw_commit("abc", "2026-01-15T10:00:00Z")]
    r2 = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert r2.erro is None and len(r2.commits) == 1
    assert cache.load(_cache_key("o", "r", "v1.0", "v2.0")) is not None


@pytest.mark.parametrize("status", [401, 403])
def test_erro_de_credencial_ou_permissao_continua_derrubando(cache: JsonCache, status: int):
    client = FakeClient({"/repos/o/r/compare/v1.0...v2.0": status})
    with pytest.raises(GitHubAPIError):
        collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)


def test_falhas_seguidas_abortam_o_repositorio_em_vez_de_gravar_lixo(cache: JsonCache):
    pares = [_par(i) for i in range(10)]
    client = FakeClient({_caminho(p): 502 for p in pares})
    with pytest.raises(GitHubAPIError, match="seguidas"):
        collect_repo_commits_between_releases(client, "o", "r", pares, cache=cache)
    assert len(client.calls) == MAX_FALHAS_SEGUIDAS  # parou na quinta, não tentou os outros


def test_falha_isolada_nao_aborta_e_zera_a_contagem(cache: JsonCache):
    pares = [_par(i) for i in range(12)]
    respostas = {_caminho(p): (500 if i % 3 == 0 else [_raw_commit(f"s{i}", "2026-01-15T10:00:00Z")])
                 for i, p in enumerate(pares)}
    results = collect_repo_commits_between_releases(FakeClient(respostas), "o", "r", pares, cache=cache)
    assert len(results) == 12 and count_erros(results) == 4


def test_falha_nao_conta_como_404(cache: JsonCache):
    client = FakeClient({"/repos/o/r/compare/v1.0...v2.0": 500})
    r = collect_commits_between(client, "o", "r", "v1.0", "v2.0", cache=cache)
    assert count_not_found([r]) == 0 and count_erros([r]) == 1

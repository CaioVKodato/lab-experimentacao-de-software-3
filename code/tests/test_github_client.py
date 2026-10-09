"""Testes do cliente HTTP (Issue #4): cache em disco, backoff exponencial e rate limit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline.cache import JsonCache
from pipeline.github_client import (
    GitHubAPIError,
    GitHubClient,
    RateLimitInfo,
    _make_cache_key,
    _parse_next_link,
    _should_wait_rate_limit,
    extract_last_page,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_response(
    status: int = 200,
    json_body: object = None,
    headers: dict | None = None,
    text: str | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text if text is not None else (str(json_body) if json_body is not None else "")
    resp.content = b"x" if json_body is not None else b""
    resp.json.return_value = json_body
    resp.headers = headers or {}
    return resp


def _make_client(
    responses: list,
    *,
    cache: JsonCache | None = None,
    max_retries: int = 3,
    backoff_base: float = 0.0,
) -> tuple[GitHubClient, MagicMock, list[float]]:
    sleeps: list[float] = []
    session = MagicMock()
    session.headers = {}
    session.get.side_effect = responses

    client = GitHubClient(
        "fake-token",
        session=session,
        sleep_fn=lambda t: sleeps.append(t),
        cache=cache,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )
    return client, session, sleeps


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

def test_constructor_rejeita_token_vazio():
    with pytest.raises(ValueError):
        GitHubClient("")


def test_constructor_aceita_cache_e_retry_params(tmp_path: Path):
    cache = JsonCache(tmp_path / "cache")
    client, _, _ = _make_client([], cache=cache, max_retries=7, backoff_base=2.0)
    assert client._cache is cache
    assert client._max_retries == 7
    assert client._backoff_base == 2.0


def test_rate_limit_initial_none():
    client, _, _ = _make_client([])
    assert client.rate_limit_remaining is None
    assert client.rate_limit_reset is None


# ---------------------------------------------------------------------------
# Backoff exponencial em 5xx
# ---------------------------------------------------------------------------

def test_retries_em_503_com_backoff_exponencial():
    body = {"key": "value"}
    responses = [
        _fake_response(503, text="Service Unavailable"),
        _fake_response(503, text="Service Unavailable"),
        _fake_response(200, json_body=body),
    ]
    client, session, sleeps = _make_client(responses, max_retries=5, backoff_base=1.0)
    result, _ = client.get_json("/test")
    assert result == body
    assert session.get.call_count == 3
    assert sleeps == [1.0, 2.0]  # 1.0*2^0, 1.0*2^1


def test_retries_em_500_com_backoff_base_diferente():
    responses = [
        _fake_response(500, text="err"),
        _fake_response(500, text="err"),
        _fake_response(200, json_body={}),
    ]
    client, _, sleeps = _make_client(responses, max_retries=5, backoff_base=2.0)
    client.get_json("/test")
    assert sleeps == [2.0, 4.0]  # 2.0*2^0, 2.0*2^1


def test_levanta_apos_max_retries():
    # max_retries=3: 1 tentativa inicial + 3 retries = 4 chamadas totais
    responses = [_fake_response(500, text="err")] * 4
    client, session, _ = _make_client(responses, max_retries=3, backoff_base=0.0)
    with pytest.raises(GitHubAPIError) as exc:
        client.get_json("/fail")
    assert exc.value.status_code == 500
    assert session.get.call_count == 4


def test_backoff_limitado_a_120_segundos():
    responses = [
        _fake_response(500, text="err"),
        _fake_response(500, text="err"),
        _fake_response(500, text="err"),
        _fake_response(200, json_body={}),
    ]
    client, _, sleeps = _make_client(responses, max_retries=5, backoff_base=200.0)
    client.get_json("/test")
    assert all(s <= 120.0 for s in sleeps)


def test_erros_4xx_nao_fazem_retry():
    responses = [_fake_response(404, text="Not Found")]
    client, session, sleeps = _make_client(responses, max_retries=3)
    with pytest.raises(GitHubAPIError) as exc:
        client.get_json("/missing")
    assert exc.value.status_code == 404
    assert session.get.call_count == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# Monitoramento de rate limit
# ---------------------------------------------------------------------------

def test_headers_de_rate_limit_sao_lidos_em_toda_resposta():
    body = {"key": "val"}
    response = _fake_response(200, json_body=body, headers={
        "X-RateLimit-Remaining": "4000",
        "X-RateLimit-Reset": "1700000000",
    })
    client, _, _ = _make_client([response])
    assert client.rate_limit_remaining is None
    client.get_json("/test")
    assert client.rate_limit_remaining == 4000
    assert client.rate_limit_reset == 1700000000


def test_rate_limit_atualizado_em_cada_resposta():
    responses = [
        _fake_response(200, json_body={}, headers={"X-RateLimit-Remaining": "100"}),
        _fake_response(200, json_body={}, headers={"X-RateLimit-Remaining": "99"}),
    ]
    client, _, _ = _make_client(responses)
    client.get_json("/first")
    assert client.rate_limit_remaining == 100
    client.get_json("/second")
    assert client.rate_limit_remaining == 99


def test_rate_limit_ignorado_se_header_ausente():
    response = _fake_response(200, json_body={}, headers={})
    client, _, _ = _make_client([response])
    client.get_json("/test")
    assert client.rate_limit_remaining is None


def test_aguarda_rate_limit_reset_em_403():
    rate_limited = _fake_response(403, headers={
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "9999999999",
    }, text="rate limit exceeded")
    ok = _fake_response(200, json_body={"ok": True})
    client, session, sleeps = _make_client([rate_limited, ok])
    result, _ = client.get_json("/test")
    assert result == {"ok": True}
    assert session.get.call_count == 2
    assert len(sleeps) == 1 and sleeps[0] > 0


# ---------------------------------------------------------------------------
# get_rate_limit()
# ---------------------------------------------------------------------------

def test_get_rate_limit_parseia_recursos_core():
    body = {
        "resources": {
            "core": {"remaining": 4999, "limit": 5000, "reset": 1700000000},
            "search": {"remaining": 30, "limit": 30, "reset": 1700000000},
        }
    }
    client, _, _ = _make_client([_fake_response(200, json_body=body)])
    info = client.get_rate_limit()
    assert isinstance(info, RateLimitInfo)
    assert info.remaining == 4999
    assert info.limit == 5000
    assert info.reset_at == 1700000000


def test_rate_limit_info_seconds_until_reset(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1700000000.0)
    info = RateLimitInfo(remaining=100, limit=5000, reset_at=1700000060)
    assert abs(info.seconds_until_reset - 60.0) < 1.0


def test_rate_limit_info_seconds_until_reset_nao_negativo(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1700000000.0)
    info = RateLimitInfo(remaining=0, limit=5000, reset_at=1699999000)
    assert info.seconds_until_reset == 0.0


def test_rate_limit_info_repr():
    info = RateLimitInfo(remaining=100, limit=5000, reset_at=9999999999)
    assert "100/5000" in repr(info)


# ---------------------------------------------------------------------------
# Cache em disco
# ---------------------------------------------------------------------------

def test_cache_armazena_e_serve_resposta(tmp_path: Path):
    cache = JsonCache(tmp_path / "cache")
    body = {"full_name": "owner/repo", "stars": 42}
    client, session, _ = _make_client([_fake_response(200, json_body=body)], cache=cache)

    result1, _ = client.get_json("/repos/owner/repo")
    assert result1 == body
    assert session.get.call_count == 1

    result2, _ = client.get_json("/repos/owner/repo")
    assert result2 == body
    assert session.get.call_count == 1  # servido do cache


def test_cache_headers_tambem_persistidos(tmp_path: Path):
    cache = JsonCache(tmp_path / "cache")
    resp = _fake_response(200, json_body={"x": 1}, headers={"X-Custom": "abc"})
    client, _, _ = _make_client([resp], cache=cache)

    _, h1 = client.get_json("/test")
    _, h2 = client.get_json("/test")
    assert h1.get("X-Custom") == "abc"
    assert h2.get("X-Custom") == "abc"


def test_cache_varia_por_params(tmp_path: Path):
    cache = JsonCache(tmp_path / "cache")
    resp_a = _fake_response(200, json_body={"page": 1})
    resp_b = _fake_response(200, json_body={"page": 2})
    client, session, _ = _make_client([resp_a, resp_b], cache=cache)

    r1, _ = client.get_json("/test", {"page": 1})
    r2, _ = client.get_json("/test", {"page": 2})
    assert r1 == {"page": 1}
    assert r2 == {"page": 2}
    assert session.get.call_count == 2


def test_urls_absolutas_nao_sao_cacheadas(tmp_path: Path):
    cache = JsonCache(tmp_path / "cache")
    resp = _fake_response(200, json_body=[{"id": 1}])
    client, session, _ = _make_client([resp, resp], cache=cache)

    client.get_json("https://api.github.com/repos/a/b?page=2")
    client.get_json("https://api.github.com/repos/a/b?page=2")
    assert session.get.call_count == 2


def test_sem_cache_sempre_faz_requisicao():
    body = {"x": 1}
    client, session, _ = _make_client(
        [_fake_response(200, json_body=body), _fake_response(200, json_body=body)],
        cache=None,
    )
    client.get_json("/test")
    client.get_json("/test")
    assert session.get.call_count == 2


# ---------------------------------------------------------------------------
# _make_cache_key
# ---------------------------------------------------------------------------

def test_make_cache_key_sem_params():
    assert _make_cache_key("/repos/owner/name", None) == "http/repos/owner/name.json"


def test_make_cache_key_com_params():
    key = _make_cache_key("/search/repositories", {"q": "stars:>1000", "per_page": 100})
    assert key.startswith("http/search/repositories/")
    assert "per_page=100" in key
    assert "q=stars" in key


def test_make_cache_key_deterministica():
    k1 = _make_cache_key("/test", {"b": 2, "a": 1})
    k2 = _make_cache_key("/test", {"a": 1, "b": 2})
    assert k1 == k2


def test_make_cache_key_sem_barra_inicial():
    assert _make_cache_key("repos/owner/name", None) == "http/repos/owner/name.json"


# ---------------------------------------------------------------------------
# Funções utilitárias (regressão)
# ---------------------------------------------------------------------------

def test_get_json_levanta_em_4xx():
    client, _, _ = _make_client([_fake_response(404, text="Not Found")])
    with pytest.raises(GitHubAPIError) as exc:
        client.get_json("/missing")
    assert exc.value.status_code == 404


def test_get_json_retorna_none_em_204():
    client, _, _ = _make_client([_fake_response(204)])
    body, _ = client.get_json("/empty")
    assert body is None


def test_parse_next_link_extrai_url():
    header = '<https://api.github.com/repos?page=2>; rel="next", <https://api.github.com/repos?page=5>; rel="last"'
    assert _parse_next_link(header) == "https://api.github.com/repos?page=2"


def test_parse_next_link_retorna_none_sem_next():
    assert _parse_next_link(None) is None
    assert _parse_next_link("") is None
    assert _parse_next_link('<url>; rel="last"') is None


def test_extract_last_page():
    header = '<https://api.github.com/repos/a/b/contributors?page=42&per_page=1>; rel="last"'
    assert extract_last_page(header) == 42
    assert extract_last_page(None) is None
    assert extract_last_page("") is None


def test_should_wait_rate_limit_remaining_zero():
    resp = _fake_response(403, headers={"X-RateLimit-Remaining": "0"}, text="")
    assert _should_wait_rate_limit(resp) is True


def test_should_wait_rate_limit_corpo_menciona_rate_limit():
    resp = _fake_response(403, headers={}, text="You have exceeded a secondary rate limit")
    assert _should_wait_rate_limit(resp) is True


def test_should_wait_rate_limit_outro_403():
    resp = _fake_response(403, headers={"X-RateLimit-Remaining": "50"}, text="Forbidden")
    assert _should_wait_rate_limit(resp) is False

"""Testes da coleta de releases e tags (Issue #5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.cache import JsonCache
from pipeline.releases import (
    Release,
    ReleasesResult,
    Tag,
    _in_window,
    _parse_releases,
    _parse_tags,
    collect_repo_releases,
    collect_sample_releases,
    run_releases_collection,
    write_releases_csv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _release(tag: str, published_at: str, *, draft=False, prerelease=False, name=None):
    return {
        "tag_name": tag,
        "name": name or tag,
        "published_at": published_at,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
    }


def _tag(name: str, sha: str = "abc123"):
    return {"name": name, "commit": {"sha": sha}}


class FakeClient:
    def __init__(self, releases: list, tags: list) -> None:
        self._releases = releases
        self._tags = tags
        self.calls: list[str] = []

    def paginate(self, path: str, params=None, *, items_key=None, max_items=None) -> list:
        self.calls.append(path)
        if "/releases" in path:
            return self._releases
        if "/tags" in path:
            return self._tags
        return []


@pytest.fixture
def cache(tmp_path: Path) -> JsonCache:
    return JsonCache(tmp_path / "cache")


WINDOW = ("2025-10-01", "2026-09-30")


# ---------------------------------------------------------------------------
# _in_window
# ---------------------------------------------------------------------------

def test_in_window_dentro():
    assert _in_window("2026-01-15T10:00:00Z", "2025-10-01", "2026-09-30") is True


def test_in_window_limite_inicio():
    assert _in_window("2025-10-01T00:00:00Z", "2025-10-01", "2026-09-30") is True


def test_in_window_limite_fim():
    assert _in_window("2026-09-30T23:59:59Z", "2025-10-01", "2026-09-30") is True


def test_in_window_antes():
    assert _in_window("2025-09-30T23:59:59Z", "2025-10-01", "2026-09-30") is False


def test_in_window_depois():
    assert _in_window("2026-10-01T00:00:00Z", "2025-10-01", "2026-09-30") is False


# ---------------------------------------------------------------------------
# _parse_releases
# ---------------------------------------------------------------------------

def test_parse_releases_exclui_draft():
    raw = [_release("v1.0", "2026-01-01T00:00:00Z", draft=True)]
    assert _parse_releases(raw, "o/r", *WINDOW) == []


def test_parse_releases_exclui_sem_published_at():
    raw = [{"tag_name": "v1.0", "draft": False, "prerelease": False, "published_at": None}]
    assert _parse_releases(raw, "o/r", *WINDOW) == []


def test_parse_releases_filtra_fora_da_janela():
    raw = [
        _release("v0.9", "2025-09-01T00:00:00Z"),  # antes
        _release("v1.0", "2026-01-15T00:00:00Z"),  # dentro
        _release("v1.1", "2026-10-01T00:00:00Z"),  # depois
    ]
    result = _parse_releases(raw, "o/r", *WINDOW)
    assert len(result) == 1
    assert result[0].tag_name == "v1.0"


def test_parse_releases_marca_prerelease():
    raw = [_release("v1.0-rc1", "2026-03-01T00:00:00Z", prerelease=True)]
    result = _parse_releases(raw, "o/r", *WINDOW)
    assert len(result) == 1
    assert result[0].prerelease is True
    assert result[0].full_name == "o/r"


def test_parse_releases_release_normal_nao_e_prerelease():
    raw = [_release("v1.0", "2026-03-01T00:00:00Z")]
    result = _parse_releases(raw, "o/r", *WINDOW)
    assert result[0].prerelease is False


# ---------------------------------------------------------------------------
# _parse_tags
# ---------------------------------------------------------------------------

def test_parse_tags_extrai_nome_e_sha():
    raw = [_tag("v1.0", sha="deadbeef"), _tag("v0.9", sha="cafebabe")]
    result = _parse_tags(raw, "o/r")
    assert len(result) == 2
    assert result[0] == Tag(full_name="o/r", tag_name="v1.0", sha="deadbeef")


def test_parse_tags_lista_vazia():
    assert _parse_tags([], "o/r") == []


# ---------------------------------------------------------------------------
# collect_repo_releases
# ---------------------------------------------------------------------------

def test_collect_repo_releases_basico(cache: JsonCache):
    client = FakeClient(
        releases=[_release("v1.0", "2026-01-15T00:00:00Z")],
        tags=[_tag("v1.0")],
    )
    result = collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert isinstance(result, ReleasesResult)
    assert result.full_name == "o/r"
    assert len(result.releases) == 1
    assert result.releases[0].tag_name == "v1.0"
    assert len(result.tags) == 1
    assert result.from_cache is False


def test_collect_repo_releases_faz_duas_chamadas(cache: JsonCache):
    client = FakeClient(releases=[], tags=[])
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert any("/releases" in c for c in client.calls)
    assert any("/tags" in c for c in client.calls)
    assert len(client.calls) == 2


def test_segunda_chamada_retorna_do_cache(cache: JsonCache):
    client = FakeClient(
        releases=[_release("v1.0", "2026-01-15T00:00:00Z")],
        tags=[_tag("v1.0")],
    )
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    calls_antes = len(client.calls)

    result2 = collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert result2.from_cache is True
    assert len(client.calls) == calls_antes  # nenhuma chamada nova
    assert result2.releases[0].tag_name == "v1.0"
    assert result2.tags[0].sha == "abc123"


def test_force_ignora_cache(cache: JsonCache):
    client = FakeClient(releases=[], tags=[])
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1], force=True)
    assert len(client.calls) == 4  # 2 na primeira + 2 na segunda (force)


def test_repos_diferentes_nao_compartilham_cache(cache: JsonCache):
    client_a = FakeClient(releases=[_release("v1.0", "2026-01-15T00:00:00Z")], tags=[])
    client_b = FakeClient(releases=[_release("v2.0", "2026-06-01T00:00:00Z")], tags=[])
    collect_repo_releases(client_a, "o", "a", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    result_b = collect_repo_releases(client_b, "o", "b", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert result_b.releases[0].tag_name == "v2.0"
    assert len(client_b.calls) == 2


# ---------------------------------------------------------------------------
# collect_sample_releases
# ---------------------------------------------------------------------------

def test_collect_sample_releases_multiplos_repos(cache: JsonCache):
    client = FakeClient(releases=[_release("v1.0", "2026-01-01T00:00:00Z")], tags=[_tag("v1.0")])
    rows = [{"full_name": "o/a"}, {"full_name": "o/b"}]
    results = collect_sample_releases(client, rows, cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert len(results) == 2
    assert results[0].full_name == "o/a"
    assert results[1].full_name == "o/b"


def test_collect_sample_releases_full_name_invalido(cache: JsonCache):
    client = FakeClient(releases=[], tags=[])
    with pytest.raises(ValueError, match="inválido"):
        collect_sample_releases(client, [{"full_name": "semslash"}], cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])


# ---------------------------------------------------------------------------
# write_releases_csv
# ---------------------------------------------------------------------------

def test_write_releases_csv_gera_dois_arquivos(tmp_path: Path):
    results = [
        ReleasesResult(
            full_name="o/r",
            releases=[Release(full_name="o/r", tag_name="v1.0", name="v1.0",
                              published_at="2026-01-15T00:00:00Z", prerelease=False,
                              html_url="https://github.com/o/r/releases/tag/v1.0")],
            tags=[Tag(full_name="o/r", tag_name="v1.0", sha="deadbeef")],
        )
    ]
    releases_path, tags_path = write_releases_csv(results, tmp_path / "output")
    assert releases_path.is_file()
    assert tags_path.is_file()
    releases_text = releases_path.read_text(encoding="utf-8")
    assert "v1.0" in releases_text
    assert "o/r" in releases_text
    tags_text = tags_path.read_text(encoding="utf-8")
    assert "deadbeef" in tags_text


def test_write_releases_csv_sem_dados(tmp_path: Path):
    releases_path, tags_path = write_releases_csv([], tmp_path / "output")
    assert releases_path.read_text(encoding="utf-8").startswith("full_name")
    assert tags_path.read_text(encoding="utf-8").startswith("full_name")


def test_write_releases_csv_prerelease_no_csv(tmp_path: Path):
    results = [
        ReleasesResult(
            full_name="o/r",
            releases=[Release(full_name="o/r", tag_name="v1.0-rc1", name="rc1",
                              published_at="2026-01-15T00:00:00Z", prerelease=True,
                              html_url="")],
            tags=[],
        )
    ]
    releases_path, _ = write_releases_csv(results, tmp_path / "output")
    text = releases_path.read_text(encoding="utf-8")
    assert "True" in text


# ---------------------------------------------------------------------------
# run_releases_collection
# ---------------------------------------------------------------------------

def test_run_releases_collection_grava_csvs(tmp_path: Path):
    sample = tmp_path / "output" / "sample_repos.csv"
    sample.parent.mkdir()
    sample.write_text(
        "full_name,owner,name\no/r,o,r\n", encoding="utf-8"
    )
    client = FakeClient(
        releases=[_release("v1.0", "2026-01-15T00:00:00Z")],
        tags=[_tag("v1.0")],
    )
    config = {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "paths": {"cache_dir": str(tmp_path / "cache"), "output_dir": str(tmp_path / "output")},
    }
    results, releases_path, tags_path = run_releases_collection(client, config)
    assert len(results) == 1
    assert releases_path.is_file()
    assert tags_path.is_file()


def test_run_releases_collection_levanta_sem_amostra(tmp_path: Path):
    client = FakeClient(releases=[], tags=[])
    config = {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "paths": {"cache_dir": str(tmp_path / "cache"), "output_dir": str(tmp_path / "output")},
    }
    with pytest.raises(FileNotFoundError):
        run_releases_collection(client, config)

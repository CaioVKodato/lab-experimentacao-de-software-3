"""Testes da coleta de metadados (Issue #3)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pipeline.cache import JsonCache, safe_key
from pipeline.metadata import (
    collect_repo_metadata,
    compute_age_days,
    count_contributors,
    load_sample_repos,
    run_metadata_collection,
    write_metadata_csv,
    RepoMetadata,
)


class FakeClient:
    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_json(self, path: str, params=None):
        self.calls.append(path)
        key = path
        handler = self.responses[key]
        if callable(handler):
            return handler(params)
        return handler


def test_safe_key_and_cache_roundtrip(tmp_path: Path):
    assert safe_key("org/repo") == "org_repo"
    cache = JsonCache(tmp_path / "cache")
    cache.save("metadata/acme/demo.json", {"full_name": "acme/demo", "n": 1})
    assert cache.exists("metadata/acme/demo.json")
    assert cache.load("metadata/acme/demo.json")["n"] == 1


def test_compute_age_days():
    assert compute_age_days("2024-01-01T00:00:00Z", date(2025, 1, 1)) == 366
    assert compute_age_days(None, date(2025, 1, 1)) is None


def test_count_contributors_uses_link_last_page():
    client = FakeClient(
        {
            "/repos/acme/demo/contributors": (
                [{"login": "a"}],
                {
                    "Link": '<https://api.github.com/repos/acme/demo/contributors?page=42&per_page=1&anon=true>; rel="last"'
                },
            )
        }
    )
    assert count_contributors(client, "acme", "demo") == 42


def test_count_contributors_without_link():
    client = FakeClient(
        {"/repos/acme/demo/contributors": ([{"login": "only"}], {})}
    )
    assert count_contributors(client, "acme", "demo") == 1

    empty = FakeClient({"/repos/acme/empty/contributors": (None, {})})
    assert count_contributors(empty, "acme", "empty") == 0


def test_collect_repo_metadata_and_cache_resume(tmp_path: Path):
    repo_body = {
        "full_name": "acme/demo",
        "html_url": "https://github.com/acme/demo",
        "stargazers_count": 1234,
        "language": "Python",
        "default_branch": "main",
        "created_at": "2020-06-15T00:00:00Z",
        "forks_count": 9,
        "open_issues_count": 2,
    }
    client = FakeClient(
        {
            "/repos/acme/demo": (repo_body, {}),
            "/repos/acme/demo/contributors": (
                [{"login": "a"}],
                {
                    "Link": '<https://api.github.com/x?page=7&per_page=1>; rel="last"'
                },
            ),
        }
    )
    cache = JsonCache(tmp_path / "cache")
    meta = collect_repo_metadata(
        client,
        "acme",
        "demo",
        cache=cache,
        reference=date(2026, 9, 30),
    )
    assert meta.stargazers_count == 1234
    assert meta.language == "Python"
    assert meta.contributors_count == 7
    assert meta.default_branch == "main"
    assert meta.age_days == compute_age_days(meta.created_at, date(2026, 9, 30))
    assert meta.from_cache is False
    assert len(client.calls) == 2

    # segunda chamada deve vir só do cache
    meta2 = collect_repo_metadata(
        client,
        "acme",
        "demo",
        cache=cache,
        reference=date(2026, 9, 30),
    )
    assert meta2.from_cache is True
    assert meta2.contributors_count == 7
    assert len(client.calls) == 2


def test_run_metadata_collection_writes_csv(tmp_path: Path):
    sample = tmp_path / "sample_repos.csv"
    sample.write_text(
        "full_name,owner,name,html_url,stargazers_count,language,default_branch,created_at,forks_count\n"
        "acme/demo,acme,demo,https://github.com/acme/demo,10,Python,main,2020-01-01T00:00:00Z,1\n",
        encoding="utf-8",
    )
    client = FakeClient(
        {
            "/repos/acme/demo": (
                {
                    "html_url": "https://github.com/acme/demo",
                    "stargazers_count": 99,
                    "language": "Python",
                    "default_branch": "main",
                    "created_at": "2020-01-01T00:00:00Z",
                    "forks_count": 1,
                    "open_issues_count": 0,
                },
                {},
            ),
            "/repos/acme/demo/contributors": ([{"login": "a"}], {}),
        }
    )
    config = {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "paths": {
            "cache_dir": str(tmp_path / "cache"),
            "output_dir": str(tmp_path / "output"),
        },
    }
    # coloca a amostra no output_dir padrão esperado
    out = tmp_path / "output"
    out.mkdir()
    (out / "sample_repos.csv").write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")

    rows, csv_path = run_metadata_collection(client, config)
    assert len(rows) == 1
    assert rows[0].stargazers_count == 99
    assert csv_path.is_file()
    text = csv_path.read_text(encoding="utf-8")
    assert "contributors_count" in text
    assert "age_days" in text


def test_load_sample_repos_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_sample_repos(tmp_path / "missing.csv")


def test_write_metadata_csv(tmp_path: Path):
    path = write_metadata_csv(
        [
            RepoMetadata(
                full_name="a/b",
                owner="a",
                name="b",
                html_url="https://github.com/a/b",
                stargazers_count=1,
                language=None,
                default_branch="main",
                created_at="2020-01-01T00:00:00Z",
                age_days=10,
                contributors_count=3,
            )
        ],
        tmp_path / "repo_metadata.csv",
    )
    assert "a/b" in path.read_text(encoding="utf-8")

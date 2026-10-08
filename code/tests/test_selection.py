"""Testes da seleção de repositórios e funil (Issue #2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pipeline.selection import (
    DEFAULT_STAR_RANGES,
    count_published_releases_in_window,
    count_valid_workflow_runs,
    evaluate_repo,
    has_github_actions,
    in_observation_window,
    parse_star_ranges,
    repo_from_search_item,
    run_selection,
    search_candidates,
    stars_query,
    write_selection_outputs,
    RepoCandidate,
    _month_slices,
)


WINDOW_START = date(2025, 10, 1)
WINDOW_END = date(2026, 9, 30)


class FakeClient:
    """Cliente falso com respostas programadas por path."""

    def __init__(self, responses: dict | None = None) -> None:
        self.per_page = 100
        self.calls: list[tuple[str, dict | None]] = []
        self._responses = responses or {}

    def get(self, path: str, params: dict | None = None):
        self.calls.append((path, params))
        key = path
        handler = self._responses.get(key)
        if callable(handler):
            return handler(params)
        if handler is not None:
            return handler
        raise AssertionError(f"sem resposta fake para GET {path} params={params}")

    def paginate(self, path: str, params: dict | None = None, **kwargs):
        body = self.get(path, params)
        if isinstance(body, list):
            items = body
        else:
            items_key = kwargs.get("items_key")
            items = body[items_key] if items_key else body
        max_items = kwargs.get("max_items")
        if max_items is not None:
            return items[:max_items]
        return items


def test_stars_query_and_ranges():
    assert stars_query(1000, 2000) == "stars:1000..2000"
    assert stars_query(100001, None) == "stars:>=100001"
    ranges = parse_star_ranges([{"min": 1, "max": 10}, {"min": 11, "max": None}])
    assert ranges == [(1, 10), (11, None)]
    assert parse_star_ranges(None) == DEFAULT_STAR_RANGES


def test_in_observation_window_and_month_slices():
    assert in_observation_window("2025-12-15T10:00:00Z", WINDOW_START, WINDOW_END)
    assert not in_observation_window("2024-01-01T00:00:00Z", WINDOW_START, WINDOW_END)
    slices = _month_slices(date(2025, 10, 15), date(2025, 12, 5))
    assert slices == [
        (date(2025, 10, 15), date(2025, 10, 31)),
        (date(2025, 11, 1), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 5)),
    ]


def test_repo_from_search_item():
    repo = repo_from_search_item(
        {
            "full_name": "acme/demo",
            "html_url": "https://github.com/acme/demo",
            "stargazers_count": 1500,
            "language": "Python",
            "default_branch": "main",
            "created_at": "2020-01-01T00:00:00Z",
            "forks_count": 10,
        }
    )
    assert repo.owner == "acme"
    assert repo.name == "demo"
    assert repo.default_branch == "main"


def test_search_candidates_slices_and_dedup():
    def search_handler(params):
        q = params["q"]
        if "stars:1000..2000" in q and params["page"] == 1:
            return {
                "items": [
                    {
                        "full_name": "org/a",
                        "stargazers_count": 1500,
                        "language": "Go",
                        "default_branch": "main",
                        "html_url": "https://github.com/org/a",
                    },
                    {
                        "full_name": "org/b",
                        "stargazers_count": 1200,
                        "language": "Python",
                        "default_branch": "master",
                        "html_url": "https://github.com/org/b",
                    },
                ]
            }
        if "stars:2001..5000" in q and params["page"] == 1:
            return {
                "items": [
                    {
                        "full_name": "org/a",  # duplicata
                        "stargazers_count": 1500,
                        "language": "Go",
                        "default_branch": "main",
                        "html_url": "https://github.com/org/a",
                    },
                    {
                        "full_name": "org/c",
                        "stargazers_count": 3000,
                        "language": "Rust",
                        "default_branch": "main",
                        "html_url": "https://github.com/org/c",
                    },
                ]
            }
        return {"items": []}

    client = FakeClient({"/search/repositories": search_handler})
    repos = search_candidates(
        client,
        [(1000, 2000), (2001, 5000)],
        max_candidates=10,
    )
    names = [r.full_name for r in repos]
    assert names == ["org/a", "org/b", "org/c"]
    assert any("stars:1000..2000" in (c[1] or {}).get("q", "") for c in client.calls)


def test_has_actions_and_release_count():
    client = FakeClient(
        {
            "/repos/acme/demo/actions/workflows": {"total_count": 2, "workflows": []},
            "/repos/acme/demo/releases": [
                {
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2025-11-01T00:00:00Z",
                },
                {
                    "draft": True,
                    "prerelease": False,
                    "published_at": "2025-11-02T00:00:00Z",
                },
                {
                    "draft": False,
                    "prerelease": True,
                    "published_at": "2025-11-03T00:00:00Z",
                },
                {
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2024-01-01T00:00:00Z",
                },
            ],
        }
    )
    assert has_github_actions(client, "acme", "demo") is True
    assert (
        count_published_releases_in_window(
            client, "acme", "demo", WINDOW_START, WINDOW_END
        )
        == 1
    )


def test_count_valid_workflow_runs_filters_conclusions():
    def runs_handler(params):
        assert params["event"] == "push"
        assert params["branch"] == "main"
        # uma fatia mensal qualquer
        return {
            "workflow_runs": [
                {"conclusion": "success"},
                {"conclusion": "failure"},
                {"conclusion": "cancelled"},
                {"conclusion": "timed_out"},
                {"conclusion": None},
                {"conclusion": "startup_failure"},
            ]
        }

    client = FakeClient({"/repos/acme/demo/actions/runs": runs_handler})
    # janela de 1 mês => uma fatia
    n = count_valid_workflow_runs(
        client,
        "acme",
        "demo",
        "main",
        date(2025, 10, 1),
        date(2025, 10, 31),
    )
    # success + failure + timed_out + startup_failure = 4 (cancelled/None ignorados)
    assert n == 4


def test_evaluate_repo_funnel_reasons():
    repo = RepoCandidate(
        full_name="acme/demo",
        owner="acme",
        name="demo",
        html_url="https://github.com/acme/demo",
        stargazers_count=2000,
        language="Python",
        default_branch="main",
    )
    no_actions = FakeClient(
        {"/repos/acme/demo/actions/workflows": {"total_count": 0, "workflows": []}}
    )
    row = evaluate_repo(
        no_actions,
        repo,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        min_releases=5,
        min_workflow_runs=50,
    )
    assert row["discard_reason"] == "no_actions"
    assert row["included"] is False


def test_run_selection_builds_funnel_and_sample(tmp_path: Path):
    search_items = [
        {
            "full_name": "org/keep",
            "stargazers_count": 5000,
            "language": "Python",
            "default_branch": "main",
            "html_url": "https://github.com/org/keep",
        },
        {
            "full_name": "org/noact",
            "stargazers_count": 4000,
            "language": "Go",
            "default_branch": "main",
            "html_url": "https://github.com/org/noact",
        },
        {
            "full_name": "org/fewrel",
            "stargazers_count": 3000,
            "language": "Rust",
            "default_branch": "main",
            "html_url": "https://github.com/org/fewrel",
        },
    ]

    def search_handler(params):
        if params["page"] == 1:
            return {"items": search_items}
        return {"items": []}

    release_dates = [
        "2025-10-15T00:00:00Z",
        "2025-11-15T00:00:00Z",
        "2025-12-15T00:00:00Z",
        "2026-01-15T00:00:00Z",
        "2026-02-15T00:00:00Z",
        "2026-03-15T00:00:00Z",
    ]
    releases_ok = [
        {"draft": False, "prerelease": False, "published_at": dt}
        for dt in release_dates
    ]
    releases_few = releases_ok[:2]

    def runs_many(_params):
        return {"workflow_runs": [{"conclusion": "success"}] * 50}

    def runs_few(_params):
        return {"workflow_runs": [{"conclusion": "success"}] * 3}

    responses = {
        "/search/repositories": search_handler,
        "/repos/org/keep/actions/workflows": {"total_count": 1},
        "/repos/org/noact/actions/workflows": {"total_count": 0},
        "/repos/org/fewrel/actions/workflows": {"total_count": 1},
        "/repos/org/keep/releases": releases_ok,
        "/repos/org/fewrel/releases": releases_few,
        "/repos/org/keep/actions/runs": runs_many,
        "/repos/org/fewrel/actions/runs": runs_few,
    }
    client = FakeClient(responses)

    config = {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "sample": {"target_size": 100, "min_releases": 5, "min_workflow_runs": 50},
        "github": {
            "star_ranges": [{"min": 1000, "max": 10000}],
            "max_search_candidates": 10,
        },
    }
    result = run_selection(client, config)
    assert result.funnel.candidates == 3
    assert result.funnel.with_actions == 2
    assert result.funnel.with_min_criteria == 1
    assert result.funnel.sample == 1
    assert result.funnel.discarded_no_actions == 1
    assert result.funnel.discarded_few_releases == 1
    assert result.sample[0].full_name == "org/keep"

    paths = write_selection_outputs(result, tmp_path)
    assert paths["funnel_csv"].is_file()
    assert paths["sample_csv"].is_file()
    content = paths["funnel_csv"].read_text(encoding="utf-8")
    assert "candidatos" in content
    assert "com_actions" in content

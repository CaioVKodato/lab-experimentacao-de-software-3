"""Testes do orquestrador do pipeline único (Issue #11)."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.orchestrator import (
    _release_pairs,
    run_compare_collection,
    run_full_pipeline,
    run_workflow_runs_collection,
)
from pipeline.releases import Release
from pipeline.selection import FunnelStats, SelectionResult
from pipeline.workflow_runs import ColetaRuns


def test_release_pairs_ignora_prerelease_e_ordena():
    releases = [
        Release("o/r", "v1.0.0", None, "2025-11-01T00:00:00Z", False, ""),
        Release("o/r", "v1.0.1-rc", None, "2025-11-02T00:00:00Z", True, ""),
        Release("o/r", "v1.1.0", None, "2025-11-03T00:00:00Z", False, ""),
    ]
    assert _release_pairs(releases) == [("v1.0.0", "v1.1.0")]


def test_run_compare_collection(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "sample_repos.csv").write_text(
        "full_name,default_branch\norg/demo,main\n", encoding="utf-8"
    )
    with (out / "releases.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "full_name",
                "tag_name",
                "name",
                "published_at",
                "prerelease",
                "html_url",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "full_name": "org/demo",
                "tag_name": "v1",
                "name": "",
                "published_at": "2025-10-01T00:00:00Z",
                "prerelease": "False",
                "html_url": "",
            }
        )
        w.writerow(
            {
                "full_name": "org/demo",
                "tag_name": "v2",
                "name": "",
                "published_at": "2025-11-01T00:00:00Z",
                "prerelease": "False",
                "html_url": "",
            }
        )

    fake_result = MagicMock()
    fake_result.not_found = False
    fake_result.full_name = "org/demo"
    fake_result.base_tag = "v1"
    fake_result.head_tag = "v2"
    fake_result.commits = []

    config = {
        "paths": {"output_dir": str(out), "cache_dir": str(tmp_path / "cache")},
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
    }
    with patch(
        "pipeline.orchestrator.collect_repo_commits_between_releases",
        return_value=[fake_result],
    ):
        results, path, n404 = run_compare_collection(MagicMock(), config)

    assert path.is_file()
    assert n404 == 0
    assert len(results) == 1


def test_run_workflow_runs_collection(tmp_path: Path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "sample_repos.csv").write_text(
        "full_name,default_branch\norg/demo,main\n", encoding="utf-8"
    )
    config = {
        "paths": {"output_dir": str(out), "cache_dir": str(tmp_path / "cache")},
        "observation_window": {"start": "2025-10-01", "end": "2025-10-31"},
    }
    coleta = ColetaRuns(
        runs=[
            {
                "id": 1,
                "workflow_id": 9,
                "name": "CI",
                "event": "push",
                "head_branch": "main",
                "head_sha": "abc",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2025-10-05T00:00:00Z",
                "run_started_at": "2025-10-05T00:00:00Z",
                "updated_at": "2025-10-05T00:10:00Z",
            }
        ],
        n_consultas=1,
        intervalos_truncados=(),
    )
    with patch(
        "pipeline.orchestrator.coletar_workflow_runs", return_value=coleta
    ):
        path, summary = run_workflow_runs_collection(MagicMock(), config)

    assert path.is_file()
    assert summary["n_runs"] == 1
    assert summary["n_repos"] == 1


def test_run_full_pipeline_all_stages(tmp_path: Path):
    out = tmp_path / "output"
    cache = tmp_path / "cache"
    config = {
        "paths": {"output_dir": str(out), "cache_dir": str(cache)},
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "sample": {"target_size": 100},
    }
    funnel = FunnelStats(candidates=10, with_actions=5, with_min_criteria=2, sample=2)
    funnel.build_stages()
    sel = SelectionResult(sample=[], funnel=funnel, evaluated=[])

    with (
        patch("pipeline.orchestrator.run_selection", return_value=sel),
        patch(
            "pipeline.orchestrator.write_selection_outputs",
            return_value={"funnel_csv": out / "funnel.csv"},
        ),
        patch(
            "pipeline.orchestrator.run_metadata_collection",
            return_value=([], out / "repo_metadata.csv"),
        ),
        patch(
            "pipeline.orchestrator.run_releases_collection",
            return_value=([], out / "releases.csv", out / "tags.csv"),
        ),
        patch(
            "pipeline.orchestrator.run_compare_collection",
            return_value=([], out / "commits.csv", 0),
        ),
        patch(
            "pipeline.orchestrator.run_workflow_runs_collection",
            return_value=(
                out / "runs.csv",
                {
                    "n_repos": 0,
                    "n_runs": 0,
                    "n_consultas": 0,
                    "n_intervalos_truncados": 0,
                    "truncados_csv": str(out / "trunc.csv"),
                },
            ),
        ),
    ):
        report = run_full_pipeline(MagicMock(), config)

    names = [s.name for s in report.stages]
    assert names[:5] == [
        "selection",
        "metadata",
        "releases",
        "compare",
        "workflow-runs",
    ]
    assert (out / "pipeline_manifest.csv").is_file()

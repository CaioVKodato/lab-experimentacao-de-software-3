"""Smoke tests da estrutura base e carregamento de config."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.cli import load_config, main
from pipeline.selection import FunnelStats, SelectionResult


ROOT = Path(__file__).resolve().parents[1]


def test_load_config_yaml():
    config = load_config(ROOT / "config.yaml")
    assert "observation_window" in config
    assert "sample" in config
    assert config["sample"]["target_size"] == 100
    assert config["sample"]["min_releases"] == 5
    assert config["sample"]["min_workflow_runs"] == 50
    assert "star_ranges" in config["github"]


def test_main_requires_github_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["--config", str(ROOT / "config.yaml")]) == 1


def test_main_runs_selection_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    monkeypatch.chdir(tmp_path)

    fake_result = SelectionResult(
        sample=[],
        funnel=FunnelStats(
            candidates=0,
            with_actions=0,
            with_min_criteria=0,
            sample=0,
        ),
    )
    fake_result.funnel.build_stages()

    with patch("pipeline.cli.run_selection", return_value=fake_result) as mocked:
        with patch("pipeline.cli.write_selection_outputs", return_value={}) as written:
            code = main(
                [
                    "--config",
                    str(ROOT / "config.yaml"),
                    "--stage",
                    "selection",
                    "--max-candidates",
                    "5",
                ]
            )
    assert code == 0
    mocked.assert_called_once()
    written.assert_called_once()


def test_main_runs_metadata_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    monkeypatch.chdir(tmp_path)

    with patch(
        "pipeline.cli.run_metadata_collection",
        return_value=([], tmp_path / "repo_metadata.csv"),
    ) as mocked:
        code = main(
            [
                "--config",
                str(ROOT / "config.yaml"),
                "--stage",
                "metadata",
            ]
        )
    assert code == 0
    mocked.assert_called_once()

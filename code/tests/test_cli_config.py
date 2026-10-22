"""Smoke tests da estrutura base e carregamento de config."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.cli import load_config, main
from pipeline.orchestrator import PipelineReport, StageReport


ROOT = Path(__file__).resolve().parents[1]


def test_load_config_yaml():
    config = load_config(ROOT / "config.yaml")
    assert "observation_window" in config
    assert "sample" in config
    assert config["sample"]["target_size"] == 300
    assert config["sample"]["stratify"] is True
    assert config["sample"]["min_releases"] == 5
    assert config["sample"]["min_workflow_runs"] == 50
    assert "star_ranges" in config["github"]
    assert config["validation"]["n_repos"] == 60
    assert config["validation"]["random_state_repos"] == 42


def test_main_requires_github_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["--config", str(ROOT / "config.yaml")]) == 1


def test_main_runs_all_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    monkeypatch.chdir(tmp_path)

    report = PipelineReport()
    report.add(
        StageReport(
            name="selection",
            ok=True,
            detail="funil amostra=2/candidatos=10",
            paths={},
        )
    )

    with patch("pipeline.cli.run_full_pipeline", return_value=report) as mocked:
        code = main(["--config", str(ROOT / "config.yaml")])
    assert code == 0
    mocked.assert_called_once()
    assert mocked.call_args.kwargs.get("stages") is None


def test_main_single_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    monkeypatch.chdir(tmp_path)

    with patch(
        "pipeline.cli.run_full_pipeline",
        return_value=PipelineReport(),
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
    assert mocked.call_args.kwargs["stages"] == ["metadata"]


def test_main_amostra_ouro_sem_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    with patch(
        "pipeline.cli.run_amostra_ouro",
        return_value={
            "n_repos": 60,
            "n_releases": 300,
            "meta_csv": tmp_path / "meta.csv",
            "planilhas": {"CaioVKodato": tmp_path / "p.csv"},
        },
    ) as mocked:
        code = main(
            [
                "--config",
                str(ROOT / "config.yaml"),
                "--stage",
                "amostra-ouro",
            ]
        )
    assert code == 0
    mocked.assert_called_once()

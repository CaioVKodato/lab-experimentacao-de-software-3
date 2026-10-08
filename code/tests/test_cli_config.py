"""Smoke tests da estrutura base e carregamento de config."""

from pathlib import Path

import pytest

from pipeline.cli import load_config, main


ROOT = Path(__file__).resolve().parents[1]


def test_load_config_yaml():
    config = load_config(ROOT / "config.yaml")
    assert "observation_window" in config
    assert "sample" in config
    assert config["sample"]["target_size"] == 100
    assert config["sample"]["min_releases"] == 5
    assert config["sample"]["min_workflow_runs"] == 50


def test_main_requires_github_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["--config", str(ROOT / "config.yaml")]) == 1


def test_main_ok_with_token(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")
    assert main(["--config", str(ROOT / "config.yaml")]) == 0
    out = capsys.readouterr().out
    assert "Pipeline DORA" in out

"""Testes do sorteio da amostra-ouro (Issue #23)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pipeline.amostra_ouro import (
    AmostraOuroConfig,
    gerar_amostra_ouro,
    montar_planilha,
    sortear_releases_por_repo,
    sortear_repositorios,
)


def _write_sample(path: Path, n: int = 80) -> None:
    rows = [
        {
            "full_name": f"org/repo{i:03d}",
            "html_url": f"https://github.com/org/repo{i:03d}",
            "stargazers_count": 1000 + i,
            "language": "Python",
            "default_branch": "main",
        }
        for i in range(n)
    ]
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_releases(path: Path, n_repos: int = 80, n_rel: int = 8) -> None:
    rows = []
    for i in range(n_repos):
        full = f"org/repo{i:03d}"
        for j in range(n_rel):
            rows.append(
                {
                    "full_name": full,
                    "tag_name": f"v1.{j}.0",
                    "name": f"Release {j}",
                    "published_at": f"2025-{10 + (j % 2):02d}-{1 + j:02d}T00:00:00Z",
                    "prerelease": j == 7,
                    "html_url": f"https://github.com/{full}/releases/tag/v1.{j}.0",
                }
            )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_sorteio_reproduzivel(tmp_path: Path):
    sample = tmp_path / "sample_repos.csv"
    _write_sample(sample, 80)
    df = pd.read_csv(sample)
    a = sortear_repositorios(df, n=60, random_state=42)
    b = sortear_repositorios(df, n=60, random_state=42)
    assert list(a["full_name"]) == list(b["full_name"])
    assert len(a) == 60


def test_gerar_amostra_ouro_escreve_planilhas(tmp_path: Path):
    sample = tmp_path / "sample_repos.csv"
    releases = tmp_path / "releases.csv"
    out = tmp_path / "validacao"
    _write_sample(sample, 65)
    _write_releases(releases, 65, 8)

    result = gerar_amostra_ouro(
        sample,
        releases,
        out,
        cfg=AmostraOuroConfig(
            n_repos=60,
            n_releases_per_repo=5,
            evaluators=("CaioVKodato", "Kjonps"),
        ),
    )
    assert result["n_repos"] == 60
    assert result["n_releases"] == 60 * 5
    assert result["planilhas"]["CaioVKodato"].is_file()
    sheet = pd.read_csv(result["planilhas"]["CaioVKodato"], dtype=str).fillna("")
    assert (sheet["tipo_projeto"] == "").all()
    assert (sheet["eh_corretiva"] == "").all()


def test_montar_planilha_sem_heuristica(tmp_path: Path):
    sample = tmp_path / "s.csv"
    releases = tmp_path / "r.csv"
    _write_sample(sample, 70)
    _write_releases(releases, 70, 8)
    repos = sortear_repositorios(pd.read_csv(sample), n=60, random_state=42)
    # frame bruto (com pré-release) — só para montar um pedaço
    rel = sortear_releases_por_repo(
        pd.read_csv(releases), repos, n_per_repo=5, random_state=42
    )
    planilha = montar_planilha(repos, rel.head(5))
    assert "heuristica" not in "".join(planilha.columns).lower()


def test_amostra_insuficiente(tmp_path: Path):
    sample = tmp_path / "sample_repos.csv"
    _write_sample(sample, 10)
    with pytest.raises(ValueError, match="necessários"):
        sortear_repositorios(pd.read_csv(sample), n=60, random_state=42)

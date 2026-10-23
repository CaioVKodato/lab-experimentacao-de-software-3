"""Testes da análise RQ01–RQ04 (Issue #36)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from analise.rq01_rq04 import (
    analisar,
    aplicar_c1,
    descritiva,
    resumo_censura_rq04,
    tabela_descritivas,
)


def _mini_csv(path: Path) -> Path:
    rows = [
        {
            "repo": "a/elite",
            "deploys_por_semana": 10.0,
            "lead_time_a_horas": 2.0,
            "lead_time_b_horas": 1.0,
            "cfr_a": 0.05,
            "cfr_b": 0.10,
            "recuperacao_mediana_horas": 0.5,
            "recuperacao_n_episodios": 4,
            "recuperacao_prop_censurados": 0.0,
        },
        {
            "repo": "b/high",
            "deploys_por_semana": 2.0,
            "lead_time_a_horas": 48.0,
            "lead_time_b_horas": 24.0,
            "cfr_a": 0.20,
            "cfr_b": 0.25,
            "recuperacao_mediana_horas": 12.0,
            "recuperacao_n_episodios": 3,
            "recuperacao_prop_censurados": 0.25,
        },
        {
            "repo": "c/ausente",
            "deploys_por_semana": 0.5,
            "lead_time_a_horas": "",
            "lead_time_b_horas": 100.0,
            "cfr_a": 0.40,
            "cfr_b": "",
            "recuperacao_mediana_horas": "",
            "recuperacao_n_episodios": 0,
            "recuperacao_prop_censurados": "",
        },
        {
            "repo": "d/low",
            "deploys_por_semana": 0.05,
            "lead_time_a_horas": 900.0,
            "lead_time_b_horas": 800.0,
            "cfr_a": 0.55,
            "cfr_b": 0.50,
            "recuperacao_mediana_horas": 200.0,
            "recuperacao_n_episodios": 2,
            "recuperacao_prop_censurados": 0.5,
        },
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_descritiva_conta_ausentes():
    s = pd.Series([1.0, 2.0, None, 4.0])
    stats = descritiva(s)
    assert stats["n"] == 3
    assert stats["n_ausentes"] == 1
    assert stats["mediana"] == pytest.approx(2.0)
    assert stats["iqr"] == pytest.approx(1.5)


def test_tabela_e_c1_com_csv_exemplo(tmp_path: Path):
    csv_path = _mini_csv(tmp_path / "mini.csv")
    df = pd.read_csv(csv_path)
    tab = tabela_descritivas(df)
    assert "deploys_por_semana" in set(tab["metrica"])
    lead = tab.set_index("metrica").loc["lead_time_a_horas"]
    assert lead["n_ausentes"] == 1
    assert lead["n"] == 3

    classificado = aplicar_c1(df)
    assert classificado.loc[0, "dora_c1"] == "Elite"
    assert classificado.loc[3, "dora_c1"] == "Low"
    assert pd.isna(classificado.loc[2, "dora_c1"]) or classificado.loc[2, "dora_c1"] is None


def test_censura_repos_sem_episodio(tmp_path: Path):
    df = pd.read_csv(_mini_csv(tmp_path / "mini.csv"))
    resumo = resumo_censura_rq04(df)
    assert resumo["n_repos_sem_episodio_falha"] == 1
    assert resumo["prop_censurados_mediana"] == pytest.approx(0.25)


def test_analisar_escreve_saidas(tmp_path: Path):
    csv_path = _mini_csv(tmp_path / "mini.csv")
    outdir = tmp_path / "s03"
    result = analisar(csv_path, outdir)
    assert result["descritivas"].is_file()
    assert result["classificacao"].is_file()
    assert result["distribuicao_c1"].is_file()
    assert result["censura"].is_file()
    assert result["readme"].is_file()
    assert len(result["figuras"]) >= 1
    dist = pd.read_csv(result["distribuicao_c1"])
    assert "Elite" in set(dist["nivel"])

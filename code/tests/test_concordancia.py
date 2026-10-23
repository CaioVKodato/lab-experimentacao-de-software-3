"""Testes de kappa de Fleiss e consenso (Issue #27)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pipeline.concordancia import (
    calcular_fleiss,
    carregar_rotulos,
    consenso_maioria,
    gerar_consenso,
    interpretar_landis_koch,
    run_concordancia,
)


def _escrever_rotulos(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def _kit_unanime(tmp_path: Path) -> Path:
    """Acordo perfeito entre avaliadores, com variação de categorias (κ definido)."""
    rows = []
    tipos = ("biblioteca/framework", "aplicacao/servico", "ferramenta_CLI")
    entregas = ("sim", "nao", "incerto")
    for i in range(6):
        tipo = tipos[i % 3]
        entrega = entregas[i % 3]
        for j, corretiva in enumerate(("nao", "sim")):
            rows.append(
                {
                    "full_name": f"org/repo{i}",
                    "repo_url": f"https://github.com/org/repo{i}",
                    "tipo_projeto": tipo,
                    "entrega_real": entrega,
                    "release_tag": f"v1.0.{j}",
                    "release_published_at": f"2025-11-{1 + j:02d}T00:00:00Z",
                    "release_url": (
                        f"https://github.com/org/repo{i}/releases/tag/v1.0.{j}"
                    ),
                    "eh_corretiva": corretiva,
                }
            )
    for av in ("CaioVKodato", "Henrique-volponi", "Kjonps"):
        _escrever_rotulos(tmp_path / f"rotulos_{av}.csv", rows)
    return tmp_path


def test_landis_koch():
    assert "quase perfeita" in interpretar_landis_koch(0.85)
    assert "moderada" in interpretar_landis_koch(0.50)
    assert "pobre" in interpretar_landis_koch(-0.1)


def test_consenso_maioria_e_empate():
    assert consenso_maioria(["sim", "sim", "nao"]) == ("sim", "maioria")
    assert consenso_maioria(["sim", "sim", "sim"]) == ("sim", "unanime")
    assert consenso_maioria(["sim", "nao", "incerto"]) == ("", "empate_reuniao")


def test_fleiss_unanime_alto(tmp_path: Path):
    _kit_unanime(tmp_path)
    frames = carregar_rotulos(tmp_path)
    for dim in ("tipo_projeto", "entrega_real", "eh_corretiva"):
        resultado = calcular_fleiss(frames, dim)
        assert resultado.n_itens >= 2
        assert resultado.kappa == pytest.approx(1.0)


def test_run_concordancia_escreve_arquivos(tmp_path: Path):
    _kit_unanime(tmp_path)
    # introduz um empate 1-1-1 em um release
    caio = pd.read_csv(tmp_path / "rotulos_CaioVKodato.csv", dtype=str)
    hen = pd.read_csv(tmp_path / "rotulos_Henrique-volponi.csv", dtype=str)
    kjo = pd.read_csv(tmp_path / "rotulos_Kjonps.csv", dtype=str)
    caio.loc[0, "eh_corretiva"] = "sim"
    hen.loc[0, "eh_corretiva"] = "nao"
    # Kjonps permanece "nao" → maioria nao; força empate trocando Kjonps para incerto inválido
    # para eh_corretiva só sim/nao — usa outro release e três valores distintos via tipo
    kjo.loc[0, "eh_corretiva"] = "nao"
    caio.to_csv(tmp_path / "rotulos_CaioVKodato.csv", index=False)
    hen.to_csv(tmp_path / "rotulos_Henrique-volponi.csv", index=False)
    kjo.to_csv(tmp_path / "rotulos_Kjonps.csv", index=False)

    # empate tipo_projeto no repo0
    for av, valor in (
        ("CaioVKodato", "biblioteca/framework"),
        ("Henrique-volponi", "aplicacao/servico"),
        ("Kjonps", "outro"),
    ):
        df = pd.read_csv(tmp_path / f"rotulos_{av}.csv", dtype=str)
        df.loc[df["full_name"] == "org/repo0", "tipo_projeto"] = valor
        df.to_csv(tmp_path / f"rotulos_{av}.csv", index=False)

    result = run_concordancia(tmp_path)
    assert result["kappa_csv"].is_file()
    assert result["consenso_csv"].is_file()
    consenso = pd.read_csv(result["consenso_csv"], dtype=str).fillna("")
    repo0 = consenso[consenso["full_name"] == "org/repo0"].iloc[0]
    assert repo0["tipo_projeto__status"] == "empate_reuniao"
    assert repo0["tipo_projeto"] == ""

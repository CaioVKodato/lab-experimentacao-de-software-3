"""Testes da classificação DORA (Issue #35).

Exemplo do enunciado: notas (4, 3, 3, 1) → High.
Fronteiras: 24h / 168h / 720h; CFR 0,15 / 0,30 / 0,45; recuperação 1h / 24h / 168h.
"""
from __future__ import annotations

import math

import pytest

from metricas.classificacao_dora import (
    DEPLOY_UM_POR_MES,
    classificar_dora,
    nota_cfr,
    nota_deploys,
    nota_geral,
    nota_lead_time,
    nota_recuperacao,
)


def test_exemplo_enunciado_notas_4_3_3_1_vira_high():
    assert nota_geral([4, 3, 3, 1]) == 3
    # Deploys elite (≥7), lead high (24≤t<168), CFR high (0,15<c≤0,30), recup low (≥168)
    assert (
        classificar_dora(
            deploys_por_semana=7.0,
            lead_time_horas=48.0,
            cfr=0.20,
            recuperacao_horas=200.0,
        )
        == "High"
    )


def test_conversao_um_por_mes():
    assert DEPLOY_UM_POR_MES == pytest.approx(12 / 52.14)
    assert DEPLOY_UM_POR_MES == pytest.approx(0.230148, abs=1e-5)


# ---------- deploys ----------


@pytest.mark.parametrize(
    "valor, esperada",
    [
        (7.0, 4),
        (7.1, 4),
        (6.999, 3),
        (1.0, 3),
        (0.999, 2),
        (DEPLOY_UM_POR_MES, 2),
        (DEPLOY_UM_POR_MES - 1e-9, 1),
        (0.0, 1),
    ],
)
def test_fronteiras_deploys(valor, esperada):
    assert nota_deploys(valor) == esperada


# ---------- lead time ----------


@pytest.mark.parametrize(
    "horas, esperada",
    [
        (0.0, 4),
        (23.999, 4),
        (24.0, 3),
        (167.999, 3),
        (168.0, 2),
        (719.999, 2),
        (720.0, 1),
        (1000.0, 1),
    ],
)
def test_fronteiras_lead_time(horas, esperada):
    assert nota_lead_time(horas) == esperada


# ---------- CFR ----------


@pytest.mark.parametrize(
    "cfr, esperada",
    [
        (0.0, 4),
        (0.15, 4),
        (0.1500001, 3),
        (0.30, 3),
        (0.3000001, 2),
        (0.45, 2),
        (0.4500001, 1),
        (1.0, 1),
    ],
)
def test_fronteiras_cfr(cfr, esperada):
    assert nota_cfr(cfr) == esperada


# ---------- recuperação ----------


@pytest.mark.parametrize(
    "horas, esperada",
    [
        (0.0, 4),
        (0.999, 4),
        (1.0, 3),
        (23.999, 3),
        (24.0, 2),
        (167.999, 2),
        (168.0, 1),
        (500.0, 1),
    ],
)
def test_fronteiras_recuperacao(horas, esperada):
    assert nota_recuperacao(horas) == esperada


def test_ausentes_retornam_none():
    assert nota_deploys(None) is None
    assert nota_lead_time(None) is None
    assert nota_cfr(None) is None
    assert nota_recuperacao(None) is None
    assert classificar_dora(None, 1.0, 0.1, 0.5) is None
    assert classificar_dora(7.0, None, 0.1, 0.5) is None
    assert classificar_dora(7.0, 1.0, None, 0.5) is None
    assert classificar_dora(7.0, 1.0, 0.1, None) is None


def test_elite_quando_todas_elite():
    assert classificar_dora(10.0, 1.0, 0.05, 0.5) == "Elite"


def test_low_quando_todas_low():
    assert classificar_dora(0.01, 800.0, 0.5, 200.0) == "Low"


def test_mediana_floor_de_par_intermediario():
    # [2, 2, 4, 4] → mediana 3.0 → floor 3 → High
    assert nota_geral([2, 2, 4, 4]) == 3
    assert math.floor(3.0) == 3


def test_mediana_impar_e_lista_vazia():
    assert nota_geral([4, 2, 3]) == 3
    with pytest.raises(ValueError, match="vazia"):
        nota_geral([])

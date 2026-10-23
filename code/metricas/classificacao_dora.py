"""Classificação DORA (Elite / High / Medium / Low) — Lab03, RQ07 e C1.

Tabela de referência da disciplina (cortes operacionais):

| Nível  | Nota | Deploys/semana      | Lead time (h) | CFR (0–1) | Recuperação (h) |
|--------|------|---------------------|---------------|-----------|-----------------|
| Elite  | 4    | ≥ 7                 | < 24          | ≤ 0,15    | < 1             |
| High   | 3    | ≥ 1 e < 7           | < 168         | ≤ 0,30    | < 24            |
| Medium | 2    | ≥ 1/mês e < 1/sem   | < 720         | ≤ 0,45    | < 168           |
| Low    | 1    | < 1/mês             | ≥ 720         | > 0,45    | ≥ 168           |

Conversão "1 por mês" → releases/semana: ``12 / 52.14 ≈ 0,2301``
(12 releases/ano ÷ 52,14 semanas/ano).

Valor ausente (``None``): a nota da métrica fica ausente; a nota geral
(``classificar_dora``) só é calculada se as quatro métricas estiverem presentes.
"""
from __future__ import annotations

import math
from typing import Literal, Optional

NivelDora = Literal["Elite", "High", "Medium", "Low"]

NOTA_PARA_NIVEL: dict[int, NivelDora] = {
    4: "Elite",
    3: "High",
    2: "Medium",
    1: "Low",
}
NIVEL_PARA_NOTA: dict[NivelDora, int] = {v: k for k, v in NOTA_PARA_NIVEL.items()}

# 1 release/mês ≈ 12/52.14 releases por semana
DEPLOY_UM_POR_MES: float = 12 / 52.14

DEPLOY_ELITE: float = 7.0  # ≥ 7 por semana
DEPLOY_HIGH: float = 1.0  # ≥ 1 por semana
DEPLOY_MEDIUM: float = DEPLOY_UM_POR_MES  # ≥ ~0,23 por semana

LEAD_ELITE_H: float = 24.0
LEAD_HIGH_H: float = 168.0
LEAD_MEDIUM_H: float = 720.0

CFR_ELITE: float = 0.15
CFR_HIGH: float = 0.30
CFR_MEDIUM: float = 0.45

RECUP_ELITE_H: float = 1.0
RECUP_HIGH_H: float = 24.0
RECUP_MEDIUM_H: float = 168.0


def nota_deploys(deploys_por_semana: Optional[float]) -> Optional[int]:
    """Nota 4–1 da frequência de deploy (maior é melhor)."""
    if deploys_por_semana is None:
        return None
    if deploys_por_semana >= DEPLOY_ELITE:
        return 4
    if deploys_por_semana >= DEPLOY_HIGH:
        return 3
    if deploys_por_semana >= DEPLOY_MEDIUM:
        return 2
    return 1


def nota_lead_time(lead_time_horas: Optional[float]) -> Optional[int]:
    """Nota 4–1 do lead time (menor é melhor). Fronteiras: < 24 / 168 / 720 h."""
    if lead_time_horas is None:
        return None
    if lead_time_horas < LEAD_ELITE_H:
        return 4
    if lead_time_horas < LEAD_HIGH_H:
        return 3
    if lead_time_horas < LEAD_MEDIUM_H:
        return 2
    return 1


def nota_cfr(cfr: Optional[float]) -> Optional[int]:
    """Nota 4–1 da CFR em proporção 0–1 (menor é melhor). Inclui o limite superior."""
    if cfr is None:
        return None
    if cfr <= CFR_ELITE:
        return 4
    if cfr <= CFR_HIGH:
        return 3
    if cfr <= CFR_MEDIUM:
        return 2
    return 1


def nota_recuperacao(recuperacao_horas: Optional[float]) -> Optional[int]:
    """Nota 4–1 do tempo de recuperação (menor é melhor). Fronteiras: < 1 / 24 / 168 h."""
    if recuperacao_horas is None:
        return None
    if recuperacao_horas < RECUP_ELITE_H:
        return 4
    if recuperacao_horas < RECUP_HIGH_H:
        return 3
    if recuperacao_horas < RECUP_MEDIUM_H:
        return 2
    return 1


def nota_geral(notas: list[int]) -> int:
    """Mediana das notas, arredondada para baixo (floor).

    Com 4 notas, a mediana é a média dos dois valores centrais; o floor
    produz a nota inteira usada na tabela Elite/High/Medium/Low.
    """
    if not notas:
        raise ValueError("lista de notas vazia")
    ordenadas = sorted(notas)
    n = len(ordenadas)
    meio = n // 2
    if n % 2 == 1:
        mediana = float(ordenadas[meio])
    else:
        mediana = (ordenadas[meio - 1] + ordenadas[meio]) / 2.0
    return int(math.floor(mediana))


def classificar_dora(
    deploys_por_semana: Optional[float],
    lead_time_horas: Optional[float],
    cfr: Optional[float],
    recuperacao_horas: Optional[float],
) -> Optional[NivelDora]:
    """Classificação geral DORA pela mediana (floor) das quatro notas.

    Retorna ``None`` se qualquer métrica estiver ausente.
    """
    notas = [
        nota_deploys(deploys_por_semana),
        nota_lead_time(lead_time_horas),
        nota_cfr(cfr),
        nota_recuperacao(recuperacao_horas),
    ]
    if any(n is None for n in notas):
        return None
    return NOTA_PARA_NIVEL[nota_geral([n for n in notas if n is not None])]

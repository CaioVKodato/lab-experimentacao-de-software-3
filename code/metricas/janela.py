"""Janela de observação (seção 3 do enunciado): 12 meses fixados pelo professor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Janela:
    inicio: datetime
    fim: datetime

    def contem(self, instante: datetime) -> bool:
        return self.inicio <= instante <= self.fim


def parse_janela(inicio: str, fim: str) -> Janela:
    """Converte as datas do config.yaml ('2025-10-01', '2026-09-30') em instantes UTC.

    Decisão: o dia final é INCLUSIVO, ou seja, a janela vai até 23:59:59.999999 UTC
    do dia `fim`. O dia inicial começa às 00:00 UTC.
    """
    ini = datetime.fromisoformat(inicio).replace(tzinfo=timezone.utc)
    f = datetime.fromisoformat(fim).replace(
        hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
    )
    return Janela(ini, f)
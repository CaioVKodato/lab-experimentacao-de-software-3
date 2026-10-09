"""Coleta de workflow runs do default branch (Lab03, Issue #8).

Definição operacional (seção 3): apenas runs do default branch disparados por `push`,
criados dentro da janela de observação.

Endpoint: GET /repos/{owner}/{repo}/actions/runs?branch=&event=push&created=A..B
Cuidado do enunciado: com filtros, a API devolve NO MÁXIMO 1.000 resultados por consulta.

Estratégia: a janela é fatiada por mês; se um intervalo bate no teto, ele é dividido ao
meio (bisseção por dias) até caber ou chegar a 1 dia. Intervalos de 1 dia que ainda batem
no teto ficam registrados em `intervalos_truncados` (nada é descartado em silêncio).

Cache/retomada: cada intervalo consultado é salvo em disco, inclusive a decisão de
"dividir". Rodar de novo depois de uma queda continua de onde parou, sem repetir chamadas.

Usa o GitHubClient existente (paginação por Link + espera de rate limit). O backoff de
erros 5xx é responsabilidade da Issue #4 e passa a valer aqui automaticamente.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from metricas.janela import Janela
from pipeline.cache import JsonCache

TETO = 1000  # máximo de resultados por consulta filtrada na API

# Só os campos que as métricas usam (a resposta crua de cada run tem dezenas de campos).
CAMPOS = (
    "id", "workflow_id", "name", "event", "head_branch", "head_sha",
    "status", "conclusion", "created_at", "run_started_at", "updated_at",
)


class _Cliente(Protocol):
    def paginate(
        self, path: str, params: dict[str, Any] | None = None, *,
        items_key: str | None = None, max_items: int | None = None,
    ) -> list[Any]: ...


@dataclass(frozen=True)
class ColetaRuns:
    runs: list[dict[str, Any]]
    n_consultas: int                                   # consultas feitas agora (cache não conta)
    intervalos_truncados: tuple[tuple[str, str], ...]  # dias que ainda bateram no teto

    @property
    def n_truncados(self) -> int:
        return len(self.intervalos_truncados)


def meses_da_janela(janela: Janela) -> list[tuple[date, date]]:
    """Fatia a janela em meses de calendário, recortando o primeiro e o último."""
    ini, fim = janela.inicio.date(), janela.fim.date()
    meses: list[tuple[date, date]] = []
    atual = ini
    while atual <= fim:
        proximo = date(atual.year + atual.month // 12, atual.month % 12 + 1, 1)
        meses.append((atual, min(proximo - timedelta(days=1), fim)))
        atual = proximo
    return meses


def _projetar(run: dict[str, Any]) -> dict[str, Any]:
    return {campo: run.get(campo) for campo in CAMPOS}


class _Acumulador:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.consultas = 0
        self.truncados: list[tuple[str, str]] = []


def _coletar_intervalo(
    client: _Cliente, cache: JsonCache, owner: str, repo: str, branch: str,
    ini: date, fim: date, acc: _Acumulador,
) -> None:
    chave = f"{owner}__{repo}/workflow_runs/{ini.isoformat()}_{fim.isoformat()}"
    registro = cache.load(chave)

    if registro is None:
        acc.consultas += 1
        runs = client.paginate(
            f"/repos/{owner}/{repo}/actions/runs",
            {
                "branch": branch,
                "event": "push",
                "status": "completed",  # em andamento não tem conclusion e seria ignorado
                "created": f"{ini.isoformat()}..{fim.isoformat()}",
            },
            items_key="workflow_runs",
        )
        bateu_teto = len(runs) >= TETO
        if bateu_teto and ini < fim:
            registro = {"dividido": True}
        else:
            registro = {
                "dividido": False,
                "truncado": bateu_teto,
                "runs": [_projetar(r) for r in runs],
            }
        cache.save(chave, registro)

    if registro["dividido"]:
        meio = ini + timedelta(days=(fim - ini).days // 2)
        _coletar_intervalo(client, cache, owner, repo, branch, ini, meio, acc)
        _coletar_intervalo(client, cache, owner, repo, branch, meio + timedelta(days=1), fim, acc)
        return

    acc.runs.extend(registro["runs"])
    if registro["truncado"]:
        acc.truncados.append((ini.isoformat(), fim.isoformat()))


def coletar_workflow_runs(
    client: _Cliente, cache: JsonCache, owner: str, repo: str,
    default_branch: str, janela: Janela,
) -> ColetaRuns:
    """Coleta todas as runs `push` do default branch dentro da janela."""
    acc = _Acumulador()
    for ini, fim in meses_da_janela(janela):
        _coletar_intervalo(client, cache, owner, repo, default_branch, ini, fim, acc)
    return ColetaRuns(acc.runs, acc.consultas, tuple(acc.truncados))
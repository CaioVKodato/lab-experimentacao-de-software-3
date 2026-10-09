"""Change failure rate (variante a) e tempo de recuperação — Lab03, RQ 03(a) e RQ 04.

Todas as funções são puras: recebem listas de dicts no formato da API do GitHub
(`GET /repos/{owner}/{repo}/actions/runs`) e devolvem números, sem acessar a rede.

Campos usados de cada workflow run:
    - conclusion      : define sucesso / falha / ignorado (tabela da seção 3)
    - run_started_at  : início da execução (cai para created_at se ausente)
    - updated_at      : fim da execução
    - workflow_id     : agrupa as runs por workflow (RQ 04 é calculada DENTRO de cada um)
    - id              : desempate quando duas runs começam no mesmo instante
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Iterable, Mapping, Optional

SUCESSOS = frozenset({"success"})
FALHAS = frozenset({"failure", "timed_out", "startup_failure"})
# Qualquer outro valor (cancelled, skipped, neutral, action_required, stale, vazio/None)
# é IGNORADO: não entra em nenhum cálculo.

Run = Mapping[str, object]


def classificar_conclusion(conclusion: Optional[str]) -> Optional[str]:
    """Devolve "sucesso", "falha" ou None (ignorar)."""
    if conclusion in SUCESSOS:
        return "sucesso"
    if conclusion in FALHAS:
        return "falha"
    return None


def _parse(ts: str) -> datetime:
    """ISO 8601 da API do GitHub ('2026-03-01T10:00:00Z') -> datetime com fuso."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _inicio(run: Run) -> datetime:
    return _parse(run.get("run_started_at") or run["created_at"])  # type: ignore[arg-type]


# ---------------------------------------------------------------- CFR (a)

def change_failure_rate_ci(runs: Iterable[Run]) -> Optional[float]:
    """CFR variante (a), proxy de CI: falhas / (falhas + sucessos).

    Considera todos os workflows do repositório juntos. Mede falha de PIPELINE, não
    falha em produção. Retorna None quando não há nenhuma run válida (denominador 0).
    """
    falhas = sucessos = 0
    for run in runs:
        classe = classificar_conclusion(run.get("conclusion"))  # type: ignore[arg-type]
        if classe == "falha":
            falhas += 1
        elif classe == "sucesso":
            sucessos += 1
    total = falhas + sucessos
    if total == 0:
        return None
    return falhas / total


# ------------------------------------------------- Tempo de recuperação (RQ 04)

@dataclass(frozen=True)
class Episodio:
    """Um episódio de falha de um workflow. `fim` é None quando censurado."""

    inicio: datetime
    fim: Optional[datetime]
    horas: Optional[float]

    @property
    def censurado(self) -> bool:
        return self.fim is None


def episodios_de_falha(runs_do_workflow: Iterable[Run]) -> list[Episodio]:
    """Episódios de falha de UM workflow, em ordem cronológica.

    Regras (decisões documentadas):
      * Runs ignoradas (cancelled, skipped, em andamento...) somem da sequência: não
        abrem nem encerram episódio.
      * O episódio começa na primeira falha DEPOIS de um sucesso. Falhas no início da
        série, antes de qualquer sucesso, não abrem episódio (não há "estado saudável"
        anterior do qual se recuperar).
      * O episódio termina no próximo sucesso. Tempo = updated_at do sucesso -
        run_started_at da primeira falha.
      * Sem sucesso posterior dentro da janela, o episódio é CENSURADO (não descartado).
      * Ordenação por início da run, com `id` como desempate.
    """
    validas = [
        r for r in runs_do_workflow
        if classificar_conclusion(r.get("conclusion")) is not None  # type: ignore[arg-type]
    ]
    validas.sort(key=lambda r: (_inicio(r), r.get("id") or 0))

    episodios: list[Episodio] = []
    ja_viu_sucesso = False
    inicio_falha: Optional[datetime] = None

    for r in validas:
        if classificar_conclusion(r.get("conclusion")) == "sucesso":  # type: ignore[arg-type]
            if inicio_falha is not None:
                fim = _parse(r["updated_at"])  # type: ignore[arg-type]
                horas = (fim - inicio_falha).total_seconds() / 3600
                episodios.append(Episodio(inicio_falha, fim, horas))
                inicio_falha = None
            ja_viu_sucesso = True
        elif ja_viu_sucesso and inicio_falha is None:
            inicio_falha = _inicio(r)

    if inicio_falha is not None:
        episodios.append(Episodio(inicio_falha, None, None))
    return episodios


@dataclass(frozen=True)
class ResultadoRecuperacao:
    mediana_horas: Optional[float]
    n_episodios: int
    n_censurados: int
    proporcao_censurados: Optional[float]


def tempo_recuperacao_repositorio(
    runs: Iterable[Run], chave_workflow: str = "workflow_id"
) -> ResultadoRecuperacao:
    """Valor do repositório: mediana (em horas) dos episódios de TODOS os workflows.

    O tempo é calculado dentro de cada workflow (um sucesso do `lint` não recupera uma
    falha do `testes`) e só depois agregado pela mediana. Episódios censurados ficam
    fora da mediana, mas são contados em `proporcao_censurados`.
    """
    por_workflow: dict[object, list[Run]] = {}
    for r in runs:
        por_workflow.setdefault(r.get(chave_workflow), []).append(r)

    episodios: list[Episodio] = []
    for runs_wf in por_workflow.values():
        episodios.extend(episodios_de_falha(runs_wf))

    concluidos = [e.horas for e in episodios if e.horas is not None]
    n_censurados = len(episodios) - len(concluidos)
    return ResultadoRecuperacao(
        mediana_horas=median(concluidos) if concluidos else None,
        n_episodios=len(episodios),
        n_censurados=n_censurados,
        proporcao_censurados=(n_censurados / len(episodios)) if episodios else None,
    )
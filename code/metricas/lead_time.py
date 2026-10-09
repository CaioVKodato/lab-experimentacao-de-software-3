"""Lead time for changes — Lab03, RQ 02 (variantes obrigatórias a e b).

Funções puras sobre o formato CRU da API do GitHub:
    release : `GET /repos/{o}/{r}/releases`  -> tag_name, published_at, draft, prerelease
    commit  : `GET /repos/{o}/{r}/compare/{base}...{head}` -> commits[].commit.author.date

Unidade de saída: HORAS (definição operacional da seção 3: diferença em horas entre
`commit.author.date` e `release.published_at`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Mapping, Optional, Sequence

from metricas.janela import Janela

Release = Mapping[str, object]
Commit = Mapping[str, object]


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def data_release(release: Release) -> datetime:
    return _parse(release["published_at"])  # type: ignore[arg-type]


def data_commit(commit: Commit) -> datetime:
    return _parse(commit["commit"]["author"]["date"])  # type: ignore[index]


def _horas(depois: datetime, antes: datetime) -> float:
    return (depois - antes).total_seconds() / 3600


# ------------------------------------------------------------- cálculo puro

def lead_time_por_release_horas(
    publicada_em: datetime, datas_commits: Sequence[datetime]
) -> Optional[float]:
    """Variante (a): data da release - data do commit MAIS ANTIGO incluído nela."""
    if not datas_commits:
        return None
    return _horas(publicada_em, min(datas_commits))


def lead_times_por_commit_horas(
    publicada_em: datetime, datas_commits: Sequence[datetime]
) -> list[float]:
    """Variante (b): um valor por commit incluído na release."""
    return [_horas(publicada_em, d) for d in datas_commits]


# ----------------------------------------------------------- pares de releases

def selecionar_pares(
    releases: Sequence[Release], janela: Janela, incluir_prerelease: bool = False
) -> list[tuple[Release, Release]]:
    """Pares (release anterior, release R) para cada R publicada DENTRO da janela.

    Definição principal de deploy: release com draft = false e prerelease = false.
    Com `incluir_prerelease=True` (variante da RQ 07), pré-releases também contam e a
    "release anterior" passa a ser escolhida dentro desse conjunto maior.

    A release anterior pode estar fora da janela, então `releases` deve trazer o
    histórico completo. A primeira release da história não tem anterior e é ignorada.
    Os pares dizem exatamente quais `compare/{anterior}...{R}` precisam ser coletados.
    """
    elegiveis = [
        r for r in releases
        if not r.get("draft")
        and (incluir_prerelease or not r.get("prerelease"))
        and r.get("published_at")
    ]
    elegiveis.sort(key=lambda r: (data_release(r), r.get("id") or 0))

    pares = []
    for i, r in enumerate(elegiveis):
        if i > 0 and janela.contem(data_release(r)):
            pares.append((elegiveis[i - 1], r))
    return pares


# ------------------------------------------------------ agregação por repositório

@dataclass(frozen=True)
class ResultadoLeadTime:
    mediana_a_horas: Optional[float]   # mediana entre releases
    mediana_b_horas: Optional[float]   # mediana entre TODOS os commits de TODAS as releases
    n_releases_avaliadas: int          # entraram no cálculo (>= 1 commit)
    n_ignoradas_compare: int           # compare falhou (404) ou não foi coletado
    n_sem_commits: int                 # compare ok, mas sem commits novos
    n_commits: int


def lead_time_repositorio(
    releases: Sequence[Release],
    commits_por_tag: Mapping[str, Optional[Sequence[Commit]]],
    janela: Janela,
    incluir_prerelease: bool = False,
) -> ResultadoLeadTime:
    """Valor do repositório para RQ 02.

    `commits_por_tag[tag]` = commits devolvidos por `compare/{anterior}...{tag}`,
    ou None quando o compare falhou (ex.: 404 por tag apagada). Tag ausente do mapa
    é tratada como falha. Releases ignoradas por esse motivo são CONTADAS, não
    silenciosas (pedido do FAQ do enunciado).
    """
    valores_a: list[float] = []
    valores_b: list[float] = []
    ignoradas = sem_commits = 0

    for _anterior, r in selecionar_pares(releases, janela, incluir_prerelease):
        commits = commits_por_tag.get(r["tag_name"])  # type: ignore[arg-type]
        if commits is None:
            ignoradas += 1
            continue
        if len(commits) == 0:
            sem_commits += 1
            continue
        publicada = data_release(r)
        datas = [data_commit(c) for c in commits]
        valores_a.append(lead_time_por_release_horas(publicada, datas))  # type: ignore[arg-type]
        valores_b.extend(lead_times_por_commit_horas(publicada, datas))

    return ResultadoLeadTime(
        mediana_a_horas=median(valores_a) if valores_a else None,
        mediana_b_horas=median(valores_b) if valores_b else None,
        n_releases_avaliadas=len(valores_a),
        n_ignoradas_compare=ignoradas,
        n_sem_commits=sem_commits,
        n_commits=len(valores_b),
    )
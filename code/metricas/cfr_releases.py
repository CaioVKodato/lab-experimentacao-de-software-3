"""Change failure rate (variante b) — Lab03, RQ 03(b).

Heurística automática de release corretiva: uma release R "falha" se for seguida,
dentro de 7 dias, por uma release classificada como corretiva.

Critérios de release corretiva (OR inclusivo):
  1. Mudança só no patch do SemVer entre R e a próxima (ex.: v2.3.0 → v2.3.1)
  2. Commits da próxima release contêm palavras-chave: revert, hotfix ou fix

Censura: releases nos 7 dias finais da janela são excluídas do denominador, pois
não é possível observar os 7 dias seguintes à release.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Optional, Sequence

from metricas.janela import Janela

Release = Mapping[str, object]
Commit = Mapping[str, object]

JANELA_CORRETIVA = timedelta(days=7)
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[.\-].*)?$")
_KEYWORDS_RE = re.compile(r"\b(revert|hotfix|fix)\b", re.IGNORECASE)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _parse_semver(tag: str) -> Optional[tuple[int, int, int]]:
    m = _SEMVER_RE.match(tag)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def so_patch_mudou(tag_anterior: str, tag_nova: str) -> bool:
    """True quando a versão nova difere da anterior SOMENTE no patch."""
    a = _parse_semver(tag_anterior)
    b = _parse_semver(tag_nova)
    if a is None or b is None:
        return False
    return a[0] == b[0] and a[1] == b[1] and a[2] != b[2]


def tem_keyword_corretiva(commits: Sequence[Commit]) -> bool:
    """True se algum commit message contém 'revert', 'hotfix' ou 'fix'."""
    for commit in commits:
        msg = (commit.get("commit") or {}).get("message") or ""
        if _KEYWORDS_RE.search(str(msg)):
            return True
    return False


def eh_release_corretiva(
    tag_anterior: str,
    tag_nova: str,
    commits: Optional[Sequence[Commit]],
) -> bool:
    """True se tag_nova é release corretiva em relação a tag_anterior.

    Quando commits é None (compare falhou), apenas o critério de SemVer é aplicado.
    """
    if so_patch_mudou(tag_anterior, tag_nova):
        return True
    if commits is not None and tem_keyword_corretiva(commits):
        return True
    return False


@dataclass(frozen=True)
class ResultadoCFRReleases:
    cfr_b: Optional[float]
    n_releases_denominador: int    # releases elegíveis na janela (não censuradas)
    n_releases_falhas: int         # seguidas de release corretiva em ≤ 7 dias
    n_releases_censuradas: int     # nos 7 dias finais (excluídas do denominador)
    n_sem_compare: int             # compare indisponível para a release seguinte


def change_failure_rate_releases(
    releases: Sequence[Release],
    commits_por_tag: Mapping[str, Optional[Sequence[Commit]]],
    janela: Janela,
    incluir_prerelease: bool = False,
) -> ResultadoCFRReleases:
    """CFR variante (b): releases com follow-up corretivo / releases no denominador.

    `commits_por_tag[tag]` = commits entre a release anterior e `tag` (via compare),
    ou None quando o compare falhou (ex.: 404). Tag ausente do mapa é tratada como None:
    nesse caso apenas o critério de SemVer é aplicado.
    """
    elegiveis = [
        r for r in releases
        if not r.get("draft")
        and (incluir_prerelease or not r.get("prerelease"))
        and r.get("published_at")
    ]
    elegiveis.sort(key=lambda r: (_parse(r["published_at"]), r.get("id") or 0))  # type: ignore[arg-type]

    corte_censura = janela.fim - JANELA_CORRETIVA

    n_denominador = n_falhas = n_censuradas = n_sem_compare = 0

    for i, r in enumerate(elegiveis):
        data_r = _parse(r["published_at"])  # type: ignore[arg-type]
        if not janela.contem(data_r):
            continue

        if data_r > corte_censura:
            n_censuradas += 1
            continue

        n_denominador += 1

        if i + 1 >= len(elegiveis):
            continue

        proxima = elegiveis[i + 1]
        data_proxima = _parse(proxima["published_at"])  # type: ignore[arg-type]

        if data_proxima - data_r > JANELA_CORRETIVA:
            continue

        tag_r = str(r["tag_name"])
        tag_proxima = str(proxima["tag_name"])
        commits_proxima = commits_por_tag.get(tag_proxima)

        if commits_proxima is None:
            n_sem_compare += 1

        if eh_release_corretiva(tag_r, tag_proxima, commits_proxima):
            n_falhas += 1

    return ResultadoCFRReleases(
        cfr_b=n_falhas / n_denominador if n_denominador > 0 else None,
        n_releases_denominador=n_denominador,
        n_releases_falhas=n_falhas,
        n_releases_censuradas=n_censuradas,
        n_sem_compare=n_sem_compare,
    )

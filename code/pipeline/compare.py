"""Coleta de commits entre releases via endpoint compare (Issue #6).

Para cada par (release_anterior, release_atual), consulta:
  GET /repos/{owner}/{repo}/compare/{base}...{head}

Estratégia de paginação: o endpoint retorna no máximo 250 commits por página;
paginação ocorre via cabeçalho Link (per_page/page).

Erros 404 (tag deletada ou histórico reescrito) são registrados, excluídos do
cálculo de lead time e contabilizados em `nao_encontrados`.

Cache: cada par (base, head) é persistido em JSON; rodar de novo após uma
queda retoma de onde parou sem repetir chamadas.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from pipeline.cache import JsonCache
from pipeline.github_client import GitHubAPIError


class _Cliente(Protocol):
    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        items_key: str | None = None,
        max_items: int | None = None,
    ) -> list[Any]: ...


@dataclass
class CommitEntry:
    sha: str
    author_date: str  # commit.author.date (ISO 8601)


@dataclass
class CompareResult:
    full_name: str
    base_tag: str
    head_tag: str
    commits: list[CommitEntry]
    not_found: bool = False  # True quando a API retornou 404
    from_cache: bool = False


_COMMIT_FIELDS = ["full_name", "base_tag", "head_tag", "sha", "author_date"]


def _cache_key(owner: str, name: str, base: str, head: str) -> str:
    return f"compare/{owner}/{name}/{base}...{head}.json"


def _extract_commit(raw: dict[str, Any]) -> CommitEntry:
    sha = raw.get("sha") or ""
    author_date = (
        (raw.get("commit") or {})
        .get("author", {})
        .get("date") or ""
    )
    return CommitEntry(sha=sha, author_date=author_date)


def collect_commits_between(
    client: _Cliente,
    owner: str,
    name: str,
    base_tag: str,
    head_tag: str,
    *,
    cache: JsonCache,
    force: bool = False,
) -> CompareResult:
    """Coleta commits entre base_tag e head_tag, com cache e retomada.

    Retorna CompareResult com not_found=True quando a API responder 404
    (tag deletada ou histórico reescrito) sem propagar a exceção.
    """
    full_name = f"{owner}/{name}"
    key = _cache_key(owner, name, base_tag, head_tag)

    if not force:
        cached = cache.load(key)
        if isinstance(cached, dict) and cached.get("full_name") == full_name:
            return CompareResult(
                full_name=full_name,
                base_tag=base_tag,
                head_tag=head_tag,
                commits=[CommitEntry(**c) for c in (cached.get("commits") or [])],
                not_found=bool(cached.get("not_found", False)),
                from_cache=True,
            )

    path = f"/repos/{owner}/{name}/compare/{base_tag}...{head_tag}"
    try:
        raw_commits = client.paginate(path, items_key="commits")
    except GitHubAPIError as exc:
        if exc.status_code == 404:
            result = CompareResult(
                full_name=full_name,
                base_tag=base_tag,
                head_tag=head_tag,
                commits=[],
                not_found=True,
            )
            cache.save(key, {
                "full_name": full_name,
                "commits": [],
                "not_found": True,
            })
            return result
        raise

    commits = [_extract_commit(c) for c in raw_commits]
    result = CompareResult(
        full_name=full_name,
        base_tag=base_tag,
        head_tag=head_tag,
        commits=commits,
        not_found=False,
    )
    cache.save(key, {
        "full_name": full_name,
        "commits": [asdict(c) for c in commits],
        "not_found": False,
    })
    return result


def collect_repo_commits_between_releases(
    client: _Cliente,
    owner: str,
    name: str,
    release_pairs: list[tuple[str, str]],
    *,
    cache: JsonCache,
    force: bool = False,
) -> list[CompareResult]:
    """Coleta commits para cada par (base_tag, head_tag) da lista."""
    return [
        collect_commits_between(client, owner, name, base, head, cache=cache, force=force)
        for base, head in release_pairs
    ]


def write_commits_csv(results: list[CompareResult], output_path: Path) -> Path:
    """Grava commits_between_releases.csv (exclui pares com not_found=True)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COMMIT_FIELDS)
        w.writeheader()
        for result in results:
            if result.not_found:
                continue
            for commit in result.commits:
                w.writerow({
                    "full_name": result.full_name,
                    "base_tag": result.base_tag,
                    "head_tag": result.head_tag,
                    "sha": commit.sha,
                    "author_date": commit.author_date,
                })
    return output_path


def count_not_found(results: list[CompareResult]) -> int:
    """Contabiliza pares com 404 para fins de rastreamento de frequência."""
    return sum(1 for r in results if r.not_found)

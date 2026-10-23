"""Coleta de commits entre releases via endpoint compare (Issue #6).

Para cada par (release_anterior, release_atual), consulta:
  GET /repos/{owner}/{repo}/compare/{base}...{head}

Estratégia de paginação: o endpoint retorna no máximo 250 commits por página;
paginação ocorre via cabeçalho Link (per_page/page).

Erros 404 (tag deletada ou histórico reescrito) são registrados, excluídos do
cálculo de lead time e contabilizados em `nao_encontrados`.

Cache: cada par (base, head) é persistido em JSON; rodar de novo após uma
queda retoma de onde parou sem repetir chamadas.

Mensagem do commit (Issue #20): a heurística de CFR (b) procura `fix`, `hotfix` e
`revert` na mensagem, então cada commit guarda `message` (truncada em MAX_MENSAGEM).
O cache é VERSIONADO: entradas gravadas antes dessa mudança não têm a mensagem e são
refeitas em vez de lidas como mensagem vazia, o que degradaria a heurística sem erro.

Falha persistente (Issue #20): um par que o GitHub não consegue comparar (5xx por "diff
longo demais", 422) NÃO derruba a etapa: vira um resultado com `erro`, NÃO é gravado no
cache e é tentado de novo na próxima execução; o dataset o conta como lacuna. Depois de
MAX_FALHAS_SEGUIDAS falhas seguidas no mesmo repositório a coleta aborta, para não varrer
centenas de pares durante uma instabilidade geral do GitHub. Credencial (401/403) continua
derrubando de propósito.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from pipeline.cache import JsonCache
from pipeline.github_client import GitHubAPIError

CACHE_VERSAO = 2        # v1 = commits só com sha e data (sem mensagem)
MAX_FALHAS_SEGUIDAS = 5  # num mesmo repositório: acima disso é instabilidade, não um par ruim
MAX_MENSAGEM = 1000     # limita o tamanho do cache; sobra de sobra para achar fix/hotfix/revert


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
    message: str = ""  # commit.message, truncada em MAX_MENSAGEM

    def as_api(self) -> dict[str, Any]:
        """Formato cru da API (`commit.message`, `commit.author.date`), que é o que
        as funções de métricas (lead time e CFR) consomem."""
        return {
            "sha": self.sha,
            "commit": {"message": self.message, "author": {"date": self.author_date}},
        }


@dataclass
class CompareResult:
    full_name: str
    base_tag: str
    head_tag: str
    commits: list[CommitEntry]
    not_found: bool = False  # True quando a API retornou 404
    from_cache: bool = False
    erro: int | None = None  # status HTTP de uma falha persistente (não cacheada)


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
    message = ((raw.get("commit") or {}).get("message") or "")[:MAX_MENSAGEM]
    return CommitEntry(sha=sha, author_date=author_date, message=message)


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
        if (
            isinstance(cached, dict)
            and cached.get("versao") == CACHE_VERSAO
            and cached.get("full_name") == full_name
        ):
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
                "versao": CACHE_VERSAO,
                "full_name": full_name,
                "commits": [],
                "not_found": True,
            })
            return result
        if exc.status_code >= 500 or exc.status_code == 422:
            print(
                f"[compare] HTTP {exc.status_code} em {base_tag}...{head_tag} de {full_name}; "
                "será tentado de novo na próxima execução",
                file=sys.stderr,
                flush=True,
            )
            return CompareResult(
                full_name=full_name, base_tag=base_tag, head_tag=head_tag,
                commits=[], not_found=False, erro=exc.status_code,
            )
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
        "versao": CACHE_VERSAO,
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
    """Coleta commits para cada par (base_tag, head_tag) da lista.

    Falhas persistentes isoladas são toleradas; MAX_FALHAS_SEGUIDAS seguidas abortam."""
    results: list[CompareResult] = []
    seguidas = 0
    for base, head in release_pairs:
        result = collect_commits_between(client, owner, name, base, head, cache=cache, force=force)
        results.append(result)
        seguidas = seguidas + 1 if result.erro is not None else 0
        if seguidas >= MAX_FALHAS_SEGUIDAS:
            raise GitHubAPIError(
                result.erro or 500,
                f"{seguidas} falhas seguidas no compare de {owner}/{name}: provável instabilidade "
                "do GitHub. O que já foi coletado está no cache; rode de novo mais tarde.",
            )
    return results


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


def count_erros(results: list[CompareResult]) -> int:
    """Quantos pares falharam de forma persistente e não foram para o cache."""
    return sum(1 for r in results if r.erro is not None)


def count_not_found(results: list[CompareResult]) -> int:
    """Contabiliza pares com 404 para fins de rastreamento de frequência."""
    return sum(1 for r in results if r.not_found)

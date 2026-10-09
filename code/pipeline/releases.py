"""Coleta de releases e tags na janela de observação (Issue #5).

Coleta releases não-rascunho publicados dentro da janela de observação.
Pre-releases são incluídos mas identificados com `prerelease=True` para uso
futuro. Tags são coletadas via endpoint próprio. Cache e retomada por JSON.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from pipeline.cache import JsonCache


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
class Release:
    full_name: str
    tag_name: str
    name: str | None
    published_at: str
    prerelease: bool
    html_url: str


@dataclass
class Tag:
    full_name: str
    tag_name: str
    sha: str


@dataclass
class ReleasesResult:
    full_name: str
    releases: list[Release]
    tags: list[Tag]
    from_cache: bool = False


_RELEASE_FIELDS = ["full_name", "tag_name", "name", "published_at", "prerelease", "html_url"]
_TAG_FIELDS = ["full_name", "tag_name", "sha"]


def _in_window(published_at: str, window_start: str, window_end: str) -> bool:
    """Verifica se published_at (ISO 8601) cai dentro de [window_start, window_end]."""
    pub_date = published_at[:10]  # "YYYY-MM-DD"
    return window_start <= pub_date <= window_end


def _parse_releases(
    raw: list[dict[str, Any]],
    full_name: str,
    window_start: str,
    window_end: str,
) -> list[Release]:
    releases = []
    for item in raw:
        if item.get("draft"):
            continue
        published_at = item.get("published_at") or ""
        if not published_at:
            continue
        if not _in_window(published_at, window_start, window_end):
            continue
        releases.append(Release(
            full_name=full_name,
            tag_name=item.get("tag_name") or "",
            name=item.get("name"),
            published_at=published_at,
            prerelease=bool(item.get("prerelease", False)),
            html_url=item.get("html_url") or "",
        ))
    return releases


def _parse_tags(raw: list[dict[str, Any]], full_name: str) -> list[Tag]:
    return [
        Tag(
            full_name=full_name,
            tag_name=t.get("name") or "",
            sha=(t.get("commit") or {}).get("sha") or "",
        )
        for t in raw
    ]


def collect_repo_releases(
    client: _Cliente,
    owner: str,
    name: str,
    *,
    cache: JsonCache,
    window_start: str,
    window_end: str,
    force: bool = False,
) -> ReleasesResult:
    """Coleta releases e tags de um repositório, com cache e retomada."""
    full_name = f"{owner}/{name}"
    cache_key = f"releases/{owner}/{name}.json"

    if not force:
        cached = cache.load(cache_key)
        if isinstance(cached, dict) and cached.get("full_name") == full_name:
            return ReleasesResult(
                full_name=full_name,
                releases=[Release(**r) for r in (cached.get("releases") or [])],
                tags=[Tag(**t) for t in (cached.get("tags") or [])],
                from_cache=True,
            )

    raw_releases = client.paginate(f"/repos/{owner}/{name}/releases")
    releases = _parse_releases(raw_releases, full_name, window_start, window_end)

    raw_tags = client.paginate(f"/repos/{owner}/{name}/tags")
    tags = _parse_tags(raw_tags, full_name)

    cache.save(cache_key, {
        "full_name": full_name,
        "releases": [asdict(r) for r in releases],
        "tags": [asdict(t) for t in tags],
    })

    return ReleasesResult(full_name=full_name, releases=releases, tags=tags, from_cache=False)


def collect_sample_releases(
    client: _Cliente,
    sample_rows: list[dict[str, str]],
    *,
    cache: JsonCache,
    window_start: str,
    window_end: str,
    force: bool = False,
) -> list[ReleasesResult]:
    results = []
    for row in sample_rows:
        full_name = (row.get("full_name") or "").strip()
        if not full_name and row.get("owner") and row.get("name"):
            full_name = f"{row['owner']}/{row['name']}"
        if "/" not in full_name:
            raise ValueError(f"full_name inválido na amostra: {row!r}")
        owner, repo_name = full_name.split("/", 1)
        results.append(collect_repo_releases(
            client, owner, repo_name,
            cache=cache, window_start=window_start, window_end=window_end, force=force,
        ))
    return results


def write_releases_csv(results: list[ReleasesResult], output_dir: Path) -> tuple[Path, Path]:
    """Grava releases.csv e tags.csv em output_dir; retorna (releases_path, tags_path)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    releases_path = output_dir / "releases.csv"
    tags_path = output_dir / "tags.csv"

    with releases_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_RELEASE_FIELDS)
        w.writeheader()
        for result in results:
            for rel in result.releases:
                w.writerow(asdict(rel))

    with tags_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_TAG_FIELDS)
        w.writeheader()
        for result in results:
            for tag in result.tags:
                w.writerow(asdict(tag))

    return releases_path, tags_path


def run_releases_collection(
    client: _Cliente,
    config: dict[str, Any],
    *,
    sample_csv: Path | None = None,
    force: bool = False,
) -> tuple[list[ReleasesResult], Path, Path]:
    """Lê amostra, coleta releases/tags e grava releases.csv e tags.csv."""
    paths = config.get("paths") or {}
    output_dir = Path(paths.get("output_dir") or "data/output")
    cache_dir = Path(paths.get("cache_dir") or "data/cache")
    window = config.get("observation_window") or {}
    window_start = str(window.get("start") or "2025-10-01")
    window_end = str(window.get("end") or "2026-09-30")

    sample_path = sample_csv or (output_dir / "sample_repos.csv")
    if not sample_path.is_file():
        raise FileNotFoundError(
            f"Amostra não encontrada: {sample_path}. Execute antes --stage selection."
        )

    with sample_path.open(encoding="utf-8", newline="") as fh:
        sample_rows = list(csv.DictReader(fh))

    cache = JsonCache(cache_dir)
    results = collect_sample_releases(
        client, sample_rows,
        cache=cache, window_start=window_start, window_end=window_end, force=force,
    )
    releases_path, tags_path = write_releases_csv(results, output_dir)
    return results, releases_path, tags_path

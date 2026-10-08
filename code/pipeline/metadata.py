"""Coleta de metadados dos repositórios da amostra (Issue #3).

Campos: estrelas, linguagem principal, nº de contribuidores (via Link
per_page=1), created_at/idade e default_branch. Respostas ficam em cache
JSON local; o CSV intermediário vai para data/output.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pipeline.cache import JsonCache
from pipeline.github_client import GitHubAPIError, extract_last_page
from pipeline.selection import parse_iso_date


class SupportsGetJson(Protocol):
    def get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, str]]: ...


@dataclass
class RepoMetadata:
    full_name: str
    owner: str
    name: str
    html_url: str
    stargazers_count: int
    language: str | None
    default_branch: str
    created_at: str | None
    age_days: int | None
    contributors_count: int | None
    forks_count: int | None = None
    open_issues_count: int | None = None
    fetched_at: str | None = None
    from_cache: bool = False


def load_sample_repos(sample_csv: Path) -> list[dict[str, str]]:
    """Lê sample_repos.csv gerado pela etapa de seleção."""
    if not sample_csv.is_file():
        raise FileNotFoundError(
            f"Amostra não encontrada: {sample_csv}. "
            "Execute antes --stage selection."
        )
    with sample_csv.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"Amostra vazia em {sample_csv}")
    return rows


def reference_date_from_config(config: dict[str, Any]) -> date:
    """Idade do repositório medida até o fim da janela (reproduzível)."""
    window = config.get("observation_window") or {}
    end = window.get("end")
    if end:
        return parse_iso_date(str(end))
    return datetime.now(timezone.utc).date()


def compute_age_days(created_at: str | None, reference: date) -> int | None:
    if not created_at:
        return None
    try:
        created = parse_iso_date(created_at)
    except ValueError:
        return None
    return max(0, (reference - created).days)


def count_contributors(client: SupportsGetJson, owner: str, repo: str) -> int:
    """Conta contribuidores com per_page=1 + última página do Link."""
    body, headers = client.get_json(
        f"/repos/{owner}/{repo}/contributors",
        {"per_page": 1, "anon": "true"},
    )
    link = headers.get("Link") or headers.get("link")
    last_page = extract_last_page(link)
    if last_page is not None:
        return last_page
    if body is None:
        return 0
    if isinstance(body, list):
        return len(body)
    return 0


def fetch_repo_payload(
    client: SupportsGetJson, owner: str, repo: str
) -> dict[str, Any]:
    body, _headers = client.get_json(f"/repos/{owner}/{repo}")
    if not isinstance(body, dict):
        raise GitHubAPIError(500, f"resposta inválida para {owner}/{repo}")
    return body


def collect_repo_metadata(
    client: SupportsGetJson,
    owner: str,
    name: str,
    *,
    cache: JsonCache,
    reference: date,
    force: bool = False,
) -> RepoMetadata:
    """Coleta metadados de um repositório, com cache/retomada por JSON."""
    full_name = f"{owner}/{name}"
    cache_key = f"metadata/{owner}/{name}.json"

    if not force:
        cached = cache.load(cache_key)
        if (
            isinstance(cached, dict)
            and cached.get("full_name") == full_name
            and "contributors_count" in cached
            and "stargazers_count" in cached
        ):
            fields = {
                key: cached.get(key) for key in RepoMetadata.__dataclass_fields__
            }
            fields["from_cache"] = True
            return RepoMetadata(**fields)

    repo_body = fetch_repo_payload(client, owner, name)
    contributors = count_contributors(client, owner, name)
    created_at = repo_body.get("created_at")
    meta = RepoMetadata(
        full_name=full_name,
        owner=owner,
        name=name,
        html_url=repo_body.get("html_url") or f"https://github.com/{full_name}",
        stargazers_count=int(repo_body.get("stargazers_count") or 0),
        language=repo_body.get("language"),
        default_branch=repo_body.get("default_branch") or "main",
        created_at=created_at,
        age_days=compute_age_days(created_at, reference),
        contributors_count=contributors,
        forks_count=repo_body.get("forks_count"),
        open_issues_count=repo_body.get("open_issues_count"),
        fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        from_cache=False,
    )

    # Cacheia também o payload bruto para auditoria/retomada
    cache.save(f"metadata/{owner}/{name}/repo.json", repo_body)
    cache.save(
        f"metadata/{owner}/{name}/contributors.json",
        {"count": contributors},
    )
    payload = asdict(meta)
    payload["from_cache"] = False
    cache.save(cache_key, payload)
    return meta


def collect_sample_metadata(
    client: SupportsGetJson,
    sample_rows: list[dict[str, str]],
    *,
    cache: JsonCache,
    reference: date,
    force: bool = False,
) -> list[RepoMetadata]:
    results: list[RepoMetadata] = []
    for row in sample_rows:
        full_name = (row.get("full_name") or "").strip()
        if not full_name and row.get("owner") and row.get("name"):
            full_name = f"{row['owner']}/{row['name']}"
        if "/" not in full_name:
            raise ValueError(f"full_name inválido na amostra: {row!r}")
        owner, name = full_name.split("/", 1)
        results.append(
            collect_repo_metadata(
                client,
                owner,
                name,
                cache=cache,
                reference=reference,
                force=force,
            )
        )
    return results


def write_metadata_csv(rows: list[RepoMetadata], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "full_name",
        "owner",
        "name",
        "html_url",
        "stargazers_count",
        "language",
        "default_branch",
        "created_at",
        "age_days",
        "contributors_count",
        "forks_count",
        "open_issues_count",
        "fetched_at",
        "from_cache",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return output_path


def run_metadata_collection(
    client: SupportsGetJson,
    config: dict[str, Any],
    *,
    sample_csv: Path | None = None,
    force: bool = False,
) -> tuple[list[RepoMetadata], Path]:
    """Lê a amostra, coleta metadados e grava CSV intermediário."""
    paths = config.get("paths") or {}
    output_dir = Path(paths.get("output_dir") or "data/output")
    cache_dir = Path(paths.get("cache_dir") or "data/cache")
    sample_path = sample_csv or (output_dir / "sample_repos.csv")

    sample_rows = load_sample_repos(sample_path)
    cache = JsonCache(cache_dir)
    reference = reference_date_from_config(config)
    rows = collect_sample_metadata(
        client,
        sample_rows,
        cache=cache,
        reference=reference,
        force=force,
    )
    csv_path = write_metadata_csv(rows, output_dir / "repo_metadata.csv")
    return rows, csv_path

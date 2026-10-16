"""Orquestração do pipeline único de coleta (Issue #11).

Encadeia: seleção → metadados → releases/tags → commits (compare) →
workflow runs. Cada etapa persiste CSVs intermediários e usa cache/retomada
dos módulos existentes. O comando padrão é:

    python -m pipeline --config config.yaml
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from metricas.janela import parse_janela
from pipeline.cache import JsonCache
from pipeline.compare import (
    CompareResult,
    collect_repo_commits_between_releases,
    count_not_found,
    write_commits_csv,
)
from pipeline.metadata import run_metadata_collection
from pipeline.releases import Release, run_releases_collection
from pipeline.selection import run_selection, write_selection_outputs
from pipeline.workflow_runs import CAMPOS, coletar_workflow_runs


class _Cliente(Protocol):
    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        items_key: str | None = None,
        max_items: int | None = None,
    ) -> list[Any]: ...

    def get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, str]]: ...

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@dataclass
class StageReport:
    name: str
    ok: bool
    detail: str = ""
    paths: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineReport:
    stages: list[StageReport] = field(default_factory=list)

    def add(self, report: StageReport) -> None:
        self.stages.append(report)


def _output_dir(config: dict[str, Any]) -> Path:
    paths = config.get("paths") or {}
    return Path(paths.get("output_dir") or "data/output")


def _cache_dir(config: dict[str, Any]) -> Path:
    paths = config.get("paths") or {}
    return Path(paths.get("cache_dir") or "data/cache")


def _load_sample_rows(sample_csv: Path) -> list[dict[str, str]]:
    if not sample_csv.is_file():
        raise FileNotFoundError(
            f"Amostra não encontrada: {sample_csv}. Rode a etapa selection antes."
        )
    with sample_csv.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"Amostra vazia: {sample_csv}")
    return rows


def _full_name(row: dict[str, str]) -> str:
    name = (row.get("full_name") or "").strip()
    if not name and row.get("owner") and row.get("name"):
        name = f"{row['owner']}/{row['name']}"
    if "/" not in name:
        raise ValueError(f"full_name inválido: {row!r}")
    return name


def _release_pairs(releases: list[Release]) -> list[tuple[str, str]]:
    """Pares (anterior, atual) só com releases não pré-release, por data."""
    ordered = sorted(
        (r for r in releases if not r.prerelease),
        key=lambda r: (r.published_at, r.tag_name),
    )
    pairs: list[tuple[str, str]] = []
    for i in range(1, len(ordered)):
        pairs.append((ordered[i - 1].tag_name, ordered[i].tag_name))
    return pairs


def run_compare_collection(
    client: _Cliente,
    config: dict[str, Any],
    *,
    sample_csv: Path | None = None,
    force: bool = False,
) -> tuple[list[CompareResult], Path, int]:
    """Compara releases consecutivas da amostra e grava commits CSV."""
    output_dir = _output_dir(config)
    cache = JsonCache(_cache_dir(config))
    sample_path = sample_csv or (output_dir / "sample_repos.csv")
    releases_path = output_dir / "releases.csv"

    sample_rows = _load_sample_rows(sample_path)
    if not releases_path.is_file():
        raise FileNotFoundError(
            f"Releases não encontradas: {releases_path}. Rode a etapa releases antes."
        )

    by_repo: dict[str, list[Release]] = {}
    with releases_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            full = row["full_name"]
            by_repo.setdefault(full, []).append(
                Release(
                    full_name=full,
                    tag_name=row["tag_name"],
                    name=row.get("name") or None,
                    published_at=row["published_at"],
                    prerelease=str(row.get("prerelease", "")).lower() in {"1", "true"},
                    html_url=row.get("html_url") or "",
                )
            )

    all_results: list[CompareResult] = []
    for row in sample_rows:
        full = _full_name(row)
        owner, name = full.split("/", 1)
        pairs = _release_pairs(by_repo.get(full, []))
        if not pairs:
            continue
        all_results.extend(
            collect_repo_commits_between_releases(
                client, owner, name, pairs, cache=cache, force=force
            )
        )

    csv_path = write_commits_csv(all_results, output_dir / "commits_between_releases.csv")
    # log de 404s
    n404 = count_not_found(all_results)
    log_path = output_dir / "compare_not_found.csv"
    with log_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["full_name", "base_tag", "head_tag", "not_found"]
        )
        w.writeheader()
        for r in all_results:
            if r.not_found:
                w.writerow(
                    {
                        "full_name": r.full_name,
                        "base_tag": r.base_tag,
                        "head_tag": r.head_tag,
                        "not_found": True,
                    }
                )
    return all_results, csv_path, n404


def run_workflow_runs_collection(
    client: _Cliente,
    config: dict[str, Any],
    *,
    sample_csv: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Coleta workflow runs da amostra e grava CSV + resumo de truncamentos."""
    output_dir = _output_dir(config)
    cache = JsonCache(_cache_dir(config))
    sample_path = sample_csv or (output_dir / "sample_repos.csv")
    window = config.get("observation_window") or {}
    janela = parse_janela(str(window["start"]), str(window["end"]))

    sample_rows = _load_sample_rows(sample_path)
    csv_path = output_dir / "workflow_runs.csv"
    trunc_path = output_dir / "workflow_runs_truncados.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    fieldnames = ["full_name", "default_branch", *CAMPOS]
    n_runs = 0
    n_consultas = 0
    trunc_rows: list[dict[str, str]] = []

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in sample_rows:
            full = _full_name(row)
            owner, name = full.split("/", 1)
            branch = (row.get("default_branch") or "main").strip() or "main"
            coleta = coletar_workflow_runs(
                client, cache, owner, name, branch, janela
            )
            n_consultas += coleta.n_consultas
            for run in coleta.runs:
                out = {"full_name": full, "default_branch": branch}
                out.update({k: run.get(k) for k in CAMPOS})
                writer.writerow(out)
                n_runs += 1
            for ini, fim in coleta.intervalos_truncados:
                trunc_rows.append(
                    {"full_name": full, "inicio": ini, "fim": fim}
                )

    with trunc_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["full_name", "inicio", "fim"])
        w.writeheader()
        w.writerows(trunc_rows)

    summary = {
        "n_repos": len(sample_rows),
        "n_runs": n_runs,
        "n_consultas": n_consultas,
        "n_intervalos_truncados": len(trunc_rows),
        "truncados_csv": str(trunc_path),
    }
    return csv_path, summary


def run_full_pipeline(
    client: _Cliente,
    config: dict[str, Any],
    *,
    max_candidates: int | None = None,
    sample_csv: Path | None = None,
    force: bool = False,
    stages: list[str] | None = None,
) -> PipelineReport:
    """Executa o pipeline completo (ou um subconjunto ordenado de etapas)."""
    wanted = stages or [
        "selection",
        "metadata",
        "releases",
        "compare",
        "workflow-runs",
    ]
    report = PipelineReport()
    output_dir = _output_dir(config)
    sample_path = sample_csv or (output_dir / "sample_repos.csv")

    if "selection" in wanted:
        result = run_selection(client, config, max_candidates=max_candidates)
        written = write_selection_outputs(result, output_dir)
        report.add(
            StageReport(
                name="selection",
                ok=True,
                detail=(
                    f"funil amostra={result.funnel.sample}/"
                    f"candidatos={result.funnel.candidates}"
                ),
                paths={k: str(v) for k, v in written.items()},
            )
        )

    if "metadata" in wanted:
        rows, csv_path = run_metadata_collection(
            client, config, sample_csv=sample_path, force=force
        )
        cached = sum(1 for r in rows if r.from_cache)
        report.add(
            StageReport(
                name="metadata",
                ok=True,
                detail=f"{len(rows)} repos (cache hit={cached})",
                paths={"repo_metadata": str(csv_path)},
            )
        )

    if "releases" in wanted:
        results, rel_path, tags_path = run_releases_collection(
            client, config, sample_csv=sample_path, force=force
        )
        n_rel = sum(len(r.releases) for r in results)
        report.add(
            StageReport(
                name="releases",
                ok=True,
                detail=f"{len(results)} repos, {n_rel} releases",
                paths={"releases": str(rel_path), "tags": str(tags_path)},
            )
        )

    if "compare" in wanted:
        results, commits_path, n404 = run_compare_collection(
            client, config, sample_csv=sample_path, force=force
        )
        report.add(
            StageReport(
                name="compare",
                ok=True,
                detail=f"{len(results)} pares compare, {n404} not_found (404)",
                paths={"commits": str(commits_path)},
            )
        )

    if "workflow-runs" in wanted:
        runs_path, summary = run_workflow_runs_collection(
            client, config, sample_csv=sample_path
        )
        report.add(
            StageReport(
                name="workflow-runs",
                ok=True,
                detail=(
                    f"{summary['n_runs']} runs em {summary['n_repos']} repos; "
                    f"truncados={summary['n_intervalos_truncados']}"
                ),
                paths={
                    "workflow_runs": str(runs_path),
                    "truncados": summary["truncados_csv"],
                },
            )
        )

    # manifesto das saídas intermediárias
    manifest = output_dir / "pipeline_manifest.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["stage", "ok", "detail", "paths"])
        w.writeheader()
        for s in report.stages:
            w.writerow(
                {
                    "stage": s.name,
                    "ok": s.ok,
                    "detail": s.detail,
                    "paths": "; ".join(f"{k}={v}" for k, v in s.paths.items()),
                }
            )
    report.add(
        StageReport(
            name="manifest",
            ok=True,
            detail="CSVs intermediários + funil",
            paths={"manifest": str(manifest)},
        )
    )
    return report

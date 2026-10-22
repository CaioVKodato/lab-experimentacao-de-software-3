"""Seleção de repositórios candidatos e funil de inclusão (Issue #2).

Busca via /search/repositories com fatiamento por faixas de estrelas para
ultrapassar o limite de 1.000 resultados por consulta. Em seguida aplica os
filtros do enunciado e gera a tabela do funil.

Issue #20 — duas melhorias opcionais, ambas desligadas por padrão:

* COTA POR FAIXA (`sample.stratify: true`). Sem ela, a amostra é formada pelos primeiros
  aprovados na ordem das faixas, ou seja, concentra-se nas faixas de MENOS estrelas, que
  também são as de menor taxa de aprovação (medido: ~10% em 1.000–2.000 estrelas contra
  ~33% em 10.001–25.000). Com ela, o `target_size` é dividido igualmente entre as faixas
  e cada faixa só é avaliada até fechar a cota. Se alguma faixa não tiver aprovados
  suficientes, o déficit é preenchido em rodízio pelas sobras das outras. A alocação
  igual SUPER-REPRESENTA as faixas altas em relação à população do GitHub: é uma escolha
  metodológica, a ser declarada no artigo. Nesse modo, `max_candidates` (CLI) vale POR
  FAIXA, e o padrão é `github.max_candidates_per_range` (1.000, o teto da API de busca).
* RETOMADA POR CACHE (`paths.cache_dir`). Cada avaliação é gravada por repositório,
  separada por janela e critérios; rodar de novo após uma queda não repete chamadas. Erro
  de API não é cacheado.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pipeline.cache import JsonCache
from pipeline.github_client import GitHubAPIError, GitHubClient

# conclusions que entram nos cálculos (seção 3 do enunciado)
VALID_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
VALID_SUCCESS_CONCLUSIONS = frozenset({"success"})
VALID_CONCLUSIONS = VALID_SUCCESS_CONCLUSIONS | VALID_FAILURE_CONCLUSIONS

# Faixas padrão para fatiar a busca (cada fatia ≤ ~1000 resultados)
DEFAULT_STAR_RANGES: list[tuple[int | None, int | None]] = [
    (1000, 2000),
    (2001, 5000),
    (5001, 10000),
    (10001, 25000),
    (25001, 50000),
    (50001, 100000),
    (100001, None),
]


@dataclass
class RepoCandidate:
    full_name: str
    owner: str
    name: str
    html_url: str
    stargazers_count: int
    language: str | None
    default_branch: str
    created_at: str | None = None
    forks_count: int | None = None


@dataclass
class FunnelStats:
    candidates: int = 0
    with_actions: int = 0
    with_min_criteria: int = 0
    sample: int = 0
    discarded_no_actions: int = 0
    discarded_few_releases: int = 0
    discarded_few_runs: int = 0
    discarded_api_error: int = 0
    stages: list[dict[str, Any]] = field(default_factory=list)

    def build_stages(self) -> list[dict[str, Any]]:
        self.stages = [
            {
                "etapa": "candidatos",
                "quantidade": self.candidates,
                "descricao": "Repositórios retornados pela busca fatiada",
            },
            {
                "etapa": "com_actions",
                "quantidade": self.with_actions,
                "descricao": "Possuem ao menos um workflow no GitHub Actions",
            },
            {
                "etapa": "criterio_minimo",
                "quantidade": self.with_min_criteria,
                "descricao": "≥ min_releases e ≥ min_workflow_runs válidos na janela",
            },
            {
                "etapa": "amostra",
                "quantidade": self.sample,
                "descricao": "Amostra final (limitada ao target_size)",
            },
        ]
        return self.stages


@dataclass
class SelectionResult:
    sample: list[RepoCandidate]
    funnel: FunnelStats
    evaluated: list[dict[str, Any]] = field(default_factory=list)
    por_faixa: list[dict[str, Any]] = field(default_factory=list)  # só no modo estratificado


def parse_star_ranges(raw: Iterable[Any] | None) -> list[tuple[int | None, int | None]]:
    """Converte faixas do config.yaml em pares (min, max). max=None => sem teto."""
    if not raw:
        return list(DEFAULT_STAR_RANGES)
    ranges: list[tuple[int | None, int | None]] = []
    for item in raw:
        if isinstance(item, dict):
            ranges.append((item.get("min"), item.get("max")))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            ranges.append((item[0], item[1]))
        else:
            raise ValueError(f"faixa de estrelas inválida: {item!r}")
    return ranges


def stars_query(min_stars: int | None, max_stars: int | None) -> str:
    if min_stars is None and max_stars is None:
        raise ValueError("faixa de estrelas vazia")
    if max_stars is None:
        return f"stars:>={min_stars}"
    if min_stars is None:
        return f"stars:<={max_stars}"
    if min_stars == max_stars:
        return f"stars:{min_stars}"
    return f"stars:{min_stars}..{max_stars}"


def parse_iso_date(value: str) -> date:
    """Aceita YYYY-MM-DD ou timestamp ISO completo."""
    text = value.strip()
    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    return date.fromisoformat(text)


def in_observation_window(
    published_at: str | None, window_start: date, window_end: date
) -> bool:
    if not published_at:
        return False
    try:
        day = parse_iso_date(published_at)
    except ValueError:
        return False
    return window_start <= day <= window_end


def repo_from_search_item(item: dict[str, Any]) -> RepoCandidate:
    full_name = item["full_name"]
    owner, name = full_name.split("/", 1)
    return RepoCandidate(
        full_name=full_name,
        owner=owner,
        name=name,
        html_url=item.get("html_url") or f"https://github.com/{full_name}",
        stargazers_count=int(item.get("stargazers_count") or 0),
        language=item.get("language"),
        default_branch=item.get("default_branch") or "main",
        created_at=item.get("created_at"),
        forks_count=item.get("forks_count"),
    )


def search_candidates(
    client: GitHubClient,
    star_ranges: list[tuple[int | None, int | None]],
    *,
    extra_query: str = "",
    max_candidates: int | None = None,
) -> list[RepoCandidate]:
    """Busca repositórios fatiando por estrelas (teto de 1.000 por consulta)."""
    seen: set[str] = set()
    candidates: list[RepoCandidate] = []

    for min_s, max_s in star_ranges:
        q_parts = [stars_query(min_s, max_s), "fork:false"]
        if extra_query:
            q_parts.append(extra_query.strip())
        query = " ".join(q_parts)

        # Search API: no máximo 1.000 resultados (10 páginas × 100)
        page = 1
        per_page = min(client.per_page, 100)
        while page <= 10:
            body = client.get(
                "/search/repositories",
                {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            items = body.get("items") or []
            if not items:
                break
            for item in items:
                repo = repo_from_search_item(item)
                if repo.full_name in seen:
                    continue
                seen.add(repo.full_name)
                candidates.append(repo)
                if max_candidates is not None and len(candidates) >= max_candidates:
                    return candidates
            if len(items) < per_page:
                break
            page += 1

    return candidates


def has_github_actions(client: GitHubClient, owner: str, repo: str) -> bool:
    body = client.get(f"/repos/{owner}/{repo}/actions/workflows", {"per_page": 1})
    return int(body.get("total_count") or 0) > 0


def count_published_releases_in_window(
    client: GitHubClient,
    owner: str,
    repo: str,
    window_start: date,
    window_end: date,
    *,
    min_needed: int | None = None,
) -> int:
    """Conta releases draft=false e prerelease=false com published_at na janela."""
    count = 0
    # Releases vêm da mais recente para a mais antiga; paramos cedo quando possível
    releases = client.paginate(
        f"/repos/{owner}/{repo}/releases",
        {"per_page": 100},
    )
    for release in releases:
        if release.get("draft") is True:
            continue
        if release.get("prerelease") is True:
            continue
        published = release.get("published_at")
        if not in_observation_window(published, window_start, window_end):
            # Se já passou do início da janela (releases mais antigas), pode parar
            if published:
                try:
                    if parse_iso_date(published) < window_start:
                        break
                except ValueError:
                    pass
            continue
        count += 1
        if min_needed is not None and count >= min_needed:
            return count
    return count


def count_valid_workflow_runs(
    client: GitHubClient,
    owner: str,
    repo: str,
    default_branch: str,
    window_start: date,
    window_end: date,
    *,
    min_needed: int | None = None,
) -> int:
    """Conta runs push no default branch com conclusion válida na janela.

    Subdivide a janela por mês para respeitar o teto de 1.000 resultados.
    """
    count = 0
    for month_start, month_end in _month_slices(window_start, window_end):
        created = f"{month_start.isoformat()}..{month_end.isoformat()}"
        page = 1
        per_page = min(client.per_page, 100)
        fetched_in_month = 0
        while page <= 10:  # teto Search/Runs: 1000
            body = client.get(
                f"/repos/{owner}/{repo}/actions/runs",
                {
                    "branch": default_branch,
                    "event": "push",
                    "created": created,
                    "per_page": per_page,
                    "page": page,
                },
            )
            runs = body.get("workflow_runs") or []
            if not runs:
                break
            for run in runs:
                fetched_in_month += 1
                conclusion = run.get("conclusion")
                if conclusion in VALID_CONCLUSIONS:
                    count += 1
                    if min_needed is not None and count >= min_needed:
                        return count
            if len(runs) < per_page:
                break
            page += 1
            if fetched_in_month >= 1000:
                break
    return count


def _month_slices(start: date, end: date) -> list[tuple[date, date]]:
    if end < start:
        return []
    slices: list[tuple[date, date]] = []
    year, month = start.year, start.month
    cursor = date(year, month, 1)
    while cursor <= end:
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        last_day = date.fromordinal(next_month.toordinal() - 1)
        slice_start = max(cursor, start)
        slice_end = min(last_day, end)
        if slice_start <= slice_end:
            slices.append((slice_start, slice_end))
        year, month = next_month.year, next_month.month
        cursor = next_month
    return slices


def faixa_de(
    estrelas: int, faixas: list[tuple[int | None, int | None]]
) -> int | None:
    """Índice da faixa que contém `estrelas` (limites inclusivos; None = sem limite)."""
    for i, (minimo, maximo) in enumerate(faixas):
        if (minimo is None or estrelas >= minimo) and (maximo is None or estrelas <= maximo):
            return i
    return None


def rotulo_faixa(minimo: int | None, maximo: int | None) -> str:
    if maximo is None:
        return f"{minimo}+"
    if minimo is None:
        return f"<={maximo}"
    return f"{minimo}-{maximo}"


def quotas_por_faixa(target_size: int, n_faixas: int) -> list[int]:
    """Divide o alvo igualmente; a sobra vai uma a uma para as primeiras faixas."""
    if n_faixas <= 0:
        return []
    base, resto = divmod(target_size, n_faixas)
    return [base + (1 if i < resto else 0) for i in range(n_faixas)]


def search_candidates_por_faixa(
    client: GitHubClient,
    star_ranges: list[tuple[int | None, int | None]],
    *,
    extra_query: str = "",
    max_por_faixa: int | None = None,
) -> list[list[RepoCandidate]]:
    """Como `search_candidates`, mas devolve os candidatos AGRUPADOS por faixa.

    Remove duplicatas entre faixas e limita cada faixa a `max_por_faixa`."""
    vistos: set[str] = set()
    grupos: list[list[RepoCandidate]] = []
    per_page = min(client.per_page, 100)
    for min_s, max_s in star_ranges:
        grupo: list[RepoCandidate] = []
        partes = [stars_query(min_s, max_s), "fork:false"]
        if extra_query:
            partes.append(extra_query.strip())
        query = " ".join(partes)
        page = 1
        while page <= 10 and (max_por_faixa is None or len(grupo) < max_por_faixa):
            body = client.get(
                "/search/repositories",
                {"q": query, "sort": "stars", "order": "desc", "per_page": per_page, "page": page},
            )
            items = body.get("items") or []
            if not items:
                break
            for item in items:
                repo = repo_from_search_item(item)
                if repo.full_name in vistos:
                    continue
                vistos.add(repo.full_name)
                grupo.append(repo)
                if max_por_faixa is not None and len(grupo) >= max_por_faixa:
                    break
            if len(items) < per_page:
                break
            page += 1
        grupos.append(grupo)
    return grupos


def _avaliar_com_cache(
    client: GitHubClient,
    cache: JsonCache | None,
    repo: RepoCandidate,
    *,
    window_start: date,
    window_end: date,
    min_releases: int,
    min_workflow_runs: int,
) -> dict[str, Any]:
    """`evaluate_repo` com cache por repositório, janela e critérios."""
    def avaliar() -> dict[str, Any]:
        return evaluate_repo(
            client, repo, window_start=window_start, window_end=window_end,
            min_releases=min_releases, min_workflow_runs=min_workflow_runs,
        )

    if cache is None:
        return avaliar()
    periodo = f"{window_start.isoformat()}_{window_end.isoformat()}_r{min_releases}_n{min_workflow_runs}"
    chave = f"selecao/{periodo}/{repo.owner}__{repo.name}"
    row = cache.load(chave)
    if isinstance(row, dict) and row.get("full_name") == repo.full_name:
        row["stargazers_count"] = repo.stargazers_count  # as estrelas mudam; o resto vale
        return row
    row = avaliar()
    if not str(row.get("discard_reason") or "").startswith("api_error"):
        cache.save(chave, row)  # erro de API é transitório: não grava
    return row


def _contabilizar(row: dict[str, Any], funnel: FunnelStats) -> bool:
    """Atualiza o funil com uma avaliação; devolve True se o repositório foi incluído."""
    reason = str(row["discard_reason"] or "")
    if not row["has_actions"]:
        if reason == "no_actions":
            funnel.discarded_no_actions += 1
        elif reason.startswith("api_error"):
            funnel.discarded_api_error += 1
        return False
    funnel.with_actions += 1
    if reason == "few_releases":
        funnel.discarded_few_releases += 1
        return False
    if reason == "few_runs":
        funnel.discarded_few_runs += 1
        return False
    if reason.startswith("api_error"):
        funnel.discarded_api_error += 1
        return False
    if row["included"]:
        funnel.with_min_criteria += 1
        return True
    return False


def _selecionar_estratificado(
    client: GitHubClient,
    cache: JsonCache | None,
    *,
    faixas: list[tuple[int | None, int | None]],
    target_size: int,
    max_por_faixa: int,
    extra_query: str,
    window_start: date,
    window_end: date,
    min_releases: int,
    min_workflow_runs: int,
) -> SelectionResult:
    n = len(faixas)
    rotulos = [rotulo_faixa(a, b) for a, b in faixas]
    quotas = quotas_por_faixa(target_size, n)
    grupos = search_candidates_por_faixa(
        client, faixas, extra_query=extra_query, max_por_faixa=max_por_faixa
    )

    funnel = FunnelStats(candidates=sum(len(g) for g in grupos))
    sample: list[RepoCandidate] = []
    evaluated: list[dict[str, Any]] = []
    avaliados = [0] * n
    aprovados = [0] * n
    sobras: list[list[RepoCandidate]] = [[] for _ in range(n)]

    def avaliar(i: int, repo: RepoCandidate) -> None:
        row = _avaliar_com_cache(
            client, cache, repo, window_start=window_start, window_end=window_end,
            min_releases=min_releases, min_workflow_runs=min_workflow_runs,
        )
        row["faixa"] = rotulos[i]
        evaluated.append(row)
        avaliados[i] += 1
        if _contabilizar(row, funnel):
            sample.append(repo)
            aprovados[i] += 1
        if len(evaluated) % 25 == 0:
            print(f"[selecao] {len(evaluated)} avaliados, {len(sample)} aprovados", file=sys.stderr, flush=True)

    # Passada 1: cada faixa até fechar a cota.
    for i, grupo in enumerate(grupos):
        for pos, repo in enumerate(grupo):
            if aprovados[i] >= quotas[i]:
                sobras[i] = grupo[pos:]
                break
            avaliar(i, repo)
        print(f"[selecao] faixa {rotulos[i]}: {aprovados[i]}/{quotas[i]} aprovados, {avaliados[i]} avaliados",
              file=sys.stderr, flush=True)

    # Passada 2: déficit das faixas que não fecharam a cota, em rodízio pelas sobras.
    while len(sample) < target_size and any(sobras):
        for i in range(n):
            if len(sample) >= target_size:
                break
            if sobras[i]:
                avaliar(i, sobras[i].pop(0))

    funnel.sample = len(sample)
    funnel.build_stages()
    por_faixa = [
        {"faixa": rotulos[i], "cota": quotas[i], "candidatos": len(grupos[i]),
         "avaliados": avaliados[i], "aprovados": aprovados[i]}
        for i in range(n)
    ]
    return SelectionResult(sample=sample, funnel=funnel, evaluated=evaluated, por_faixa=por_faixa)


def evaluate_repo(
    client: GitHubClient,
    repo: RepoCandidate,
    *,
    window_start: date,
    window_end: date,
    min_releases: int,
    min_workflow_runs: int,
) -> dict[str, Any]:
    """Avalia um candidato nas etapas do funil."""
    row: dict[str, Any] = {
        "full_name": repo.full_name,
        "stargazers_count": repo.stargazers_count,
        "language": repo.language,
        "default_branch": repo.default_branch,
        "has_actions": False,
        "releases_in_window": 0,
        "valid_runs_in_window": 0,
        "included": False,
        "discard_reason": None,
    }
    try:
        if not has_github_actions(client, repo.owner, repo.name):
            row["discard_reason"] = "no_actions"
            return row
        row["has_actions"] = True

        releases = count_published_releases_in_window(
            client,
            repo.owner,
            repo.name,
            window_start,
            window_end,
            min_needed=min_releases,
        )
        row["releases_in_window"] = releases
        if releases < min_releases:
            row["discard_reason"] = "few_releases"
            return row

        runs = count_valid_workflow_runs(
            client,
            repo.owner,
            repo.name,
            repo.default_branch,
            window_start,
            window_end,
            min_needed=min_workflow_runs,
        )
        row["valid_runs_in_window"] = runs
        if runs < min_workflow_runs:
            row["discard_reason"] = "few_runs"
            return row

        row["included"] = True
        return row
    except GitHubAPIError as exc:
        row["discard_reason"] = f"api_error:{exc.status_code}"
        return row


def run_selection(
    client: GitHubClient,
    config: dict[str, Any],
    *,
    max_candidates: int | None = None,
) -> SelectionResult:
    """Executa busca fatiada + filtros e monta o funil."""
    window = config.get("observation_window") or {}
    sample_cfg = config.get("sample") or {}
    github_cfg = config.get("github") or {}

    window_start = parse_iso_date(window["start"])
    window_end = parse_iso_date(window["end"])
    target_size = int(sample_cfg.get("target_size") or 100)
    min_releases = int(sample_cfg.get("min_releases") or 5)
    min_runs = int(sample_cfg.get("min_workflow_runs") or 50)
    star_ranges = parse_star_ranges(github_cfg.get("star_ranges"))
    extra_query = str(github_cfg.get("search_extra_query") or "")
    cache_dir = (config.get("paths") or {}).get("cache_dir")
    cache = JsonCache(Path(cache_dir)) if cache_dir else None

    if sample_cfg.get("stratify"):
        return _selecionar_estratificado(
            client,
            cache,
            faixas=star_ranges,
            target_size=target_size,
            max_por_faixa=max_candidates
            if max_candidates is not None
            else int(github_cfg.get("max_candidates_per_range") or 1000),
            extra_query=extra_query,
            window_start=window_start,
            window_end=window_end,
            min_releases=min_releases,
            min_workflow_runs=min_runs,
        )

    search_cap = max_candidates
    if search_cap is None and github_cfg.get("max_search_candidates") is not None:
        search_cap = int(github_cfg["max_search_candidates"])

    candidates = search_candidates(
        client,
        star_ranges,
        extra_query=extra_query,
        max_candidates=search_cap,
    )

    funnel = FunnelStats(candidates=len(candidates))
    sample: list[RepoCandidate] = []
    evaluated: list[dict[str, Any]] = []

    for repo in candidates:
        if len(sample) >= target_size:
            break
        row = _avaliar_com_cache(
            client,
            cache,
            repo,
            window_start=window_start,
            window_end=window_end,
            min_releases=min_releases,
            min_workflow_runs=min_runs,
        )
        evaluated.append(row)

        if not row["has_actions"]:
            if row["discard_reason"] == "no_actions":
                funnel.discarded_no_actions += 1
            elif row["discard_reason"] and str(row["discard_reason"]).startswith(
                "api_error"
            ):
                funnel.discarded_api_error += 1
            continue

        funnel.with_actions += 1
        if row["discard_reason"] == "few_releases":
            funnel.discarded_few_releases += 1
            continue
        if row["discard_reason"] == "few_runs":
            funnel.discarded_few_runs += 1
            continue
        if row["discard_reason"] and str(row["discard_reason"]).startswith("api_error"):
            funnel.discarded_api_error += 1
            continue
        if row["included"]:
            funnel.with_min_criteria += 1
            sample.append(repo)

    funnel.sample = len(sample)
    funnel.build_stages()
    return SelectionResult(sample=sample, funnel=funnel, evaluated=evaluated)


def write_selection_outputs(
    result: SelectionResult, output_dir: Path
) -> dict[str, Path]:
    """Persiste funil (CSV+JSON), amostra e log de avaliação."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    funnel_csv = output_dir / "funnel.csv"
    with funnel_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["etapa", "quantidade", "descricao"])
        writer.writeheader()
        writer.writerows(result.funnel.stages)
    paths["funnel_csv"] = funnel_csv

    funnel_json = output_dir / "funnel.json"
    funnel_json.write_text(
        json.dumps(asdict(result.funnel), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["funnel_json"] = funnel_json

    sample_csv = output_dir / "sample_repos.csv"
    with sample_csv.open("w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "full_name",
            "owner",
            "name",
            "html_url",
            "stargazers_count",
            "language",
            "default_branch",
            "created_at",
            "forks_count",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for repo in result.sample:
            writer.writerow(asdict(repo))
    paths["sample_csv"] = sample_csv

    evaluated_csv = output_dir / "selection_evaluated.csv"
    if result.evaluated:
        with evaluated_csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(result.evaluated[0].keys()))
            writer.writeheader()
            writer.writerows(result.evaluated)
        paths["evaluated_csv"] = evaluated_csv

    if result.por_faixa:
        por_faixa_csv = output_dir / "funnel_por_faixa.csv"
        with por_faixa_csv.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["faixa", "cota", "candidatos", "avaliados", "aprovados"]
            )
            writer.writeheader()
            writer.writerows(result.por_faixa)
        paths["funnel_por_faixa_csv"] = por_faixa_csv

    return paths


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

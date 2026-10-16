"""Sorteio da amostra-ouro e geração de planilhas de rotulagem (Issue #23).

Sorteia 60 repositórios da amostra final e 5 releases por repositório, ambos
com semente fixa documentada. Gera uma planilha por avaliador com links do
GitHub e colunas de rótulo vazias (sem saída da heurística automática).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

RANDOM_STATE_REPOS = 42
RANDOM_STATE_RELEASES = 42
DEFAULT_N_REPOS = 60
DEFAULT_N_RELEASES = 5
DEFAULT_EVALUATORS = ("CaioVKodato", "Henrique-volponi", "Kjonps")

TIPO_PROJETO_OPCOES = (
    "biblioteca/framework",
    "aplicacao/servico",
    "ferramenta_CLI",
    "outro",
)
ENTREGA_REAL_OPCOES = ("sim", "nao", "incerto")
CORRETIVA_OPCOES = ("sim", "nao")


@dataclass(frozen=True)
class AmostraOuroConfig:
    n_repos: int = DEFAULT_N_REPOS
    n_releases_per_repo: int = DEFAULT_N_RELEASES
    random_state_repos: int = RANDOM_STATE_REPOS
    random_state_releases: int = RANDOM_STATE_RELEASES
    evaluators: tuple[str, ...] = DEFAULT_EVALUATORS


def config_from_dict(raw: dict[str, Any] | None) -> AmostraOuroConfig:
    data = raw or {}
    evaluators = data.get("evaluators") or list(DEFAULT_EVALUATORS)
    return AmostraOuroConfig(
        n_repos=int(data.get("n_repos") or DEFAULT_N_REPOS),
        n_releases_per_repo=int(
            data.get("n_releases_per_repo") or DEFAULT_N_RELEASES
        ),
        random_state_repos=int(
            data.get("random_state_repos") or RANDOM_STATE_REPOS
        ),
        random_state_releases=int(
            data.get("random_state_releases") or RANDOM_STATE_RELEASES
        ),
        evaluators=tuple(str(e) for e in evaluators),
    )


def load_sample_frame(sample_csv: Path) -> pd.DataFrame:
    if not sample_csv.is_file():
        raise FileNotFoundError(
            f"Amostra não encontrada: {sample_csv}. "
            "Execute a seleção/coleta antes (sample_repos.csv)."
        )
    df = pd.read_csv(sample_csv)
    if "full_name" not in df.columns:
        raise ValueError(f"{sample_csv} precisa da coluna full_name")
    df = df.dropna(subset=["full_name"]).copy()
    df["full_name"] = df["full_name"].astype(str).str.strip()
    df = df[df["full_name"].str.contains("/", regex=False)]
    return df.drop_duplicates(subset=["full_name"]).reset_index(drop=True)


def load_releases_frame(releases_csv: Path) -> pd.DataFrame:
    if not releases_csv.is_file():
        raise FileNotFoundError(
            f"Releases não encontradas: {releases_csv}. "
            "Execute a coleta de releases antes (releases.csv)."
        )
    df = pd.read_csv(releases_csv)
    required = {"full_name", "tag_name", "html_url", "published_at"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{releases_csv} sem colunas: {sorted(missing)}")
    df = df.dropna(subset=["full_name", "tag_name"]).copy()
    if "prerelease" in df.columns:
        df = df[~df["prerelease"].fillna(False).astype(bool)]
    df["full_name"] = df["full_name"].astype(str).str.strip()
    return df.reset_index(drop=True)


def sortear_repositorios(
    sample_df: pd.DataFrame, *, n: int, random_state: int
) -> pd.DataFrame:
    if len(sample_df) < n:
        raise ValueError(
            f"Amostra tem {len(sample_df)} repositórios; são necessários ≥ {n} "
            "para a amostra-ouro. Conclua a coleta da amostra final (Issue #20)."
        )
    return (
        sample_df.sample(n=n, random_state=random_state)
        .sort_values("full_name")
        .reset_index(drop=True)
    )


def sortear_releases_por_repo(
    releases_df: pd.DataFrame,
    repos: pd.DataFrame,
    *,
    n_per_repo: int,
    random_state: int,
) -> pd.DataFrame:
    """Semente por repositório: random_state + índice (repos ordenados por full_name)."""
    rows: list[pd.DataFrame] = []
    for idx, full_name in enumerate(repos["full_name"].tolist()):
        subset = releases_df[releases_df["full_name"] == full_name]
        if subset.empty:
            raise ValueError(
                f"Nenhuma release (não pré-release) para {full_name} em releases.csv"
            )
        k = min(n_per_repo, len(subset))
        chosen = subset.sample(n=k, random_state=random_state + idx)
        rows.append(chosen)
    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["full_name", "published_at", "tag_name"]).reset_index(
        drop=True
    )


def _repo_url(full_name: str, html_url: Any = None) -> str:
    if html_url is not None and str(html_url).startswith("http"):
        return str(html_url)
    return f"https://github.com/{full_name}"


def _release_url(full_name: str, tag_name: str, html_url: Any = None) -> str:
    if html_url is not None and str(html_url).startswith("http"):
        return str(html_url)
    return f"https://github.com/{full_name}/releases/tag/{tag_name}"


def montar_planilha(repos: pd.DataFrame, releases: pd.DataFrame) -> pd.DataFrame:
    repo_urls: dict[str, str] = {}
    for _, row in repos.iterrows():
        full_name = row["full_name"]
        repo_urls[full_name] = _repo_url(
            full_name, row["html_url"] if "html_url" in repos.columns else None
        )

    records: list[dict[str, Any]] = []
    for _, rel in releases.iterrows():
        full_name = str(rel["full_name"])
        tag = str(rel["tag_name"])
        records.append(
            {
                "full_name": full_name,
                "repo_url": repo_urls.get(full_name, _repo_url(full_name)),
                "tipo_projeto": "",
                "entrega_real": "",
                "release_tag": tag,
                "release_published_at": rel.get("published_at", ""),
                "release_url": _release_url(full_name, tag, rel.get("html_url")),
                "eh_corretiva": "",
                "tipo_projeto_opcoes": "|".join(TIPO_PROJETO_OPCOES),
                "entrega_real_opcoes": "|".join(ENTREGA_REAL_OPCOES),
                "eh_corretiva_opcoes": "|".join(CORRETIVA_OPCOES),
            }
        )
    return pd.DataFrame.from_records(records)


def escrever_planilhas(
    planilha: pd.DataFrame,
    output_dir: Path,
    evaluators: tuple[str, ...],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for evaluator in evaluators:
        path = output_dir / f"planilha_{evaluator}.csv"
        planilha.to_csv(path, index=False, encoding="utf-8")
        paths[evaluator] = path
    return paths


def gerar_amostra_ouro(
    sample_csv: Path,
    releases_csv: Path,
    output_dir: Path,
    *,
    cfg: AmostraOuroConfig | None = None,
) -> dict[str, Any]:
    cfg = cfg or AmostraOuroConfig()
    sample_df = load_sample_frame(sample_csv)
    releases_df = load_releases_frame(releases_csv)

    repos = sortear_repositorios(
        sample_df, n=cfg.n_repos, random_state=cfg.random_state_repos
    )
    releases = sortear_releases_por_repo(
        releases_df,
        repos,
        n_per_repo=cfg.n_releases_per_repo,
        random_state=cfg.random_state_releases,
    )
    planilha = montar_planilha(repos, releases)

    output_dir.mkdir(parents=True, exist_ok=True)
    repos_path = output_dir / "amostra_ouro_repos.csv"
    releases_path = output_dir / "amostra_ouro_releases.csv"
    meta_path = output_dir / "amostra_ouro_meta.csv"
    master = output_dir / "planilha_modelo.csv"

    repos.to_csv(repos_path, index=False, encoding="utf-8")
    releases.to_csv(releases_path, index=False, encoding="utf-8")
    pd.DataFrame(
        [
            {
                "n_repos": cfg.n_repos,
                "n_releases_per_repo": cfg.n_releases_per_repo,
                "random_state_repos": cfg.random_state_repos,
                "random_state_releases": cfg.random_state_releases,
                "n_repos_sorteados": len(repos),
                "n_releases_sorteadas": len(releases),
                "nota": (
                    "Semente de releases por repositório = "
                    "random_state_releases + índice "
                    "(repos ordenados por full_name)."
                ),
            }
        ]
    ).to_csv(meta_path, index=False, encoding="utf-8")
    planilha.to_csv(master, index=False, encoding="utf-8")
    planilha_paths = escrever_planilhas(planilha, output_dir, cfg.evaluators)

    return {
        "repos_csv": repos_path,
        "releases_csv": releases_path,
        "meta_csv": meta_path,
        "planilha_modelo": master,
        "planilhas": planilha_paths,
        "n_repos": len(repos),
        "n_releases": len(releases),
    }


def run_amostra_ouro(config: dict[str, Any]) -> dict[str, Any]:
    """Ponto de entrada a partir do config.yaml (cwd = code/)."""
    paths = config.get("paths") or {}
    output_dir = Path(paths.get("output_dir") or "data/output")
    validacao_dir = Path(paths.get("validacao_dir") or "../validacao")
    sample_csv = Path(paths.get("sample_csv") or (output_dir / "sample_repos.csv"))
    releases_csv = Path(paths.get("releases_csv") or (output_dir / "releases.csv"))
    cfg = config_from_dict(config.get("validation") or config.get("amostra_ouro"))
    return gerar_amostra_ouro(sample_csv, releases_csv, validacao_dir, cfg=cfg)

"""RQ01–RQ04: descritivas, classificação C1 e figuras (Issue #36).

Uso (a partir de ``code/``)::

    python -m analise.rq01_rq04 caminho/do/dataset.csv --outdir ../docs/s03

C1 = deploys (release) + lead time (a) + CFR (a) + recuperação.
Ausentes são contados e reportados; nunca descartados em silêncio.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from metricas.classificacao_dora import (
    NOTA_PARA_NIVEL,
    classificar_dora,
    nota_cfr,
    nota_deploys,
    nota_lead_time,
    nota_recuperacao,
)

# Métricas reportadas: referência C1 + variantes (a)/(b) de lead time e CFR
METRICAS: tuple[tuple[str, str], ...] = (
    ("deploys_por_semana", "RQ01 — deploys/semana"),
    ("lead_time_a_horas", "RQ02 — lead time (a)"),
    ("lead_time_b_horas", "RQ02 — lead time (b)"),
    ("cfr_a", "RQ03 — CFR (a)"),
    ("cfr_b", "RQ03 — CFR (b)"),
    ("recuperacao_mediana_horas", "RQ04 — recuperação (h)"),
)

NIVEIS = ("Elite", "High", "Medium", "Low")


def _to_float(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def descritiva(serie: pd.Series) -> dict[str, Any]:
    """Mediana, IQR, n e nº de ausentes (ausentes nunca entram no cálculo)."""
    n_total = int(len(serie))
    numeric = pd.to_numeric(serie, errors="coerce")
    n_ausentes = int(numeric.isna().sum())
    validos = numeric.dropna()
    n = int(len(validos))
    if n == 0:
        return {
            "n": 0,
            "n_ausentes": n_ausentes,
            "n_total": n_total,
            "mediana": None,
            "q1": None,
            "q3": None,
            "iqr": None,
        }
    q1 = float(validos.quantile(0.25))
    q3 = float(validos.quantile(0.75))
    return {
        "n": n,
        "n_ausentes": n_ausentes,
        "n_total": n_total,
        "mediana": float(validos.median()),
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
    }


def tabela_descritivas(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col, rotulo in METRICAS:
        if col not in df.columns:
            rows.append(
                {
                    "metrica": col,
                    "rotulo": rotulo,
                    "n": 0,
                    "n_ausentes": len(df),
                    "n_total": len(df),
                    "mediana": None,
                    "q1": None,
                    "q3": None,
                    "iqr": None,
                    "presente_no_csv": False,
                }
            )
            continue
        stats = descritiva(df[col])
        rows.append(
            {
                "metrica": col,
                "rotulo": rotulo,
                **stats,
                "presente_no_csv": True,
            }
        )
    return pd.DataFrame(rows)


def classificar_c1_linha(row: pd.Series) -> Optional[str]:
    """C1: release + lead time (a) + CFR (a) + recuperação."""
    return classificar_dora(
        _to_float(row.get("deploys_por_semana")),
        _to_float(row.get("lead_time_a_horas")),
        _to_float(row.get("cfr_a")),
        _to_float(row.get("recuperacao_mediana_horas")),
    )


def notas_c1_por_metrica(row: pd.Series) -> dict[str, Optional[str]]:
    return {
        "nivel_deploys": _nivel(nota_deploys(_to_float(row.get("deploys_por_semana")))),
        "nivel_lead_time_a": _nivel(
            nota_lead_time(_to_float(row.get("lead_time_a_horas")))
        ),
        "nivel_cfr_a": _nivel(nota_cfr(_to_float(row.get("cfr_a")))),
        "nivel_recuperacao": _nivel(
            nota_recuperacao(_to_float(row.get("recuperacao_mediana_horas")))
        ),
    }


def _nivel(nota: Optional[int]) -> Optional[str]:
    if nota is None:
        return None
    return NOTA_PARA_NIVEL[nota]


def aplicar_c1(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    niveis = [notas_c1_por_metrica(row) for _, row in out.iterrows()]
    niveis_df = pd.DataFrame(niveis)
    out = pd.concat([out.reset_index(drop=True), niveis_df], axis=1)
    out["dora_c1"] = [classificar_c1_linha(row) for _, row in df.iterrows()]
    return out


def distribuicao_niveis(serie: pd.Series) -> pd.DataFrame:
    counts = serie.value_counts(dropna=False)
    rows = []
    for nivel in NIVEIS:
        n = int(counts.get(nivel, 0))
        rows.append({"nivel": nivel, "n": n})
    n_ausentes = int(serie.isna().sum())
    rows.append({"nivel": "ausente", "n": n_ausentes})
    total = sum(r["n"] for r in rows)
    for r in rows:
        r["proporcao"] = (r["n"] / total) if total else 0.0
    return pd.DataFrame(rows)


def resumo_censura_rq04(df: pd.DataFrame) -> dict[str, Any]:
    """Proporção de episódios censurados e repos sem episódio de falha."""
    prop_col = "recuperacao_prop_censurados"
    n_ep_col = "recuperacao_n_episodios"
    recup_col = "recuperacao_mediana_horas"

    n_repos = len(df)
    sem_episodio = 0
    if n_ep_col in df.columns:
        n_ep = pd.to_numeric(df[n_ep_col], errors="coerce")
        sem_episodio = int((n_ep.fillna(0) <= 0).sum())
    elif recup_col in df.columns:
        recup = pd.to_numeric(df[recup_col], errors="coerce")
        sem_episodio = int(recup.isna().sum())

    prop_mediana = None
    prop_media = None
    n_com_prop = 0
    if prop_col in df.columns:
        props = pd.to_numeric(df[prop_col], errors="coerce").dropna()
        n_com_prop = int(len(props))
        if n_com_prop:
            prop_mediana = float(props.median())
            prop_media = float(props.mean())

    return {
        "n_repos": n_repos,
        "n_repos_sem_episodio_falha": sem_episodio,
        "proporcao_repos_sem_episodio": (sem_episodio / n_repos) if n_repos else None,
        "n_repos_com_prop_censurados": n_com_prop,
        "prop_censurados_mediana": prop_mediana,
        "prop_censurados_media": prop_media,
        "tratamento": (
            "Repositórios sem episódio de falha ficam com recuperação ausente "
            "e entram em n_ausentes da descritiva; não são imputados."
        ),
    }


def _usar_log(valores: pd.Series) -> bool:
    v = pd.to_numeric(valores, errors="coerce").dropna()
    if len(v) < 2:
        return False
    positivos = v[v > 0]
    if len(positivos) < 2:
        return False
    return float(positivos.max() / positivos.min()) >= 100


def gerar_figuras(df: pd.DataFrame, outdir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for col, rotulo in METRICAS:
        if col not in df.columns:
            continue
        serie = pd.to_numeric(df[col], errors="coerce").dropna()
        if serie.empty:
            continue
        fig, ax = plt.subplots(figsize=(7, 4))
        log = _usar_log(serie)
        data = serie[serie > 0] if log else serie
        ax.hist(data, bins=20, color="#2c5f7c", edgecolor="white")
        ax.set_title(rotulo)
        ax.set_xlabel(col + (" (log)" if log else ""))
        ax.set_ylabel("repositórios")
        if log:
            ax.set_xscale("log")
        fig.tight_layout()
        path = outdir / f"dist_{col}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        paths.append(path)

    classificado = aplicar_c1(df)
    fig, axes = plt.subplots(1, 5, figsize=(14, 3.5), sharey=True)
    series_plot = [
        ("nivel_deploys", "Deploys"),
        ("nivel_lead_time_a", "Lead time a"),
        ("nivel_cfr_a", "CFR a"),
        ("nivel_recuperacao", "Recuperação"),
        ("dora_c1", "C1 geral"),
    ]
    for ax, (col, titulo) in zip(axes, series_plot):
        dist = distribuicao_niveis(classificado[col])
        dist_plot = dist[dist["nivel"] != "ausente"]
        ax.bar(dist_plot["nivel"], dist_plot["n"], color="#2c5f7c")
        ax.set_title(titulo)
        ax.tick_params(axis="x", rotation=45)
    fig.suptitle("Distribuição DORA — referência C1")
    fig.tight_layout()
    path_c1 = outdir / "dist_dora_c1.png"
    fig.savefig(path_c1, dpi=120)
    plt.close(fig)
    paths.append(path_c1)
    return paths


def analisar(csv_path: Path, outdir: Path) -> dict[str, Any]:
    df = pd.read_csv(csv_path)
    outdir.mkdir(parents=True, exist_ok=True)

    desc = tabela_descritivas(df)
    desc_path = outdir / "tabela_descritivas_rq01_rq04.csv"
    desc.to_csv(desc_path, index=False, encoding="utf-8")

    classificado = aplicar_c1(df)
    class_path = outdir / "classificacao_c1_por_repo.csv"
    cols_keep = [
        c
        for c in (
            "repo",
            "deploys_por_semana",
            "lead_time_a_horas",
            "cfr_a",
            "recuperacao_mediana_horas",
            "nivel_deploys",
            "nivel_lead_time_a",
            "nivel_cfr_a",
            "nivel_recuperacao",
            "dora_c1",
        )
        if c in classificado.columns
    ]
    classificado[cols_keep].to_csv(class_path, index=False, encoding="utf-8")

    dist_geral = distribuicao_niveis(classificado["dora_c1"])
    dist_path = outdir / "dist_niveis_c1.csv"
    dist_geral.to_csv(dist_path, index=False, encoding="utf-8")

    for col, nome in (
        ("nivel_deploys", "deploys"),
        ("nivel_lead_time_a", "lead_time_a"),
        ("nivel_cfr_a", "cfr_a"),
        ("nivel_recuperacao", "recuperacao"),
    ):
        distribuicao_niveis(classificado[col]).to_csv(
            outdir / f"dist_niveis_{nome}.csv", index=False, encoding="utf-8"
        )

    censura = resumo_censura_rq04(df)
    censura_path = outdir / "resumo_censura_rq04.csv"
    pd.DataFrame([censura]).to_csv(censura_path, index=False, encoding="utf-8")

    figs = gerar_figuras(df, outdir / "figuras")

    readme = outdir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# RQ01–RQ04 — descritivas e classificação C1 (Issue #36)",
                "",
                f"Gerado a partir de `{csv_path.as_posix()}`.",
                "",
                "## Arquivos",
                "",
                "- `tabela_descritivas_rq01_rq04.csv` — mediana, IQR, n, ausentes",
                "- `classificacao_c1_por_repo.csv` — nível por métrica e geral",
                "- `dist_niveis_*.csv` — distribuição Elite/High/Medium/Low",
                "- `resumo_censura_rq04.csv` — episódios censurados / sem falha",
                "- `figuras/` — histogramas e barras C1",
                "",
                "## Censura (RQ04)",
                "",
                censura["tratamento"],
                "",
                f"- Repos sem episódio de falha: "
                f"**{censura['n_repos_sem_episodio_falha']}** / {censura['n_repos']}",
                f"- Mediana da proporção de episódios censurados: "
                f"**{censura['prop_censurados_mediana']}**",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "descritivas": desc_path,
        "classificacao": class_path,
        "distribuicao_c1": dist_path,
        "censura": censura_path,
        "figuras": figs,
        "readme": readme,
        "resumo_censura": censura,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Análise RQ01–RQ04 (descritivas + classificação C1)."
    )
    parser.add_argument("csv", type=Path, help="Caminho do dataset CSV consolidado")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("../docs/s03"),
        help="Diretório de saída (tabelas e figuras)",
    )
    args = parser.parse_args(argv)
    if not args.csv.is_file():
        raise SystemExit(f"CSV não encontrado: {args.csv}")
    result = analisar(args.csv, args.outdir)
    print(f"Descritivas → {result['descritivas']}")
    print(f"Classificação C1 → {result['classificacao']}")
    print(f"Figuras → {len(result['figuras'])} arquivo(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

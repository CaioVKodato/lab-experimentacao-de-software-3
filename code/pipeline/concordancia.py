"""Concordância entre avaliadores (kappa de Fleiss) e consenso (Issue #27).

Lê ``validacao/rotulos_<avaliador>.csv`` (ou ``planilha_<avaliador>.csv``),
calcula Fleiss kappa nas três dimensões com ``aggregate_raters`` +
``fleiss_kappa`` (statsmodels), interpreta pela escala de Landis & Koch e
gera o rótulo de consenso (maioria simples; empates 1-1-1 marcados para reunião).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import pandas as pd
from statsmodels.stats.inter_rater import aggregate_raters, fleiss_kappa

DIMENSOES = ("tipo_projeto", "entrega_real", "eh_corretiva")
DEFAULT_AVALIADORES = ("CaioVKodato", "Henrique-volponi", "Kjonps")

# Landis & Koch (1977)
LANDIS_KOCH: tuple[tuple[float, float, str], ...] = (
    (-1.0, 0.0, "pobre (poor)"),
    (0.0, 0.20, "leve (slight)"),
    (0.20, 0.40, "razoável (fair)"),
    (0.40, 0.60, "moderada (moderate)"),
    (0.60, 0.80, "substancial (substantial)"),
    (0.80, 1.01, "quase perfeita (almost perfect)"),
)


@dataclass(frozen=True)
class ResultadoKappa:
    dimensao: str
    kappa: float
    interpretacao: str
    n_itens: int
    n_avaliadores: int


def interpretar_landis_koch(kappa: float) -> str:
    for lo, hi, label in LANDIS_KOCH:
        if lo <= kappa < hi:
            return label
    return "fora da escala"


def _resolver_arquivo(validacao_dir: Path, avaliador: str) -> Path:
    for nome in (f"rotulos_{avaliador}.csv", f"planilha_{avaliador}.csv"):
        path = validacao_dir / nome
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Não achei rotulos_{avaliador}.csv nem planilha_{avaliador}.csv em {validacao_dir}"
    )


def carregar_rotulos(
    validacao_dir: Path,
    avaliadores: Sequence[str] = DEFAULT_AVALIADORES,
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for av in avaliadores:
        path = _resolver_arquivo(validacao_dir, av)
        df = pd.read_csv(path, dtype=str).fillna("")
        for col in ("full_name", "release_tag", *DIMENSOES):
            if col not in df.columns:
                raise ValueError(f"{path} sem coluna obrigatória: {col}")
        frames[av] = df
    return frames


def _chave_item(dimensao: str, row: pd.Series) -> str:
    if dimensao == "eh_corretiva":
        return f"{row['full_name']}::{row['release_tag']}"
    return str(row["full_name"])


def matriz_avaliadores(
    frames: dict[str, pd.DataFrame],
    dimensao: str,
    avaliadores: Sequence[str],
) -> tuple[pd.DataFrame, list[str]]:
    """DataFrame itens × avaliadores e lista de chaves (só itens com todos preenchidos)."""
    por_av: dict[str, dict[str, str]] = {}
    for av in avaliadores:
        mapa: dict[str, str] = {}
        for _, row in frames[av].iterrows():
            chave = _chave_item(dimensao, row)
            valor = str(row[dimensao]).strip()
            if dimensao != "eh_corretiva":
                # uma linha por release; usa o primeiro rótulo não vazio do repo
                if chave in mapa and mapa[chave]:
                    continue
            if valor:
                mapa[chave] = valor
        por_av[av] = mapa

    chaves = sorted(
        set.intersection(*(set(m.keys()) for m in por_av.values()))
    )
    # só itens em que todos os avaliadores rotularam
    chaves_completas = [
        c
        for c in chaves
        if all(por_av[av].get(c, "").strip() for av in avaliadores)
    ]
    data = {
        av: [por_av[av][c] for c in chaves_completas] for av in avaliadores
    }
    return pd.DataFrame(data, index=chaves_completas), chaves_completas


def calcular_fleiss(
    frames: dict[str, pd.DataFrame],
    dimensao: str,
    avaliadores: Sequence[str] = DEFAULT_AVALIADORES,
) -> ResultadoKappa:
    mat, chaves = matriz_avaliadores(frames, dimensao, avaliadores)
    if mat.empty or len(mat) < 2:
        raise ValueError(
            f"Itens insuficientes com rótulo completo em '{dimensao}' "
            f"(n={len(mat)}). Preencha as planilhas das Issues #24–#26."
        )
    # aggregate_raters espera array (n_itens, n_avaliadores) com códigos
    table, _cats = aggregate_raters(mat.to_numpy())
    kappa = float(fleiss_kappa(table, method="fleiss"))
    return ResultadoKappa(
        dimensao=dimensao,
        kappa=kappa,
        interpretacao=interpretar_landis_koch(kappa),
        n_itens=len(chaves),
        n_avaliadores=len(avaliadores),
    )


def consenso_maioria(valores: Sequence[str]) -> tuple[str, str]:
    """Retorna (rótulo, status).

    status: ``maioria`` | ``unanime`` | ``empate_reuniao``
    """
    limpos = [v.strip() for v in valores if str(v).strip()]
    if not limpos:
        return "", "sem_rotulo"
    counts: dict[str, int] = {}
    for v in limpos:
        counts[v] = counts.get(v, 0) + 1
    ordenado = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    melhor, n_melhor = ordenado[0]
    if n_melhor == len(limpos):
        return melhor, "unanime"
    # empate no topo (ex.: 1-1-1 ou 2-2 com 4 avaliadores)
    empatados = [lab for lab, n in ordenado if n == n_melhor]
    if len(empatados) > 1:
        return "", "empate_reuniao"
    return melhor, "maioria"


def gerar_consenso(
    frames: dict[str, pd.DataFrame],
    avaliadores: Sequence[str] = DEFAULT_AVALIADORES,
) -> pd.DataFrame:
    """Uma linha por release, com consenso nas três dimensões."""
    base = next(iter(frames.values()))[
        ["full_name", "repo_url", "release_tag", "release_published_at", "release_url"]
    ].copy()
    # alinha por (full_name, release_tag)
    for av, df in frames.items():
        sub = df[["full_name", "release_tag", *DIMENSOES]].copy()
        sub = sub.rename(columns={d: f"{d}__{av}" for d in DIMENSOES})
        base = base.merge(sub, on=["full_name", "release_tag"], how="outer")

    rows: list[dict[str, Any]] = []
    for _, row in base.iterrows():
        registro: dict[str, Any] = {
            "full_name": row["full_name"],
            "repo_url": row.get("repo_url", ""),
            "release_tag": row["release_tag"],
            "release_published_at": row.get("release_published_at", ""),
            "release_url": row.get("release_url", ""),
        }
        for dim in DIMENSOES:
            vals = [str(row.get(f"{dim}__{av}", "") or "") for av in avaliadores]
            for av, v in zip(avaliadores, vals):
                registro[f"{dim}__{av}"] = v
            label, status = consenso_maioria(vals)
            registro[dim] = label
            registro[f"{dim}__status"] = status
        rows.append(registro)
    return pd.DataFrame(rows)


def casos_divergentes(consenso: pd.DataFrame) -> pd.DataFrame:
    mask = False
    for dim in DIMENSOES:
        col = f"{dim}__status"
        mask = mask | consenso[col].isin(["maioria", "empate_reuniao"])
    return consenso.loc[mask].reset_index(drop=True)


def run_concordancia(
    validacao_dir: Path,
    *,
    avaliadores: Sequence[str] = DEFAULT_AVALIADORES,
) -> dict[str, Any]:
    frames = carregar_rotulos(validacao_dir, avaliadores)
    kappas = [calcular_fleiss(frames, dim, avaliadores) for dim in DIMENSOES]
    consenso = gerar_consenso(frames, avaliadores)
    divergentes = casos_divergentes(consenso)

    kappa_path = validacao_dir / "kappa_fleiss.csv"
    pd.DataFrame(
        [
            {
                "dimensao": k.dimensao,
                "kappa": k.kappa,
                "interpretacao_landis_koch": k.interpretacao,
                "n_itens": k.n_itens,
                "n_avaliadores": k.n_avaliadores,
            }
            for k in kappas
        ]
    ).to_csv(kappa_path, index=False, encoding="utf-8")

    consenso_path = validacao_dir / "rotulos_consenso.csv"
    consenso.to_csv(consenso_path, index=False, encoding="utf-8")

    div_path = validacao_dir / "casos_divergentes.csv"
    divergentes.to_csv(div_path, index=False, encoding="utf-8")

    return {
        "kappas": kappas,
        "kappa_csv": kappa_path,
        "consenso_csv": consenso_path,
        "divergentes_csv": div_path,
        "n_divergentes": len(divergentes),
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kappa de Fleiss + consenso da amostra-ouro (Issue #27)."
    )
    parser.add_argument(
        "--validacao-dir",
        type=Path,
        default=Path("../validacao"),
        help="Diretório com rotulos_*.csv",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = run_concordancia(args.validacao_dir)
    print("Kappa de Fleiss (Landis & Koch):")
    for k in result["kappas"]:
        print(
            f"  {k.dimensao}: κ={k.kappa:.4f} → {k.interpretacao} "
            f"(n={k.n_itens})"
        )
    print(f"Consenso → {result['consenso_csv']}")
    print(f"Divergentes → {result['divergentes_csv']} ({result['n_divergentes']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

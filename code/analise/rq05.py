"""RQ05 — correlação de Spearman entre frequência de deploy e CFR.

A pergunta é se velocidade e estabilidade são um trade-off (quem entrega mais quebra
mais?). A pesquisa DORA sugere que NÃO: times de elite entregam mais e também falham
menos. Testamos isso no nosso conjunto medindo a correlação de posto (Spearman) entre
`deploys_por_semana` e cada variante do CFR, separadamente.

DECISÕES
  * Spearman (posto), não Pearson: as duas distribuições são assimétricas e não há motivo
    para supor relação linear.
  * Repositórios sem CFR (variante indisponível, ex.: nenhuma run válida para `cfr_a`) são
    EXCLUÍDOS do par e contados em `n_excluidos`. Sem contagem explícita, o número da
    correlação esconde quem ficou de fora.
  * Correlação NÃO IMPLICA CAUSALIDADE. Mesmo ρ muito negativo não prova que entregar mais
    causa menos falhas; pode ser que times maduros tenham AS DUAS coisas.
  * Variante como parâmetro (`Variante` dataclass + `VARIANTES_PADRAO`): a RQ07 pode trocar
    `cfr_b` por `cfr_b_pre` sem copiar código.

Reexecutável a partir do CSV (nada é editado à mão):
    python -m analise.rq05 CAMINHO_DO_DATASET.csv --saida ../docs/s03
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd
from scipy.stats import spearmanr


@dataclass(frozen=True)
class Variante:
    """Uma variante do CFR, pareada com a coluna de frequência correspondente."""

    nome: str
    coluna_cfr: str
    coluna_deploys: str


VARIANTES_PADRAO: tuple[Variante, ...] = (
    Variante("a", "cfr_a", "deploys_por_semana"),
    Variante("b", "cfr_b", "deploys_por_semana"),
)


@dataclass(frozen=True)
class ResultadoCorrelacao:
    variante: str
    rho: Optional[float]
    p_valor: Optional[float]
    n: int                 # pares (deploys, cfr) com os dois valores presentes
    n_excluidos: int       # linhas com cfr ausente nessa variante
    nota: str


@dataclass(frozen=True)
class ResultadoRQ05:
    n_total: int
    correlacoes: tuple[ResultadoCorrelacao, ...]


def calcular_spearman(df: pd.DataFrame, var: Variante) -> ResultadoCorrelacao:
    """Spearman entre `var.coluna_deploys` e `var.coluna_cfr`, excluindo CFR ausente."""
    faltando = [c for c in (var.coluna_cfr, var.coluna_deploys) if c not in df.columns]
    if faltando:
        raise KeyError(f"variante {var.nome}: coluna(s) ausente(s) no CSV: {', '.join(faltando)}")

    pares = df[[var.coluna_deploys, var.coluna_cfr]].apply(pd.to_numeric, errors="coerce").dropna()
    n = len(pares)
    n_excluidos = len(df) - n

    if n < 3:
        return ResultadoCorrelacao(
            var.nome, None, None, n, n_excluidos,
            "n < 3 após excluir CFR ausente: Spearman indefinido",
        )
    deploys = pares[var.coluna_deploys].to_numpy()
    cfr = pares[var.coluna_cfr].to_numpy()
    if len(set(deploys)) < 2 or len(set(cfr)) < 2:
        return ResultadoCorrelacao(
            var.nome, None, None, n, n_excluidos,
            "sem variância em uma das colunas: Spearman indefinido",
        )
    r = spearmanr(deploys, cfr)
    return ResultadoCorrelacao(var.nome, float(r.correlation), float(r.pvalue), n, n_excluidos, "")


def analisar(df: pd.DataFrame, variantes: Sequence[Variante] = VARIANTES_PADRAO) -> ResultadoRQ05:
    correlacoes = tuple(calcular_spearman(df, v) for v in variantes)
    return ResultadoRQ05(len(df), correlacoes)


def filtrar_completos(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Igual ao usado nas outras RQs: só linhas com `lacunas_de_coleta == 0`."""
    if "lacunas_de_coleta" not in df.columns:
        return df, 0
    lacunas = pd.to_numeric(df["lacunas_de_coleta"], errors="coerce")
    completos = df[lacunas == 0]
    return completos, len(df) - len(completos)


def _parse_variantes(spec: str) -> tuple[Variante, ...]:
    if spec == "ambas":
        return VARIANTES_PADRAO
    for v in VARIANTES_PADRAO:
        if v.nome == spec:
            return (v,)
    raise ValueError(f"variante desconhecida: {spec!r} (use a, b ou ambas)")


def _num(x: Optional[float], casas: int = 4) -> str:
    return "—" if x is None else f"{x:.{casas}f}"


def relatorio_md(res: ResultadoRQ05, nota: str = "") -> str:
    linhas = [
        "# RQ05 — Frequência de deploy × CFR (Spearman)",
        "",
        f"Repositórios no CSV: **{res.n_total}**. Para cada variante do CFR, correlação de "
        "posto (Spearman) entre `deploys_por_semana` e a coluna de CFR correspondente. "
        "Repositórios com CFR ausente nessa variante são excluídos e contados.",
        "",
        "Correlação **não implica causalidade**: um ρ negativo não prova que entregar com "
        "frequência cause menos falhas, nem o contrário. Pode ser efeito de maturidade de "
        "engenharia (times melhores têm as duas coisas) ou de confusores não medidos.",
        "",
    ] + ([nota, ""] if nota else []) + [
        "## Correlação por variante",
        "",
        "| variante | n | n excluídos (CFR ausente) | ρ Spearman | p-valor | nota |",
        "|---|---|---|---|---|---|",
    ]
    for c in res.correlacoes:
        linhas.append(
            f"| {c.variante} | {c.n} | {c.n_excluidos} | {_num(c.rho)} | {_num(c.p_valor)} | {c.nota or '—'} |"
        )
    linhas += [
        "",
        "Interpretação usual (Cohen, 1988): |ρ| < 0,10 trivial; 0,10–0,30 pequena; "
        "0,30–0,50 moderada; > 0,50 grande. É convenção, não regra.",
        "",
    ]
    return "\n".join(linhas)


def gravar_csv(res: ResultadoRQ05, destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variante", "n", "n_excluidos", "rho_spearman", "p_valor", "nota"])
        for c in res.correlacoes:
            w.writerow([
                c.variante,
                c.n,
                c.n_excluidos,
                "" if c.rho is None else f"{c.rho:.6f}",
                "" if c.p_valor is None else f"{c.p_valor:.6f}",
                c.nota,
            ])
    return destino


def plotar(df: pd.DataFrame, res: ResultadoRQ05, pasta: Path) -> list[Path]:
    """Scatter log-x por variante, salvo em um PNG com um painel por variante."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)

    fig, eixos = plt.subplots(1, len(res.correlacoes), figsize=(5 * len(res.correlacoes), 4), squeeze=False)
    for ax, c in zip(eixos[0], res.correlacoes):
        var = next(v for v in VARIANTES_PADRAO if v.nome == c.variante)
        pares = df[[var.coluna_deploys, var.coluna_cfr]].apply(pd.to_numeric, errors="coerce").dropna()
        ax.scatter(pares[var.coluna_deploys], pares[var.coluna_cfr], alpha=0.6, color="#1f77b4")
        if (pares[var.coluna_deploys] > 0).all():
            ax.set_xscale("log")
            ax.set_xlabel(var.coluna_deploys + " (log)")
        else:
            ax.set_xlabel(var.coluna_deploys)
        ax.set_ylabel(var.coluna_cfr)
        rho = "—" if c.rho is None else f"{c.rho:.2f}"
        p = "—" if c.p_valor is None else f"{c.p_valor:.3f}"
        ax.set_title(f"Variante {c.variante} (ρ = {rho}, p = {p}, n = {c.n})")
    fig.tight_layout()
    arquivo = pasta / "rq05_scatter.png"
    fig.savefig(arquivo, dpi=150)
    plt.close(fig)
    return [arquivo]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="RQ05: Spearman entre deploys e CFR")
    ap.add_argument("csv", help="dataset (docs/dicionario_dados.md descreve as colunas)")
    ap.add_argument("--saida", default="docs/s03", help="pasta de saída (rq05.md, CSV e figura)")
    ap.add_argument("--variante", choices=("a", "b", "ambas"), default="ambas")
    ap.add_argument("--sem-figuras", action="store_true")
    ap.add_argument(
        "--incluir-incompletos", action="store_true",
        help="NÃO filtra linhas com lacunas_de_coleta > 0 (os números ficam sobre dado cortado)",
    )
    args = ap.parse_args(argv)

    df = pd.read_csv(args.csv)
    n_lido = len(df)
    if args.incluir_incompletos:
        nota = "**ATENÇÃO:** linhas com coleta incompleta (`lacunas_de_coleta` > 0) foram INCLUÍDAS."
    else:
        df, excluidos = filtrar_completos(df)
        if "lacunas_de_coleta" not in df.columns:
            nota = "**ATENÇÃO:** o CSV não tem `lacunas_de_coleta`; não foi possível excluir linhas incompletas."
        else:
            nota = (f"Linhas no arquivo: {n_lido}; analisadas: {len(df)}; excluídas por coleta incompleta "
                    f"(`lacunas_de_coleta` > 0): **{excluidos}**.")
        if df.empty:
            print("ERRO: nenhuma linha completa (lacunas_de_coleta == 0) no CSV. Termine a coleta ou use "
                  "--incluir-incompletos.")
            return 2
    res = analisar(df, _parse_variantes(args.variante))
    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    texto = relatorio_md(res, nota)
    (pasta / "rq05.md").write_text(texto, encoding="utf-8", newline="\n")
    gravar_csv(res, pasta / "rq05_correlacoes.csv")
    if not args.sem_figuras:
        try:
            plotar(df, res, pasta)
        except ImportError:
            print("AVISO: matplotlib não instalado, figuras não geradas (pip install matplotlib).")
    print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""RQ06 — fatores do repositório × métricas DORA, com Kruskal-Wallis + Holm + tamanho de efeito.

A pergunta é se características do repositório (linguagem, estrelas, contribuidores, idade)
mudam o que vemos nas 4 métricas DORA (frequência, lead time, CFR, recuperação).

DECISÕES
  * Teste não paramétrico (Kruskal-Wallis para 3+ grupos, Mann-Whitney para 2) porque as
    distribuições das métricas são assimétricas (lognormais). O teste paramétrico t/ANOVA
    assumiria normalidade e seria frágil.
  * Correção de Holm (`statsmodels.stats.multitest.multipletests(method="holm")`) para o
    conjunto de todos os testes do run. 16 testes com α = 0,05 sem correção dariam
    ~0,8 falso positivo esperado só por acaso.
  * Tamanho de efeito separado do p-valor:
      - ε² = H / (n − 1) para Kruskal-Wallis. Interpretação (Tomczak & Tomczak, 2014):
        < 0,01 trivial; 0,01–0,06 pequeno; 0,06–0,14 moderado; > 0,14 grande.
      - δ de Cliff = 2·U/(n₁·n₂) − 1 para Mann-Whitney. Romano et al. (2006):
        |δ| < 0,147 trivial; 0,147–0,33 pequeno; 0,33–0,474 moderado; > 0,474 grande.
    p-valor diz se o efeito é plausivelmente ≠ 0; tamanho de efeito diz se é grande o
    bastante para importar.
  * Fatores contínuos (estrelas, contribuidores, idade) viram quartis. Quartis são
    reprodutíveis a partir do CSV e lidam com cauda longa melhor que faixas fixas.
  * Linguagens raras (< MIN_POR_GRUPO repos) são agrupadas em "Outros". Sem isso, um
    teste pode falhar por grupo com n = 1 em vez de por falta de diferença real.
  * NaN nas métricas excluem a linha do teste da métrica (não imputamos).

Reexecutável a partir do CSV (nada é editado à mão):
    python -m analise.rq06 CAMINHO_DO_DATASET.csv --saida ../docs/s03
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.stats import kruskal, mannwhitneyu
from statsmodels.stats.multitest import multipletests


MIN_POR_GRUPO = 3              # grupos com menos entram em "Outros" (línguas) ou são removidos
AGRUPADOR_OUTROS = "Outros"

METRICAS: tuple[str, ...] = (
    "deploys_por_semana",
    "lead_time_a_horas",
    "cfr_a",
    "recuperacao_mediana_horas",
)


Agrupador = Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True)
class Fator:
    """Um fator a testar contra as métricas. O agrupador recebe o DF e devolve uma Series
    com o rótulo de grupo por linha (NaN para linhas que não devem entrar no teste)."""

    nome: str
    agrupador: Agrupador


@dataclass(frozen=True)
class ResultadoTeste:
    fator: str
    metrica: str
    teste: str                    # "kruskal-wallis" | "mann-whitney" | "indeterminado"
    k_grupos: int                 # 0 quando indeterminado
    n: int
    estatistica: Optional[float]  # H (kruskal) ou U (mann-whitney)
    p_bruto: Optional[float]
    p_holm: Optional[float]
    efeito_tipo: str              # "epsilon2" | "cliff_delta" | ""
    efeito_valor: Optional[float]
    efeito_classe: str            # "trivial" | "pequeno" | "moderado" | "grande" | ""
    nota: str


@dataclass(frozen=True)
class ResultadoRQ06:
    n_total: int
    fatores: tuple[str, ...]
    metricas: tuple[str, ...]
    testes: tuple[ResultadoTeste, ...]
    minimo_por_grupo: int


# ------------------------------------------------------------ agrupadores padrão

def _idade_anos(df: pd.DataFrame, referencia: Optional[datetime] = None) -> pd.Series:
    if "criado_em" not in df.columns:
        return pd.Series([float("nan")] * len(df), index=df.index)
    ref = referencia or datetime.now(timezone.utc)
    datas = pd.to_datetime(df["criado_em"], utc=True, errors="coerce")
    return (ref - datas).dt.total_seconds() / (365.25 * 86400)


def _quartis(valores: pd.Series) -> pd.Series:
    """Rotula cada valor com Q1/Q2/Q3/Q4 por quartil, com NaN preservado."""
    v = pd.to_numeric(valores, errors="coerce")
    if v.dropna().nunique() < 2:
        return pd.Series([float("nan")] * len(v), index=v.index, dtype=object)
    rotulos = ["Q1", "Q2", "Q3", "Q4"]
    try:
        cat = pd.qcut(v, 4, labels=rotulos, duplicates="drop")
    except ValueError:
        return pd.Series([float("nan")] * len(v), index=v.index, dtype=object)
    return cat.astype(object).where(~v.isna(), float("nan"))


def _linguagem(df: pd.DataFrame, minimo: int = MIN_POR_GRUPO) -> pd.Series:
    """Agrupa línguas com menos de `minimo` repos em 'Outros'."""
    if "linguagem_principal" not in df.columns:
        return pd.Series([float("nan")] * len(df), index=df.index)
    linguas = df["linguagem_principal"].astype(object)
    contagem = linguas.value_counts(dropna=True)
    frequentes = set(contagem[contagem >= minimo].index)
    return linguas.where(linguas.isin(frequentes), AGRUPADOR_OUTROS).where(linguas.notna(), float("nan"))


def fatores_padrao(referencia: Optional[datetime] = None) -> tuple[Fator, ...]:
    """Quatro fatores: linguagem, quartis de estrelas, quartis de contribuidores, quartis de idade."""
    return (
        Fator("linguagem_principal", _linguagem),
        Fator("estrelas_q", lambda df: _quartis(df.get("estrelas", pd.Series(dtype=float)))),
        Fator("contribuidores_q", lambda df: _quartis(df.get("n_contribuidores", pd.Series(dtype=float)))),
        Fator("idade_anos_q", lambda df: _quartis(_idade_anos(df, referencia))),
    )


# ------------------------------------------------------------ efeito de tamanho

def epsilon_squared(h: float, n: int) -> Optional[float]:
    """ε² para Kruskal-Wallis. Devolve None quando n ≤ 1."""
    if n <= 1:
        return None
    return float(h / (n - 1))


def cliff_delta(x: Sequence[float], y: Sequence[float]) -> float:
    """δ de Cliff a partir das amostras x e y: 2·U/(n₁·n₂) − 1 (sinal: x > y → δ > 0)."""
    xa = np.asarray(list(x), dtype=float)
    ya = np.asarray(list(y), dtype=float)
    maior = 0
    menor = 0
    for xi in xa:
        maior += int((xi > ya).sum())
        menor += int((xi < ya).sum())
    total = len(xa) * len(ya)
    if total == 0:
        return 0.0
    return (maior - menor) / total


def classe_epsilon2(valor: Optional[float]) -> str:
    if valor is None:
        return ""
    v = abs(valor)
    if v < 0.01:
        return "trivial"
    if v < 0.06:
        return "pequeno"
    if v < 0.14:
        return "moderado"
    return "grande"


def classe_cliff(valor: Optional[float]) -> str:
    if valor is None:
        return ""
    v = abs(valor)
    if v < 0.147:
        return "trivial"
    if v < 0.33:
        return "pequeno"
    if v < 0.474:
        return "moderado"
    return "grande"


# ------------------------------------------------------------ testes

def _grupos_validos(valores: pd.Series, grupos: pd.Series, minimo: int) -> list[tuple[str, np.ndarray]]:
    """Lista de (rótulo, valores) com grupo ≥ `minimo` repos e todas as métricas presentes."""
    df = pd.DataFrame({"g": grupos, "v": pd.to_numeric(valores, errors="coerce")}).dropna()
    resultado: list[tuple[str, np.ndarray]] = []
    for rotulo, parte in df.groupby("g"):
        if len(parte) >= minimo:
            resultado.append((str(rotulo), parte["v"].to_numpy()))
    return resultado


def um_teste(df: pd.DataFrame, fator: Fator, metrica: str, minimo: int = MIN_POR_GRUPO) -> ResultadoTeste:
    if metrica not in df.columns:
        return ResultadoTeste(fator.nome, metrica, "indeterminado", 0, 0, None, None, None,
                              "", None, "", f"coluna {metrica!r} ausente no CSV")
    grupos = fator.agrupador(df)
    partes = _grupos_validos(df[metrica], grupos, minimo)
    k = len(partes)
    n = sum(len(v) for _, v in partes)

    if k < 2:
        return ResultadoTeste(fator.nome, metrica, "indeterminado", k, n, None, None, None,
                              "", None, "", f"menos de 2 grupos com n ≥ {minimo}")

    if k == 2:
        (_, x), (_, y) = partes
        if len(set(x)) < 2 and len(set(y)) < 2:
            return ResultadoTeste(fator.nome, metrica, "mann-whitney", k, n, None, None, None,
                                  "", None, "", "sem variância em ambos os grupos")
        u, p = mannwhitneyu(x, y, alternative="two-sided")
        delta = cliff_delta(x, y)
        return ResultadoTeste(fator.nome, metrica, "mann-whitney", k, n,
                              float(u), float(p), None, "cliff_delta", float(delta), classe_cliff(delta), "")

    amostras = [v for _, v in partes]
    if all(len(set(v)) < 2 for v in amostras):
        return ResultadoTeste(fator.nome, metrica, "kruskal-wallis", k, n, None, None, None,
                              "", None, "", "sem variância em nenhum grupo")
    h, p = kruskal(*amostras)
    eps = epsilon_squared(float(h), n)
    return ResultadoTeste(fator.nome, metrica, "kruskal-wallis", k, n,
                          float(h), float(p), None, "epsilon2", eps, classe_epsilon2(eps), "")


def analisar(
    df: pd.DataFrame,
    fatores: Optional[Sequence[Fator]] = None,
    metricas: Sequence[str] = METRICAS,
    minimo: int = MIN_POR_GRUPO,
    referencia: Optional[datetime] = None,
) -> ResultadoRQ06:
    fatores = tuple(fatores) if fatores is not None else fatores_padrao(referencia)
    bruto = [um_teste(df, f, m, minimo) for f in fatores for m in metricas]
    com_p = [i for i, t in enumerate(bruto) if t.p_bruto is not None]
    if com_p:
        ps = [bruto[i].p_bruto for i in com_p]
        _, p_corrig, _, _ = multipletests(ps, method="holm")
        for i, p_h in zip(com_p, p_corrig):
            t = bruto[i]
            bruto[i] = ResultadoTeste(
                t.fator, t.metrica, t.teste, t.k_grupos, t.n, t.estatistica, t.p_bruto, float(p_h),
                t.efeito_tipo, t.efeito_valor, t.efeito_classe, t.nota,
            )
    return ResultadoRQ06(
        n_total=len(df),
        fatores=tuple(f.nome for f in fatores),
        metricas=tuple(metricas),
        testes=tuple(bruto),
        minimo_por_grupo=minimo,
    )


def filtrar_completos(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if "lacunas_de_coleta" not in df.columns:
        return df, 0
    lacunas = pd.to_numeric(df["lacunas_de_coleta"], errors="coerce")
    completos = df[lacunas == 0]
    return completos, len(df) - len(completos)


# ------------------------------------------------------------ relatório / CSV

def _num(x: Optional[float], casas: int = 4) -> str:
    return "—" if x is None else f"{x:.{casas}f}"


def relatorio_md(res: ResultadoRQ06, nota: str = "") -> str:
    linhas = [
        "# RQ06 — Fatores do repositório × métricas DORA",
        "",
        f"Repositórios no CSV: **{res.n_total}**. Para cada fator × métrica, teste não "
        "paramétrico (Kruskal-Wallis com 3+ grupos ou Mann-Whitney com 2) + correção de "
        "Holm sobre todos os p-valores + tamanho de efeito (ε² ou δ de Cliff).",
        "",
        f"Grupos com n < {res.minimo_por_grupo} são agrupados em `Outros` (linguagem) ou "
        "ignorados (quartis), para evitar \"significância\" por grupo com 1 repo.",
        "",
    ] + ([nota, ""] if nota else []) + [
        "## Testes",
        "",
        "| fator | métrica | teste | k | n | estatística | p bruto | p Holm | efeito | valor | classe | nota |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in res.testes:
        linhas.append(
            f"| {t.fator} | {t.metrica} | {t.teste} | {t.k_grupos} | {t.n} | "
            f"{_num(t.estatistica, 3)} | {_num(t.p_bruto)} | {_num(t.p_holm)} | "
            f"{t.efeito_tipo or '—'} | {_num(t.efeito_valor)} | {t.efeito_classe or '—'} | {t.nota or '—'} |"
        )
    linhas += [
        "",
        "ε² (Tomczak & Tomczak, 2014): < 0,01 trivial; < 0,06 pequeno; < 0,14 moderado; "
        "≥ 0,14 grande. δ de Cliff (Romano et al., 2006): < 0,147 trivial; < 0,33 pequeno; "
        "< 0,474 moderado; ≥ 0,474 grande. p-valor diz se o efeito é ≠ 0; o tamanho diz "
        "se é relevante.",
        "",
    ]
    return "\n".join(linhas)


def gravar_csv(res: ResultadoRQ06, destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "fator", "metrica", "teste", "k_grupos", "n", "estatistica",
            "p_bruto", "p_holm", "efeito_tipo", "efeito_valor", "efeito_classe", "nota",
        ])
        for t in res.testes:
            w.writerow([
                t.fator, t.metrica, t.teste, t.k_grupos, t.n,
                "" if t.estatistica is None else f"{t.estatistica:.6f}",
                "" if t.p_bruto is None else f"{t.p_bruto:.6f}",
                "" if t.p_holm is None else f"{t.p_holm:.6f}",
                t.efeito_tipo,
                "" if t.efeito_valor is None else f"{t.efeito_valor:.6f}",
                t.efeito_classe,
                t.nota,
            ])
    return destino


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="RQ06: fatores × métricas (Kruskal-Wallis + Holm)")
    ap.add_argument("csv", help="dataset (docs/dicionario_dados.md descreve as colunas)")
    ap.add_argument("--saida", default="docs/s03", help="pasta de saída (rq06.md e rq06_testes.csv)")
    ap.add_argument("--minimo-por-grupo", type=int, default=MIN_POR_GRUPO,
                    help=f"grupos com menos entram em Outros/são removidos (padrão {MIN_POR_GRUPO})")
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
    res = analisar(df, minimo=args.minimo_por_grupo)
    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    texto = relatorio_md(res, nota)
    (pasta / "rq06.md").write_text(texto, encoding="utf-8", newline="\n")
    gravar_csv(res, pasta / "rq06_testes.csv")
    print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""RQ07 — o quanto a classificação DORA depende da definição operacional escolhida.

Para cada combinação de definições (C1, C2, C3...), classifica cada repositório em
Elite/High/Medium/Low e compara as combinações duas a duas: % de repositórios que mudam
de categoria e kappa de Cohen ponderado (linear), que pesa menos uma troca Elite→High do
que Elite→Low porque as categorias são ordenadas.

Reexecutável a partir do CSV (nada é editado à mão):
    python -m analise.rq07 CAMINHO_DO_DATASET.csv --saida ../docs/s03

DECISÕES QUE O GRUPO PRECISA REGISTRAR
  * C3 padrão = release, lead time (b), CFR (a): usa só variantes já coletadas. O exemplo
    do enunciado usa TAGS como unidade de deploy; se o grupo coletar tags, troque as
    colunas de C3 em COMBINACOES_PADRAO (o resto da análise não muda). Sem tags, isso é um
    desvio do exemplo do enunciado e deve ser justificado no artigo.
  * Repositório sem alguma das 4 métricas (ex.: sem episódio de falha, logo sem tempo de
    recuperação) NÃO é classificável naquela combinação e fica fora dos pares em que ela
    aparece. Os n são reportados para a exclusão ficar visível, nunca silenciosa.
  * O classificador é o do Caio (`metricas.classificacao_dora.classificar_dora`), com a
    assinatura combinada: (deploys_por_semana, lead_time_horas, cfr, recuperacao_horas)
    -> "Elite" | "High" | "Medium" | "Low".
"""
from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import pandas as pd
from sklearn.metrics import cohen_kappa_score

CATEGORIAS = ("Low", "Medium", "High", "Elite")  # ordem crescente: define as distâncias do kappa linear

Classificador = Callable[[float, float, float, float], str]


@dataclass(frozen=True)
class Combinacao:
    nome: str
    deploys: str
    lead_time: str
    cfr: str
    recuperacao: str

    def colunas(self) -> list[str]:
        return [self.deploys, self.lead_time, self.cfr, self.recuperacao]


COMBINACOES_PADRAO: tuple[Combinacao, ...] = (
    Combinacao("C1", "deploys_por_semana", "lead_time_a_horas", "cfr_a", "recuperacao_mediana_horas"),
    Combinacao("C2", "deploys_por_semana_pre", "lead_time_b_horas_pre", "cfr_b_pre", "recuperacao_mediana_horas"),
    Combinacao("C3", "deploys_por_semana", "lead_time_b_horas", "cfr_a", "recuperacao_mediana_horas"),
)


@dataclass(frozen=True)
class ComparacaoPar:
    a: str
    b: str
    n: int                              # repositórios classificáveis nas DUAS combinações
    pct_mudou: Optional[float]          # 0–1
    kappa_ponderado: Optional[float]
    nota: str
    matriz: dict[str, dict[str, int]]   # matriz[categoria_em_a][categoria_em_b]


@dataclass(frozen=True)
class ResultadoRQ07:
    combinacoes: tuple[str, ...]
    n_total: int
    n_classificados: dict[str, int]
    distribuicao: dict[str, dict[str, int]]
    pares: tuple[ComparacaoPar, ...]


def _classificador_padrao() -> Classificador:
    try:
        from metricas.classificacao_dora import classificar_dora  # type: ignore[import-not-found]
    except ImportError as erro:
        raise RuntimeError(
            "metricas/classificacao_dora.py (função classificar_dora) ainda não existe neste "
            "repositório: ele é entregue pelo Caio. Passe `classificador=` explicitamente para testar."
        ) from erro
    return classificar_dora


def classificar_combinacao(df: pd.DataFrame, comb: Combinacao, classificador: Classificador) -> pd.Series:
    """Categoria de cada repositório; None quando alguma das 4 métricas está ausente."""
    faltando = [c for c in comb.colunas() if c not in df.columns]
    if faltando:
        raise KeyError(f"combinação {comb.nome}: coluna(s) ausente(s) no CSV: {', '.join(faltando)}")

    def uma_linha(linha: pd.Series) -> Optional[str]:
        valores = [linha[c] for c in comb.colunas()]
        if any(pd.isna(v) for v in valores):
            return None
        categoria = classificador(*[float(v) for v in valores])
        if categoria not in CATEGORIAS:
            raise ValueError(f"classificador devolveu {categoria!r}; esperado um de {list(CATEGORIAS)}")
        return categoria

    return df.apply(uma_linha, axis=1).astype(object)


def comparar_par(cls_a: pd.Series, cls_b: pd.Series, nome_a: str, nome_b: str) -> ComparacaoPar:
    ambos = pd.DataFrame({"a": cls_a, "b": cls_b}).dropna()
    n = len(ambos)
    matriz = {
        ca: {cb: int(((ambos["a"] == ca) & (ambos["b"] == cb)).sum()) for cb in CATEGORIAS}
        for ca in CATEGORIAS
    }
    if n == 0:
        return ComparacaoPar(nome_a, nome_b, 0, None, None,
                             "nenhum repositório classificável nas duas combinações", matriz)

    pct = float((ambos["a"] != ambos["b"]).mean())
    if ambos["a"].nunique() == 1 and ambos["b"].nunique() == 1 and ambos["a"].iloc[0] == ambos["b"].iloc[0]:
        return ComparacaoPar(nome_a, nome_b, n, pct, None,
                             "sem variância (todos na mesma categoria nas duas combinações): kappa indefinido",
                             matriz)

    kappa = float(cohen_kappa_score(ambos["a"], ambos["b"], labels=list(CATEGORIAS), weights="linear"))
    return ComparacaoPar(nome_a, nome_b, n, pct, kappa, "", matriz)


def analisar(
    df: pd.DataFrame,
    combinacoes: Sequence[Combinacao] = COMBINACOES_PADRAO,
    classificador: Optional[Classificador] = None,
) -> ResultadoRQ07:
    classificador = classificador or _classificador_padrao()
    classes = {c.nome: classificar_combinacao(df, c, classificador) for c in combinacoes}
    nomes = tuple(classes)

    distribuicao = {
        nome: {cat: int((serie == cat).sum()) for cat in CATEGORIAS} for nome, serie in classes.items()
    }
    n_classificados = {nome: int(serie.notna().sum()) for nome, serie in classes.items()}
    pares = tuple(comparar_par(classes[a], classes[b], a, b) for a, b in itertools.combinations(nomes, 2))
    return ResultadoRQ07(nomes, len(df), n_classificados, distribuicao, pares)


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def _num(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:.3f}"


def filtrar_completos(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Mantém só as linhas com `lacunas_de_coleta == 0` e devolve quantas foram excluídas.

    Linha com lacunas tem partes da coleta que nunca foram feitas (meses de workflow runs,
    pares de compare...), então CFR, recuperação e lead time sairiam calculados sobre dado
    cortado. Valor vazio é desconhecido, e desconhecido não é zero: é excluído. Sem a coluna
    (dataset antigo), nada é filtrado."""
    if "lacunas_de_coleta" not in df.columns:
        return df, 0
    lacunas = pd.to_numeric(df["lacunas_de_coleta"], errors="coerce")
    completos = df[lacunas == 0]
    return completos, len(df) - len(completos)


def relatorio_md(res: ResultadoRQ07, nota: str = "") -> str:
    linhas = [
        "# RQ07 — Sensibilidade da classificação DORA à definição operacional",
        "",
        f"Repositórios no CSV: **{res.n_total}**. Um repositório sem alguma das 4 métricas não é "
        "classificável naquela combinação e fica de fora dos pares em que ela aparece.",
        "",
    ] + ([nota, ""] if nota else []) + [
        "## Distribuição por combinação",
        "",
        "| Combinação | classificáveis | " + " | ".join(reversed(CATEGORIAS)) + " |",
        "|---|---|" + "---|" * len(CATEGORIAS),
    ]
    for nome in res.combinacoes:
        d = res.distribuicao[nome]
        linhas.append(f"| {nome} | {res.n_classificados[nome]} | " + " | ".join(str(d[c]) for c in reversed(CATEGORIAS)) + " |")

    linhas += ["", "## Comparação entre pares", "",
               "| Par | n (nos dois) | % que mudou de categoria | kappa ponderado (linear) | nota |",
               "|---|---|---|---|---|"]
    for p in res.pares:
        linhas.append(f"| {p.a} × {p.b} | {p.n} | {_pct(p.pct_mudou)} | {_num(p.kappa_ponderado)} | {p.nota or '—'} |")

    linhas += ["", "Interpretação usual do kappa (Landis & Koch, 1977): < 0,20 fraca; 0,21–0,40 razoável; "
               "0,41–0,60 moderada; 0,61–0,80 substancial; > 0,80 quase perfeita. É convenção, não regra.", ""]
    return "\n".join(linhas)


def plotar(res: ResultadoRQ07, pasta: Path) -> list[Path]:
    """Duas figuras: distribuição das categorias por combinação e matrizes de confusão dos pares."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    cores = {"Elite": "#2ca02c", "High": "#8fd175", "Medium": "#f0b64a", "Low": "#d9603b"}

    fig, ax = plt.subplots(figsize=(6, 4))
    base = [0] * len(res.combinacoes)
    for cat in reversed(CATEGORIAS):
        valores = [res.distribuicao[n][cat] for n in res.combinacoes]
        ax.bar(res.combinacoes, valores, bottom=base, label=cat, color=cores[cat])
        base = [b + v for b, v in zip(base, valores)]
    ax.set_ylabel("Repositórios")
    ax.set_title("Categoria DORA por combinação de definições")
    ax.legend(title="Categoria")
    fig.tight_layout()
    f1 = pasta / "rq07_distribuicao.png"
    fig.savefig(f1, dpi=150)
    plt.close(fig)

    fig, eixos = plt.subplots(1, len(res.pares), figsize=(4.2 * len(res.pares), 4), squeeze=False)
    for ax, par in zip(eixos[0], res.pares):
        grade = [[par.matriz[ca][cb] for cb in CATEGORIAS] for ca in CATEGORIAS]
        im = ax.imshow(grade, cmap="Blues")
        ax.set_xticks(range(len(CATEGORIAS)), CATEGORIAS, rotation=45)
        ax.set_yticks(range(len(CATEGORIAS)), CATEGORIAS)
        ax.set_xlabel(par.b)
        ax.set_ylabel(par.a)
        k = "—" if par.kappa_ponderado is None else f"{par.kappa_ponderado:.2f}"
        ax.set_title(f"{par.a} × {par.b} (κ = {k})")
        for i in range(len(CATEGORIAS)):
            for j in range(len(CATEGORIAS)):
                ax.text(j, i, grade[i][j], ha="center", va="center", fontsize=9)
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    f2 = pasta / "rq07_matrizes.png"
    fig.savefig(f2, dpi=150)
    plt.close(fig)
    return [f1, f2]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="RQ07: sensibilidade da classificação DORA")
    ap.add_argument("csv", help="dataset (docs/dicionario_dados.md descreve as colunas)")
    ap.add_argument("--saida", default="docs/s03", help="pasta de saída (rq07.md e figuras)")
    ap.add_argument("--sem-figuras", action="store_true")
    ap.add_argument("--incluir-incompletos", action="store_true",
                    help="NÃO filtra linhas com lacunas_de_coleta > 0 (os números ficam sobre dado cortado)")
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
    res = analisar(df)
    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    texto = relatorio_md(res, nota)
    (pasta / "rq07.md").write_text(texto, encoding="utf-8", newline="\n")
    if not args.sem_figuras:
        try:
            plotar(res, pasta)
        except ImportError:
            print("AVISO: matplotlib não instalado, figuras não geradas (pip install matplotlib).")
    print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

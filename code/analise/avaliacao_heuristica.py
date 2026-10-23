"""Avaliação da heurística corretiva (CFR variante b) contra o consenso da amostra-ouro.

Para cada release rotulada no consenso (Issue #27), comparamos o rótulo humano
(`eh_corretiva ∈ {sim, nao}`) com o que a heurística diria sobre a MESMA release. Com os
dois rótulos lado a lado por (full_name, release_tag), calculamos precisão, revocação,
F1 e acurácia (sklearn `precision_recall_fscore_support`) tratando "sim" como classe
positiva.

DECISÕES
  * F1 (classe "sim") é a métrica principal: errar POUCAS releases corretivas (recall alto)
    com POUCOS falsos positivos (precisão alta) é o que torna a heurística útil para o CFR.
    O alvo do enunciado é F1 ≥ 0,70; abaixo disso, a heurística deve ser refinada.
  * Comparação é por (full_name, release_tag): releases que o consenso rotulou mas que a
    heurística não cobre (ex.: nunca houve release seguinte dentro de 7 dias) ficam
    contadas em `n_sem_heuristica` e saem da conta de precisão/revocação.
  * `gravar_csv_cumulativo` acrescenta uma linha nova a cada rodada: a tabela do artigo
    sai do próprio arquivo (v1 → v2 → v3...), nada é editado à mão.
  * Rótulos aceitos: "sim" e "nao" (case-insensitive, strip). Qualquer outra string vira
    erro com mensagem clara em vez de ser silenciosamente classificada como "nao".

Reexecutável:
    python -m analise.avaliacao_heuristica \\
        --consenso ../validacao/consenso.csv \\
        --heuristica ../validacao/heuristica_v1.csv \\
        --nome v1 --saida ../docs/s03
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

ROTULO_POSITIVO = "sim"
ROTULOS_VALIDOS = frozenset({"sim", "nao"})
F1_MINIMO_RECOMENDADO = 0.70

COLUNAS_CSV_SAIDA = (
    "heuristica", "n_total", "n_pareadas", "n_sem_heuristica",
    "precisao", "revocacao", "f1", "acuracia",
    "vp", "fp", "vn", "fn", "nota",
)


@dataclass(frozen=True)
class ResultadoAvaliacao:
    heuristica: str
    n_total: int               # releases no consenso
    n_pareadas: int            # releases com rótulo nas duas fontes
    n_sem_heuristica: int      # releases no consenso que a heurística não cobre
    precisao: Optional[float]
    revocacao: Optional[float]
    f1: Optional[float]
    acuracia: Optional[float]
    vp: int                    # verdadeiros positivos (sim/sim)
    fp: int                    # falsos positivos  (heurística disse sim, consenso disse nao)
    vn: int                    # verdadeiros negativos (nao/nao)
    fn: int                    # falsos negativos (heurística disse nao, consenso disse sim)
    nota: str


def _normalizar(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().str.lower()


def _validar(serie: pd.Series, fonte: str) -> None:
    invalidos = sorted(set(serie.dropna().unique()) - ROTULOS_VALIDOS)
    if invalidos:
        raise ValueError(
            f"{fonte}: rótulos inválidos {invalidos!r} em `eh_corretiva` (esperado 'sim' ou 'nao')"
        )


def _validar_colunas(df: pd.DataFrame, fonte: str) -> None:
    faltando = [c for c in ("full_name", "release_tag", "eh_corretiva") if c not in df.columns]
    if faltando:
        raise KeyError(f"{fonte}: coluna(s) ausente(s): {', '.join(faltando)}")


def avaliar(
    df_heuristica: pd.DataFrame,
    df_consenso: pd.DataFrame,
    nome_heuristica: str = "v1",
    f1_minimo: float = F1_MINIMO_RECOMENDADO,
) -> ResultadoAvaliacao:
    """Compara heurística vs consenso linha a linha e devolve precisão/revocação/F1.

    Ambos os DataFrames têm colunas `full_name, release_tag, eh_corretiva`.
    """
    _validar_colunas(df_heuristica, "heurística")
    _validar_colunas(df_consenso, "consenso")

    h = df_heuristica[["full_name", "release_tag", "eh_corretiva"]].copy()
    c = df_consenso[["full_name", "release_tag", "eh_corretiva"]].copy()
    h["eh_corretiva"] = _normalizar(h["eh_corretiva"])
    c["eh_corretiva"] = _normalizar(c["eh_corretiva"])
    _validar(h["eh_corretiva"], "heurística")
    _validar(c["eh_corretiva"], "consenso")

    merged = c.merge(h, on=["full_name", "release_tag"], how="left", suffixes=("_cons", "_heur"))
    n_total = len(c)
    n_sem_heur = int(merged["eh_corretiva_heur"].isna().sum())
    pareados = merged.dropna(subset=["eh_corretiva_heur"])
    n_par = len(pareados)

    if n_par == 0:
        return ResultadoAvaliacao(
            nome_heuristica, n_total, 0, n_sem_heur,
            None, None, None, None, 0, 0, 0, 0,
            "nenhuma release pareada entre heurística e consenso",
        )

    y_true = pareados["eh_corretiva_cons"].tolist()
    y_pred = pareados["eh_corretiva_heur"].tolist()
    # confusion_matrix com labels=["sim","nao"] põe sim na primeira linha/coluna:
    #   [[VP, FN], [FP, VN]]
    cm = confusion_matrix(y_true, y_pred, labels=[ROTULO_POSITIVO, "nao"])
    vp = int(cm[0][0]); fn = int(cm[0][1]); fp = int(cm[1][0]); vn = int(cm[1][1])
    precisao, revocacao, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=[ROTULO_POSITIVO], average="binary",
        pos_label=ROTULO_POSITIVO, zero_division=0,
    )
    acc = (vp + vn) / n_par

    nota = ""
    if f1 < f1_minimo:
        nota = f"F1 < {f1_minimo:.2f}: considerar refinar a heurística"

    return ResultadoAvaliacao(
        nome_heuristica, n_total, n_par, n_sem_heur,
        float(precisao), float(revocacao), float(f1), float(acc),
        vp, fp, vn, fn, nota,
    )


def gravar_csv_cumulativo(res: ResultadoAvaliacao, destino: Path) -> Path:
    """Acrescenta uma linha ao CSV (cria com cabeçalho na primeira chamada).

    Chamar uma vez por versão da heurística (v1, v2...): o CSV vira a tabela do artigo
    sozinho, sem ninguém editar à mão.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    novo = not destino.exists()
    with open(destino, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if novo:
            w.writerow(COLUNAS_CSV_SAIDA)
        w.writerow([
            res.heuristica, res.n_total, res.n_pareadas, res.n_sem_heuristica,
            "" if res.precisao is None else f"{res.precisao:.6f}",
            "" if res.revocacao is None else f"{res.revocacao:.6f}",
            "" if res.f1 is None else f"{res.f1:.6f}",
            "" if res.acuracia is None else f"{res.acuracia:.6f}",
            res.vp, res.fp, res.vn, res.fn, res.nota,
        ])
    return destino


def ler_csv_cumulativo(origem: Path) -> list[dict]:
    origem = Path(origem)
    if not origem.exists():
        return []
    with open(origem, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _num(x: Optional[float], casas: int = 4) -> str:
    return "—" if x is None else f"{x:.{casas}f}"


def relatorio_md(res: ResultadoAvaliacao, historico: Optional[list[dict]] = None) -> str:
    linhas = [
        f"# Avaliação da heurística corretiva ({res.heuristica}) contra o consenso",
        "",
        f"Releases rotuladas no consenso: **{res.n_total}**. "
        f"Pareadas com a heurística: **{res.n_pareadas}**. "
        f"Sem cobertura da heurística: **{res.n_sem_heuristica}**.",
        "",
        "## Métricas (classe positiva = `sim`)",
        "",
        "| métrica | valor |",
        "|---|---|",
        f"| precisão | {_num(res.precisao)} |",
        f"| revocação | {_num(res.revocacao)} |",
        f"| F1 | {_num(res.f1)} |",
        f"| acurácia | {_num(res.acuracia)} |",
        "",
        "## Matriz de confusão",
        "",
        "| | heurística: sim | heurística: nao |",
        "|---|---|---|",
        f"| **consenso: sim** | {res.vp} (VP) | {res.fn} (FN) |",
        f"| **consenso: nao** | {res.fp} (FP) | {res.vn} (VN) |",
        "",
    ]
    if res.nota:
        linhas += [f"**Nota:** {res.nota}.", ""]
    linhas.append(
        f"Alvo do enunciado: F1 ≥ {F1_MINIMO_RECOMENDADO:.2f}. Abaixo disso, refinar a "
        "heurística (ex.: ajustar janela corretiva, lista de keywords, política SemVer) e "
        "reavaliar — cada versão vira uma linha nova em `avaliacao_heuristica.csv`."
    )
    if historico:
        linhas += ["", "## Histórico de versões", "",
                   "| versão | n pareadas | precisão | revocação | F1 | acurácia |",
                   "|---|---|---|---|---|---|"]
        for h in historico:
            linhas.append(
                f"| {h.get('heuristica', '—')} | {h.get('n_pareadas', '—')} | "
                f"{h.get('precisao', '—') or '—'} | {h.get('revocacao', '—') or '—'} | "
                f"{h.get('f1', '—') or '—'} | {h.get('acuracia', '—') or '—'} |"
            )
    linhas.append("")
    return "\n".join(linhas)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Avalia a heurística corretiva contra o consenso (precisão, revocação, F1)",
    )
    ap.add_argument("--consenso", required=True,
                    help="CSV com full_name, release_tag, eh_corretiva (consenso da Issue #27)")
    ap.add_argument("--heuristica", required=True,
                    help="CSV com full_name, release_tag, eh_corretiva (saída da heurística)")
    ap.add_argument("--nome", default="v1", help="rótulo da versão da heurística (v1, v2...)")
    ap.add_argument("--saida", default="docs/s03",
                    help="pasta de saída (avaliacao_heuristica.md e .csv acumulativo)")
    ap.add_argument("--f1-minimo", type=float, default=F1_MINIMO_RECOMENDADO,
                    help=f"F1 abaixo do qual a nota recomenda refinar (padrão {F1_MINIMO_RECOMENDADO:.2f})")
    args = ap.parse_args(argv)

    df_cons = pd.read_csv(args.consenso)
    df_heur = pd.read_csv(args.heuristica)
    res = avaliar(df_heur, df_cons, args.nome, args.f1_minimo)

    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    csv_hist = pasta / "avaliacao_heuristica.csv"
    gravar_csv_cumulativo(res, csv_hist)
    historico = ler_csv_cumulativo(csv_hist)
    texto = relatorio_md(res, historico)
    (pasta / "avaliacao_heuristica.md").write_text(texto, encoding="utf-8", newline="\n")
    print(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

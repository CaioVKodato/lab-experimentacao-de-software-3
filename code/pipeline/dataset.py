"""Esquema do CSV final, consolidação por repositório e dicionário de dados (Lab03, #21/#22).

O ESQUEMA (`COLUNAS`) é a fonte única: dele saem o cabeçalho do CSV e o dicionário de
dados. Assim os dois não divergem, e os testes garantem que cada coluna do CSV está
documentada com tipo, unidade e fórmula/origem.

Convenções do dataset:
  * tempos em HORAS; proporções em 0–1 (não em %); datas em ISO 8601, UTC;
  * célula vazia = valor ausente (não calculável ou ainda não preenchido), nunca zero.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from metricas.cfr_recuperacao import (
    change_failure_rate_ci,
    classificar_conclusion,
    tempo_recuperacao_repositorio,
)
from metricas.janela import Janela
from metricas.lead_time import data_release, lead_time_repositorio
from pipeline.workflow_runs import meses_da_janela


@dataclass(frozen=True)
class Coluna:
    nome: str
    tipo: str      # "texto" | "inteiro" | "decimal"
    unidade: str   # "horas", "proporção 0–1", "contagem", "releases/semana", "—"
    origem: str    # campo da API ou fórmula
    rq: str        # RQ a que serve (ou "ID", "Qualidade")


COLUNAS: tuple[Coluna, ...] = (
    # --- identificação e metadados (fatores da RQ 06) ---
    Coluna("repo", "texto", "—", "`full_name` do repositório (`dono/nome`)", "ID"),
    Coluna("default_branch", "texto", "—", "`default_branch` de `GET /repos/{o}/{r}`", "ID"),
    Coluna("estrelas", "inteiro", "contagem", "`stargazers_count` de `GET /repos/{o}/{r}`", "RQ06"),
    Coluna("linguagem_principal", "texto", "—", "`language` de `GET /repos/{o}/{r}`", "RQ06"),
    Coluna("criado_em", "texto", "ISO 8601 UTC", "`created_at` de `GET /repos/{o}/{r}` (idade = coleta − criado_em)", "RQ06"),
    Coluna("n_contribuidores", "inteiro", "contagem", "última página de `GET /repos/{o}/{r}/contributors?per_page=1&anon=true`", "RQ06"),
    # --- RQ 01: frequência de deploy ---
    Coluna("n_releases_janela", "inteiro", "contagem", "releases com `draft=false`, `prerelease=false` e `published_at` dentro da janela (inclui a primeira da história)", "RQ01"),
    Coluna("deploys_por_semana", "decimal", "releases/semana", "`n_releases_janela` ÷ semanas da janela (dias da janela ÷ 7, ≈ 52,1)", "RQ01"),
    # --- RQ 02: lead time (variantes a e b) ---
    Coluna("lead_time_a_horas", "decimal", "horas", "mediana, entre releases, de (`published_at` da release − `author.date` do commit mais antigo incluído nela)", "RQ02"),
    Coluna("lead_time_b_horas", "decimal", "horas", "mediana, entre TODOS os commits de TODAS as releases, de (`published_at` − `author.date` do commit)", "RQ02"),
    Coluna("lt_n_releases_avaliadas", "inteiro", "contagem", "releases com ao menos 1 commit no `compare`, usadas no cálculo", "Qualidade"),
    Coluna("lt_n_ignoradas_compare", "inteiro", "contagem", "releases cujo `compare` falhou (ex.: 404 por tag apagada) e ficaram de fora", "Qualidade"),
    Coluna("lt_n_sem_commits", "inteiro", "contagem", "releases com `compare` ok mas sem commits novos", "Qualidade"),
    Coluna("lt_n_commits", "inteiro", "contagem", "total de commits usados na variante (b)", "Qualidade"),
    # --- RQ 03: change failure rate ---
    Coluna("n_runs_validas", "inteiro", "contagem", "workflow runs `push` do default branch com conclusion em sucesso ou falha (ignorados não contam)", "RQ03"),
    Coluna("cfr_a", "decimal", "proporção 0–1", "falhas ÷ (falhas + sucessos), todos os workflows juntos (proxy de CI)", "RQ03"),
    Coluna("cfr_b", "decimal", "proporção 0–1", "releases seguidas por release corretiva em ≤ 7 dias ÷ releases avaliadas (heurística; vazio até a heurística ser aplicada)", "RQ03"),
    Coluna("cfr_b_n_avaliadas", "inteiro", "contagem", "releases avaliadas na variante (b), sem as dos últimos 7 dias da janela (censura)", "Qualidade"),
    # --- RQ 04: tempo de recuperação ---
    Coluna("recuperacao_mediana_horas", "decimal", "horas", "mediana dos episódios concluídos de todos os workflows: `updated_at` do sucesso − `run_started_at` da primeira falha", "RQ04"),
    Coluna("recuperacao_n_episodios", "inteiro", "contagem", "episódios de falha (concluídos + censurados) em todos os workflows", "Qualidade"),
    Coluna("recuperacao_n_censurados", "inteiro", "contagem", "episódios sem sucesso posterior dentro da janela", "Qualidade"),
    Coluna("recuperacao_prop_censurados", "decimal", "proporção 0–1", "`recuperacao_n_censurados` ÷ `recuperacao_n_episodios`", "RQ04"),
    # --- qualidade e reprodutibilidade da coleta ---
    Coluna("meses_sem_runs", "inteiro", "meses (0–12)", "meses da janela sem nenhuma workflow run coletada (indício de histórico apagado pela API)", "Qualidade"),
    Coluna("coletado_em", "texto", "ISO 8601 UTC", "instante da coleta (a API pode deixar de devolver runs antigas)", "Qualidade"),
)

_TIPOS_VALIDOS = {"texto", "inteiro", "decimal"}


def nomes_colunas() -> list[str]:
    return [c.nome for c in COLUNAS]


# ------------------------------------------------------------ funções auxiliares

def semanas_da_janela(janela: Janela) -> float:
    """Dias da janela (ambos os extremos inclusivos) ÷ 7. Janela de 12 meses ≈ 52,14."""
    dias = (janela.fim.date() - janela.inicio.date()).days + 1
    return dias / 7


def deploys_por_semana(n_releases: int, janela: Janela) -> float:
    return n_releases / semanas_da_janela(janela)


def releases_na_janela(
    releases: Sequence[Mapping[str, Any]], janela: Janela, incluir_prerelease: bool = False
) -> list[Mapping[str, Any]]:
    """Releases que contam como deploy dentro da janela (inclui a primeira da história)."""
    return [
        r for r in releases
        if not r.get("draft")
        and (incluir_prerelease or not r.get("prerelease"))
        and r.get("published_at")
        and janela.contem(data_release(r))
    ]


def contar_runs_validas(runs: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for r in runs if classificar_conclusion(r.get("conclusion")) is not None)


def meses_sem_runs(runs: Iterable[Mapping[str, Any]], janela: Janela) -> int:
    """Quantos meses da janela não têm nenhuma run (sinal de histórico apagado)."""
    com_runs = {str(r["created_at"])[:7] if r.get("created_at") else str(r["run_started_at"])[:7]
                for r in runs}
    return sum(1 for ini, _fim in meses_da_janela(janela) if ini.isoformat()[:7] not in com_runs)


# ------------------------------------------------------------ linha do repositório

def linha_repositorio(
    *,
    repo: str,
    default_branch: str,
    metadados: Mapping[str, Any],
    releases: Sequence[Mapping[str, Any]],
    commits_por_tag: Mapping[str, Optional[Sequence[Mapping[str, Any]]]],
    runs: Sequence[Mapping[str, Any]],
    janela: Janela,
    coletado_em: str,
    cfr_b: Optional[tuple[float, int]] = None,
) -> dict[str, Any]:
    """Uma linha do dataset, pronta para `escrever_csv`.

    `metadados` usa as chaves: estrelas, linguagem_principal, criado_em, n_contribuidores
    (as que faltarem viram células vazias). `cfr_b` é (valor, n_avaliadas) quando a
    heurística de release corretiva já tiver sido aplicada.
    """
    lt = lead_time_repositorio(releases, commits_por_tag, janela)
    rec = tempo_recuperacao_repositorio(runs)
    n_rel = len(releases_na_janela(releases, janela))

    return {
        "repo": repo,
        "default_branch": default_branch,
        "estrelas": metadados.get("estrelas"),
        "linguagem_principal": metadados.get("linguagem_principal"),
        "criado_em": metadados.get("criado_em"),
        "n_contribuidores": metadados.get("n_contribuidores"),
        "n_releases_janela": n_rel,
        "deploys_por_semana": deploys_por_semana(n_rel, janela),
        "lead_time_a_horas": lt.mediana_a_horas,
        "lead_time_b_horas": lt.mediana_b_horas,
        "lt_n_releases_avaliadas": lt.n_releases_avaliadas,
        "lt_n_ignoradas_compare": lt.n_ignoradas_compare,
        "lt_n_sem_commits": lt.n_sem_commits,
        "lt_n_commits": lt.n_commits,
        "n_runs_validas": contar_runs_validas(runs),
        "cfr_a": change_failure_rate_ci(runs),
        "cfr_b": cfr_b[0] if cfr_b else None,
        "cfr_b_n_avaliadas": cfr_b[1] if cfr_b else None,
        "recuperacao_mediana_horas": rec.mediana_horas,
        "recuperacao_n_episodios": rec.n_episodios,
        "recuperacao_n_censurados": rec.n_censurados,
        "recuperacao_prop_censurados": rec.proporcao_censurados,
        "meses_sem_runs": meses_sem_runs(runs, janela),
        "coletado_em": coletado_em,
    }


# ------------------------------------------------------------ saída

def escrever_csv(linhas: Iterable[Mapping[str, Any]], destino: Path) -> Path:
    """CSV com o cabeçalho do esquema. None vira célula vazia; coluna fora do esquema dá erro."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=nomes_colunas(), extrasaction="raise")
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow({k: ("" if v is None else v) for k, v in linha.items()})
    return destino


def escrever_dicionario(destino: Path) -> Path:
    """Grava o dicionário em UTF-8 (evita problemas de codificação no Windows)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": mesmas quebras de linha em qualquer sistema (no Windows, o padrão
    # converteria para \r\n e o arquivo gerado mudaria conforme a máquina de quem roda).
    with destino.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(gerar_dicionario_md())
    return destino


def gerar_dicionario_md() -> str:
    """Dicionário de dados em Markdown, gerado do esquema (nunca escrito à mão)."""
    linhas = [
        "# Dicionário de dados — dataset DORA (Lab03)",
        "",
        "Gerado automaticamente a partir do esquema em `code/pipeline/dataset.py`.",
        "",
        "## Convenções",
        "",
        "- Tempos em **horas**. Proporções entre **0 e 1** (não em porcentagem).",
        "- Datas em ISO 8601, UTC. A janela de observação é a definida pelo professor (`config.yaml`).",
        "- **Célula vazia** = valor ausente (não calculável, ou etapa ainda não aplicada); nunca significa zero.",
        "- Unidade de deploy: release publicada (`draft=false`, `prerelease=false`), só no default branch.",
        "- Workflow runs: apenas `event=push` no default branch; `conclusion` classificada conforme a seção 3 do enunciado.",
        "",
        "## Colunas",
        "",
        "| Coluna | Tipo | Unidade | RQ | Origem / fórmula |",
        "|---|---|---|---|---|",
    ]
    for c in COLUNAS:
        linhas.append(f"| `{c.nome}` | {c.tipo} | {c.unidade} | {c.rq} | {c.origem} |")
    linhas.append("")
    return "\n".join(linhas)


if __name__ == "__main__":
    # A partir de code/:   python -m pipeline.dataset ../docs/dicionario_dados.md
    import sys

    if len(sys.argv) != 2:
        sys.exit("uso: python -m pipeline.dataset CAMINHO_DO_DICIONARIO.md")
    print(f"dicionário gravado em {escrever_dicionario(Path(sys.argv[1]))}")

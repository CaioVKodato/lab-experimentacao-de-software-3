"""Fase 1 da coleta (Lab03, #20): elegibilidade barata, antes da coleta pesada.

Aplica os critérios de inclusão da seção 3 do enunciado — usa Actions, ao menos 50
workflow runs válidos e ao menos 5 releases dentro da janela — usando só `total_count` e
uma página de releases. O resultado fecha a lista da amostra final (e libera o sorteio da
amostra-ouro) sem gastar as centenas de chamadas da coleta completa.

Ordem das checagens, da mais barata/seletiva para a mais cara:
  1. `actions/workflows`            -> sem Actions? descarta (1 chamada)
  2. runs `status=completed`        -> limite superior de runs válidas (1 chamada)
  3. releases na janela (>= 5)      -> 1 a 3 chamadas
  4. runs `success`+`failure`+`timed_out` -> contagem exata de válidas (3 chamadas)

Limitação documentada: a API só filtra por `status` os valores success/failure/timed_out;
`startup_failure` (também falha, na seção 3) não é contável por esse filtro. A contagem
da elegibilidade pode, então, subestimar em poucas unidades os repositórios que tiveram
startup_failure; o `n_runs_validas` do dataset final (que sai das runs coletadas) é exato.

Resultados ficam em cache por repositório E por janela (a janela pode mudar quando o
professor publicar as datas oficiais). Erro da API não é cacheado: roda de novo depois.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from metricas.janela import Janela
from metricas.lead_time import data_release
from pipeline.cache import JsonCache
from pipeline.github_client import GitHubAPIError

MIN_RELEASES = 5
MIN_RUNS_VALIDAS = 50
STATUS_VALIDOS = ("success", "failure", "timed_out")
MAX_RELEASES_LIDAS = 300  # releases vêm da mais recente para a mais antiga; 3 páginas bastam para achar 5 na janela

COLUNAS_CSV = ("repo", "elegivel", "motivo", "default_branch", "n_workflows",
               "n_runs_completas", "n_runs_validas", "n_releases_janela")
_COLUNAS_NOME = ("full_name", "repo", "name_with_owner", "nameWithOwner")


@dataclass(frozen=True)
class Elegibilidade:
    repo: str
    elegivel: bool
    motivo: str  # elegivel | sem_actions | runs_insuficientes | releases_insuficientes | erro_api
    default_branch: Optional[str] = None
    n_workflows: Optional[int] = None
    n_runs_completas: Optional[int] = None   # limite superior das válidas
    n_runs_validas: Optional[int] = None     # None = nem chegou a contar (já descartado antes)
    n_releases_janela: Optional[int] = None  # contadas entre as mais recentes (até MAX_RELEASES_LIDAS)


def _chave_cache(repo: str, janela: Janela) -> str:
    dono, nome = repo.split("/", 1)
    periodo = f"{janela.inicio.date().isoformat()}_{janela.fim.date().isoformat()}"
    return f"elegibilidade/{periodo}/{dono}__{nome}"


def _total_runs(client: Any, repo: str, branch: str, janela: Janela, status: str) -> int:
    corpo = client.get(
        f"/repos/{repo}/actions/runs",
        {
            "per_page": 1, "branch": branch, "event": "push", "status": status,
            "created": f"{janela.inicio.date().isoformat()}..{janela.fim.date().isoformat()}",
        },
    )
    return int(corpo["total_count"])


def _contar_releases_na_janela(client: Any, repo: str, janela: Janela) -> int:
    releases = client.paginate(f"/repos/{repo}/releases", {"per_page": 100}, max_items=MAX_RELEASES_LIDAS)
    return sum(
        1 for r in releases
        if not r.get("draft") and not r.get("prerelease") and r.get("published_at")
        and janela.contem(data_release(r))
    )


def avaliar_repositorio(
    client: Any, cache: JsonCache, repo: str, janela: Janela,
    default_branch: Optional[str] = None,
    min_releases: int = MIN_RELEASES, min_runs: int = MIN_RUNS_VALIDAS,
) -> Elegibilidade:
    chave = _chave_cache(repo, janela)
    em_cache = cache.load(chave)
    if em_cache is not None:
        return Elegibilidade(**em_cache)

    try:
        res = _avaliar(client, repo, janela, default_branch, min_releases, min_runs)
    except GitHubAPIError:
        return Elegibilidade(repo, False, "erro_api")  # sem cache: tenta de novo na próxima rodada

    cache.save(chave, asdict(res))
    return res


def _avaliar(client, repo, janela, default_branch, min_releases, min_runs) -> Elegibilidade:
    n_wf = int(client.get(f"/repos/{repo}/actions/workflows", {"per_page": 1})["total_count"])
    if n_wf == 0:
        return Elegibilidade(repo, False, "sem_actions", default_branch, n_wf)

    branch = default_branch or client.get(f"/repos/{repo}")["default_branch"]

    completas = _total_runs(client, repo, branch, janela, "completed")
    if completas < min_runs:
        return Elegibilidade(repo, False, "runs_insuficientes", branch, n_wf, completas)

    n_rel = _contar_releases_na_janela(client, repo, janela)
    if n_rel < min_releases:
        return Elegibilidade(repo, False, "releases_insuficientes", branch, n_wf, completas, None, n_rel)

    validas = sum(_total_runs(client, repo, branch, janela, s) for s in STATUS_VALIDOS)
    if validas < min_runs:
        return Elegibilidade(repo, False, "runs_insuficientes", branch, n_wf, completas, validas, n_rel)

    return Elegibilidade(repo, True, "elegivel", branch, n_wf, completas, validas, n_rel)


def avaliar_candidatos(
    client: Any, cache: JsonCache, candidatos: Iterable[tuple[str, Optional[str]]], janela: Janela,
    progresso: Optional[Callable[[int, int, Elegibilidade], None]] = None,
) -> list[Elegibilidade]:
    """Avalia cada (repo, default_branch|None) em ordem; retoma do cache se interrompido."""
    lista = list(candidatos)
    resultados = []
    for i, (repo, branch) in enumerate(lista, start=1):
        res = avaliar_repositorio(client, cache, repo, janela, default_branch=branch)
        resultados.append(res)
        if progresso:
            progresso(i, len(lista), res)
    return resultados


def funil(resultados: Iterable[Elegibilidade]) -> dict[str, int]:
    return dict(Counter(r.motivo for r in resultados))


def escrever_csv(resultados: Iterable[Elegibilidade], destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=COLUNAS_CSV)
        escritor.writeheader()
        for r in resultados:
            escritor.writerow({k: ("" if v is None else v) for k, v in asdict(r).items()})
    return destino


def ler_candidatos(caminho: Path) -> list[tuple[str, Optional[str]]]:
    """Lê `dono/nome` (e `default_branch`, se houver) de um CSV de candidatos."""
    with Path(caminho).open(encoding="utf-8", newline="") as fh:
        leitor = csv.DictReader(fh)
        cabecalho = leitor.fieldnames or []
        coluna = next((c for c in _COLUNAS_NOME if c in cabecalho), None)
        if coluna is None:
            raise ValueError(
                f"nenhuma coluna de repositório reconhecida ({', '.join(_COLUNAS_NOME)}); "
                f"cabeçalho do arquivo: {', '.join(cabecalho)}"
            )
        return [(linha[coluna].strip(), (linha.get("default_branch") or "").strip() or None)
                for linha in leitor if (linha[coluna] or "").strip()]


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import os

    import yaml

    from metricas.janela import parse_janela
    from pipeline.github_client import GitHubClient

    ap = argparse.ArgumentParser(description="Fase 1 da coleta: elegibilidade dos candidatos")
    ap.add_argument("candidatos", help="CSV com a coluna do repositório (dono/nome)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--saida", default=None, help="CSV de saída (padrão: <output_dir>/elegibilidade.csv)")
    ap.add_argument("--limite", type=int, default=None, help="avalia só os N primeiros (para testar)")
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Defina o token antes:  export GITHUB_TOKEN=...  (PowerShell: $env:GITHUB_TOKEN='...')")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    janela = parse_janela(cfg["observation_window"]["start"], cfg["observation_window"]["end"])
    cache = JsonCache(Path(cfg["paths"]["cache_dir"]))
    saida = Path(args.saida) if args.saida else Path(cfg["paths"]["output_dir"]) / "elegibilidade.csv"

    candidatos = ler_candidatos(Path(args.candidatos))
    if args.limite:
        candidatos = candidatos[: args.limite]
    print(f"{len(candidatos)} candidatos | janela {janela.inicio.date()}..{janela.fim.date()}")

    def progresso(i: int, n: int, r: Elegibilidade) -> None:
        print(f"[{i}/{n}] {r.repo}: {r.motivo}", flush=True)

    resultados = avaliar_candidatos(GitHubClient(token), cache, candidatos, janela, progresso)
    escrever_csv(resultados, saida)

    f = funil(resultados)
    print(f"\nFunil: {f}\nElegíveis: {f.get('elegivel', 0)} de {len(resultados)}\nCSV: {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Etapa final da #21: lê os caches e as saídas da coleta e monta o `dataset.csv` real.

Não usa rede nem token: só LÊ o que as etapas anteriores gravaram (releases, compare,
workflow runs, metadados e o estrato do sorteio). Pode ser rodada a qualquer momento,
inclusive com a coleta incompleta: o que ainda falta vira LACUNA, e não zero.

    python -m pipeline.consolidar --config config.yaml

Dado ausente não pode se passar por dado zero. Por isso cada linha traz
`lacunas_de_coleta`: quantas partes faltavam no cache (par de compare não coletado, mês de
workflow runs nunca coletado, releases, metadados ou estrato ausentes). Linha com 0 está
completa; linha com mais de 0 não deve entrar na análise sem essa declaração no artigo.
Um par que o GitHub respondeu 404 JÁ foi coletado (sem dado) e não é lacuna; um par que
falhou com 5xx/422 não foi gravado e é lacuna.

Os formatos de chave de cache vêm dos próprios coletores; os testes gravam os caches com os
coletores reais, então uma divergência de formato quebra o teste da linha completa.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from metricas.janela import Janela, parse_janela
from metricas.lead_time import selecionar_pares
from pipeline import compare, releases as releases_mod
from pipeline.cache import JsonCache
from pipeline.dataset import escrever_csv, linha_repositorio
from pipeline.workflow_runs import meses_da_janela

# onde o sorteio foi versionado, caso o arquivo da pasta de saída não exista mais
_ESTRATOS_VERSIONADO = Path("..") / "docs" / "s02" / "selection_evaluated.csv"


@dataclass(frozen=True)
class Resumo:
    n_repos: int
    n_completos: int
    n_incompletos: int
    total_lacunas: int
    caminho: Path


# ------------------------------------------------------------------ leitura dos caches

def ler_releases_do_cache(
    cache: JsonCache, owner: str, name: str, janela_str: tuple[str, str]
) -> Optional[list[dict[str, Any]]]:
    """Histórico completo de releases, ou None se ausente, de outra versão ou de outra janela."""
    payload = cache.load(f"releases/{owner}/{name}.json")
    if not isinstance(payload, dict):
        return None
    if payload.get("versao") != releases_mod.CACHE_VERSAO:
        return None
    if payload.get("janela") != list(janela_str) or payload.get("full_name") != f"{owner}/{name}":
        return None
    return [{**r, "draft": False} for r in (payload.get("historico") or [])]


def _commits_do_par(
    cache: JsonCache, owner: str, name: str, base: str, head: str
) -> tuple[Optional[list[dict[str, Any]]], bool]:
    """(commits no formato da API | None, é_lacuna). 404 já registrado não é lacuna."""
    cached = cache.load(compare._cache_key(owner, name, base, head))
    valido = (
        isinstance(cached, dict)
        and cached.get("versao") == compare.CACHE_VERSAO
        and cached.get("full_name") == f"{owner}/{name}"
    )
    if not valido:
        return None, True
    if cached.get("not_found"):
        return None, False
    return [compare.CommitEntry(**c).as_api() for c in (cached.get("commits") or [])], False


def ler_runs_do_cache(
    cache: JsonCache, owner: str, name: str, janela: Janela
) -> tuple[list[dict[str, Any]], int]:
    """Runs gravadas e quantos intervalos NUNCA foram coletados (seguindo as bisseções)."""
    runs: list[dict[str, Any]] = []
    faltando = 0

    def visitar(ini: date, fim: date) -> None:
        nonlocal faltando
        registro = cache.load(f"{owner}__{name}/workflow_runs/{ini.isoformat()}_{fim.isoformat()}")
        if registro is None:
            faltando += 1
            return
        if registro.get("dividido"):
            meio = ini + timedelta(days=(fim - ini).days // 2)
            visitar(ini, meio)
            visitar(meio + timedelta(days=1), fim)
            return
        runs.extend(registro.get("runs") or [])

    for ini, fim in meses_da_janela(janela):
        visitar(ini, fim)
    return runs, faltando


# ------------------------------------------------------------------ arquivos da coleta

def _int(valor: Optional[str]) -> Optional[int]:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def ler_metadados(caminho: Path) -> dict[str, dict[str, Any]]:
    """repo_metadata.csv -> {full_name: {estrelas, linguagem_principal, criado_em, n_contribuidores}}."""
    meta: dict[str, dict[str, Any]] = {}
    with Path(caminho).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            meta[row["full_name"]] = {
                "estrelas": _int(row.get("stargazers_count")),
                "linguagem_principal": (row.get("language") or "").strip() or None,
                "criado_em": (row.get("created_at") or "").strip() or None,
                "n_contribuidores": _int(row.get("contributors_count")),
            }
    return meta


def ler_estratos(caminho: Path) -> dict[str, str]:
    """selection_evaluated.csv -> {full_name: faixa} só dos incluídos na amostra."""
    estratos: dict[str, str] = {}
    with Path(caminho).open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("included", "")).strip().lower() == "true" and row.get("faixa"):
                estratos[row["full_name"]] = row["faixa"]
    return estratos


# ------------------------------------------------------------------ montagem

def construir_linha(
    *,
    owner: str,
    name: str,
    default_branch: str,
    cache: JsonCache,
    janela: Janela,
    janela_str: tuple[str, str],
    metadados: Optional[Mapping[str, Any]],
    faixa_estrelas: Optional[str],
    coletado_em: str,
) -> dict[str, Any]:
    """Uma linha do dataset lida só do cache, com a contagem de lacunas."""
    lacunas = 0

    historico = ler_releases_do_cache(cache, owner, name, janela_str)
    if historico is None:
        historico, lacunas = [], lacunas + 1

    lidos: dict[tuple[str, str], Optional[list[dict[str, Any]]]] = {}

    def mapa(incluir_prerelease: bool) -> dict[str, Optional[list[dict[str, Any]]]]:
        nonlocal lacunas
        saida: dict[str, Optional[list[dict[str, Any]]]] = {}
        for base, head in selecionar_pares(historico, janela, incluir_prerelease=incluir_prerelease):
            chave = (str(base["tag_name"]), str(head["tag_name"]))
            if chave not in lidos:  # um par comum às duas variantes conta uma vez só
                lidos[chave], lacuna = _commits_do_par(cache, owner, name, *chave)
                lacunas += int(lacuna)
            saida[chave[1]] = lidos[chave]
        return saida

    commits_por_tag = mapa(False)
    commits_por_tag_pre = mapa(True)

    runs, faltando = ler_runs_do_cache(cache, owner, name, janela)
    lacunas += faltando
    if metadados is None:
        lacunas += 1
    if faixa_estrelas is None:
        lacunas += 1

    return linha_repositorio(
        repo=f"{owner}/{name}",
        default_branch=default_branch,
        metadados=metadados or {},
        releases=historico,
        commits_por_tag=commits_por_tag,
        commits_por_tag_pre=commits_por_tag_pre,
        runs=runs,
        janela=janela,
        coletado_em=coletado_em,
        faixa_estrelas=faixa_estrelas,
        lacunas_de_coleta=lacunas,
    )


def _primeiro_existente(*caminhos: Path) -> Path:
    for caminho in caminhos:
        if Path(caminho).is_file():
            return Path(caminho)
    raise FileNotFoundError("arquivo não encontrado: " + " ou ".join(str(c) for c in caminhos))


def consolidar(
    config: Mapping[str, Any],
    *,
    sample_csv: Optional[Path] = None,
    metadados_csv: Optional[Path] = None,
    estratos_csv: Optional[Path] = None,
    saida: Optional[Path] = None,
    coletado_em: Optional[str] = None,
) -> Resumo:
    pasta = Path(config["paths"]["output_dir"])
    cache = JsonCache(Path(config["paths"]["cache_dir"]))
    janela_str = (str(config["observation_window"]["start"]), str(config["observation_window"]["end"]))
    janela = parse_janela(*janela_str)
    coletado_em = coletado_em or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    amostra_path = Path(sample_csv) if sample_csv else _primeiro_existente(pasta / "sample_repos.csv")
    metadados = ler_metadados(Path(metadados_csv) if metadados_csv else _primeiro_existente(pasta / "repo_metadata.csv"))
    estratos = ler_estratos(
        Path(estratos_csv) if estratos_csv else _primeiro_existente(pasta / "selection_evaluated.csv", _ESTRATOS_VERSIONADO)
    )

    linhas = []
    with amostra_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            full = row["full_name"].strip()
            owner, name = full.split("/", 1)
            linhas.append(
                construir_linha(
                    owner=owner, name=name, default_branch=(row.get("default_branch") or "main").strip(),
                    cache=cache, janela=janela, janela_str=janela_str,
                    metadados=metadados.get(full), faixa_estrelas=estratos.get(full), coletado_em=coletado_em,
                )
            )

    destino = escrever_csv(linhas, Path(saida) if saida else pasta / "dataset.csv")
    completos = sum(1 for l in linhas if l["lacunas_de_coleta"] == 0)
    return Resumo(len(linhas), completos, len(linhas) - completos, sum(l["lacunas_de_coleta"] for l in linhas), destino)


def main(argv: Optional[list[str]] = None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description="Monta o dataset.csv a partir dos caches da coleta (sem rede)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sample-csv", default=None, help="lista de repositórios (padrão: <output_dir>/sample_repos.csv)")
    ap.add_argument("--saida", default=None, help="CSV de saída (padrão: <output_dir>/dataset.csv)")
    args = ap.parse_args(argv)

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    r = consolidar(config, sample_csv=Path(args.sample_csv) if args.sample_csv else None,
                   saida=Path(args.saida) if args.saida else None)
    print(f"{r.n_repos} repositórios, {r.n_completos} completos, {r.n_incompletos} incompletos "
          f"({r.total_lacunas} lacunas no total)\nDataset: {r.caminho}")
    if r.n_incompletos:
        print("AVISO: há linhas incompletas (lacunas_de_coleta > 0). Termine a coleta ou declare "
              "no artigo que a análise cobre só os repositórios completos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

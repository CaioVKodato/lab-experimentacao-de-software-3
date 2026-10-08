"""Ponto de entrada da CLI do pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from pipeline.github_client import GitHubClient
from pipeline.metadata import run_metadata_collection
from pipeline.selection import run_selection, write_selection_outputs


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("config.yaml deve conter um mapeamento YAML na raiz")
    return data


def _build_client(token: str, github: dict) -> GitHubClient:
    return GitHubClient(
        token,
        api_base_url=str(github.get("api_base_url") or "https://api.github.com"),
        per_page=int(github.get("per_page") or 100),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline de mineração de métricas DORA a partir da API do GitHub."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Caminho para o arquivo de configuração (padrão: config.yaml)",
    )
    parser.add_argument(
        "--stage",
        choices=["selection", "metadata", "all"],
        default="selection",
        help="Etapa a executar (padrão: selection)",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Limita quantos candidatos da busca serão avaliados (útil para testes)",
    )
    parser.add_argument(
        "--sample-csv",
        default=None,
        help="CSV da amostra para --stage metadata (padrão: data/output/sample_repos.csv)",
    )
    parser.add_argument(
        "--force-metadata",
        action="store_true",
        help="Ignora cache JSON e recoleta metadados da API",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "Erro: defina a variável de ambiente GITHUB_TOKEN "
            "(veja .env.example em code/). O token não deve ser commitado.",
            file=sys.stderr,
        )
        return 1

    try:
        config = load_config(Path(args.config))
    except (OSError, ValueError) as exc:
        print(f"Erro ao carregar configuração: {exc}", file=sys.stderr)
        return 1

    window = config.get("observation_window", {})
    sample = config.get("sample", {})
    paths = config.get("paths", {})
    github = config.get("github", {})
    client = _build_client(token, github)

    print("Pipeline DORA — Lab03S01")
    print(f"  config: {args.config}")
    print(f"  stage: {args.stage}")
    print(f"  janela: {window.get('start')} → {window.get('end')}")
    print(f"  amostra alvo: {sample.get('target_size')} repositórios")

    if args.stage in {"selection", "all"}:
        print("Executando seleção de repositórios e funil de inclusão…")
        result = run_selection(
            client,
            config,
            max_candidates=args.max_candidates,
        )
        output_dir = Path(paths.get("output_dir") or "data/output")
        written = write_selection_outputs(result, output_dir)

        print("Funil:")
        for stage in result.funnel.stages:
            print(f"  {stage['etapa']}: {stage['quantidade']}")
        print("Arquivos gerados:")
        for label, path in written.items():
            print(f"  {label}: {path}")

        if result.funnel.sample < int(sample.get("target_size") or 100):
            print(
                "Aviso: amostra abaixo da meta. Amplie star_ranges / "
                "max-candidates ou rode de novo com mais cota de API.",
                file=sys.stderr,
            )

    if args.stage in {"metadata", "all"}:
        print("Executando coleta de metadados da amostra…")
        try:
            rows, csv_path = run_metadata_collection(
                client,
                config,
                sample_csv=Path(args.sample_csv) if args.sample_csv else None,
                force=args.force_metadata,
            )
        except (OSError, ValueError, FileNotFoundError) as exc:
            print(f"Erro na coleta de metadados: {exc}", file=sys.stderr)
            return 1

        cached = sum(1 for row in rows if row.from_cache)
        print(f"  repositórios: {len(rows)} (cache hit: {cached})")
        print(f"  CSV: {csv_path}")

    return 0

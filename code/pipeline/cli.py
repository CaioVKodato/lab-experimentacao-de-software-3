"""Ponto de entrada da CLI do pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from pipeline.github_client import GitHubClient
from pipeline.orchestrator import run_full_pipeline

STAGE_CHOICES = [
    "selection",
    "metadata",
    "releases",
    "compare",
    "workflow-runs",
    "all",
]


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
        description=(
            "Pipeline de mineração de métricas DORA a partir da API do GitHub. "
            "Comando único: python -m pipeline --config config.yaml"
        )
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Caminho para o arquivo de configuração (padrão: config.yaml)",
    )
    parser.add_argument(
        "--stage",
        choices=STAGE_CHOICES,
        default="all",
        help=(
            "Etapa a executar (padrão: all = seleção + metadados + releases + "
            "compare + workflow-runs)"
        ),
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="Limita candidatos avaliados na seleção (útil para testes)",
    )
    parser.add_argument(
        "--sample-csv",
        default=None,
        help="CSV da amostra (padrão: data/output/sample_repos.csv)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora cache JSON e recoleta da API nas etapas aplicáveis",
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
    github = config.get("github", {})
    client = _build_client(token, github)

    print("Pipeline DORA — Lab03")
    print(f"  config: {args.config}")
    print(f"  stage: {args.stage}")
    print(f"  janela: {window.get('start')} → {window.get('end')}")
    print(f"  amostra alvo: {sample.get('target_size')} repositórios")

    stages = None if args.stage == "all" else [args.stage]
    sample_csv = Path(args.sample_csv) if args.sample_csv else None

    try:
        report = run_full_pipeline(
            client,
            config,
            max_candidates=args.max_candidates,
            sample_csv=sample_csv,
            force=args.force,
            stages=stages,
        )
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(f"Erro no pipeline: {exc}", file=sys.stderr)
        return 1

    print("Etapas:")
    for stage in report.stages:
        print(f"  [{stage.name}] {stage.detail}")
        for label, path in stage.paths.items():
            print(f"    - {label}: {path}")

    if args.stage in {"selection", "all"}:
        # aviso de meta S01 se o manifesto de selection existir nos detalhes
        sel = next((s for s in report.stages if s.name == "selection"), None)
        if sel and "amostra=" in sel.detail:
            # detail: funil amostra=N/candidatos=M
            try:
                part = sel.detail.split("amostra=")[1].split("/")[0]
                n_sample = int(part)
            except (IndexError, ValueError):
                n_sample = -1
            target = int(sample.get("target_size") or 100)
            if 0 <= n_sample < target:
                print(
                    "Aviso: amostra abaixo da meta S01 "
                    f"({n_sample} < {target}). Amplie a busca ou a cota da API.",
                    file=sys.stderr,
                )

    return 0

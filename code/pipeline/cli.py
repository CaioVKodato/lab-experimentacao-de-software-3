"""Ponto de entrada da CLI do pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("config.yaml deve conter um mapeamento YAML na raiz")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline de mineração de métricas DORA a partir da API do GitHub."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Caminho para o arquivo de configuração (padrão: config.yaml)",
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

    print("Pipeline DORA — estrutura base pronta (Lab03S01).")
    print(f"  config: {args.config}")
    print(f"  janela: {window.get('start')} → {window.get('end')}")
    print(f"  amostra alvo: {sample.get('target_size')} repositórios")
    print("Próximos passos: coletores de seleção, releases e workflow runs.")
    return 0

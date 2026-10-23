"""Gerador de CSV SINTÉTICO com o esquema do dataset DORA (Lab03).

ATENÇÃO: os números são inventados. Servem só para desenvolver e testar as análises
(RQ01 a RQ08) antes de a coleta real terminar. NUNCA cite um resultado calculado sobre
este arquivo no artigo. Os repositórios se chamam `exemplo/repo-NNN` justamente para o
arquivo ser reconhecível como falso.

O gerador usa o esquema de `pipeline.dataset` como fonte da verdade: se o esquema mudar,
os testes avisam quando o gerador ficar defasado. A semente fixa torna o arquivo
reproduzível: todos do grupo trabalham com o mesmo CSV.

Uso (a partir de code/):
    python -m analise.dados_exemplo ../data/exemplo/dataset_exemplo.csv --n 60 --seed 42
"""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path
from typing import Any, Optional

from pipeline.dataset import escrever_csv, nomes_colunas
from pipeline.selection import DEFAULT_STAR_RANGES, rotulo_faixa

LINGUAGENS = ("Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C++", "C#")
SEMANAS_JANELA = 365 / 7
COLETADO_EM = "2026-10-20T12:00:00Z"


def _limitar(valor: float, minimo: float = 0.0, maximo: float = 1.0) -> float:
    return max(minimo, min(maximo, valor))


def _estrelas_e_faixa(rng: random.Random, i: int) -> tuple[int, str]:
    """Estrato primeiro (alocação igual, como no desenho real) e estrelas no TOPO da faixa.

    Na amostra real, a busca devolve os mais estrelados de cada faixa, então as estrelas se
    concentram perto do limite superior (ex.: 1970-2000 na faixa 1000-2000)."""
    minimo, maximo = DEFAULT_STAR_RANGES[(i - 1) % len(DEFAULT_STAR_RANGES)]
    if maximo is None:
        return rng.randint(114_399, 392_786), rotulo_faixa(minimo, maximo)
    return rng.randint(int(maximo * 0.97), maximo), rotulo_faixa(minimo, maximo)


def _linha(rng: random.Random, i: int) -> dict[str, Any]:
    estrelas, faixa = _estrelas_e_faixa(rng, i)
    # Frequência de deploy (releases/semana), assimétrica à direita como nos dados reais.
    deploys = rng.lognormvariate(math.log(0.8), 1.0)
    n_rel = max(5, round(deploys * SEMANAS_JANELA))
    deploys_real = n_rel / SEMANAS_JANELA

    lead_a = rng.lognormvariate(math.log(120), 1.2)            # horas
    lead_b = lead_a * rng.uniform(0.3, 1.0)                    # mediana por commit costuma ser menor

    # CFR (a): leve tendência inversa com a frequência, para a RQ05 ter o que medir.
    cfr_a = _limitar(rng.betavariate(1.5, 7) - 0.03 * math.log(deploys_real), 0.0, 0.95)

    censuradas = rng.randint(0, min(2, n_rel - 1))
    n_aval = max(1, n_rel - censuradas)
    falhas = sum(rng.random() < cfr_a * 0.8 for _ in range(n_aval))
    cfr_b = falhas / n_aval

    n_pre = n_rel + rng.randint(0, max(1, n_rel // 4))
    n_aval_pre = max(1, n_pre - censuradas)
    falhas_pre = min(n_aval_pre, falhas + rng.randint(0, 2))

    sem_recuperacao = rng.random() < 0.12
    if sem_recuperacao:
        n_ep = n_cens = 0
        rec: Optional[float] = None
        prop: Optional[float] = None
    else:
        n_ep = rng.randint(1, 40)
        n_cens = sum(rng.random() < 0.15 for _ in range(n_ep))
        rec = round(rng.lognormvariate(math.log(6), 1.3), 4)   # horas
        prop = round(n_cens / n_ep, 4)

    meses_vazios = rng.choices([0, 1, 2, 3, 6], weights=[80, 8, 5, 4, 3])[0]

    return {
        "repo": f"exemplo/repo-{i:03d}",
        "default_branch": rng.choice(["main", "master"]),
        "estrelas": estrelas,
        "faixa_estrelas": faixa,
        "linguagem_principal": rng.choice(LINGUAGENS),
        "criado_em": f"{rng.randint(2012, 2024)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T00:00:00Z",
        "n_contribuidores": int(rng.lognormvariate(math.log(60), 1.1)) + 1,
        "n_releases_janela": n_rel,
        "deploys_por_semana": round(deploys_real, 4),
        "lead_time_a_horas": round(lead_a, 4),
        "lead_time_b_horas": round(lead_b, 4),
        "lt_n_releases_avaliadas": max(1, n_rel - rng.randint(0, 2)),
        "lt_n_ignoradas_compare": rng.randint(0, 2),
        "lt_n_sem_commits": rng.randint(0, 1),
        "lt_n_commits": n_rel * rng.randint(3, 40),
        "n_runs_validas": rng.randint(50, 3000),
        "cfr_a": round(cfr_a, 4),
        "cfr_b": round(cfr_b, 4),
        "cfr_b_n_avaliadas": n_aval,
        "cfr_b_n_falhas": falhas,
        "cfr_b_n_censuradas": censuradas,
        "cfr_b_n_sem_compare": rng.randint(0, 1),
        "cfr_b_heuristica": "v1",
        "n_releases_janela_pre": n_pre,
        "deploys_por_semana_pre": round(n_pre / SEMANAS_JANELA, 4),
        "lead_time_a_horas_pre": round(lead_a * rng.uniform(0.8, 1.2), 4),
        "lead_time_b_horas_pre": round(lead_b * rng.uniform(0.8, 1.2), 4),
        "lt_n_releases_avaliadas_pre": max(1, n_pre - rng.randint(0, 2)),
        "lt_n_ignoradas_compare_pre": rng.randint(0, 2),
        "cfr_b_pre": round(falhas_pre / n_aval_pre, 4),
        "cfr_b_n_avaliadas_pre": n_aval_pre,
        "recuperacao_mediana_horas": rec,
        "recuperacao_n_episodios": n_ep,
        "recuperacao_n_censurados": n_cens,
        "recuperacao_prop_censurados": prop,
        "meses_sem_runs": meses_vazios,
        "lacunas_de_coleta": 0,
        "coletado_em": COLETADO_EM,
    }


def gerar_linhas(n: int = 60, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    linhas = [_linha(rng, i) for i in range(1, n + 1)]
    faltando = set(nomes_colunas()) - set(linhas[0]) if linhas else set()
    sobrando = set(linhas[0]) - set(nomes_colunas()) if linhas else set()
    if faltando or sobrando:  # esquema mudou e o gerador não acompanhou
        raise RuntimeError(f"gerador defasado em relação ao esquema: faltam {sorted(faltando)}, sobram {sorted(sobrando)}")
    return linhas


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Gera um CSV SINTÉTICO com o esquema do dataset DORA")
    ap.add_argument("saida", help="caminho do CSV a gravar")
    ap.add_argument("--n", type=int, default=60, help="número de repositórios (padrão 60)")
    ap.add_argument("--seed", type=int, default=42, help="semente (todos do grupo devem usar a mesma)")
    args = ap.parse_args(argv)
    destino = escrever_csv(gerar_linhas(args.n, args.seed), Path(args.saida))
    print(f"{args.n} repositórios SINTÉTICOS gravados em {destino} (dados falsos: não citar no artigo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

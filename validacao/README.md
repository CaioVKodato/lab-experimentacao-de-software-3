# Validação manual (amostra-ouro) — Issue #23

## Pré-requisitos

1. `code/data/output/sample_repos.csv` com **≥ 60** repositórios
2. `code/data/output/releases.csv` com releases da janela

## Geração

```bash
cd code
python -m pipeline --config config.yaml --stage amostra-ouro
```

Sementes fixas (`config.yaml` → `validation`): `random_state_repos=42`; releases por repo usam `42 + índice` (repos ordenados por `full_name`).

## Arquivos gerados

| Arquivo | Conteúdo |
|---|---|
| `amostra_ouro_repos.csv` | 60 repositórios sorteados |
| `amostra_ouro_releases.csv` | 5 releases por repositório |
| `amostra_ouro_meta.csv` | parâmetros do sorteio |
| `planilha_modelo.csv` / `planilha_<avaliador>.csv` | links + rótulos vazios (**sem** heurística) |

Rotulagem independente: Issues #24–#26 (`rotulos_<usuario>.csv`).

## Concordância e consenso (Issue #27)

Após as três planilhas preenchidas:

```bash
cd code
python -m pipeline.concordancia --validacao-dir ../validacao
```

Protocolo de desempate (maioria simples; 1-1-1 em reunião): [`protocolo_desempate.md`](protocolo_desempate.md).

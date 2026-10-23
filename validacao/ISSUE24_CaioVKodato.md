# Issue #24 — Rotulagem (CaioVKodato)

Bloqueada até existir a planilha da amostra-ouro.

## Pré-requisitos

1. `code/data/output/releases.csv` (coleta de releases da amostra de 300 em `docs/s02/sample_repos.csv`)
2. Gerar a amostra-ouro:

```bash
cd code
python -m pipeline --config config.yaml --stage amostra-ouro
```

3. Copiar a planilha pessoal e preencher **sem** consultar os outros avaliadores nem a heurística:

```bash
copy ..\validacao\planilha_CaioVKodato.csv ..\validacao\rotulos_CaioVKodato.csv
```

Colunas a preencher: `tipo_projeto`, `entrega_real`, `eh_corretiva`.

Depois disso, a Issue #27 (kappa + consenso) consome `rotulos_*.csv`.

# RQ01–RQ04 — descritivas e classificação C1 (Issue #36)

Rodada a partir do dataset de exemplo (70 repositórios). Reexecutar com o CSV consolidado real quando disponível:

```bash
cd code
python -m analise.rq01_rq04 caminho/do/dataset.csv --outdir ../docs/s03
```

## Resultados (exemplo, n=70)

| Métrica | n | ausentes | mediana | IQR |
|---|---:|---:|---:|---:|
| deploys/semana | 70 | 0 | 0,93 | 1,05 |
| lead time (a) h | 70 | 0 | 114,0 | 157,3 |
| lead time (b) h | 70 | 0 | 63,4 | 120,3 |
| CFR (a) | 70 | 0 | 0,162 | 0,156 |
| CFR (b) | 70 | 0 | 0,117 | 0,152 |
| recuperação h | 61 | 9 | 8,33 | 18,21 |

### Classificação C1 (geral)

| Nível | n | proporção |
|---|---:|---:|
| Elite | 0 | 0% |
| High | 37 | 52,9% |
| Medium | 24 | 34,3% |
| Low | 0 | 0% |
| ausente | 9 | 12,9% |

### Censura (RQ04)

Repositórios sem episódio de falha ficam com recuperação ausente (não imputados): **9 / 70**.  
Mediana da proporção de episódios censurados: **0,143**.

## Arquivos

- `tabela_descritivas_rq01_rq04.csv`
- `classificacao_c1_por_repo.csv`
- `dist_niveis_*.csv`
- `resumo_censura_rq04.csv`
- `figuras/` — histogramas e barras C1

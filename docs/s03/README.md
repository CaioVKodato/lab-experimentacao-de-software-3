# Sprint S03 — RQ01 a RQ04 (Issue #36)

Análise descritiva e classificação DORA de referência **C1** (release + lead time a + CFR a + recuperação).

## Como gerar (reexecutável)

Com o CSV consolidado do pipeline:

```bash
cd code
python -m analise.rq01_rq04 data/output/dataset.csv --outdir ../docs/s03
```

Saídas esperadas neste diretório:

| Arquivo | Conteúdo |
|---|---|
| `tabela_descritivas_rq01_rq04.csv` | mediana, IQR, n, ausentes por métrica |
| `classificacao_c1_por_repo.csv` | nível por métrica e nota geral |
| `dist_niveis_*.csv` | contagem Elite/High/Medium/Low (+ ausentes) |
| `resumo_censura_rq04.csv` | proporção de episódios censurados / repos sem falha |
| `figuras/` | histogramas e barras da distribuição C1 |

Ausentes são sempre reportados; repositórios sem episódio de falha não recebem imputação de recuperação.

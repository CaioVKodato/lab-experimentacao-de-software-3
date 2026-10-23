# Protocolo de desempate — amostra-ouro (Issue #27)

## Escopo

Aplica-se às três dimensões rotuladas independentemente (Issues #24–#26):

1. `tipo_projeto` — por repositório  
2. `entrega_real` — por repositório  
3. `eh_corretiva` — por release sorteada  

## Regra principal: maioria simples

Com três avaliadores (`CaioVKodato`, `Henrique-volponi`, `Kjonps`):

| Situação | Status | Rótulo de consenso |
|---|---|---|
| 3–0 | `unanime` | o valor comum |
| 2–1 | `maioria` | o valor com 2 votos |
| 1–1–1 | `empate_reuniao` | **vazio** até a reunião |

## Casos 1–1–1 (reunião)

1. Exportar `validacao/casos_divergentes.csv` (gerado pelo script).  
2. Em reunião síncrona, abrir o `release_url` / `repo_url` e discutir só as evidências públicas (notes, commits, README) — **sem** olhar a heurística automática.  
3. Registrar a decisão por consenso do grupo (ou voto após a discussão) na coluna correspondente de `rotulos_consenso.csv` e marcar `*__status` como `decidido_reuniao`.  
4. Anexar ata breve (data + lista de itens decididos) em `validacao/ata_desempate.md` quando a reunião ocorrer.

## Concordância (Fleiss)

```bash
cd code
python -m pipeline.concordancia --validacao-dir ../validacao
```

Interpretação do κ pela escala de **Landis & Koch (1977)**: pobre / leve / razoável / moderada / substancial / quase perfeita.

## Saídas

| Arquivo | Conteúdo |
|---|---|
| `kappa_fleiss.csv` | κ e interpretação por dimensão |
| `rotulos_consenso.csv` | rótulo consolidado + votos individuais |
| `casos_divergentes.csv` | itens com maioria 2–1 ou empate 1–1–1 |

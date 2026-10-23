# Metodologia (rascunho para o artigo SBC) — Issue #30

> Texto para colar no [template SBC no Overleaf](https://www.overleaf.com/latex/templates/sbc-conferences-template/blbxwjwzdngr).
> Complementa a Introdução (`introducao.md`). Atualize números do funil com a tabela gerada pelo pipeline (`funnel.csv`).

## Texto da Metodologia

### Fonte de dados e janela de observação

A unidade de análise é o repositório público no GitHub que utiliza GitHub Actions. Os dados são obtidos exclusivamente pela API REST pública do GitHub (`/search/repositories`, `/repos`, `/releases`, `/compare`, `/actions/runs`, `/contributors`), com cache local, respeito a *rate limit* e *backoff* exponencial. O token de acesso permanece apenas em variável de ambiente e não é versionado.

A janela de observação é de **12 meses**, configurada em `config.yaml` (`observation_window`). Na execução desta sprint, a janela adotada é **2025-10-01 a 2026-09-30** (UTC). Métricas temporais (frequência de deploy, *lead time*, recuperação) e contagens de releases / *workflow runs* consideram apenas eventos com *timestamp* dentro dessa janela, salvo quando o enunciado permite âncora anterior (ex.: release imediatamente anterior fora da janela para o *compare*).

### Funil de seleção

A busca de candidatos é fatiada por faixas de estrelas para ultrapassar o teto de 1.000 resultados por consulta da API de busca. Em seguida aplica-se o funil de inclusão alinhado ao enunciado:

1. repositório não arquivado e com *default branch* identificável;
2. ao menos **5** releases publicadas (`draft=false`, `prerelease=false`) na janela;
3. ao menos **50** *workflow runs* de evento `push` no *default branch* na janela (conclusions válidas de sucesso/falha conforme a seção 3 do enunciado).

A meta da amostra final da Lab03S02 é **≥ 300** repositórios aprovados. Quando `sample.stratify` está ativo, o `target_size` é dividido em cotas iguais por faixa de estrelas, evitando concentração nas faixas de menor popularidade (primeira ordem da busca). A alocação igual **super-representa** faixas altas em relação à população do GitHub; trata-se de escolha metodológica declarada aqui. O funil quantitativo (candidatos por etapa) é exportado automaticamente em `funnel.csv` / `funnel.json` e deve ser colado como tabela nesta seção na versão final do artigo.

### Definições operacionais e variantes

Adotamos as quatro métricas DORA clássicas, com *proxies* no GitHub:

| Métrica | Definição operacional (referência C1) | Variantes |
|---|---|---|
| Frequência de deploy | Releases publicadas na janela ÷ semanas da janela | + pré-releases; *tags* (RQ07) |
| *Lead time for changes* | (a) mediana, por release, de `published_at − author.date` do commit mais antigo do *compare* | (b) mediana sobre todos os commits de todas as releases |
| *Change failure rate* | (a) falhas ÷ (falhas + sucessos) nos *workflow runs* `push` | (b) fração de releases seguidas, em ≤ 7 dias, por release classificada como corretiva (patch SemVer **ou** palavras-chave `revert`/`hotfix`/`fix` nos commits); releases nos 7 dias finais são **censuradas** |
| Tempo de recuperação | Mediana dos episódios concluídos: sucesso − início da primeira falha no mesmo *workflow* | Episódios sem sucesso até o fim da janela são censurados; repositórios sem episódio ficam com valor ausente (não imputado) |

Tempos são reportados em **horas**; proporções em **0–1**. Célula vazia significa valor ausente, nunca zero. A classificação DORA (Elite/High/Medium/Low) usa a tabela de cortes da disciplina; a nota geral é a mediana das quatro notas, arredondada para baixo (*floor*). A combinação de referência **C1** usa release + *lead time* (a) + CFR (a) + recuperação.

### Protocolo de validação manual (amostra-ouro)

Para avaliar a heurística de release corretiva (CFR b) e o construto “entrega real”, sorteamos uma **amostra-ouro** de 60 repositórios da amostra final e 5 releases por repositório, ambos com sementes fixas (`random_state_repos=42`; releases: `42 + índice` com repositórios ordenados por `full_name`). As planilhas de rotulagem **não** incluem a saída da heurística automática.

Três avaliadores (`CaioVKodato`, `Henrique-volponi`, `Kjonps`) rotulam de forma independente:

- (i) tipo do projeto (biblioteca/framework, aplicação/serviço, ferramenta CLI, outro);
- (ii) se as releases representam entregas reais ao usuário (sim / não / incerto);
- (iii) para cada release sorteada, se é corretiva (sim / não), com base em *release notes* e commits.

A concordância é medida pelo **kappa de Fleiss** (`aggregate_raters` + `fleiss_kappa`, *statsmodels*) nas três dimensões, interpretado pela escala de Landis & Koch. O consenso usa **maioria simples**; empates 1–1–1 são decididos em reunião síncrona, sem consultar a heurística (ver `validacao/protocolo_desempate.md`). Sobre o consenso, calculam-se precisão, *recall* e F1 da heurística automática (Issue #29), eventualmente refinando regras se o desempenho for insuficiente.

### Ameaças à validade (prévia)

*Construto:* releases e CI não são produção. *Interna:* censura nos 7 dias finais (CFR b) e episódios de recuperação sem sucesso. *Externa:* amostra estratificada por estrelas e restrita a GitHub Actions. *Conclusão:* múltiplas variantes (RQ07) e testes com correção de Holm (RQ06) mitigam *p-hacking*, mas não o eliminam.

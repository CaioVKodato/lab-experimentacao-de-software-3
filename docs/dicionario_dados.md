# Dicionário de dados — dataset DORA (Lab03)

Gerado automaticamente a partir do esquema em `code/pipeline/dataset.py`.

## Convenções

- Tempos em **horas**. Proporções entre **0 e 1** (não em porcentagem).
- Datas em ISO 8601, UTC. A janela de observação é a definida pelo professor (`config.yaml`).
- **Célula vazia** = valor ausente (não calculável, ou etapa ainda não aplicada); nunca significa zero.
- Unidade de deploy: release publicada (`draft=false`, `prerelease=false`), só no default branch.
- Workflow runs: apenas `event=push` no default branch; `conclusion` classificada conforme a seção 3 do enunciado.

## Colunas

| Coluna | Tipo | Unidade | RQ | Origem / fórmula |
|---|---|---|---|---|
| `repo` | texto | — | ID | `full_name` do repositório (`dono/nome`) |
| `default_branch` | texto | — | ID | `default_branch` de `GET /repos/{o}/{r}` |
| `estrelas` | inteiro | contagem | RQ06 | `stargazers_count` de `GET /repos/{o}/{r}` |
| `linguagem_principal` | texto | — | RQ06 | `language` de `GET /repos/{o}/{r}` |
| `criado_em` | texto | ISO 8601 UTC | RQ06 | `created_at` de `GET /repos/{o}/{r}` (idade = coleta − criado_em) |
| `n_contribuidores` | inteiro | contagem | RQ06 | última página de `GET /repos/{o}/{r}/contributors?per_page=1&anon=true` |
| `n_releases_janela` | inteiro | contagem | RQ01 | releases com `draft=false`, `prerelease=false` e `published_at` dentro da janela (inclui a primeira da história) |
| `deploys_por_semana` | decimal | releases/semana | RQ01 | `n_releases_janela` ÷ semanas da janela (dias da janela ÷ 7, ≈ 52,1) |
| `lead_time_a_horas` | decimal | horas | RQ02 | mediana, entre releases, de (`published_at` da release − `author.date` do commit mais antigo incluído nela) |
| `lead_time_b_horas` | decimal | horas | RQ02 | mediana, entre TODOS os commits de TODAS as releases, de (`published_at` − `author.date` do commit) |
| `lt_n_releases_avaliadas` | inteiro | contagem | Qualidade | releases com ao menos 1 commit no `compare`, usadas no cálculo |
| `lt_n_ignoradas_compare` | inteiro | contagem | Qualidade | releases cujo `compare` falhou (ex.: 404 por tag apagada) e ficaram de fora |
| `lt_n_sem_commits` | inteiro | contagem | Qualidade | releases com `compare` ok mas sem commits novos |
| `lt_n_commits` | inteiro | contagem | Qualidade | total de commits usados na variante (b) |
| `n_runs_validas` | inteiro | contagem | RQ03 | workflow runs `push` do default branch com conclusion em sucesso ou falha (ignorados não contam) |
| `cfr_a` | decimal | proporção 0–1 | RQ03 | falhas ÷ (falhas + sucessos), todos os workflows juntos (proxy de CI) |
| `cfr_b` | decimal | proporção 0–1 | RQ03 | `cfr_b_n_falhas` ÷ `cfr_b_n_avaliadas` (heurística de `metricas/cfr_releases.py`); vazio se não há release avaliável |
| `cfr_b_n_avaliadas` | inteiro | contagem | Qualidade | releases no denominador: publicadas na janela e fora dos 7 dias finais (censura) |
| `cfr_b_n_falhas` | inteiro | contagem | Qualidade | releases seguidas de release corretiva em até 7 dias |
| `cfr_b_n_censuradas` | inteiro | contagem | Qualidade | releases publicadas nos 7 dias finais da janela, excluídas do denominador |
| `cfr_b_n_sem_compare` | inteiro | contagem | Qualidade | releases seguintes sem `compare` disponível (só o critério SemVer foi aplicado) |
| `cfr_b_heuristica` | texto | — | Qualidade | versão da heurística de release corretiva (v1 = patch do SemVer OU revert/hotfix/fix nos commits) |
| `recuperacao_mediana_horas` | decimal | horas | RQ04 | mediana dos episódios concluídos de todos os workflows: `updated_at` do sucesso − `run_started_at` da primeira falha |
| `recuperacao_n_episodios` | inteiro | contagem | Qualidade | episódios de falha (concluídos + censurados) em todos os workflows |
| `recuperacao_n_censurados` | inteiro | contagem | Qualidade | episódios sem sucesso posterior dentro da janela |
| `recuperacao_prop_censurados` | decimal | proporção 0–1 | RQ04 | `recuperacao_n_censurados` ÷ `recuperacao_n_episodios` |
| `meses_sem_runs` | inteiro | meses (0–12) | Qualidade | meses da janela sem nenhuma workflow run coletada (indício de histórico apagado pela API) |
| `coletado_em` | texto | ISO 8601 UTC | Qualidade | instante da coleta (a API pode deixar de devolver runs antigas) |

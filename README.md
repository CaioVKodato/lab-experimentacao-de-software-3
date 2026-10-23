# lab-experimentacao-de-software-3

Repositório do Lab03 — Mineração de Métricas DORA  
Disciplina: Laboratório de Experimentação de Software (PUC Minas)

Todo o código do pipeline fica em [`code/`](code/).

## Pré-requisitos

- Python 3.12+
- Token do GitHub com acesso de leitura à API pública

## Configuração

```bash
cd code
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Edite .env e preencha GITHUB_TOKEN
```

O token **nunca** deve ser commitado. O pipeline lê apenas a variável de ambiente `GITHUB_TOKEN`.

Ajuste a janela de observação e os demais parâmetros em [`code/config.yaml`](code/config.yaml) quando o professor publicar as datas oficiais do Lab03S01.

## Execução (comando único) — Issue #11

A partir da pasta `code/`:

```bash
# Windows (PowerShell)
$env:GITHUB_TOKEN = "seu_token"
python -m pipeline --config config.yaml

# Linux/macOS
export GITHUB_TOKEN=seu_token
python -m pipeline --config config.yaml
```

O padrão `--stage all` encadeia:

1. **selection** — busca fatiada + funil (≥ 5 releases, ≥ 50 runs)
2. **metadata** — estrelas, linguagem, contribuidores, idade, `default_branch`
3. **releases** — releases/tags na janela
4. **compare** — commits entre releases consecutivas
5. **workflow-runs** — runs `push` no default branch (com bisseção do teto de 1.000)

Cache/retomada em `data/cache/`. CSVs intermediários e `pipeline_manifest.csv` em `data/output/`.

Etapas isoladas (ex.: só metadados):

```bash
python -m pipeline --config config.yaml --stage metadata
```

Para testar com poucos candidatos na seleção:

```bash
python -m pipeline --config config.yaml --max-candidates 50
```

| Arquivo (em `data/output/`) | Conteúdo |
|---|---|
| `funnel.csv` / `funnel.json` | Funil de seleção |
| `sample_repos.csv` | Amostra aprovada (meta S01: 100) |
| `repo_metadata.csv` | Metadados |
| `releases.csv` / `tags.csv` | Releases e tags |
| `commits_between_releases.csv` | Commits do compare |
| `workflow_runs.csv` | Runs de CI |
| `pipeline_manifest.csv` | Resumo das etapas |

## CI (Issue #10)

A cada `push` e `pull_request`, [`.github/workflows/testes.yml`](.github/workflows/testes.yml) executa:

```bash
cd code
pytest --cov=metricas --cov-report=term-missing --cov-fail-under=80
```

## Artigo — Introdução (Issue #12) e Metodologia (Issue #30)

Rascunho da Introdução com hipóteses informais das RQ 01–07 (antes de ver os dados):

- Markdown: [`docs/artigo/introducao.md`](docs/artigo/introducao.md)
- Fragmento LaTeX (template SBC): [`docs/artigo/introducao.tex`](docs/artigo/introducao.tex)

Metodologia (fonte de dados, funil, definições, validação manual):

- Markdown: [`docs/artigo/metodologia.md`](docs/artigo/metodologia.md)
- Fragmento LaTeX: [`docs/artigo/metodologia.tex`](docs/artigo/metodologia.tex)

**Overleaf do grupo:** _adicionar URL do projeto aqui após criar no [template SBC](https://www.overleaf.com/latex/templates/sbc-conferences-template/blbxwjwzdngr)._

## Estrutura

```
code/
  pipeline/
    orchestrator.py   # pipeline único (Issue #11)
    amostra_ouro.py   # sorteio amostra-ouro (Issue #23)
    selection.py, metadata.py, releases.py, compare.py, workflow_runs.py, …
  metricas/
  tests/
  config.yaml
  data/               # cache/saídas (não versionado)
validacao/            # planilhas amostra-ouro (Issue #23)
docs/artigo/          # Introdução (Issue #12)
.github/workflows/    # CI (Issue #10)
```

## Amostra-ouro (Issue #23)

Com `sample_repos.csv` (≥ 60) e `releases.csv`:

```bash
python -m pipeline --config config.yaml --stage amostra-ouro
```

Saídas em [`validacao/`](validacao/). Ver [`validacao/README.md`](validacao/README.md).

## Testes

```bash
cd code
pytest
```

## Grupo

- CaioVKodato
- Kjonps
- Henrique-volponi

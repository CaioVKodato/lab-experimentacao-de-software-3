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

## Execução (comando único)

A partir da pasta `code/`:

```bash
# Windows (PowerShell)
$env:GITHUB_TOKEN = "seu_token"
python -m pipeline --config config.yaml --stage selection

# Linux/macOS
export GITHUB_TOKEN=seu_token
python -m pipeline --config config.yaml --stage selection
```

Para limitar quantos candidatos da busca serão avaliados (útil enquanto a cota da API é escassa):

```bash
python -m pipeline --config config.yaml --stage selection --max-candidates 50
```

Saídas da seleção (em `code/data/output/`):

| Arquivo | Conteúdo |
|---|---|
| `funnel.csv` / `funnel.json` | Funil: candidatos → com Actions → critério mínimo → amostra |
| `sample_repos.csv` | Repositórios aprovados (meta S01: 100) |
| `selection_evaluated.csv` | Log por repositório (motivo de descarte, contagens) |

A busca usa fatiamento por faixas de estrelas (`github.star_ranges` no `config.yaml`) para ultrapassar o limite de 1.000 resultados por consulta da Search API.

### Metadados da amostra (Issue #3)

Depois da seleção:

```bash
python -m pipeline --config config.yaml --stage metadata
```

Isso lê `data/output/sample_repos.csv`, busca na API (com cache JSON em `data/cache/metadata/`) e gera `data/output/repo_metadata.csv` com estrelas, linguagem, contribuidores (contagem via `Link` + `per_page=1`), `created_at`, idade em dias (até o fim da janela) e `default_branch`.

Para forçar nova coleta ignorando o cache: `--force-metadata`.

## Estrutura

```
code/
  pipeline/       # orquestração e coleta (API REST/GraphQL própria)
    github_client.py  # cliente HTTP mínimo (cache/backoff na Issue #4)
    cache.py          # cache JSON local / retomada
    selection.py      # seleção + funil de inclusão (Issue #2)
    metadata.py       # metadados da amostra (Issue #3)
  metricas/       # cálculo das métricas DORA
  tests/          # testes unitários (pytest)
  config.yaml     # janela, amostra alvo e caminhos
  requirements.txt
  .env.example
  data/           # cache e saídas (gerado em runtime; não versionado)
```

## Testes

```bash
cd code
pytest
```

## Grupo

- CaioVKodato
- Kjonps
- Henrique-volponi

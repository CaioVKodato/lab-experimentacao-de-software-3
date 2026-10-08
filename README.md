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
python -m pipeline --config config.yaml

# Linux/macOS
export GITHUB_TOKEN=seu_token
python -m pipeline --config config.yaml
```

## Estrutura

```
code/
  pipeline/       # orquestração e coleta (API REST/GraphQL própria)
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

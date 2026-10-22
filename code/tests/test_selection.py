"""Testes da seleção de repositórios e funil (Issue #2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pipeline.selection import (
    DEFAULT_STAR_RANGES,
    count_published_releases_in_window,
    count_valid_workflow_runs,
    evaluate_repo,
    has_github_actions,
    in_observation_window,
    parse_star_ranges,
    repo_from_search_item,
    run_selection,
    search_candidates,
    stars_query,
    write_selection_outputs,
    RepoCandidate,
    _month_slices,
)


WINDOW_START = date(2025, 10, 1)
WINDOW_END = date(2026, 9, 30)


class FakeClient:
    """Cliente falso com respostas programadas por path."""

    def __init__(self, responses: dict | None = None) -> None:
        self.per_page = 100
        self.calls: list[tuple[str, dict | None]] = []
        self._responses = responses or {}

    def get(self, path: str, params: dict | None = None):
        self.calls.append((path, params))
        key = path
        handler = self._responses.get(key)
        if callable(handler):
            return handler(params)
        if handler is not None:
            return handler
        raise AssertionError(f"sem resposta fake para GET {path} params={params}")

    def paginate(self, path: str, params: dict | None = None, **kwargs):
        body = self.get(path, params)
        if isinstance(body, list):
            items = body
        else:
            items_key = kwargs.get("items_key")
            items = body[items_key] if items_key else body
        max_items = kwargs.get("max_items")
        if max_items is not None:
            return items[:max_items]
        return items


def test_stars_query_and_ranges():
    assert stars_query(1000, 2000) == "stars:1000..2000"
    assert stars_query(100001, None) == "stars:>=100001"
    ranges = parse_star_ranges([{"min": 1, "max": 10}, {"min": 11, "max": None}])
    assert ranges == [(1, 10), (11, None)]
    assert parse_star_ranges(None) == DEFAULT_STAR_RANGES


def test_in_observation_window_and_month_slices():
    assert in_observation_window("2025-12-15T10:00:00Z", WINDOW_START, WINDOW_END)
    assert not in_observation_window("2024-01-01T00:00:00Z", WINDOW_START, WINDOW_END)
    slices = _month_slices(date(2025, 10, 15), date(2025, 12, 5))
    assert slices == [
        (date(2025, 10, 15), date(2025, 10, 31)),
        (date(2025, 11, 1), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 5)),
    ]


def test_repo_from_search_item():
    repo = repo_from_search_item(
        {
            "full_name": "acme/demo",
            "html_url": "https://github.com/acme/demo",
            "stargazers_count": 1500,
            "language": "Python",
            "default_branch": "main",
            "created_at": "2020-01-01T00:00:00Z",
            "forks_count": 10,
        }
    )
    assert repo.owner == "acme"
    assert repo.name == "demo"
    assert repo.default_branch == "main"


def test_search_candidates_slices_and_dedup():
    def search_handler(params):
        q = params["q"]
        if "stars:1000..2000" in q and params["page"] == 1:
            return {
                "items": [
                    {
                        "full_name": "org/a",
                        "stargazers_count": 1500,
                        "language": "Go",
                        "default_branch": "main",
                        "html_url": "https://github.com/org/a",
                    },
                    {
                        "full_name": "org/b",
                        "stargazers_count": 1200,
                        "language": "Python",
                        "default_branch": "master",
                        "html_url": "https://github.com/org/b",
                    },
                ]
            }
        if "stars:2001..5000" in q and params["page"] == 1:
            return {
                "items": [
                    {
                        "full_name": "org/a",  # duplicata
                        "stargazers_count": 1500,
                        "language": "Go",
                        "default_branch": "main",
                        "html_url": "https://github.com/org/a",
                    },
                    {
                        "full_name": "org/c",
                        "stargazers_count": 3000,
                        "language": "Rust",
                        "default_branch": "main",
                        "html_url": "https://github.com/org/c",
                    },
                ]
            }
        return {"items": []}

    client = FakeClient({"/search/repositories": search_handler})
    repos = search_candidates(
        client,
        [(1000, 2000), (2001, 5000)],
        max_candidates=10,
    )
    names = [r.full_name for r in repos]
    assert names == ["org/a", "org/b", "org/c"]
    assert any("stars:1000..2000" in (c[1] or {}).get("q", "") for c in client.calls)


def test_has_actions_and_release_count():
    client = FakeClient(
        {
            "/repos/acme/demo/actions/workflows": {"total_count": 2, "workflows": []},
            "/repos/acme/demo/releases": [
                {
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2025-11-01T00:00:00Z",
                },
                {
                    "draft": True,
                    "prerelease": False,
                    "published_at": "2025-11-02T00:00:00Z",
                },
                {
                    "draft": False,
                    "prerelease": True,
                    "published_at": "2025-11-03T00:00:00Z",
                },
                {
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2024-01-01T00:00:00Z",
                },
            ],
        }
    )
    assert has_github_actions(client, "acme", "demo") is True
    assert (
        count_published_releases_in_window(
            client, "acme", "demo", WINDOW_START, WINDOW_END
        )
        == 1
    )


def test_count_valid_workflow_runs_filters_conclusions():
    def runs_handler(params):
        assert params["event"] == "push"
        assert params["branch"] == "main"
        # uma fatia mensal qualquer
        return {
            "workflow_runs": [
                {"conclusion": "success"},
                {"conclusion": "failure"},
                {"conclusion": "cancelled"},
                {"conclusion": "timed_out"},
                {"conclusion": None},
                {"conclusion": "startup_failure"},
            ]
        }

    client = FakeClient({"/repos/acme/demo/actions/runs": runs_handler})
    # janela de 1 mês => uma fatia
    n = count_valid_workflow_runs(
        client,
        "acme",
        "demo",
        "main",
        date(2025, 10, 1),
        date(2025, 10, 31),
    )
    # success + failure + timed_out + startup_failure = 4 (cancelled/None ignorados)
    assert n == 4


def test_evaluate_repo_funnel_reasons():
    repo = RepoCandidate(
        full_name="acme/demo",
        owner="acme",
        name="demo",
        html_url="https://github.com/acme/demo",
        stargazers_count=2000,
        language="Python",
        default_branch="main",
    )
    no_actions = FakeClient(
        {"/repos/acme/demo/actions/workflows": {"total_count": 0, "workflows": []}}
    )
    row = evaluate_repo(
        no_actions,
        repo,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        min_releases=5,
        min_workflow_runs=50,
    )
    assert row["discard_reason"] == "no_actions"
    assert row["included"] is False


def test_run_selection_builds_funnel_and_sample(tmp_path: Path):
    search_items = [
        {
            "full_name": "org/keep",
            "stargazers_count": 5000,
            "language": "Python",
            "default_branch": "main",
            "html_url": "https://github.com/org/keep",
        },
        {
            "full_name": "org/noact",
            "stargazers_count": 4000,
            "language": "Go",
            "default_branch": "main",
            "html_url": "https://github.com/org/noact",
        },
        {
            "full_name": "org/fewrel",
            "stargazers_count": 3000,
            "language": "Rust",
            "default_branch": "main",
            "html_url": "https://github.com/org/fewrel",
        },
    ]

    def search_handler(params):
        if params["page"] == 1:
            return {"items": search_items}
        return {"items": []}

    release_dates = [
        "2025-10-15T00:00:00Z",
        "2025-11-15T00:00:00Z",
        "2025-12-15T00:00:00Z",
        "2026-01-15T00:00:00Z",
        "2026-02-15T00:00:00Z",
        "2026-03-15T00:00:00Z",
    ]
    releases_ok = [
        {"draft": False, "prerelease": False, "published_at": dt}
        for dt in release_dates
    ]
    releases_few = releases_ok[:2]

    def runs_many(_params):
        return {"workflow_runs": [{"conclusion": "success"}] * 50}

    def runs_few(_params):
        return {"workflow_runs": [{"conclusion": "success"}] * 3}

    responses = {
        "/search/repositories": search_handler,
        "/repos/org/keep/actions/workflows": {"total_count": 1},
        "/repos/org/noact/actions/workflows": {"total_count": 0},
        "/repos/org/fewrel/actions/workflows": {"total_count": 1},
        "/repos/org/keep/releases": releases_ok,
        "/repos/org/fewrel/releases": releases_few,
        "/repos/org/keep/actions/runs": runs_many,
        "/repos/org/fewrel/actions/runs": runs_few,
    }
    client = FakeClient(responses)

    config = {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "sample": {"target_size": 100, "min_releases": 5, "min_workflow_runs": 50},
        "github": {
            "star_ranges": [{"min": 1000, "max": 10000}],
            "max_search_candidates": 10,
        },
    }
    result = run_selection(client, config)
    assert result.funnel.candidates == 3
    assert result.funnel.with_actions == 2
    assert result.funnel.with_min_criteria == 1
    assert result.funnel.sample == 1
    assert result.funnel.discarded_no_actions == 1
    assert result.funnel.discarded_few_releases == 1
    assert result.sample[0].full_name == "org/keep"

    paths = write_selection_outputs(result, tmp_path)
    assert paths["funnel_csv"].is_file()
    assert paths["sample_csv"].is_file()
    content = paths["funnel_csv"].read_text(encoding="utf-8")
    assert "candidatos" in content
    assert "com_actions" in content


# ---------------------------------------------------------------------------
# Cota por faixa de estrelas e retomada por cache (Issue #20)
# ---------------------------------------------------------------------------

import csv  # noqa: E402

from pipeline.github_client import GitHubAPIError  # noqa: E402
from pipeline.selection import (  # noqa: E402
    faixa_de,
    quotas_por_faixa,
    rotulo_faixa,
    search_candidates_por_faixa,
)


def _item(nome: str, estrelas: int) -> dict:
    return {"full_name": nome, "stargazers_count": estrelas, "language": "Python",
            "default_branch": "main", "html_url": f"https://github.com/{nome}"}


def _cliente_de_mundo(mundo: dict[tuple[int, int], list[tuple[str, int, bool]]], erro_em: set[str] | None = None):
    """Cliente falso. `mundo[(min,max)]` = [(nome, estrelas, passa_nos_criterios)] por faixa."""
    erro_em = erro_em or set()
    datas = ["2025-10-15", "2025-11-15", "2025-12-15", "2026-01-15", "2026-02-15", "2026-03-15"]
    releases_ok = [{"draft": False, "prerelease": False, "published_at": f"{d}T00:00:00Z"} for d in datas]
    todos = {nome: passa for faixa in mundo.values() for nome, _e, passa in faixa}
    responses: dict = {}

    def busca(params):
        for (mn, mx), repos in mundo.items():
            if f"stars:{mn}..{mx}" in params["q"] or (mx is None and f"stars:>={mn}" in params["q"]):
                inicio = (params["page"] - 1) * params["per_page"]
                return {"items": [_item(n, e) for n, e, _p in repos][inicio:inicio + params["per_page"]]}
        return {"items": []}

    responses["/search/repositories"] = busca
    for nome, passa in todos.items():
        def workflows(_p, nome=nome):
            if nome in erro_em:
                raise GitHubAPIError(502, "bad gateway")
            return {"total_count": 1}
        responses[f"/repos/{nome}/actions/workflows"] = workflows
        responses[f"/repos/{nome}/releases"] = releases_ok if passa else releases_ok[:1]
        responses[f"/repos/{nome}/actions/runs"] = lambda _p: {"workflow_runs": [{"conclusion": "success"}] * 50}
    return FakeClient(responses)


def _config(target: int, faixas: list[tuple[int, int | None]], **extra) -> dict:
    return {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "sample": {"target_size": target, "min_releases": 5, "min_workflow_runs": 50, "stratify": True},
        "github": {"star_ranges": [{"min": a, "max": b} for a, b in faixas], **extra},
    }


def _por_faixa(sample, faixas):
    contagem = [0] * len(faixas)
    for r in sample:
        contagem[faixa_de(r.stargazers_count, faixas)] += 1
    return contagem


FAIXAS3 = [(1000, 2000), (2001, 5000), (5001, 10000)]


def test_faixa_de_respeita_limites_inclusivos_e_faixa_sem_teto():
    assert faixa_de(1000, FAIXAS3) == 0 and faixa_de(2000, FAIXAS3) == 0
    assert faixa_de(2001, FAIXAS3) == 1 and faixa_de(10000, FAIXAS3) == 2
    assert faixa_de(500, FAIXAS3) is None and faixa_de(10001, FAIXAS3) is None
    assert faixa_de(10**6, [(100001, None)]) == 0


def test_rotulo_da_faixa():
    assert rotulo_faixa(1000, 2000) == "1000-2000"
    assert rotulo_faixa(100001, None) == "100001+"


def test_quotas_dividem_o_alvo_e_distribuem_a_sobra_nas_primeiras_faixas():
    assert quotas_por_faixa(10, 3) == [4, 3, 3]
    assert quotas_por_faixa(300, 7) == [43, 43, 43, 43, 43, 43, 42]
    assert sum(quotas_por_faixa(300, 7)) == 300


def test_busca_por_faixa_agrupa_remove_duplicata_e_respeita_o_limite_por_faixa():
    mundo = {(1000, 2000): [("o/a", 1500, True), ("o/b", 1400, True), ("o/c", 1300, True)],
             (2001, 5000): [("o/a", 1500, True), ("o/d", 3000, True)]}  # o/a repetido
    grupos = search_candidates_por_faixa(_cliente_de_mundo(mundo), [(1000, 2000), (2001, 5000)], max_por_faixa=2)
    assert [[r.full_name for r in g] for g in grupos] == [["o/a", "o/b"], ["o/d"]]


def test_estratificado_respeita_a_cota_e_nao_avalia_alem_dela():
    mundo = {faixa: [(f"f{i}/r{j}", faixa[0] + j, True) for j in range(6)] for i, faixa in enumerate(FAIXAS3)}
    cliente = _cliente_de_mundo(mundo)
    res = run_selection(cliente, _config(6, FAIXAS3))
    assert _por_faixa(res.sample, FAIXAS3) == [2, 2, 2]
    assert len(res.evaluated) == 6  # parou de avaliar cada faixa ao fechar a cota
    assert {row["faixa"] for row in res.evaluated} == {"1000-2000", "2001-5000", "5001-10000"}


def test_estratificado_preenche_o_deficit_em_rodizio_entre_as_outras_faixas():
    mundo = {
        (1000, 2000): [("a/x", 1500, True)] + [(f"a/n{j}", 1500 + j, False) for j in range(3)],  # só 1 passa
        (2001, 5000): [(f"b/r{j}", 2001 + j, True) for j in range(6)],
        (5001, 10000): [(f"c/r{j}", 5001 + j, True) for j in range(6)],
    }
    res = run_selection(_cliente_de_mundo(mundo), _config(6, FAIXAS3))
    assert len(res.sample) == 6
    assert _por_faixa(res.sample, FAIXAS3) == [1, 3, 2]  # déficit de 1 vai para a primeira faixa com sobra


def test_estratificado_nao_atinge_o_alvo_quando_faltam_candidatos_e_nao_quebra():
    mundo = {faixa: [(f"f{i}/r{j}", faixa[0] + j, True) for j in range(1)] for i, faixa in enumerate(FAIXAS3)}
    res = run_selection(_cliente_de_mundo(mundo), _config(9, FAIXAS3))
    assert len(res.sample) == 3


def test_modo_estratificado_usa_max_candidates_como_limite_por_faixa():
    mundo = {faixa: [(f"f{i}/r{j}", faixa[0] + j, False) for j in range(8)] for i, faixa in enumerate(FAIXAS3)}
    res = run_selection(_cliente_de_mundo(mundo), _config(6, FAIXAS3), max_candidates=3)
    assert res.funnel.candidates == 9  # 3 por faixa, e não 3 no total


def test_funil_por_faixa_e_gravado_em_csv(tmp_path: Path):
    mundo = {faixa: [(f"f{i}/r{j}", faixa[0] + j, True) for j in range(4)] for i, faixa in enumerate(FAIXAS3)}
    res = run_selection(_cliente_de_mundo(mundo), _config(6, FAIXAS3))
    paths = write_selection_outputs(res, tmp_path)
    with paths["funnel_por_faixa_csv"].open(encoding="utf-8", newline="") as fh:
        linhas = list(csv.DictReader(fh))
    assert [l["faixa"] for l in linhas] == ["1000-2000", "2001-5000", "5001-10000"]
    assert all(l["cota"] == "2" and l["aprovados"] == "2" and l["avaliados"] == "2" for l in linhas)


def test_modo_antigo_nao_gera_funil_por_faixa(tmp_path: Path):
    mundo = {(1000, 2000): [("o/a", 1500, True)]}
    cfg = _config(5, [(1000, 2000)])
    cfg["sample"].pop("stratify")
    paths = write_selection_outputs(run_selection(_cliente_de_mundo(mundo), cfg), tmp_path)
    assert "funnel_por_faixa_csv" not in paths


# ---- retomada por cache ----

def _cfg_cache(tmp_path: Path, **sample) -> dict:
    cfg = _config(2, [(1000, 2000)])
    cfg["sample"].update(sample)
    cfg["paths"] = {"cache_dir": str(tmp_path / "cache")}
    return cfg


def _chamadas_por_repo(cliente) -> int:
    return sum(1 for caminho, _p in cliente.calls if caminho.startswith("/repos/"))


def test_segunda_execucao_retoma_do_cache_sem_reavaliar(tmp_path: Path):
    mundo = {(1000, 2000): [("o/a", 1500, True), ("o/b", 1400, True), ("o/c", 1300, False)]}
    primeiro = _cliente_de_mundo(mundo)
    r1 = run_selection(primeiro, _cfg_cache(tmp_path))
    segundo = _cliente_de_mundo(mundo)
    r2 = run_selection(segundo, _cfg_cache(tmp_path))
    assert _chamadas_por_repo(primeiro) > 0 and _chamadas_por_repo(segundo) == 0
    assert [r.full_name for r in r1.sample] == [r.full_name for r in r2.sample]


def test_erro_de_api_nao_e_cacheado_e_e_refeito(tmp_path: Path):
    mundo = {(1000, 2000): [("o/a", 1500, True), ("o/b", 1400, True)]}
    r1 = run_selection(_cliente_de_mundo(mundo, erro_em={"o/a"}), _cfg_cache(tmp_path))
    assert r1.funnel.discarded_api_error == 1 and len(r1.sample) == 1
    r2 = run_selection(_cliente_de_mundo(mundo), _cfg_cache(tmp_path))
    assert r2.funnel.discarded_api_error == 0 and len(r2.sample) == 2


def test_cache_nao_e_reaproveitado_com_outra_janela_ou_outros_criterios(tmp_path: Path):
    mundo = {(1000, 2000): [("o/a", 1500, True), ("o/b", 1400, True)]}
    run_selection(_cliente_de_mundo(mundo), _cfg_cache(tmp_path))
    outra_janela = _cfg_cache(tmp_path)
    outra_janela["observation_window"] = {"start": "2025-11-01", "end": "2026-10-31"}
    c1 = _cliente_de_mundo(mundo)
    run_selection(c1, outra_janela)
    assert _chamadas_por_repo(c1) > 0
    c2 = _cliente_de_mundo(mundo)
    run_selection(c2, _cfg_cache(tmp_path, min_releases=6))
    assert _chamadas_por_repo(c2) > 0


def test_sem_cache_dir_no_config_nada_e_gravado(tmp_path: Path):
    mundo = {(1000, 2000): [("o/a", 1500, True)]}
    cfg = _config(1, [(1000, 2000)])
    run_selection(_cliente_de_mundo(mundo), cfg)
    assert not (tmp_path / "cache").exists()


def test_estrelas_do_cache_sao_atualizadas_com_as_atuais(tmp_path: Path):
    mundo_antes = {(1000, 2000): [("o/a", 1500, True)]}
    run_selection(_cliente_de_mundo(mundo_antes), _cfg_cache(tmp_path))
    mundo_depois = {(1000, 2000): [("o/a", 1900, True)]}
    r = run_selection(_cliente_de_mundo(mundo_depois), _cfg_cache(tmp_path))
    assert r.evaluated[0]["stargazers_count"] == 1900

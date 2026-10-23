"""Testes da coleta de workflow runs (Issue #8), com cliente falso.

O cliente falso reproduz o comportamento relevante da API: filtra pelo intervalo
`created=A..B` e devolve no máximo TETO resultados por consulta.
"""
from datetime import date

import pytest

from metricas.janela import parse_janela
from pipeline import workflow_runs as wr
from pipeline.cache import JsonCache
from pipeline.workflow_runs import CAMPOS, coletar_workflow_runs, meses_da_janela


def run(rid, dia):
    """Run no formato cru da API, com campos extras que devem ser descartados."""
    return {
        "id": rid, "workflow_id": 1, "name": "CI", "event": "push",
        "head_branch": "main", "head_sha": "abc", "status": "completed",
        "conclusion": "success", "created_at": f"{dia}T10:00:00Z",
        "run_started_at": f"{dia}T10:00:05Z", "updated_at": f"{dia}T10:05:00Z",
        "repository": {"full_name": "o/r", "enorme": "x" * 100}, "actor": {"login": "u"},
    }


class ClienteFalso:
    def __init__(self, runs, falhar_na_consulta=None):
        self.runs = runs
        self.chamadas = []
        self.falhar = falhar_na_consulta

    def paginate(self, path, params=None, *, items_key=None, max_items=None):
        self.chamadas.append((path, dict(params or {}), items_key))
        if self.falhar is not None and len(self.chamadas) >= self.falhar:
            raise RuntimeError("queda de rede")
        ini, fim = params["created"].split("..")
        achados = [r for r in self.runs if ini <= r["created_at"][:10] <= fim]
        return achados[: wr.TETO]


@pytest.fixture
def cache(tmp_path):
    return JsonCache(tmp_path / "cache")


MARCO = parse_janela("2026-03-01", "2026-03-31")


# ---------- fatiamento da janela em meses ----------

def test_janela_de_12_meses_vira_12_intervalos():
    meses = meses_da_janela(parse_janela("2025-10-01", "2026-09-30"))
    assert len(meses) == 12
    assert meses[0] == (date(2025, 10, 1), date(2025, 10, 31))
    assert meses[-1] == (date(2026, 9, 1), date(2026, 9, 30))


def test_primeiro_e_ultimo_mes_sao_recortados():
    meses = meses_da_janela(parse_janela("2026-01-15", "2026-02-10"))
    assert meses == [(date(2026, 1, 15), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 2, 10))]


def test_virada_de_ano_e_fevereiro_bissexto():
    meses = meses_da_janela(parse_janela("2027-12-01", "2028-02-29"))
    assert meses == [
        (date(2027, 12, 1), date(2027, 12, 31)),
        (date(2028, 1, 1), date(2028, 1, 31)),
        (date(2028, 2, 1), date(2028, 2, 29)),
    ]


# ---------- parâmetros da consulta e projeção ----------

def test_consulta_usa_branch_push_e_intervalo_do_mes(cache):
    cliente = ClienteFalso([])
    coletar_workflow_runs(cliente, cache, "o", "r", "main", MARCO)
    path, params, items_key = cliente.chamadas[0]
    assert path == "/repos/o/r/actions/runs"
    assert params == {
        "branch": "main", "event": "push", "status": "completed",
        "created": "2026-03-01..2026-03-31",
    }
    assert items_key == "workflow_runs"


def test_guarda_apenas_os_campos_usados_pelas_metricas(cache):
    cliente = ClienteFalso([run(1, "2026-03-05")])
    res = coletar_workflow_runs(cliente, cache, "o", "r", "main", MARCO)
    assert len(res.runs) == 1
    assert set(res.runs[0]) == set(CAMPOS)
    assert "repository" not in res.runs[0]


def test_uma_consulta_por_mes_quando_nao_ha_teto(cache):
    cliente = ClienteFalso([])
    res = coletar_workflow_runs(cliente, cache, "o", "r", "main", parse_janela("2025-10-01", "2026-09-30"))
    assert len(cliente.chamadas) == 12
    assert res.n_consultas == 12
    assert res.runs == [] and res.n_truncados == 0


# ---------- teto de 1.000 resultados ----------

def test_mes_que_bate_no_teto_e_dividido_e_nada_se_perde(cache, monkeypatch):
    monkeypatch.setattr(wr, "TETO", 5)
    todas = [run(i, f"2026-03-{i:02d}") for i in range(1, 13)]
    cliente = ClienteFalso(todas)
    res = coletar_workflow_runs(cliente, cache, "o", "r", "main", MARCO)
    assert sorted(r["id"] for r in res.runs) == list(range(1, 13))
    assert res.n_consultas > 1
    assert res.n_truncados == 0


def test_dia_unico_acima_do_teto_e_registrado_como_truncado(cache, monkeypatch):
    monkeypatch.setattr(wr, "TETO", 5)
    todas = [run(i, "2026-03-10") for i in range(1, 9)]
    res = coletar_workflow_runs(ClienteFalso(todas), cache, "o", "r", "main", MARCO)
    assert res.intervalos_truncados == (("2026-03-10", "2026-03-10"),)
    assert res.n_truncados == 1
    assert len(res.runs) == 5  # o que a API entregou, sem inventar o resto


# ---------- cache e retomada ----------

def test_segunda_execucao_nao_faz_nenhuma_chamada(cache):
    runs = [run(1, "2026-03-05")]
    coletar_workflow_runs(ClienteFalso(runs), cache, "o", "r", "main", MARCO)
    segundo = ClienteFalso(runs)
    res = coletar_workflow_runs(segundo, cache, "o", "r", "main", MARCO)
    assert segundo.chamadas == []
    assert res.n_consultas == 0
    assert [r["id"] for r in res.runs] == [1]


def test_decisao_de_dividir_tambem_fica_em_cache(cache, monkeypatch):
    monkeypatch.setattr(wr, "TETO", 5)
    todas = [run(i, f"2026-03-{i:02d}") for i in range(1, 13)]
    primeira = coletar_workflow_runs(ClienteFalso(todas), cache, "o", "r", "main", MARCO)
    segundo = ClienteFalso(todas)
    res = coletar_workflow_runs(segundo, cache, "o", "r", "main", MARCO)
    assert segundo.chamadas == []
    assert sorted(r["id"] for r in res.runs) == sorted(r["id"] for r in primeira.runs)


def test_retoma_de_onde_parou_apos_queda_de_rede(cache):
    tres_meses = parse_janela("2026-01-01", "2026-03-31")
    runs = [run(1, "2026-01-10"), run(2, "2026-02-10"), run(3, "2026-03-10")]
    caiu = ClienteFalso(runs, falhar_na_consulta=3)
    with pytest.raises(RuntimeError):
        coletar_workflow_runs(caiu, cache, "o", "r", "main", tres_meses)

    retomada = ClienteFalso(runs)
    res = coletar_workflow_runs(retomada, cache, "o", "r", "main", tres_meses)
    assert len(retomada.chamadas) == 1  # só o mês que faltava
    assert sorted(r["id"] for r in res.runs) == [1, 2, 3]


def test_repositorios_diferentes_nao_compartilham_cache(cache):
    coletar_workflow_runs(ClienteFalso([run(1, "2026-03-05")]), cache, "o", "a", "main", MARCO)
    outro = ClienteFalso([run(2, "2026-03-05")])
    res = coletar_workflow_runs(outro, cache, "o", "b", "main", MARCO)
    assert len(outro.chamadas) == 1
    assert [r["id"] for r in res.runs] == [2]

# ---------------------------------------------------------------------------
# Um mês que falha de forma persistente não derruba a coleta (Issue #20)
# ---------------------------------------------------------------------------

from pipeline.github_client import GitHubAPIError  # noqa: E402


class ClienteComErro(ClienteFalso):
    """Como o ClienteFalso, mas falha com GitHubAPIError nos meses indicados."""

    def __init__(self, runs, erros_por_mes):
        super().__init__(runs)
        self.erros_por_mes = erros_por_mes

    def paginate(self, path, params=None, *, items_key=None, max_items=None):
        mes = params["created"].split("..")[0][:7]
        if mes in self.erros_por_mes:
            self.chamadas.append((path, dict(params or {}), items_key))
            raise GitHubAPIError(self.erros_por_mes[mes], "simulado")
        return super().paginate(path, params, items_key=items_key, max_items=max_items)


TRES_MESES = parse_janela("2026-01-01", "2026-03-31")
RUNS_3M = [run(1, "2026-01-10"), run(2, "2026-02-10"), run(3, "2026-03-10")]


def test_mes_com_erro_5xx_e_pulado_nao_vai_para_o_cache_e_e_refeito(cache):
    res = coletar_workflow_runs(ClienteComErro(RUNS_3M, {"2026-02": 503}), cache, "o", "r", "main", TRES_MESES)
    assert res.n_falhas == 1 and sorted(r["id"] for r in res.runs) == [1, 3]
    assert cache.load("o__r/workflow_runs/2026-02-01_2026-02-28") is None

    retomada = ClienteFalso(RUNS_3M)
    res2 = coletar_workflow_runs(retomada, cache, "o", "r", "main", TRES_MESES)
    assert len(retomada.chamadas) == 1 and res2.n_falhas == 0  # só o mês que faltava
    assert sorted(r["id"] for r in res2.runs) == [1, 2, 3]


def test_falhas_seguidas_abortam_a_coleta_do_repositorio(cache):
    meses = {f"2026-{m:02d}": 503 for m in range(1, 13)}
    cliente = ClienteComErro([], meses)
    with pytest.raises(GitHubAPIError, match="seguidas"):
        coletar_workflow_runs(cliente, cache, "o", "r", "main", parse_janela("2026-01-01", "2026-12-31"))
    assert len(cliente.chamadas) == wr.MAX_FALHAS_SEGUIDAS


def test_falhas_isoladas_nao_abortam(cache):
    erros = {"2026-01": 500, "2026-03": 500, "2026-05": 500}
    res = coletar_workflow_runs(ClienteComErro([], erros), cache, "o", "r", "main",
                                parse_janela("2026-01-01", "2026-06-30"))
    assert res.n_falhas == 3


@pytest.mark.parametrize("status", [401, 403])
def test_erro_de_credencial_continua_derrubando(cache, status):
    with pytest.raises(GitHubAPIError):
        coletar_workflow_runs(ClienteComErro([], {"2026-01": status}), cache, "o", "r", "main", TRES_MESES)


def test_repositorio_inexistente_404_e_registrado_sem_abortar(cache):
    erros = {f"2026-{m:02d}": 404 for m in range(1, 4)}
    res = coletar_workflow_runs(ClienteComErro([], erros), cache, "o", "r", "main", TRES_MESES)
    assert res.n_falhas == 3 and res.runs == []

"""Testes de CFR (variante a) e tempo de recuperação — RQ 03(a) e RQ 04.

Os casos numéricos vêm dos exemplos da seção 5 do enunciado do Lab03.
"""
import pytest

from metricas.cfr_recuperacao import (
    change_failure_rate_ci,
    classificar_conclusion,
    episodios_de_falha,
    tempo_recuperacao_repositorio,
)


def run(wid, inicio, conclusion, fim=None, rid=0):
    """Fixture mínima de um workflow run no formato da API do GitHub."""
    return {
        "id": rid,
        "workflow_id": wid,
        "conclusion": conclusion,
        "run_started_at": f"2026-03-01T{inicio}:00Z",
        "updated_at": f"2026-03-01T{fim or inicio}:00Z",
    }


# ---------- classificação de conclusion ----------

@pytest.mark.parametrize("conclusion", ["success"])
def test_sucesso(conclusion):
    assert classificar_conclusion(conclusion) == "sucesso"


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_falha(conclusion):
    assert classificar_conclusion(conclusion) == "falha"


@pytest.mark.parametrize(
    "conclusion",
    ["cancelled", "skipped", "neutral", "action_required", "stale", None, ""],
)
def test_ignorados(conclusion):
    assert classificar_conclusion(conclusion) is None


# ---------- CFR variante (a) ----------

def test_cfr_mistura_de_falhas_e_sucessos():
    runs = [run(1, "09:00", c) for c in ["success", "success", "success", "failure"]]
    assert change_failure_rate_ci(runs) == pytest.approx(0.25)


def test_cfr_conta_timed_out_e_startup_failure_como_falha():
    runs = [run(1, "09:00", c) for c in ["success", "timed_out", "startup_failure", "success"]]
    assert change_failure_rate_ci(runs) == pytest.approx(0.5)


def test_cfr_ignora_cancelled_skipped_e_em_andamento():
    runs = [run(1, "09:00", c) for c in ["success", "failure", "cancelled", "skipped", None]]
    assert change_failure_rate_ci(runs) == pytest.approx(0.5)


def test_cfr_so_sucessos_vale_zero():
    assert change_failure_rate_ci([run(1, "09:00", "success")] * 4) == 0.0


def test_cfr_so_falhas_vale_um():
    assert change_failure_rate_ci([run(1, "09:00", "failure")] * 4) == 1.0


def test_cfr_sem_runs_validos_retorna_none():
    assert change_failure_rate_ci([]) is None
    assert change_failure_rate_ci([run(1, "09:00", "cancelled")]) is None


def test_cfr_soma_todos_os_workflows():
    runs = [run(1, "09:00", "failure"), run(2, "09:00", "success"),
            run(3, "09:00", "success"), run(3, "10:00", "success")]
    assert change_failure_rate_ci(runs) == pytest.approx(0.25)


# ---------- episódios de falha (exemplo do enunciado) ----------

def test_episodio_do_enunciado_vale_1h20():
    runs = [
        run(1, "09:00", "success", fim="09:05"),
        run(1, "10:00", "failure"),
        run(1, "10:30", "failure"),
        run(1, "11:15", "success", fim="11:20"),
    ]
    eps = episodios_de_falha(runs)
    assert len(eps) == 1
    assert eps[0].censurado is False
    assert eps[0].horas == pytest.approx(80 / 60)


def test_falha_sem_recuperacao_e_censurada_e_nao_descartada():
    runs = [run(1, "09:00", "success"), run(1, "10:00", "failure"), run(1, "11:00", "failure")]
    eps = episodios_de_falha(runs)
    assert len(eps) == 1
    assert eps[0].censurado is True
    assert eps[0].horas is None


def test_falha_antes_de_qualquer_sucesso_nao_abre_episodio():
    runs = [run(1, "08:00", "failure"), run(1, "09:00", "success"), run(1, "10:00", "success")]
    assert episodios_de_falha(runs) == []


def test_cancelled_no_meio_nao_encerra_nem_abre_episodio():
    runs = [
        run(1, "09:00", "success"),
        run(1, "10:00", "failure"),
        run(1, "10:30", "cancelled"),
        run(1, "11:00", "success", fim="11:20"),
    ]
    eps = episodios_de_falha(runs)
    assert len(eps) == 1
    assert eps[0].horas == pytest.approx(80 / 60)


def test_em_andamento_nao_encerra_episodio():
    runs = [run(1, "09:00", "success"), run(1, "10:00", "failure"), run(1, "10:30", None)]
    eps = episodios_de_falha(runs)
    assert len(eps) == 1 and eps[0].censurado is True


def test_ordem_de_entrada_nao_importa():
    runs = [
        run(1, "11:15", "success", fim="11:20", rid=4),
        run(1, "10:00", "failure", rid=2),
        run(1, "09:00", "success", rid=1),
        run(1, "10:30", "failure", rid=3),
    ]
    eps = episodios_de_falha(runs)
    assert len(eps) == 1 and eps[0].horas == pytest.approx(80 / 60)


def test_dois_episodios_no_mesmo_workflow():
    runs = [
        run(1, "09:00", "success"),
        run(1, "10:00", "failure"), run(1, "11:00", "success"),
        run(1, "12:00", "success"),
        run(1, "13:00", "failure"), run(1, "15:00", "success"),
    ]
    eps = episodios_de_falha(runs)
    assert [e.horas for e in eps] == [pytest.approx(1.0), pytest.approx(2.0)]


def test_sem_runs_nao_ha_episodios():
    assert episodios_de_falha([]) == []


# ---------- agregação por repositório ----------

def test_repositorio_agrupa_por_workflow_e_nao_mistura_sucessos():
    # O sucesso do workflow 2 NÃO pode encerrar a falha do workflow 1.
    runs = [
        run(1, "09:00", "success"), run(1, "10:00", "failure"),
        run(2, "09:00", "success"), run(2, "10:00", "failure"), run(2, "12:00", "success"),
    ]
    res = tempo_recuperacao_repositorio(runs)
    assert res.n_episodios == 2
    assert res.n_censurados == 1
    assert res.proporcao_censurados == pytest.approx(0.5)
    assert res.mediana_horas == pytest.approx(2.0)


def test_repositorio_mediana_entre_episodios_de_varios_workflows():
    runs = [
        run(1, "09:00", "success"), run(1, "10:00", "failure"), run(1, "11:00", "success"),
        run(2, "09:00", "success"), run(2, "10:00", "failure"), run(2, "13:00", "success"),
        run(3, "09:00", "success"), run(3, "10:00", "failure"), run(3, "15:00", "success"),
    ]
    res = tempo_recuperacao_repositorio(runs)
    assert res.mediana_horas == pytest.approx(3.0)  # mediana de 1h, 3h, 5h
    assert res.proporcao_censurados == 0.0


def test_repositorio_sem_falhas():
    runs = [run(1, "09:00", "success"), run(1, "10:00", "success")]
    res = tempo_recuperacao_repositorio(runs)
    assert res.n_episodios == 0
    assert res.mediana_horas is None
    assert res.proporcao_censurados is None


def test_repositorio_so_com_episodios_censurados_tem_mediana_none():
    runs = [run(1, "09:00", "success"), run(1, "10:00", "failure")]
    res = tempo_recuperacao_repositorio(runs)
    assert res.mediana_horas is None
    assert res.proporcao_censurados == 1.0
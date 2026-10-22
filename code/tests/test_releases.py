"""Testes da coleta de releases e tags (Issue #5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.cache import JsonCache
from pipeline.releases import (
    Release,
    ReleasesResult,
    Tag,
    _in_window,
    _parse_releases,
    _parse_tags,
    collect_repo_releases,
    collect_sample_releases,
    run_releases_collection,
    write_releases_csv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _release(tag: str, published_at: str, *, draft=False, prerelease=False, name=None):
    return {
        "tag_name": tag,
        "name": name or tag,
        "published_at": published_at,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
    }


def _tag(name: str, sha: str = "abc123"):
    return {"name": name, "commit": {"sha": sha}}


class FakeClient:
    def __init__(self, releases: list, tags: list) -> None:
        self._releases = releases
        self._tags = tags
        self.calls: list[str] = []

    def paginate(self, path: str, params=None, *, items_key=None, max_items=None) -> list:
        self.calls.append(path)
        if "/releases" in path:
            return self._releases
        if "/tags" in path:
            return self._tags
        return []


@pytest.fixture
def cache(tmp_path: Path) -> JsonCache:
    return JsonCache(tmp_path / "cache")


WINDOW = ("2025-10-01", "2026-09-30")


# ---------------------------------------------------------------------------
# _in_window
# ---------------------------------------------------------------------------

def test_in_window_dentro():
    assert _in_window("2026-01-15T10:00:00Z", "2025-10-01", "2026-09-30") is True


def test_in_window_limite_inicio():
    assert _in_window("2025-10-01T00:00:00Z", "2025-10-01", "2026-09-30") is True


def test_in_window_limite_fim():
    assert _in_window("2026-09-30T23:59:59Z", "2025-10-01", "2026-09-30") is True


def test_in_window_antes():
    assert _in_window("2025-09-30T23:59:59Z", "2025-10-01", "2026-09-30") is False


def test_in_window_depois():
    assert _in_window("2026-10-01T00:00:00Z", "2025-10-01", "2026-09-30") is False


# ---------------------------------------------------------------------------
# _parse_releases
# ---------------------------------------------------------------------------

def test_parse_releases_exclui_draft():
    raw = [_release("v1.0", "2026-01-01T00:00:00Z", draft=True)]
    assert _parse_releases(raw, "o/r", *WINDOW) == []


def test_parse_releases_exclui_sem_published_at():
    raw = [{"tag_name": "v1.0", "draft": False, "prerelease": False, "published_at": None}]
    assert _parse_releases(raw, "o/r", *WINDOW) == []


def test_parse_releases_filtra_fora_da_janela():
    raw = [
        _release("v0.9", "2025-09-01T00:00:00Z"),  # antes
        _release("v1.0", "2026-01-15T00:00:00Z"),  # dentro
        _release("v1.1", "2026-10-01T00:00:00Z"),  # depois
    ]
    result = _parse_releases(raw, "o/r", *WINDOW)
    assert len(result) == 1
    assert result[0].tag_name == "v1.0"


def test_parse_releases_marca_prerelease():
    raw = [_release("v1.0-rc1", "2026-03-01T00:00:00Z", prerelease=True)]
    result = _parse_releases(raw, "o/r", *WINDOW)
    assert len(result) == 1
    assert result[0].prerelease is True
    assert result[0].full_name == "o/r"


def test_parse_releases_release_normal_nao_e_prerelease():
    raw = [_release("v1.0", "2026-03-01T00:00:00Z")]
    result = _parse_releases(raw, "o/r", *WINDOW)
    assert result[0].prerelease is False


# ---------------------------------------------------------------------------
# _parse_tags
# ---------------------------------------------------------------------------

def test_parse_tags_extrai_nome_e_sha():
    raw = [_tag("v1.0", sha="deadbeef"), _tag("v0.9", sha="cafebabe")]
    result = _parse_tags(raw, "o/r")
    assert len(result) == 2
    assert result[0] == Tag(full_name="o/r", tag_name="v1.0", sha="deadbeef")


def test_parse_tags_lista_vazia():
    assert _parse_tags([], "o/r") == []


# ---------------------------------------------------------------------------
# collect_repo_releases
# ---------------------------------------------------------------------------

def test_collect_repo_releases_basico(cache: JsonCache):
    client = FakeClient(
        releases=[_release("v1.0", "2026-01-15T00:00:00Z")],
        tags=[_tag("v1.0")],
    )
    result = collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert isinstance(result, ReleasesResult)
    assert result.full_name == "o/r"
    assert len(result.releases) == 1
    assert result.releases[0].tag_name == "v1.0"
    assert len(result.tags) == 1
    assert result.from_cache is False


def test_collect_repo_releases_faz_duas_chamadas(cache: JsonCache):
    client = FakeClient(releases=[], tags=[])
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert any("/releases" in c for c in client.calls)
    assert any("/tags" in c for c in client.calls)
    assert len(client.calls) == 2


def test_segunda_chamada_retorna_do_cache(cache: JsonCache):
    client = FakeClient(
        releases=[_release("v1.0", "2026-01-15T00:00:00Z")],
        tags=[_tag("v1.0")],
    )
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    calls_antes = len(client.calls)

    result2 = collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert result2.from_cache is True
    assert len(client.calls) == calls_antes  # nenhuma chamada nova
    assert result2.releases[0].tag_name == "v1.0"
    assert result2.tags[0].sha == "abc123"


def test_force_ignora_cache(cache: JsonCache):
    client = FakeClient(releases=[], tags=[])
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1], force=True)
    assert len(client.calls) == 4  # 2 na primeira + 2 na segunda (force)


def test_repos_diferentes_nao_compartilham_cache(cache: JsonCache):
    client_a = FakeClient(releases=[_release("v1.0", "2026-01-15T00:00:00Z")], tags=[])
    client_b = FakeClient(releases=[_release("v2.0", "2026-06-01T00:00:00Z")], tags=[])
    collect_repo_releases(client_a, "o", "a", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    result_b = collect_repo_releases(client_b, "o", "b", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert result_b.releases[0].tag_name == "v2.0"
    assert len(client_b.calls) == 2


# ---------------------------------------------------------------------------
# collect_sample_releases
# ---------------------------------------------------------------------------

def test_collect_sample_releases_multiplos_repos(cache: JsonCache):
    client = FakeClient(releases=[_release("v1.0", "2026-01-01T00:00:00Z")], tags=[_tag("v1.0")])
    rows = [{"full_name": "o/a"}, {"full_name": "o/b"}]
    results = collect_sample_releases(client, rows, cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert len(results) == 2
    assert results[0].full_name == "o/a"
    assert results[1].full_name == "o/b"


def test_collect_sample_releases_full_name_invalido(cache: JsonCache):
    client = FakeClient(releases=[], tags=[])
    with pytest.raises(ValueError, match="inválido"):
        collect_sample_releases(client, [{"full_name": "semslash"}], cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])


# ---------------------------------------------------------------------------
# write_releases_csv
# ---------------------------------------------------------------------------

def test_write_releases_csv_gera_dois_arquivos(tmp_path: Path):
    results = [
        ReleasesResult(
            full_name="o/r",
            releases=[Release(full_name="o/r", tag_name="v1.0", name="v1.0",
                              published_at="2026-01-15T00:00:00Z", prerelease=False,
                              html_url="https://github.com/o/r/releases/tag/v1.0")],
            tags=[Tag(full_name="o/r", tag_name="v1.0", sha="deadbeef")],
        )
    ]
    releases_path, tags_path = write_releases_csv(results, tmp_path / "output")
    assert releases_path.is_file()
    assert tags_path.is_file()
    releases_text = releases_path.read_text(encoding="utf-8")
    assert "v1.0" in releases_text
    assert "o/r" in releases_text
    tags_text = tags_path.read_text(encoding="utf-8")
    assert "deadbeef" in tags_text


def test_write_releases_csv_sem_dados(tmp_path: Path):
    releases_path, tags_path = write_releases_csv([], tmp_path / "output")
    assert releases_path.read_text(encoding="utf-8").startswith("full_name")
    assert tags_path.read_text(encoding="utf-8").startswith("full_name")


def test_write_releases_csv_prerelease_no_csv(tmp_path: Path):
    results = [
        ReleasesResult(
            full_name="o/r",
            releases=[Release(full_name="o/r", tag_name="v1.0-rc1", name="rc1",
                              published_at="2026-01-15T00:00:00Z", prerelease=True,
                              html_url="")],
            tags=[],
        )
    ]
    releases_path, _ = write_releases_csv(results, tmp_path / "output")
    text = releases_path.read_text(encoding="utf-8")
    assert "True" in text


# ---------------------------------------------------------------------------
# run_releases_collection
# ---------------------------------------------------------------------------

def test_run_releases_collection_grava_csvs(tmp_path: Path):
    sample = tmp_path / "output" / "sample_repos.csv"
    sample.parent.mkdir()
    sample.write_text(
        "full_name,owner,name\no/r,o,r\n", encoding="utf-8"
    )
    client = FakeClient(
        releases=[_release("v1.0", "2026-01-15T00:00:00Z")],
        tags=[_tag("v1.0")],
    )
    config = {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "paths": {"cache_dir": str(tmp_path / "cache"), "output_dir": str(tmp_path / "output")},
    }
    results, releases_path, tags_path = run_releases_collection(client, config)
    assert len(results) == 1
    assert releases_path.is_file()
    assert tags_path.is_file()


def test_run_releases_collection_levanta_sem_amostra(tmp_path: Path):
    client = FakeClient(releases=[], tags=[])
    config = {
        "observation_window": {"start": "2025-10-01", "end": "2026-09-30"},
        "paths": {"cache_dir": str(tmp_path / "cache"), "output_dir": str(tmp_path / "output")},
    }
    with pytest.raises(FileNotFoundError):
        run_releases_collection(client, config)


# ---------------------------------------------------------------------------
# Histórico completo e cache sensível à janela (Issue #20)
# ---------------------------------------------------------------------------

from dataclasses import asdict  # noqa: E402

from metricas.janela import parse_janela  # noqa: E402
from metricas.lead_time import selecionar_pares  # noqa: E402
from pipeline.releases import CACHE_VERSAO, _parse_historico  # noqa: E402

JANELA = parse_janela(*WINDOW)


def _client_com_historico():
    return FakeClient(
        releases=[
            _release("v0.9", "2025-09-01T00:00:00Z"),  # antes da janela
            _release("v1.0", "2026-01-15T00:00:00Z"),  # dentro
            _release("v1.1", "2026-10-01T00:00:00Z"),  # depois
            _release("v0.8", "2025-08-01T00:00:00Z", draft=True),
        ],
        tags=[],
    )


def test_parse_historico_guarda_qualquer_epoca_e_descarta_draft_e_sem_data():
    raw = [
        _release("v0.9", "2025-09-01T00:00:00Z"),
        _release("v1.0", "2026-01-15T00:00:00Z"),
        _release("v1.1", "2026-10-01T00:00:00Z"),
        _release("rascunho", "2026-02-01T00:00:00Z", draft=True),
        {"tag_name": "sem-data", "draft": False, "prerelease": False, "published_at": None},
    ]
    assert [r.tag_name for r in _parse_historico(raw, "o/r")] == ["v0.9", "v1.0", "v1.1"]


def test_releases_continua_so_com_a_janela_e_historico_tem_tudo(cache: JsonCache):
    result = collect_repo_releases(_client_com_historico(), "o", "r", cache=cache,
                                   window_start=WINDOW[0], window_end=WINDOW[1])
    assert [r.tag_name for r in result.releases] == ["v1.0"]
    assert [r.tag_name for r in result.historico] == ["v0.9", "v1.0", "v1.1"]


def test_historico_sobrevive_ao_cache(cache: JsonCache):
    client = _client_com_historico()
    collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    segundo = collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert segundo.from_cache is True and len(client.calls) == 2
    assert [r.tag_name for r in segundo.historico] == ["v0.9", "v1.0", "v1.1"]


def test_cache_grava_a_versao_e_a_janela(cache: JsonCache):
    collect_repo_releases(_client_com_historico(), "o", "r", cache=cache,
                          window_start=WINDOW[0], window_end=WINDOW[1])
    payload = cache.load("releases/o/r.json")
    assert payload["versao"] == CACHE_VERSAO and payload["janela"] == list(WINDOW)


def test_janela_diferente_nao_reaproveita_o_cache(cache: JsonCache):
    # As datas do config.yaml são provisórias; quando o professor publicar as oficiais,
    # um cache da janela antiga não pode ser lido como se fosse da nova.
    client = _client_com_historico()
    collect_repo_releases(client, "o", "r", cache=cache, window_start="2025-10-01", window_end="2026-09-30")
    nova = collect_repo_releases(client, "o", "r", cache=cache, window_start="2025-08-15", window_end="2026-08-14")
    assert nova.from_cache is False and len(client.calls) == 4
    assert [r.tag_name for r in nova.releases] == ["v0.9", "v1.0"]


def test_cache_antigo_sem_historico_e_refeito(cache: JsonCache):
    cache.save("releases/o/r.json", {"full_name": "o/r", "tags": [], "releases": [
        {"full_name": "o/r", "tag_name": "v1.0", "name": "v1.0", "published_at": "2026-01-15T00:00:00Z",
         "prerelease": False, "html_url": ""}]})
    client = _client_com_historico()
    result = collect_repo_releases(client, "o", "r", cache=cache, window_start=WINDOW[0], window_end=WINDOW[1])
    assert result.from_cache is False and len(result.historico) == 3


def test_com_o_historico_a_primeira_release_da_janela_ganha_o_par_anterior(cache: JsonCache):
    result = collect_repo_releases(_client_com_historico(), "o", "r", cache=cache,
                                   window_start=WINDOW[0], window_end=WINDOW[1])
    so_janela = selecionar_pares([asdict(r) for r in result.releases], JANELA)
    com_historico = selecionar_pares([asdict(r) for r in result.historico], JANELA)
    assert so_janela == []  # o comportamento anterior: v1.0 parecia a primeira da história
    assert [(a["tag_name"], b["tag_name"]) for a, b in com_historico] == [("v0.9", "v1.0")]


def test_csv_de_historico_inclui_o_que_o_csv_da_janela_exclui(tmp_path: Path, cache: JsonCache):
    result = collect_repo_releases(_client_com_historico(), "o", "r", cache=cache,
                                   window_start=WINDOW[0], window_end=WINDOW[1])
    releases_path, _ = write_releases_csv([result], tmp_path / "out")
    historico_path = tmp_path / "out" / "releases_historico.csv"
    na_janela = releases_path.read_text(encoding="utf-8")
    historico = historico_path.read_text(encoding="utf-8")
    assert "v0.9" not in na_janela and "v1.0" in na_janela  # contrato antigo preservado
    assert "v0.9" in historico and "v1.0" in historico and "v1.1" in historico


def test_csv_de_historico_vazio_tem_cabecalho(tmp_path: Path):
    write_releases_csv([], tmp_path / "out")
    assert (tmp_path / "out" / "releases_historico.csv").read_text(encoding="utf-8").startswith("full_name")

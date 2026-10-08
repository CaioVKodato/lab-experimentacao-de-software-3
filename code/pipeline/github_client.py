"""Cliente HTTP mínimo para a API REST do GitHub (sem PyGithub).

A camada completa de cache/retomada e backoff exponencial fica na Issue #4;
aqui há apenas o necessário para a seleção (Issue #2): autenticação, paginação
via cabeçalho Link e espera básica quando a cota de rate limit acaba.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import requests

DEFAULT_API_BASE = "https://api.github.com"
_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubAPIError(RuntimeError):
    """Erro de resposta da API do GitHub."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"GitHub API {status_code}: {message}")
        self.status_code = status_code


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        api_base_url: str = DEFAULT_API_BASE,
        per_page: int = 100,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("token do GitHub é obrigatório")
        self.api_base_url = api_base_url.rstrip("/")
        self.per_page = per_page
        self._sleep = sleep_fn
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "lab03-dora-pipeline",
            }
        )

    def get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, str]]:
        """GET em path relativo ou URL absoluta; devolve (json, headers)."""
        url = path if path.startswith("http") else f"{self.api_base_url}{path}"
        query: dict[str, Any] | None
        if path.startswith("http") and params is None:
            query = None
        else:
            query = dict(params or {})
            if "per_page" not in query:
                query["per_page"] = self.per_page

        while True:
            response = self.session.get(url, params=query, timeout=60)
            if response.status_code == 403 and _should_wait_rate_limit(response):
                self._wait_for_reset(response)
                continue
            if response.status_code >= 400:
                raise GitHubAPIError(response.status_code, response.text[:300])
            if response.status_code == 204 or not response.content:
                return None, dict(response.headers)
            return response.json(), dict(response.headers)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET que devolve apenas o corpo JSON."""
        body, _headers = self.get_json(path, params)
        return body

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        items_key: str | None = None,
        max_items: int | None = None,
    ) -> list[Any]:
        """Percorre páginas seguindo Link rel=next até o fim ou max_items."""
        collected: list[Any] = []
        query = dict(params or {})
        next_url: str | None = None
        first = True

        while True:
            if first:
                body, headers = self.get_json(path, query)
                first = False
            else:
                assert next_url is not None
                body, headers = self.get_json(next_url, params=None)

            if items_key is None:
                if not isinstance(body, list):
                    raise GitHubAPIError(500, "esperado array JSON na paginação")
                page_items = body
            else:
                if not isinstance(body, dict) or items_key not in body:
                    raise GitHubAPIError(
                        500, f"chave '{items_key}' ausente na resposta"
                    )
                page_items = body[items_key]

            for item in page_items:
                collected.append(item)
                if max_items is not None and len(collected) >= max_items:
                    return collected

            next_url = _parse_next_link(headers.get("Link") or headers.get("link"))
            if not next_url:
                break

        return collected

    def _wait_for_reset(self, response: requests.Response) -> None:
        reset = response.headers.get("X-RateLimit-Reset")
        if reset and str(reset).isdigit():
            wait = max(0.0, int(reset) - time.time()) + 1.0
        else:
            wait = 60.0
        self._sleep(min(wait, 3600.0))


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = _LINK_NEXT_RE.search(link_header)
    return match.group(1) if match else None


def _should_wait_rate_limit(response: requests.Response) -> bool:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining == "0":
        return True
    body = (response.text or "").lower()
    return "rate limit" in body or "secondary rate limit" in body


def extract_last_page(link_header: str | None) -> int | None:
    """Utilitário: última página no cabeçalho Link (ex.: contribuidores)."""
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="last"' in part:
            url = part[part.find("<") + 1 : part.find(">")]
            page = parse_qs(urlparse(url).query).get("page", [None])[0]
            return int(page) if page and str(page).isdigit() else None
    return None

"""Cache local em JSON por chave (retomada sem repetir chamadas).

A Issue #4 evolui esta base com backoff/rate-limit unificados; aqui basta
persistir respostas/artefatos intermediários em disco.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_key(value: str) -> str:
    """Normaliza um identificador para nome de arquivo/pasta."""
    cleaned = _SAFE_RE.sub("_", value.strip())
    return cleaned.strip("._") or "item"


class JsonCache:
    """Armazena um JSON por chave relativa sob cache_dir."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        relative = Path(*[safe_key(part) for part in key.replace("\\", "/").split("/") if part])
        return self.cache_dir / relative

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def load(self, key: str) -> Any | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, key: str, payload: Any) -> Path:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        tmp.replace(path)
        return path

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class RuntimeCopyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime_ns: int | None = None
        self._cached: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        try:
            stat = self.path.stat()
            if self._cached is not None and self._mtime_ns == stat.st_mtime_ns:
                return self._cached
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("copy file must contain a top-level JSON object")
            self._cached = data
            self._mtime_ns = stat.st_mtime_ns
            return data
        except Exception as exc:
            if self._cached is not None:
                logger.warning("Failed to reload copy file %s, using last good snapshot: %s", self.path, exc)
                return self._cached
            raise


COPY_FILE_PATH = Path(__file__).resolve().parents[2] / "copy.json"
_STORE = RuntimeCopyStore(COPY_FILE_PATH)


def load_copy() -> dict[str, Any]:
    return _STORE.load()


def copy_get(path: str | Iterable[str], default: Any = None) -> Any:
    current: Any = load_copy()
    parts = path.split(".") if isinstance(path, str) else list(path)
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def copy_text(path: str | Iterable[str], default: str = "") -> str:
    value = copy_get(path, default)
    return value if isinstance(value, str) else default


def copy_list(path: str | Iterable[str], default: list[str] | None = None) -> list[str]:
    value = copy_get(path, default or [])
    if not isinstance(value, list):
        return list(default or [])
    return [str(item) for item in value]


def copy_dict(path: str | Iterable[str], default: dict[str, Any] | None = None) -> dict[str, Any]:
    value = copy_get(path, default or {})
    return value if isinstance(value, dict) else dict(default or {})


def copy_format(path: str | Iterable[str], default: str = "", **kwargs: Any) -> str:
    template = copy_text(path, default)
    try:
        return template.format(**kwargs)
    except Exception:
        return template

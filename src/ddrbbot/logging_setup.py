from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

from .config import Settings

_CONFIGURED = False
_HUMAN_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "uvicorn.access",
    "asyncio",
    "websockets",
    "playwright",
    "playwright._impl",
)


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def _resolve_level(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip().upper() or default
    level = logging.getLevelName(raw)
    return level if isinstance(level, int) else logging.INFO


def configure_logging(settings: Settings | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    root_level = _resolve_level("LOG_LEVEL", "INFO")
    fmt_mode = (os.getenv("LOG_FORMAT", "text") or "text").strip().lower()
    formatter: logging.Formatter
    if fmt_mode == "json":
        formatter = JsonLineFormatter()
    else:
        formatter = logging.Formatter(_HUMAN_FORMAT)

    root = logging.getLogger()
    root.setLevel(root_level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    log_file = os.getenv("LOG_FILE", "").strip()
    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = max(int(os.getenv("LOG_FILE_MAX_BYTES", "10485760")), 1024)
        backup_count = max(int(os.getenv("LOG_FILE_BACKUP_COUNT", "5")), 0)
        file_handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    for name in _NOISY_LOGGERS:
        env_key = "LOG_LEVEL_" + name.replace(".", "_").upper()
        logging.getLogger(name).setLevel(_resolve_level(env_key, "WARNING"))

    _CONFIGURED = True

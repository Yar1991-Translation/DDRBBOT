from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str, *, fallback: str = "artifact") -> str:
    normalized = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    normalized = re.sub(r"[-\s]+", "-", normalized)
    return normalized or fallback


def make_external_id(*parts: str) -> str:
    joined = "|".join(part for part in parts if part)
    if joined:
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]
    return hashlib.sha256(b"default").hexdigest()[:24]


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


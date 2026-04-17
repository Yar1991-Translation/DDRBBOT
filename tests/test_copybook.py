from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from ddrbbot import copybook
from ddrbbot.main import create_app


def _configure_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCREENSHOT_ENABLED", "0")
    monkeypatch.setenv("DELIVERY_RETRY_DELAYS_SECONDS", "0,0")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_runtime_copy_store_reloads_after_file_edit(tmp_path: Path) -> None:
    copy_path = tmp_path / "copy.json"
    _write_json(copy_path, {"preview_console": {"title": "初始标题"}})
    store = copybook.RuntimeCopyStore(copy_path)

    first = store.load()
    assert first["preview_console"]["title"] == "初始标题"

    updated_payload = {"preview_console": {"title": "已更新标题"}}
    _write_json(copy_path, updated_payload)
    stat = copy_path.stat()
    os.utime(copy_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))

    second = store.load()
    assert second["preview_console"]["title"] == "已更新标题"


def test_preview_card_defaults_follow_copy_file(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    source_copy_path = Path(__file__).resolve().parents[1] / "copy.json"
    payload = json.loads(source_copy_path.read_text(encoding="utf-8"))
    payload["preview_defaults"]["title"] = "可热更新的默认标题"
    payload["preview_defaults"]["summary"] = "这段摘要来自统一文案文件。"
    payload["preview_defaults"]["highlights"] = "第一条\n第二条"

    copy_path = tmp_path / "copy.json"
    _write_json(copy_path, payload)
    monkeypatch.setattr(copybook, "_STORE", copybook.RuntimeCopyStore(copy_path))

    app = create_app()

    with TestClient(app) as client:
        response = client.get("/preview/md3/card")

        assert response.status_code == 200
        assert "可热更新的默认标题" in response.text
        assert "这段摘要来自统一文案文件。" in response.text
        assert "第一条" in response.text
        assert "第二条" in response.text

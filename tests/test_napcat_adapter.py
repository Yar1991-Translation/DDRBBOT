from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ddrbbot.copybook import copy_text
from ddrbbot.main import create_app
from ddrbbot.qq.napcat import normalize_inbound_event


def _configure_env(monkeypatch, tmp_path: Path, *, admin_users: str = "10001") -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCREENSHOT_ENABLED", "0")
    monkeypatch.setenv("QQ_ADMIN_USER_IDS", admin_users)


def test_normalize_inbound_event_from_onebot_group_message() -> None:
    event = normalize_inbound_event(
        {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 7788,
            "self_id": 5566,
            "group_id": 123456,
            "user_id": 10001,
            "message": [{"type": "text", "data": {"text": "/status"}}],
            "time": 1710000000,
        }
    )

    assert event.event_type == "group_message"
    assert event.post_type == "message"
    assert event.sub_type == "normal"
    assert event.group_id == "123456"
    assert event.user_id == "10001"
    assert event.message_id == "7788"
    assert event.self_id == "5566"
    assert event.raw_message == "/status"
    assert event.segments == [{"type": "text", "data": {"text": "/status"}}]
    assert event.raw_payload["post_type"] == "message"


def test_receive_qq_event_accepts_raw_napcat_payload(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()

    sent_messages: list[tuple[str, str]] = []

    async def fake_send_text(target: str, text: str) -> str:
        sent_messages.append((target, text))
        return "msg-ping"

    async def fake_health_check() -> bool:
        return True

    with TestClient(app) as client:
        app.state.services.bot_adapter.send_text = fake_send_text
        app.state.services.bot_adapter.health_check = fake_health_check

        response = client.post(
            "/api/events/qq",
            json={
                "post_type": "message",
                "message_type": "group",
                "message_id": 7788,
                "group_id": 123456,
                "user_id": 10001,
                "message": [{"type": "text", "data": {"text": "/ping"}}],
                "time": 1710000000,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["received"] == "group_message"
        assert payload["message_id"] == "7788"
        assert payload["platform_event_saved"] is True
        assert payload["handled"] is True
        assert payload["command"] == "ping"
        assert payload["authorized"] is True
        expected = "\n".join(
            [
                copy_text("qq_commands.ping.title", "PONG"),
                (
                    f"{copy_text('qq_commands.shared.napcat_label', 'NapCat')}: "
                    f"{copy_text('qq_commands.shared.online', 'online')}"
                ),
                f"{copy_text('qq_commands.shared.queue_label', 'Queue')}: 0",
            ]
        )
        assert sent_messages == [("group:123456", expected)]


def test_qq_adapter_status_endpoint_reports_adapter_snapshot(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DEFAULT_QQ_GROUP_ID", "2")
    app = create_app()

    async def fake_health_check() -> bool:
        return True

    async def fake_get_login_info() -> dict[str, str]:
        return {"nickname": "DDRBBOT", "user_id": "10000"}

    async def fake_get_version_info() -> dict[str, str]:
        return {"app_name": "NapCat", "app_version": "4.7.0"}

    async def fake_get_group_list() -> list[dict[str, str]]:
        return [{"group_id": "1"}, {"group_id": "2", "group_name": "Ops Group"}]

    with TestClient(app) as client:
        app.state.services.bot_adapter.health_check = fake_health_check
        app.state.services.bot_adapter.get_login_info = fake_get_login_info
        app.state.services.bot_adapter.get_version_info = fake_get_version_info
        app.state.services.bot_adapter.get_group_list = fake_get_group_list

        response = client.get("/api/qq/adapter/status")

        assert response.status_code == 200
        payload = response.json()
        assert payload["connected"] is True
        assert payload["login_info"]["nickname"] == "DDRBBOT"
        assert payload["version_info"]["app_name"] == "NapCat"
        assert payload["groups_count"] == 2
        assert payload["default_group_id"] == "2"
        assert payload["default_group_found"] is True
        assert payload["default_group_name"] == "Ops Group"

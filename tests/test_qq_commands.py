from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from ddrbbot.copybook import copy_format, copy_text
from ddrbbot.main import create_app
from ddrbbot.models import DeliveryRecord, RenderArtifact


def _configure_env(monkeypatch, tmp_path: Path, *, admin_users: str = "10001") -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCREENSHOT_ENABLED", "0")
    monkeypatch.setenv("QQ_ADMIN_USER_IDS", admin_users)
    monkeypatch.delenv("QQ_ADMIN_GROUP_IDS", raising=False)
    monkeypatch.setenv("DELIVERY_RETRY_DELAYS_SECONDS", "0,0")
    monkeypatch.setenv("DELIVERY_WORKER_POLL_SECONDS", "0.2")
    monkeypatch.setenv("DELIVERY_WORKER_ENABLED", "0")


def test_ping_command_replies_to_group(monkeypatch, tmp_path: Path) -> None:
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
                "platform": "qq",
                "adapter": "napcat",
                "event_type": "group_message",
                "group_id": "123456",
                "user_id": "10001",
                "raw_message": "/ping",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["handled"] is True
        assert payload["command"] == "ping"
        assert payload["authorized"] is True
        assert payload["response_message_id"] == "msg-ping"
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
        assert app.state.services.repository.get_stats()["platform_events"] == 1


def test_status_command_denies_non_admin_user(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()

    sent_messages: list[tuple[str, str]] = []

    async def fake_send_text(target: str, text: str) -> str:
        sent_messages.append((target, text))
        return "msg-denied"

    with TestClient(app) as client:
        app.state.services.bot_adapter.send_text = fake_send_text

        response = client.post(
            "/api/events/qq",
            json={
                "platform": "qq",
                "adapter": "napcat",
                "event_type": "group_message",
                "group_id": "123456",
                "user_id": "99999",
                "raw_message": "/status",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["handled"] is True
        assert payload["command"] == "status"
        assert payload["authorized"] is False
        assert payload["response_message_id"] == "msg-denied"
        assert sent_messages == [
            (
                "group:123456",
                copy_text(
                    "qq_commands.authorizer.user_denied",
                    "This user is not in the admin allowlist.",
                ),
            )
        ]


def test_push_test_command_sends_news_card(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()

    sent_cards: list[tuple[str, str, str | None]] = []

    async def fake_render(raw_event, processed_event, *, theme: str = "light") -> RenderArtifact:
        assert raw_event.raw_payload["preset_key"] == "pressure"
        assert processed_event.game == "Pressure"
        image_path = tmp_path / "pressure-test.png"
        image_path.write_bytes(b"png")
        return RenderArtifact(
            processed_event_id=processed_event.id,
            theme=theme,
            html_path=str(tmp_path / "pressure-test.html"),
            image_path=str(image_path),
        )

    async def fake_send_news_card(target: str, image_path: str, caption: str | None = None) -> str:
        sent_cards.append((target, image_path, caption))
        return "msg-card"

    sent_text: list[tuple[str, str]] = []

    async def fake_send_text(target: str, text: str) -> str:
        sent_text.append((target, text))
        return "msg-reply"

    with TestClient(app) as client:
        app.state.services.renderer.render = fake_render
        app.state.services.bot_adapter.send_news_card = fake_send_news_card
        app.state.services.bot_adapter.send_text = fake_send_text

        response = client.post(
            "/api/events/qq",
            json={
                "platform": "qq",
                "adapter": "napcat",
                "event_type": "group_message",
                "group_id": "123456",
                "user_id": "10001",
                "raw_message": "/push test pressure",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["handled"] is True
        assert payload["command"] == "push_test"
        assert payload["authorized"] is True
        assert payload["response_message_id"] == "msg-reply"
        asyncio.run(app.state.services.delivery_worker.drain())
        assert sent_cards == [
            (
                "group:123456",
                str((tmp_path / "pressure-test.png")),
                copy_format(
                    "qq_operations.push_test_caption",
                    "{game} / test card",
                    game="Pressure",
                ),
            )
        ]


def test_review_queue_command_reports_failed_delivery(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    image_path = tmp_path / "failed.png"
    image_path.write_bytes(b"png")
    app = create_app()

    sent_messages: list[tuple[str, str]] = []

    async def fake_send_text(target: str, text: str) -> str:
        sent_messages.append((target, text))
        return "msg-review"

    with TestClient(app) as client:
        app.state.services.repository.reserve_delivery_record(
            DeliveryRecord(
                trace_id="trace-review",
                target_type="group",
                target_id="123456",
                status="failed",
                attempts=2,
                error_message="temporary napcat error",
                request_payload={
                    "trace_id": "trace-review",
                    "target_type": "group",
                    "target_id": "123456",
                    "image_path": str(image_path.resolve()),
                    "caption": "Review me",
                },
            )
        )
        app.state.services.bot_adapter.send_text = fake_send_text

        response = client.post(
            "/api/events/qq",
            json={
                "platform": "qq",
                "adapter": "napcat",
                "event_type": "group_message",
                "group_id": "123456",
                "user_id": "10001",
                "raw_message": "/review queue",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["handled"] is True
        assert payload["command"] == "review_queue"
        assert payload["authorized"] is True
        assert payload["response_message_id"] == "msg-review"
        assert (
            f"{copy_text('qq_commands.review_queue.failed_deliveries', 'Failed deliveries')}: 1"
            in sent_messages[0][1]
        )
        assert "trace-review" in sent_messages[0][1]


def test_retry_failed_command_retries_latest_failed_record(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    image_path = tmp_path / "retryable.png"
    image_path.write_bytes(b"png")
    app = create_app()

    sent_messages: list[tuple[str, str]] = []
    sent_cards: list[tuple[str, str, str | None]] = []

    async def fake_send_text(target: str, text: str) -> str:
        sent_messages.append((target, text))
        return "msg-retry-status"

    async def fake_send_news_card(target: str, image_path: str, caption: str | None = None) -> str:
        sent_cards.append((target, image_path, caption))
        return "msg-retry-card"

    with TestClient(app) as client:
        app.state.services.repository.reserve_delivery_record(
            DeliveryRecord(
                trace_id="trace-retry-command",
                target_type="group",
                target_id="123456",
                status="failed",
                attempts=3,
                error_message="timeout",
                request_payload={
                    "trace_id": "trace-retry-command",
                    "target_type": "group",
                    "target_id": "123456",
                    "image_path": str(image_path.resolve()),
                    "caption": "Retry command",
                },
            )
        )
        app.state.services.bot_adapter.send_text = fake_send_text
        app.state.services.bot_adapter.send_news_card = fake_send_news_card

        response = client.post(
            "/api/events/qq",
            json={
                "platform": "qq",
                "adapter": "napcat",
                "event_type": "group_message",
                "group_id": "123456",
                "user_id": "10001",
                "raw_message": "/retry failed",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["handled"] is True
        assert payload["command"] == "retry_failed"
        assert payload["authorized"] is True
        assert payload["response_message_id"] == "msg-retry-status"
        assert copy_text("qq_commands.retry_failed.sent", "Retry sent") in sent_messages[0][1]
        asyncio.run(app.state.services.delivery_worker.drain())
        assert sent_cards == [("group:123456", str(image_path.resolve()), "Retry command")]
        record = app.state.services.repository.get_delivery_record("trace-retry-command")
        assert record is not None
        assert record.status == "sent"
        assert record.attempts == 1

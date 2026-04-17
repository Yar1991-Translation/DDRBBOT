from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from ddrbbot.main import create_app
from ddrbbot.models import DeliveryRecord


def _configure_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCREENSHOT_ENABLED", "0")
    monkeypatch.setenv("DELIVERY_RETRY_DELAYS_SECONDS", "0,0,0,0,0")
    monkeypatch.setenv("DELIVERY_WORKER_POLL_SECONDS", "0.2")
    monkeypatch.setenv("DELIVERY_WORKER_ENABLED", "0")
    monkeypatch.setenv("DELIVERY_DEAD_LETTER_MAX_ATTEMPTS", "6")


def _drain(app) -> None:
    asyncio.get_event_loop().run_until_complete(app.state.services.delivery_worker.drain())


def test_send_news_card_api_enqueues_and_worker_delivers(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    image_path = tmp_path / "card.png"
    image_path.write_bytes(b"png")
    app = create_app()

    sent_calls: list[tuple[str, str, str | None]] = []

    async def fake_send_news_card(target: str, image_path: str, caption: str | None = None) -> str:
        sent_calls.append((target, image_path, caption))
        return "msg-1"

    with TestClient(app) as client:
        app.state.services.bot_adapter.send_news_card = fake_send_news_card
        payload = {
            "trace_id": "trace-1",
            "target_type": "group",
            "target_id": "123456",
            "image_path": str(image_path),
            "caption": "DOORS test card",
        }

        first = client.post("/api/qq/send-news-card", json=payload)
        assert first.status_code == 200
        assert first.json()["status"] == "queued"
        asyncio.run(app.state.services.delivery_worker.drain())

        record = app.state.services.repository.get_delivery_record("trace-1")
        assert record is not None
        assert record.status == "sent"
        assert record.message_id == "msg-1"

        second = client.post("/api/qq/send-news-card", json=payload)
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"
        assert second.json()["deduplicated"] is True
        assert sent_calls == [("group:123456", str(image_path.resolve()), "DOORS test card")]


def test_worker_retries_until_success(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    image_path = tmp_path / "retry-card.png"
    image_path.write_bytes(b"png")
    app = create_app()

    attempts = {"count": 0}

    async def flaky_send_news_card(target: str, image_path: str, caption: str | None = None) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary napcat error")
        return "msg-retried"

    with TestClient(app) as client:
        app.state.services.bot_adapter.send_news_card = flaky_send_news_card
        response = client.post(
            "/api/qq/send-news-card",
            json={
                "trace_id": "trace-retry",
                "target_type": "group",
                "target_id": "123456",
                "image_path": str(image_path),
                "caption": "Pressure test card",
            },
        )
        assert response.status_code == 200
        asyncio.run(app.state.services.delivery_worker.drain())

        record = app.state.services.repository.get_delivery_record("trace-retry")
        assert record is not None
        assert record.status == "sent"
        assert record.attempts == 3
        assert attempts["count"] == 3


def test_worker_dead_letter_after_max_attempts(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DELIVERY_DEAD_LETTER_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("QQ_IMAGE_FAIL_TEXT_FALLBACK", "0")
    image_path = tmp_path / "dead.png"
    image_path.write_bytes(b"png")
    app = create_app()

    async def always_fail(target: str, image_path: str, caption: str | None = None) -> str:
        raise RuntimeError("napcat offline")

    with TestClient(app) as client:
        app.state.services.bot_adapter.send_news_card = always_fail
        response = client.post(
            "/api/qq/send-news-card",
            json={
                "trace_id": "trace-dead",
                "target_type": "group",
                "target_id": "123456",
                "image_path": str(image_path),
                "caption": "will die",
            },
        )
        assert response.status_code == 200
        asyncio.run(app.state.services.delivery_worker.drain())

        record = app.state.services.repository.get_delivery_record("trace-dead")
        assert record is not None
        assert record.status == "dead_letter"
        assert record.attempts >= 3

        retry_resp = client.post(f"/api/delivery/dead-letter/{record.id}/retry")
        assert retry_resp.status_code == 200
        assert retry_resp.json()["status"] == "queued"
        reopened = app.state.services.repository.get_delivery_record("trace-dead")
        assert reopened is not None
        assert reopened.status == "pending"


def test_worker_text_fallback_when_image_fails(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DELIVERY_DEAD_LETTER_MAX_ATTEMPTS", "2")
    image_path = tmp_path / "fb.png"
    image_path.write_bytes(b"png")
    app = create_app()
    calls: dict[str, int] = {"img": 0, "txt": 0}

    async def fail_image(target: str, image_path: str, caption: str | None = None) -> str:
        calls["img"] += 1
        raise RuntimeError("image send down")

    async def ok_text(target: str, text: str) -> str:
        calls["txt"] += 1
        return "msg-text"

    with TestClient(app) as client:
        app.state.services.bot_adapter.send_news_card = fail_image
        app.state.services.bot_adapter.send_text = ok_text
        response = client.post(
            "/api/qq/send-news-card",
            json={
                "trace_id": "trace-fb",
                "target_type": "group",
                "target_id": "123456",
                "image_path": str(image_path),
                "caption": "卡片标题",
            },
        )
        assert response.status_code == 200
        asyncio.run(app.state.services.delivery_worker.drain())

        record = app.state.services.repository.get_delivery_record("trace-fb")
        assert record is not None
        assert record.status == "sent"
        assert record.error_code == "text_fallback"
        assert calls["txt"] == 1
        assert calls["img"] >= 2


def test_worker_rejects_oversized_image_after_max_attempts(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    monkeypatch.setenv("QQ_NEWS_CARD_MAX_BYTES", "3")
    monkeypatch.setenv("DELIVERY_DEAD_LETTER_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("QQ_IMAGE_FAIL_TEXT_FALLBACK", "0")
    image_path = tmp_path / "too-large.png"
    image_path.write_bytes(b"1234")
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/qq/send-news-card",
            json={
                "trace_id": "trace-too-large",
                "target_type": "group",
                "target_id": "123456",
                "image_path": str(image_path),
                "caption": "Too large",
            },
        )
        assert response.status_code == 200
        asyncio.run(app.state.services.delivery_worker.drain())
        record = app.state.services.repository.get_delivery_record("trace-too-large")
        assert record is not None
        assert record.status == "dead_letter"
        assert record.error_message  # some error captured


def test_delivery_review_queue_and_retry_api(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    image_path = tmp_path / "api-retry.png"
    image_path.write_bytes(b"png")
    app = create_app()

    async def fake_send_news_card(target: str, image_path: str, caption: str | None = None) -> str:
        return "msg-api-retry"

    with TestClient(app) as client:
        app.state.services.repository.reserve_delivery_record(
            DeliveryRecord(
                trace_id="trace-api",
                target_type="group",
                target_id="123456",
                status="failed",
                attempts=3,
                error_message="timeout",
                request_payload={
                    "trace_id": "trace-api",
                    "target_type": "group",
                    "target_id": "123456",
                    "image_path": str(image_path.resolve()),
                    "caption": "Retry from API",
                },
            )
        )
        app.state.services.bot_adapter.send_news_card = fake_send_news_card

        review = client.get("/api/qq/delivery/review-queue")
        assert review.status_code == 200
        review_payload = review.json()
        assert review_payload["failed_deliveries"] == 1
        assert review_payload["recent_failed"][0]["trace_id"] == "trace-api"

        retry = client.post("/api/qq/delivery/retry-failed", json={"trace_id": "trace-api"})
        assert retry.status_code == 200
        assert retry.json()["status"] == "queued"
        asyncio.run(app.state.services.delivery_worker.drain())

        record = app.state.services.repository.get_delivery_record("trace-api")
        assert record is not None
        assert record.status == "sent"
        assert record.message_id == "msg-api-retry"

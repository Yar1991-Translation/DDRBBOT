from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from ddrbbot.main import create_app
from ddrbbot.models import ProcessedEvent, RawEvent, RenderArtifact
from ddrbbot.utils import utc_now


def _configure_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SCREENSHOT_ENABLED", "0")
    monkeypatch.setenv("DELIVERY_RETRY_DELAYS_SECONDS", "0,0")
    monkeypatch.setenv("DEFAULT_QQ_GROUP_ID", "123456")
    monkeypatch.setenv("DELIVERY_WORKER_POLL_SECONDS", "0.2")
    monkeypatch.setenv("DELIVERY_WORKER_ENABLED", "0")


def _seed_review_item(app) -> tuple[RawEvent, ProcessedEvent]:
    now = utc_now()
    raw_event = RawEvent(
        source_type="discord",
        source_name="DOORS Official",
        channel_name="announcements",
        author="Official",
        content="Raw source line one\nRaw source line two",
        attachments=[],
        external_id=f"seed-{now.timestamp()}",
        published_at=now,
        raw_payload={"preset_key": "doors", "custom_css": ""},
    )
    inserted = app.state.services.repository.insert_raw_event(raw_event)
    assert inserted is True

    processed_event = ProcessedEvent(
        raw_event_id=raw_event.id,
        title="Original Review Title",
        summary="Original summary",
        highlights=["Original point A", "Original point B"],
        category="announcement",
        game="DOORS",
        source_credibility="official",
        render_status="html_ready",
        delivery_status="skipped",
        published_at=now,
    )
    app.state.services.repository.upsert_processed_event(processed_event)
    return raw_event, processed_event


def test_review_console_renders_selected_item(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()

    with TestClient(app) as client:
        _, processed_event = _seed_review_item(app)

        response = client.get(f"/review?processed_event_id={processed_event.id}")

        assert response.status_code == 200
        assert "Original Review Title" in response.text
        assert "Raw source line one" in response.text
        assert "/api/review/" in response.text


def test_review_rerender_updates_processed_event_and_artifact(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()

    async def fake_render(raw_event, processed_event, *, theme: str = "light") -> RenderArtifact:
        assert processed_event.title == "Updated Review Title"
        return RenderArtifact(
            processed_event_id=processed_event.id,
            theme=theme,
            html_path=str(tmp_path / "updated-review.html"),
            image_path=None,
        )

    with TestClient(app) as client:
        _, processed_event = _seed_review_item(app)
        app.state.services.renderer.render = fake_render

        response = client.post(
            f"/api/review/{processed_event.id}/rerender",
            json={
                "title": "Updated Review Title",
                "summary": "Updated summary",
                "highlights": ["Fresh highlight", "Second highlight"],
                "category": "teaser",
                "game": "DOORS",
                "theme": "dark",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["render_status"] == "html_ready"
        updated = app.state.services.repository.get_processed_event(processed_event.id)
        assert updated is not None
        assert updated.title == "Updated Review Title"
        assert updated.summary == "Updated summary"
        assert updated.highlights == ["Fresh highlight", "Second highlight"]
        assert updated.delivery_status == "review_pending"
        artifact = app.state.services.repository.get_latest_render_artifact(processed_event.id)
        assert artifact is not None
        assert artifact.theme == "dark"


def test_review_approve_and_send_uses_default_group(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()
    image_path = tmp_path / "approved.png"
    image_path.write_bytes(b"png")
    sent_cards: list[tuple[str, str, str | None]] = []

    async def fake_render(raw_event, processed_event, *, theme: str = "light") -> RenderArtifact:
        return RenderArtifact(
            processed_event_id=processed_event.id,
            theme=theme,
            html_path=str(tmp_path / "approved.html"),
            image_path=str(image_path),
        )

    async def fake_send_news_card(target: str, image_path: str, caption: str | None = None) -> str:
        sent_cards.append((target, image_path, caption))
        return "msg-approved"

    with TestClient(app) as client:
        _, processed_event = _seed_review_item(app)
        app.state.services.renderer.render = fake_render
        app.state.services.bot_adapter.send_news_card = fake_send_news_card

        response = client.post(
            f"/api/review/{processed_event.id}/approve-send",
            json={
                "title": "Approved Title",
                "summary": "Approved summary",
                "highlights": ["Approved point"],
                "category": "announcement",
                "game": "DOORS",
                "theme": "light",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["target_id"] == "123456"
        asyncio.run(app.state.services.delivery_worker.drain())
        assert sent_cards == [("group:123456", str(image_path), "DOORS / Approved Title")]
        updated = app.state.services.repository.get_processed_event(processed_event.id)
        assert updated is not None
        assert updated.delivery_status == "queued"


def test_review_reject_marks_processed_event(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()

    with TestClient(app) as client:
        _, processed_event = _seed_review_item(app)

        response = client.post(f"/api/review/{processed_event.id}/reject", json={})

        assert response.status_code == 200
        updated = app.state.services.repository.get_processed_event(processed_event.id)
        assert updated is not None
        assert updated.delivery_status == "rejected"


def test_review_resend_uses_latest_screenshot(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()
    image_path = tmp_path / "latest.png"
    image_path.write_bytes(b"png")
    sent_cards: list[tuple[str, str, str | None]] = []

    async def fake_send_news_card(target: str, image_path: str, caption: str | None = None) -> str:
        sent_cards.append((target, image_path, caption))
        return "msg-resent"

    with TestClient(app) as client:
        _, processed_event = _seed_review_item(app)
        app.state.services.repository.save_render_artifact(
            RenderArtifact(
                processed_event_id=processed_event.id,
                theme="light",
                html_path=str(tmp_path / "latest.html"),
                image_path=str(image_path),
            )
        )
        app.state.services.bot_adapter.send_news_card = fake_send_news_card

        response = client.post(f"/api/review/{processed_event.id}/resend", json={})

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "queued"
        asyncio.run(app.state.services.delivery_worker.drain())
        record = app.state.services.repository.get_delivery_record(payload["trace_id"])
        assert record is not None
        assert record.status == "sent"
        assert record.message_id == "msg-resent"
        assert sent_cards == [("group:123456", str(image_path), "DOORS / Original Review Title")]


def test_review_items_api_returns_list_and_selected(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()
    with TestClient(app) as client:
        _, processed_event = _seed_review_item(app)
        response = client.get(f"/api/review/items?processed_event_id={processed_event.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["items"]) >= 1
        assert data["selected_id"] == processed_event.id
        assert data["selected"]["id"] == processed_event.id
        assert data["selected"]["preset_key"] == "doors"


def test_review_item_get_api(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()
    with TestClient(app) as client:
        _, processed_event = _seed_review_item(app)
        response = client.get(f"/api/review/items/{processed_event.id}")
        assert response.status_code == 200
        assert response.json()["item"]["raw_event_id"] == processed_event.raw_event_id


def test_review_rerender_persists_preset_and_credibility(monkeypatch, tmp_path: Path) -> None:
    _configure_env(monkeypatch, tmp_path)
    app = create_app()

    async def fake_render(raw_event, processed_event, *, theme: str = "light") -> RenderArtifact:
        return RenderArtifact(
            processed_event_id=processed_event.id,
            theme=theme,
            html_path=str(tmp_path / "p.html"),
            image_path=None,
        )

    with TestClient(app) as client:
        raw_event, processed_event = _seed_review_item(app)
        app.state.services.renderer.render = fake_render
        response = client.post(
            f"/api/review/{processed_event.id}/rerender",
            json={
                "title": "T",
                "summary": "S",
                "highlights": ["a", "b", "c"],
                "category": "announcement",
                "game": "DOORS",
                "theme": "dark",
                "preset_key": "roblox",
                "orientation": "horizontal",
                "custom_css": "body{}",
                "channel_name": "ch2",
                "author": "au2",
                "source_credibility": "community",
                "need_translation": True,
                "discovered_sources": ["x"],
                "media": [
                    {
                        "type": "image",
                        "url": "https://example.com/i.png",
                        "description": "d",
                        "reference_url": None,
                        "reference_label": None,
                    }
                ],
            },
        )
        assert response.status_code == 200
        reloaded_raw = app.state.services.repository.get_raw_event(raw_event.id)
        assert reloaded_raw is not None
        assert reloaded_raw.raw_payload.get("preset_key") == "roblox"
        assert reloaded_raw.raw_payload.get("orientation") == "horizontal"
        assert reloaded_raw.channel_name == "ch2"
        assert reloaded_raw.author == "au2"
        updated = app.state.services.repository.get_processed_event(processed_event.id)
        assert updated is not None
        assert updated.source_credibility == "community"
        assert updated.need_translation is True
        assert updated.discovered_sources == ["x"]
        assert len(updated.media) == 1
        assert updated.media[0].url == "https://example.com/i.png"

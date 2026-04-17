from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from ..copybook import copy_dict, copy_format, copy_text
from ..database import SQLiteRepository
from ..delivery import DeliveryExecutionResult, QQDeliveryService
from ..models import ProcessedEvent, QQSendNewsCardRequest, RawEvent
from ..pipeline import PipelineCoordinator
from ..rendering import NewsCardRenderer
from ..utils import make_external_id, utc_now
from .napcat import BotAdapter

logger = logging.getLogger(__name__)


def get_test_card_fixtures() -> dict[str, dict[str, Any]]:
    fixtures = copy_dict("qq_operations.test_fixtures", {})
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in fixtures.items():
        if isinstance(value, dict):
            normalized[str(key)] = value
    return normalized


class QQOperationsService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: SQLiteRepository,
        renderer: NewsCardRenderer,
        bot_adapter: BotAdapter,
        delivery_service: QQDeliveryService,
        pipeline: PipelineCoordinator,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.renderer = renderer
        self.bot_adapter = bot_adapter
        self.delivery_service = delivery_service
        self.pipeline = pipeline

    async def health_snapshot(self) -> dict[str, Any]:
        connected = await self.bot_adapter.health_check()
        return {
            "connected": connected,
            "queue_size": self.pipeline.queue_size(),
            "failed_deliveries": self.delivery_service.failed_records_count(),
        }

    async def adapter_snapshot(
        self,
        *,
        include_groups: bool = False,
        group_limit: int = 20,
    ) -> dict[str, Any]:
        connected = await self.bot_adapter.health_check()
        login_info: dict[str, Any] = {}
        version_info: dict[str, Any] = {}
        groups: list[dict[str, Any]] = []
        if connected:
            try:
                login_info = await self.bot_adapter.get_login_info()
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                logger.warning("Failed to fetch NapCat login info: %s", exc)
            try:
                version_info = await self.bot_adapter.get_version_info()
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                logger.warning("Failed to fetch NapCat version info: %s", exc)
            try:
                groups = await self.bot_adapter.get_group_list()
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                logger.warning("Failed to fetch NapCat group list: %s", exc)

        default_group_id = self.settings.default_qq_group_id
        default_group = None
        if default_group_id:
            default_group = next(
                (group for group in groups if str(group.get("group_id") or "") == default_group_id),
                None,
            )

        payload = {
            "connected": connected,
            "login_info": login_info,
            "version_info": version_info,
            "groups_count": len(groups),
            "default_group_id": default_group_id,
            "default_group_configured": bool(default_group_id),
            "default_group_found": bool(default_group) if default_group_id else None,
            "default_group_name": default_group.get("group_name") if default_group else None,
        }
        if include_groups:
            safe_limit = min(max(group_limit, 1), 100)
            payload["groups"] = groups[:safe_limit]
        return payload

    async def list_groups(self, *, limit: int = 20) -> dict[str, Any]:
        snapshot = await self.adapter_snapshot(include_groups=True, group_limit=limit)
        return {
            "connected": snapshot["connected"],
            "groups_count": snapshot["groups_count"],
            "groups": snapshot.get("groups", []),
        }

    def review_queue(self, *, limit: int = 5) -> dict[str, Any]:
        safe_limit = min(max(limit, 1), 20)
        failed_records = self.delivery_service.list_failed_records(limit=safe_limit)
        return {
            "pipeline_pending": self.pipeline.queue_size(),
            "failed_deliveries": self.delivery_service.failed_records_count(),
            "recent_failed": [
                {
                    "trace_id": record.trace_id,
                    "target_type": record.target_type,
                    "target_id": record.target_id,
                    "attempts": record.attempts,
                    "error_code": record.error_code,
                    "error_message": record.error_message,
                    "updated_at": record.updated_at.isoformat(),
                }
                for record in failed_records
            ],
        }

    async def retry_failed(self, *, trace_id: str | None = None) -> dict[str, Any]:
        result = await self.delivery_service.retry_failed(trace_id=trace_id)
        return self._delivery_result_to_dict(result)

    async def send_text(
        self,
        *,
        text: str,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_target_type, resolved_target_id = self._resolve_target(target_type=target_type, target_id=target_id)
        route = f"{resolved_target_type}:{resolved_target_id}"
        message_id = await self.bot_adapter.send_text(route, text)
        return {
            "status": "sent",
            "target_type": resolved_target_type,
            "target_id": resolved_target_id,
            "message_id": message_id,
        }

    async def send_news_card(
        self,
        *,
        image_path: str,
        caption: str | None = None,
        trace_id: str | None = None,
        processed_event_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_target_type, resolved_target_id = self._resolve_target(target_type=target_type, target_id=target_id)
        result = self.delivery_service.enqueue_delivery(
            QQSendNewsCardRequest(
                trace_id=trace_id,
                processed_event_id=processed_event_id,
                target_type=resolved_target_type,
                target_id=resolved_target_id,
                image_path=image_path,
                caption=caption,
            )
        )
        return self._delivery_result_to_dict(result)

    async def push_test(
        self,
        *,
        preset_key: str = "doors",
        theme: str = "light",
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        fixtures = get_test_card_fixtures()
        normalized_preset = preset_key.casefold()
        if normalized_preset not in fixtures:
            raise ValueError(
                copy_format(
                    "qq_operations.errors.unsupported_preset",
                    "Unsupported test preset: {preset_key}. Available: {supported}",
                    preset_key=preset_key,
                    supported=", ".join(sorted(fixtures)),
                )
            )

        raw_event, processed_event = self._build_test_card(normalized_preset)
        artifact = await self.renderer.render(raw_event, processed_event, theme=theme)
        payload: dict[str, Any] = {
            "preset_key": normalized_preset,
            "theme": theme,
            "html_path": artifact.html_path,
            "image_path": artifact.image_path,
        }

        if not artifact.image_path:
            payload["status"] = "rendered_html_only"
            return payload

        caption = copy_format(
            "qq_operations.push_test_caption",
            "{game} / test card",
            game=processed_event.game or "Roblox",
        )
        delivery = await self.send_news_card(
            image_path=artifact.image_path,
            caption=caption,
            processed_event_id=processed_event.id,
            target_type=target_type,
            target_id=target_id,
        )
        return {**payload, **delivery}

    def _resolve_target(
        self,
        *,
        target_type: str | None,
        target_id: str | None,
    ) -> tuple[str, str]:
        if target_type and target_type not in {"group", "private"}:
            raise ValueError(
                copy_text(
                    "qq_operations.errors.target_type_invalid",
                    "target_type must be either 'group' or 'private'.",
                )
            )
        if bool(target_type) != bool(target_id):
            raise ValueError(
                copy_text(
                    "qq_operations.errors.target_pair_required",
                    "target_type and target_id must be provided together.",
                )
            )
        if target_type and target_id:
            return target_type, target_id
        if not target_type and not target_id and self.settings.default_qq_group_id:
            return "group", self.settings.default_qq_group_id
        raise ValueError(
            copy_text(
                "qq_operations.errors.default_target_required",
                "A target_type and target_id are required unless DEFAULT_QQ_GROUP_ID is configured.",
            )
        )

    @staticmethod
    def _delivery_result_to_dict(result: DeliveryExecutionResult) -> dict[str, Any]:
        return {
            "trace_id": result.trace_id,
            "status": result.status,
            "message_id": result.message_id,
            "attempts": result.attempts,
            "target_type": result.target_type,
            "target_id": result.target_id,
            "deduplicated": result.deduplicated,
        }

    def _build_test_card(self, preset_key: str) -> tuple[RawEvent, ProcessedEvent]:
        fixture = get_test_card_fixtures()[preset_key]
        now = utc_now()
        external_id = make_external_id(
            "qq-command-test",
            preset_key,
            fixture["title"],
            now.isoformat(),
        )
        raw_event = RawEvent(
            source_type="qq_command",
            source_name=fixture["source_name"],
            channel_name=fixture["channel_name"],
            author=fixture["author"],
            content="\n".join([fixture["title"], fixture["summary"], *fixture["highlights"]]),
            attachments=[],
            external_id=external_id,
            published_at=now,
            raw_payload={"preset_key": preset_key, "custom_css": ""},
        )
        processed_event = ProcessedEvent(
            raw_event_id=raw_event.id,
            title=fixture["title"],
            summary=fixture["summary"],
            highlights=list(fixture["highlights"]),
            category=fixture["category"],
            game=fixture["game"],
            need_translation=False,
            source_credibility=fixture["source_credibility"],
            media=[],
            discovered_sources=list(fixture["discovered_sources"]),
            language="en",
            published_at=now,
        )
        return raw_event, processed_event

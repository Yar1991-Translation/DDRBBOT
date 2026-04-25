from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .copybook import copy_format, copy_text
from .database import SQLiteRepository
from .models import DeliveryLog, DeliveryRecord, QQSendNewsCardRequest
from .qq.napcat import BotAdapter
from .utils import make_external_id, utc_now


class DeliveryError(RuntimeError):
    def __init__(self, trace_id: str, attempts: int, message: str) -> None:
        super().__init__(message)
        self.trace_id = trace_id
        self.attempts = attempts
        self.message = message


class FileTooLargeError(RuntimeError):
    pass


@dataclass(slots=True)
class DeliveryExecutionResult:
    trace_id: str
    status: str
    message_id: str | None
    attempts: int
    target_type: str
    target_id: str
    deduplicated: bool = False

    def to_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "trace_id": self.trace_id,
            "status": self.status,
            "message_id": self.message_id,
            "attempts": self.attempts,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "deduplicated": self.deduplicated,
        }


class QQDeliveryService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: SQLiteRepository,
        bot_adapter: BotAdapter,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.bot_adapter = bot_adapter

    def enqueue_delivery(
        self,
        request: QQSendNewsCardRequest,
    ) -> DeliveryExecutionResult:
        trace_id = request.trace_id or self._build_trace_id(request)
        request_payload = self._request_payload(request)
        record, created = self.repository.reserve_delivery_record(
            DeliveryRecord(
                trace_id=trace_id,
                processed_event_id=request.processed_event_id,
                target_type=request.target_type,
                target_id=request.target_id,
                request_payload=request_payload,
                status="pending",
                next_retry_at=utc_now(),
            )
        )
        if not created:
            if record.status in {"sent"} and record.message_id:
                return DeliveryExecutionResult(
                    trace_id=trace_id,
                    status="duplicate",
                    message_id=record.message_id,
                    attempts=record.attempts,
                    target_type=record.target_type,
                    target_id=record.target_id,
                    deduplicated=True,
                )
            self.repository.update_delivery_record(
                trace_id,
                processed_event_id=request.processed_event_id or record.processed_event_id,
                request_payload=request_payload,
                status="pending" if record.status == "dead_letter" else record.status,
                next_retry_at=utc_now(),
            )
            record = self.repository.get_delivery_record(trace_id) or record
        return DeliveryExecutionResult(
            trace_id=trace_id,
            status="queued",
            message_id=None,
            attempts=record.attempts,
            target_type=record.target_type,
            target_id=record.target_id,
            deduplicated=False,
        )

    async def send_once(
        self,
        request: QQSendNewsCardRequest,
        *,
        trace_id: str,
    ) -> tuple[bool, str | None, str | None]:
        route = f"{request.target_type}:{request.target_id}"
        try:
            image_path = self._validate_image_path(request.image_path)
        except (FileNotFoundError, FileTooLargeError) as exc:
            self.repository.save_delivery_log(
                DeliveryLog(
                    processed_event_id=request.processed_event_id,
                    target_id=route,
                    delivery_result="failed",
                    error_message=str(exc),
                )
            )
            return False, type(exc).__name__, str(exc)
        try:
            message_id = await self.bot_adapter.send_news_card(
                route,
                str(image_path),
                request.caption,
            )
        except Exception as exc:
            self.repository.save_delivery_log(
                DeliveryLog(
                    processed_event_id=request.processed_event_id,
                    target_id=route,
                    delivery_result="failed",
                    error_message=str(exc),
                )
            )
            return False, type(exc).__name__, str(exc)
        self.repository.save_delivery_log(
            DeliveryLog(
                processed_event_id=request.processed_event_id,
                target_id=route,
                delivery_result="sent",
                message_id=message_id,
            )
        )
        return True, message_id, None

    async def send_text_fallback_once(
        self,
        request: QQSendNewsCardRequest,
    ) -> tuple[bool, str | None, str | None]:
        if not self.settings.qq_image_fail_text_fallback_enabled:
            return False, None, "text_fallback_disabled"
        text = self._build_text_fallback(request)
        if not text:
            return False, None, "empty_fallback_text"
        route = f"{request.target_type}:{request.target_id}"
        try:
            message_id = await self.bot_adapter.send_text(route, text[:3500])
        except Exception as exc:
            return False, None, str(exc)
        self.repository.save_delivery_log(
            DeliveryLog(
                processed_event_id=request.processed_event_id,
                target_id=route,
                delivery_result="sent_text_fallback",
                message_id=message_id or None,
                error_message=None,
            )
        )
        return True, message_id, None

    def request_from_record(self, record: DeliveryRecord) -> QQSendNewsCardRequest:
        return self._request_from_record(record)

    def _build_text_fallback(self, request: QQSendNewsCardRequest) -> str | None:
        parts: list[str] = []
        if request.processed_event_id:
            ev = self.repository.get_processed_event(request.processed_event_id)
            if ev:
                parts.append(ev.title.strip())
                if ev.summary.strip():
                    parts.append(ev.summary.strip()[:800])
        if request.caption and request.caption.strip():
            parts.append(request.caption.strip())
        parts.append(
            copy_text(
                "delivery.image_fail_text_fallback_note",
                "（图片未发出）以下为摘要；可打开卡片 HTML 查看排版。",
            )
        )
        if request.image_path:
            parts.append(str(Path(request.image_path).expanduser().resolve()))
        body = "\n\n".join(p for p in parts if p)
        return body if body.strip() else None

    def list_failed_records(self, *, limit: int = 5) -> list[DeliveryRecord]:
        return self.repository.list_delivery_records(
            statuses=("failed", "dead_letter"),
            limit=limit,
        )

    def failed_records_count(self) -> int:
        return self.repository.count_delivery_records(statuses=("failed", "dead_letter"))

    def list_dead_letter_records(self, *, limit: int = 20) -> list[DeliveryRecord]:
        return self.repository.list_delivery_records(
            statuses=("dead_letter",),
            limit=limit,
        )

    def requeue_record(self, record: DeliveryRecord) -> DeliveryExecutionResult:
        self.repository.update_delivery_record(
            record.trace_id,
            status="pending",
            error_code="",
            error_message="",
            attempts=0,
            next_retry_at=utc_now(),
        )
        record = self.repository.get_delivery_record(record.trace_id) or record
        return DeliveryExecutionResult(
            trace_id=record.trace_id,
            status="queued",
            message_id=None,
            attempts=record.attempts,
            target_type=record.target_type,
            target_id=record.target_id,
            deduplicated=False,
        )

    async def retry_failed(self, trace_id: str | None = None) -> DeliveryExecutionResult:
        record = self._resolve_retry_record(trace_id)
        if record is None:
            raise DeliveryError(
                trace_id or "",
                0,
                copy_text("delivery.no_failed_record", "No failed delivery record found."),
            )
        if record.status not in {"failed", "dead_letter", "retry"}:
            raise DeliveryError(
                record.trace_id,
                record.attempts,
                copy_text("delivery.record_not_failed", "Delivery record is not failed."),
            )
        return self.requeue_record(record)

    @staticmethod
    def _build_trace_id(request: QQSendNewsCardRequest) -> str:
        return make_external_id(
            "qq-delivery",
            request.processed_event_id or "",
            request.target_type,
            request.target_id,
            request.image_path,
            request.caption or "",
        )

    def _validate_image_path(self, image_path: str) -> Path:
        path = Path(image_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(
                copy_format(
                    "delivery.image_not_found",
                    "Image file not found: {image_path}",
                    image_path=path,
                )
            )
        size_bytes = path.stat().st_size
        if size_bytes > self.settings.qq_news_card_max_bytes:
            raise FileTooLargeError(
                copy_format(
                    "delivery.image_too_large",
                    "Image file is too large: {image_path} ({size_bytes} bytes > {limit_bytes} bytes)",
                    image_path=path,
                    size_bytes=size_bytes,
                    limit_bytes=self.settings.qq_news_card_max_bytes,
                )
            )
        return path

    def _resolve_retry_record(self, trace_id: str | None) -> DeliveryRecord | None:
        if trace_id:
            return self.repository.get_delivery_record(trace_id)
        records = self.list_failed_records(limit=1)
        return records[0] if records else None

    @staticmethod
    def _request_payload(request: QQSendNewsCardRequest) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        payload["image_path"] = str(Path(request.image_path).expanduser().resolve())
        return payload

    def _request_from_record(self, record: DeliveryRecord) -> QQSendNewsCardRequest:
        payload = dict(record.request_payload)
        payload.setdefault("trace_id", record.trace_id)
        payload.setdefault("processed_event_id", record.processed_event_id)
        payload.setdefault("target_type", record.target_type)
        payload.setdefault("target_id", record.target_id)
        image_path = payload.get("image_path")
        if not image_path:
            raise DeliveryError(
                record.trace_id,
                record.attempts,
                copy_text("delivery.missing_image_path", "Stored delivery payload is missing image_path."),
            )
        return QQSendNewsCardRequest.model_validate(payload)

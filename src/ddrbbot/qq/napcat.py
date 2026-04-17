from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..models import QQInboundEvent
from ..config import Settings
from ..copybook import copy_text

logger = logging.getLogger(__name__)


class NapCatAPIError(RuntimeError):
    pass


class BotAdapter(Protocol):
    async def send_news_card(self, target: str, image_path: str, caption: str | None = None) -> str:
        ...

    async def send_text(self, target: str, text: str) -> str:
        ...

    async def recall_message(self, message_id: str) -> None:
        ...

    async def get_login_info(self) -> dict[str, Any]:
        ...

    async def get_version_info(self) -> dict[str, Any]:
        ...

    async def get_group_list(self) -> list[dict[str, Any]]:
        ...

    async def health_check(self) -> bool:
        ...


class NapCatAdapter:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.napcat_base_url
        self.access_token = settings.napcat_access_token
        self.timeout = settings.napcat_timeout_seconds

    async def send_news_card(self, target: str, image_path: str, caption: str | None = None) -> str:
        route = self._parse_target(target)
        file_value = self._encode_image_for_napcat(image_path)
        message = [{"type": "image", "data": {"file": file_value}}]
        if caption:
            message.append({"type": "text", "data": {"text": f"\n{caption}"}})
        payload = {**route, "message": message}
        response = await self._call_action("send_msg", payload)
        return self._extract_message_id(response)

    @staticmethod
    def _encode_image_for_napcat(image_path: str) -> str:
        if image_path.startswith(("http://", "https://", "base64://")):
            return image_path
        local = Path(image_path).expanduser()
        if local.is_file():
            encoded = base64.b64encode(local.read_bytes()).decode("ascii")
            return f"base64://{encoded}"
        return image_path

    async def send_text(self, target: str, text: str) -> str:
        route = self._parse_target(target)
        response = await self._call_action("send_msg", {**route, "message": text})
        return self._extract_message_id(response)

    async def recall_message(self, message_id: str) -> None:
        await self._call_action("delete_msg", {"message_id": message_id})

    async def get_login_info(self) -> dict[str, Any]:
        response = await self._call_action("get_login_info", {})
        return response.get("data", {})

    async def get_version_info(self) -> dict[str, Any]:
        response = await self._call_action("get_version_info", {})
        return response.get("data", {})

    async def get_group_list(self) -> list[dict[str, Any]]:
        response = await self._call_action("get_group_list", {})
        data = response.get("data")
        return data if isinstance(data, list) else []

    async def health_check(self) -> bool:
        try:
            await self.get_login_info()
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            logger.warning("NapCat health check failed: %s", exc)
            return False
        return True

    async def _call_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/{action}", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        status = str(data.get("status") or "").lower()
        retcode = data.get("retcode")
        if status == "failed" or retcode not in (None, 0):
            message = str(
                data.get("message")
                or data.get("wording")
                or copy_text("napcat.action_failed", "NapCat action failed.")
            )
            raise NapCatAPIError(f"{action} failed: {message}")
        return data

    @staticmethod
    def _parse_target(target: str) -> dict[str, Any]:
        if target.startswith("private:"):
            return {
                "message_type": "private",
                "user_id": target.split(":", 1)[1],
            }
        if target.startswith("group:"):
            target = target.split(":", 1)[1]
        return {
            "message_type": "group",
            "group_id": target,
        }

    @staticmethod
    def _extract_message_id(response: dict[str, Any]) -> str:
        data = response.get("data") or {}
        message_id = data.get("message_id")
        return str(message_id) if message_id is not None else ""


def normalize_inbound_event(payload: dict[str, Any]) -> QQInboundEvent:
    if "event_type" in payload and "platform" in payload and "adapter" in payload:
        normalized = dict(payload)
        normalized.setdefault("raw_payload", payload)
        event = QQInboundEvent.model_validate(normalized)
        event.at_self = _detect_at_self(event.self_id, event.segments)
        return event

    post_type = str(payload.get("post_type") or "")
    message_type = str(payload.get("message_type") or "")
    sub_type = str(payload.get("sub_type") or "") or None
    event_type = _event_type_from_onebot(post_type, message_type)
    segments = _normalize_segments(payload.get("message"))

    user_id = payload.get("user_id")
    sender = payload.get("sender")
    if user_id is None and isinstance(sender, dict):
        user_id = sender.get("user_id")

    self_id = _string_or_none(payload.get("self_id"))
    return QQInboundEvent.model_validate(
        {
            "platform": "qq",
            "adapter": "napcat",
            "event_type": event_type,
            "post_type": post_type or None,
            "sub_type": sub_type,
            "group_id": _string_or_none(payload.get("group_id")),
            "user_id": _string_or_none(user_id),
            "message_id": _string_or_none(payload.get("message_id")),
            "self_id": self_id,
            "raw_message": _extract_raw_message(payload.get("raw_message"), segments),
            "segments": segments,
            "at_self": _detect_at_self(self_id, segments),
            "time": payload.get("time"),
            "raw_payload": payload,
        }
    )


def _detect_at_self(self_id: str | None, segments: list[dict[str, Any]]) -> bool:
    if not self_id:
        return False
    for segment in segments:
        if segment.get("type") != "at":
            continue
        data = segment.get("data") or {}
        qq_val = str(data.get("qq") or data.get("user_id") or "").strip()
        if qq_val == "all":
            return True
        if qq_val == str(self_id).strip():
            return True
    return False


def _event_type_from_onebot(post_type: str, message_type: str) -> str:
    if post_type in {"message", "message_sent"}:
        if message_type == "group":
            return "group_message"
        if message_type == "private":
            return "private_message"
        return "message"
    return post_type or "unknown"


def _normalize_segments(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, str):
        text = message.strip()
        return [{"type": "text", "data": {"text": text}}] if text else []

    normalized: list[dict[str, Any]] = []
    if not isinstance(message, list):
        return normalized

    for item in message:
        if not isinstance(item, dict):
            continue
        segment_type = str(item.get("type") or "")
        data = item.get("data")
        normalized.append(
            {
                "type": segment_type,
                "data": data if isinstance(data, dict) else {},
            }
        )
    return normalized


def _extract_raw_message(raw_message: Any, segments: list[dict[str, Any]]) -> str | None:
    if isinstance(raw_message, str) and raw_message.strip():
        return raw_message.strip()

    parts: list[str] = []
    for segment in segments:
        if segment.get("type") != "text":
            continue
        data = segment.get("data") or {}
        text = str(data.get("text") or "").strip()
        if text:
            parts.append(text)
    return "".join(parts).strip() or None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

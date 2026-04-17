from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..copybook import copy_format, copy_text
from ..database import SQLiteRepository
from ..delivery import DeliveryError
from ..llm_agent import (
    AgentContext,
    ChatService,
    ChatTurnRequest,
    LLMAgent,
    PersonaStore,
)
from ..models import CustomPersonaPayload, QQInboundEvent
from ..pipeline import PipelineCoordinator
from .napcat import BotAdapter
from .operations import QQOperationsService, get_test_card_fixtures

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QQCommandDispatchResult:
    handled: bool = False
    command: str | None = None
    authorized: bool | None = None
    response_message_id: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "command": self.command,
            "authorized": self.authorized,
            "response_message_id": self.response_message_id,
            "response_sent": bool(self.response_message_id),
            "detail": self.detail,
        }


class QQCommandAuthorizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def authorize(self, event: QQInboundEvent) -> tuple[bool, str | None]:
        if self.settings.qq_admin_group_ids and event.group_id:
            if event.group_id not in self.settings.qq_admin_group_ids:
                return False, copy_text(
                    "qq_commands.authorizer.group_denied",
                    "This group is not in the admin allowlist.",
                )
        if not self.settings.qq_admin_user_ids:
            return True, None
        if event.user_id and event.user_id in self.settings.qq_admin_user_ids:
            return True, None
        return False, copy_text(
            "qq_commands.authorizer.user_denied",
            "This user is not in the admin allowlist.",
        )

    def describe_mode(self) -> str:
        if not self.settings.qq_admin_user_ids and not self.settings.qq_admin_group_ids:
            return copy_text("qq_commands.status.auth_open", "open")
        parts: list[str] = []
        if self.settings.qq_admin_user_ids:
            parts.append(
                copy_format(
                    "qq_commands.status.auth_users",
                    "{count} users",
                    count=len(self.settings.qq_admin_user_ids),
                )
            )
        if self.settings.qq_admin_group_ids:
            parts.append(
                copy_format(
                    "qq_commands.status.auth_groups",
                    "{count} groups",
                    count=len(self.settings.qq_admin_group_ids),
                )
            )
        return ", ".join(parts) if parts else copy_text("qq_commands.status.auth_open", "open")


class QQCommandRouter:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: SQLiteRepository,
        bot_adapter: BotAdapter,
        pipeline: PipelineCoordinator,
        operations_service: QQOperationsService,
        llm_agent: LLMAgent | None = None,
        chat_service: ChatService | None = None,
        persona_store: PersonaStore | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.bot_adapter = bot_adapter
        self.pipeline = pipeline
        self.operations_service = operations_service
        self.llm_agent = llm_agent
        self.chat_service = chat_service
        self.persona_store = persona_store
        self.authorizer = QQCommandAuthorizer(settings)

    async def dispatch(self, event: QQInboundEvent) -> QQCommandDispatchResult:
        if event.event_type not in {"group_message", "private_message"}:
            return QQCommandDispatchResult(detail="unsupported_event")

        message = self._extract_message_text(event)
        if message.startswith("/"):
            normalized = " ".join(message.split())
            lowered = normalized.casefold()
            if lowered == "/ping":
                return await self._dispatch_admin(event, "ping", self._handle_ping)
            if lowered == "/status":
                return await self._dispatch_admin(event, "status", self._handle_status)
            if lowered == "/review queue":
                return await self._dispatch_admin(event, "review_queue", self._handle_review_queue)
            if lowered.startswith("/retry failed"):
                return await self._dispatch_admin(
                    event,
                    "retry_failed",
                    lambda inbound: self._handle_retry_failed(inbound, normalized),
                )
            if lowered.startswith("/push test"):
                return await self._dispatch_admin(
                    event,
                    "push_test",
                    lambda inbound: self._handle_push_test(inbound, normalized),
                )
            if lowered.startswith("/ai ") or lowered.startswith("/chat "):
                prompt_body = normalized.split(" ", 1)[1] if " " in normalized else ""
                return await self._dispatch_admin(
                    event,
                    "ai_chat",
                    lambda inbound: self._handle_ai_chat(inbound, prompt_body),
                )
            if lowered == "/persona" or lowered.startswith("/persona "):
                body = normalized.split(" ", 1)[1].strip() if " " in normalized else ""
                return await self._dispatch_admin(
                    event,
                    "persona",
                    lambda inbound: self._handle_persona_command(inbound, body),
                )
            if lowered == "/forget":
                return await self._dispatch_admin(
                    event,
                    "forget",
                    self._handle_forget_command,
                )
            return QQCommandDispatchResult(detail="unknown_command")

        if self._should_route_to_agent(event, message):
            return await self._dispatch_admin(
                event,
                "ai_chat",
                lambda inbound: self._handle_ai_chat(inbound, message),
            )
        return QQCommandDispatchResult(detail="not_command")

    def _should_route_to_agent(self, event: QQInboundEvent, message: str) -> bool:
        if not message.strip() or self.llm_agent is None or not self.llm_agent.enabled:
            return False
        if event.event_type == "private_message":
            return True
        if event.event_type == "group_message" and event.at_self:
            return True
        return False

    async def _handle_ai_chat(
        self,
        event: QQInboundEvent,
        user_prompt: str,
    ) -> QQCommandDispatchResult:
        prompt = user_prompt.strip()
        if not prompt or self.llm_agent is None:
            return await self._send_text(
                event,
                text=copy_text("llm_agent.empty_prompt", "请输入要让 AI 处理的内容。"),
                detail="empty_prompt",
            )
        reply_target_type, reply_target_id = self._reply_target_pair(event)
        context = AgentContext(
            origin="qq_chat",
            reply_target_type=reply_target_type,
            reply_target_id=reply_target_id,
            initiator_user_id=event.user_id,
            is_private=event.event_type == "private_message",
            at_self=event.at_self,
        )

        if self.chat_service is None:
            system_prompt = copy_text(
                "llm_agent.chat_system_prompt",
                "你是 DDRBBOT 的 QQ 助手。最终通过 send_reply_text 向当前会话回复。",
            )
            transcript = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            result = await self.llm_agent.run(context, transcript)
        else:
            turn = await self.chat_service.run_turn(
                ChatTurnRequest(
                    origin="qq_chat",
                    user_message=prompt,
                    group_id=event.group_id,
                    user_id=event.user_id,
                    agent_context=context,
                )
            )
            result = turn.run_result

        if result.final_text and not self._reply_already_sent(result.messages):
            return await self._send_text(
                event,
                text=result.final_text,
                detail=result.error or "ok",
            )
        return QQCommandDispatchResult(
            handled=True,
            response_message_id=self._find_reply_message_id(result.messages),
            detail=result.error or "ok",
        )

    async def _handle_persona_command(
        self,
        event: QQInboundEvent,
        body: str,
    ) -> QQCommandDispatchResult:
        if self.persona_store is None or self.chat_service is None:
            return await self._send_text(
                event,
                text=copy_text(
                    "llm_agent.disabled",
                    "LLM Agent 未启用。",
                ),
                detail="chat_disabled",
            )
        trimmed = body.strip()
        if not trimmed or trimmed.casefold() in {"help", "show", "status"}:
            return await self._send_persona_status(event)
        parts = trimmed.split(maxsplit=1)
        action = parts[0].casefold()
        payload = parts[1].strip() if len(parts) > 1 else ""
        if action == "list":
            return await self._send_persona_list(event)
        if action == "use":
            if not payload:
                return await self._send_text(
                    event,
                    text=copy_text(
                        "chat_commands.persona_usage",
                        "用法：/persona list | /persona use <key> | /persona custom <设定> | /persona reset | /persona show",
                    ),
                    detail="persona_missing_key",
                )
            return await self._apply_persona_key(event, payload)
        if action == "custom":
            return await self._apply_persona_custom(event, payload)
        if action == "reset" or action == "clear":
            return await self._reset_persona(event)
        return await self._send_text(
            event,
            text=copy_text(
                "chat_commands.persona_usage",
                "用法：/persona list | /persona use <key> | /persona custom <设定> | /persona reset | /persona show",
            ),
            detail="persona_unknown_action",
        )

    async def _send_persona_status(self, event: QQInboundEvent) -> QQCommandDispatchResult:
        assert self.chat_service is not None and self.persona_store is not None
        session = self.chat_service.ensure_session(
            origin="qq_chat",
            group_id=event.group_id,
            user_id=event.user_id,
        )
        if session.custom_persona and session.custom_persona.system_prompt.strip():
            text = copy_format(
                "chat_commands.persona_active_custom",
                "当前会话使用自定义角色：{label}",
                label=session.custom_persona.label or "自定义角色",
            )
        elif session.persona_id:
            persona = self.repository.get_chat_persona(session.persona_id)
            if persona is None:
                text = copy_text(
                    "chat_commands.persona_active_default",
                    "当前会话使用默认助手角色。",
                )
            else:
                text = copy_format(
                    "chat_commands.persona_active",
                    "当前会话角色：{label}（key={key}）",
                    label=persona.label,
                    key=persona.persona_key,
                )
        else:
            text = copy_text(
                "chat_commands.persona_active_default",
                "当前会话使用默认助手角色。",
            )
        return await self._send_text(event, text=text)

    async def _send_persona_list(self, event: QQInboundEvent) -> QQCommandDispatchResult:
        assert self.persona_store is not None
        personas = self.persona_store.list_personas()
        if not personas:
            return await self._send_text(
                event,
                text=copy_text(
                    "chat_commands.persona_active_default",
                    "当前会话使用默认助手角色。",
                ),
            )
        lines = [copy_text("chat_commands.persona_list_title", "可用角色：")]
        for persona in personas:
            lines.append(
                copy_format(
                    "chat_commands.persona_list_item",
                    "- {key}: {label}",
                    key=persona.persona_key,
                    label=persona.label,
                )
            )
        return await self._send_text(event, text="\n".join(lines))

    async def _apply_persona_key(
        self,
        event: QQInboundEvent,
        persona_key: str,
    ) -> QQCommandDispatchResult:
        assert self.chat_service is not None and self.persona_store is not None
        key = persona_key.strip()
        session = self.chat_service.ensure_session(
            origin="qq_chat",
            group_id=event.group_id,
            user_id=event.user_id,
        )
        try:
            self.persona_store.update_session_persona(
                session,
                persona_id_or_key=key,
            )
        except ValueError:
            return await self._send_text(
                event,
                text=copy_format(
                    "chat_commands.persona_not_found",
                    "找不到角色：{key}。",
                    key=key,
                ),
                detail="persona_not_found",
            )
        persona = self.repository.get_chat_persona(key)
        label = persona.label if persona else key
        return await self._send_text(
            event,
            text=copy_format(
                "chat_commands.persona_switched",
                "已切换角色为 {label}（key={key}）。",
                label=label,
                key=persona.persona_key if persona else key,
            ),
        )

    async def _apply_persona_custom(
        self,
        event: QQInboundEvent,
        payload: str,
    ) -> QQCommandDispatchResult:
        assert self.chat_service is not None and self.persona_store is not None
        body = payload.strip()
        if not body:
            return await self._send_text(
                event,
                text=copy_text(
                    "chat_commands.persona_custom_empty",
                    "自定义角色内容不能为空。",
                ),
                detail="persona_custom_empty",
            )
        session = self.chat_service.ensure_session(
            origin="qq_chat",
            group_id=event.group_id,
            user_id=event.user_id,
        )
        custom = CustomPersonaPayload(
            label="自定义角色",
            description="",
            system_prompt=body,
            allow_tools=True,
        )
        self.persona_store.update_session_persona(session, custom=custom)
        return await self._send_text(
            event,
            text=copy_text(
                "chat_commands.persona_custom_saved",
                "已保存自定义角色设定，接下来按新人格回复。",
            ),
        )

    async def _reset_persona(self, event: QQInboundEvent) -> QQCommandDispatchResult:
        assert self.chat_service is not None and self.persona_store is not None
        session = self.chat_service.ensure_session(
            origin="qq_chat",
            group_id=event.group_id,
            user_id=event.user_id,
        )
        self.persona_store.update_session_persona(session, reset=True)
        return await self._send_text(
            event,
            text=copy_text(
                "chat_commands.persona_reset",
                "已清除角色设定，恢复默认助手。",
            ),
        )

    async def _handle_forget_command(
        self,
        event: QQInboundEvent,
    ) -> QQCommandDispatchResult:
        if self.chat_service is None:
            return await self._send_text(
                event,
                text=copy_text("llm_agent.disabled", "LLM Agent 未启用。"),
                detail="chat_disabled",
            )
        session = self.chat_service.ensure_session(
            origin="qq_chat",
            group_id=event.group_id,
            user_id=event.user_id,
        )
        self.repository.clear_chat_messages(session.id)
        self.repository.update_chat_session(
            session.id,
            summary="",
            touch_summary=True,
        )
        return await self._send_text(
            event,
            text=copy_text(
                "chat_commands.forget_done",
                "已清空当前会话的历史记忆。",
            ),
        )

    @staticmethod
    def _reply_already_sent(messages: list[dict[str, Any]]) -> bool:
        for msg in messages:
            if msg.get("role") != "tool":
                continue
            if msg.get("name") == "send_reply_text":
                try:
                    content = msg.get("content") or "{}"
                    data = json.loads(content) if isinstance(content, str) else content
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("ok"):
                    return True
        return False

    @staticmethod
    def _find_reply_message_id(messages: list[dict[str, Any]]) -> str | None:
        for msg in reversed(messages):
            if msg.get("role") != "tool" or msg.get("name") != "send_reply_text":
                continue
            try:
                content = msg.get("content") or "{}"
                data = json.loads(content) if isinstance(content, str) else content
            except Exception:
                continue
            if isinstance(data, dict) and data.get("ok"):
                inner = data.get("data") or {}
                mid = inner.get("message_id")
                if mid:
                    return str(mid)
        return None

    @staticmethod
    def _reply_target_pair(event: QQInboundEvent) -> tuple[str | None, str | None]:
        if event.group_id:
            return "group", event.group_id
        if event.user_id:
            return "private", event.user_id
        return None, None

    async def _dispatch_admin(
        self,
        event: QQInboundEvent,
        command: str,
        handler: Any,
    ) -> QQCommandDispatchResult:
        allowed, denial_reason = self.authorizer.authorize(event)
        if not allowed:
            return await self._send_text(
                event,
                text=denial_reason
                or copy_text(
                    "qq_commands.authorizer.command_unavailable",
                    "This command is not available.",
                ),
                command=command,
                authorized=False,
            )
        result = await handler(event)
        result.command = command
        result.authorized = True
        return result

    async def _handle_ping(self, event: QQInboundEvent) -> QQCommandDispatchResult:
        napcat_connected = await self.bot_adapter.health_check()
        text = "\n".join(
            [
                copy_text("qq_commands.ping.title", "PONG"),
                (
                    f"{copy_text('qq_commands.shared.napcat_label', 'NapCat')}: "
                    f"{copy_text('qq_commands.shared.online', 'online') if napcat_connected else copy_text('qq_commands.shared.offline', 'offline')}"
                ),
                f"{copy_text('qq_commands.shared.queue_label', 'Queue')}: {self.pipeline.queue_size()}",
            ]
        )
        return await self._send_text(event, text=text)

    async def _handle_status(self, event: QQInboundEvent) -> QQCommandDispatchResult:
        stats = self.repository.get_stats()
        snapshot = await self.operations_service.adapter_snapshot()
        napcat_connected = bool(snapshot["connected"])
        login_info = snapshot.get("login_info") or {}
        version_info = snapshot.get("version_info") or {}
        login_label = str(
            login_info.get("nickname")
            or login_info.get("user_id")
            or login_info.get("uin")
            or copy_text("qq_commands.shared.unknown", "unknown")
        )
        version_label = str(
            version_info.get("app_name")
            or version_info.get("app_version")
            or version_info.get("version")
            or copy_text("qq_commands.shared.unknown", "unknown")
        )
        group_count = str(snapshot.get("groups_count", copy_text("qq_commands.shared.unknown", "unknown")))

        text = "\n".join(
            [
                copy_text("qq_commands.status.title", "DDRBBOT Status"),
                (
                    f"{copy_text('qq_commands.shared.napcat_label', 'NapCat')}: "
                    f"{copy_text('qq_commands.shared.online', 'online') if napcat_connected else copy_text('qq_commands.shared.offline', 'offline')}"
                ),
                f"{copy_text('qq_commands.status.bot_label', 'Bot')}: {login_label}",
                f"{copy_text('qq_commands.status.version_label', 'Version')}: {version_label}",
                f"{copy_text('qq_commands.status.groups_label', 'Groups')}: {group_count}",
                f"{copy_text('qq_commands.shared.queue_label', 'Queue')}: {self.pipeline.queue_size()}",
                (
                    f"{copy_text('qq_commands.status.totals_label', 'Raw / Processed / Rendered / Delivered')}: "
                    f"{stats.get('raw_events', 0)} / "
                    f"{stats.get('processed_events', 0)} / "
                    f"{stats.get('render_artifacts', 0)} / "
                    f"{stats.get('delivery_logs', 0)}"
                ),
                f"{copy_text('qq_commands.status.delivery_records_label', 'Delivery Records')}: {stats.get('delivery_records', 0)}",
                (
                    f"{copy_text('qq_commands.status.failed_deliveries_label', 'Failed Deliveries')}: "
                    f"{self.operations_service.review_queue(limit=1)['failed_deliveries']}"
                ),
                f"{copy_text('qq_commands.status.qq_events_label', 'QQ Events')}: {stats.get('platform_events', 0)}",
                f"{copy_text('qq_commands.status.admin_auth_label', 'Admin Auth')}: {self.authorizer.describe_mode()}",
            ]
        )
        return await self._send_text(event, text=text)

    async def _handle_review_queue(self, event: QQInboundEvent) -> QQCommandDispatchResult:
        queue_snapshot = self.operations_service.review_queue(limit=5)
        failed_records = queue_snapshot["recent_failed"]
        lines = [
            copy_text("qq_commands.review_queue.title", "Review Queue"),
            (
                f"{copy_text('qq_commands.review_queue.pipeline_pending', 'Pipeline pending')}: "
                f"{queue_snapshot['pipeline_pending']}"
            ),
            (
                f"{copy_text('qq_commands.review_queue.failed_deliveries', 'Failed deliveries')}: "
                f"{queue_snapshot['failed_deliveries']}"
            ),
        ]
        if not failed_records:
            lines.append(copy_text("qq_commands.review_queue.recent_failed_none", "Recent failed: none"))
            return await self._send_text(event, text="\n".join(lines))

        lines.append(copy_text("qq_commands.review_queue.recent_failed_title", "Recent failed:"))
        for record in failed_records:
            error_label = self._shorten(
                record.get("error_message")
                or record.get("error_code")
                or copy_text("qq_commands.shared.unknown", "unknown")
            )
            lines.append(
                copy_format(
                    "qq_commands.review_queue.recent_failed_item",
                    "- {trace_id} [{target_type}:{target_id}] attempts={attempts} error={error}",
                    trace_id=record["trace_id"],
                    target_type=record["target_type"],
                    target_id=record["target_id"],
                    attempts=record["attempts"],
                    error=error_label,
                )
            )
        return await self._send_text(event, text="\n".join(lines))

    async def _handle_retry_failed(
        self,
        event: QQInboundEvent,
        normalized_command: str,
    ) -> QQCommandDispatchResult:
        tokens = normalized_command.split(maxsplit=2)
        trace_id = tokens[2].strip() if len(tokens) >= 3 else None

        try:
            result = await self.operations_service.retry_failed(trace_id=trace_id or None)
        except DeliveryError as exc:
            return await self._send_text(
                event,
                text=copy_format(
                    "qq_commands.retry_failed.failed",
                    "Retry failed: {message}",
                    message=exc.message,
                ),
                detail=exc.message,
            )

        text = "\n".join(
            [
                copy_text("qq_commands.retry_failed.sent", "Retry sent"),
                f"{copy_text('qq_commands.retry_failed.trace', 'Trace')}: {result['trace_id']}",
                f"{copy_text('qq_commands.retry_failed.route', 'Route')}: {result['target_type']}:{result['target_id']}",
                f"{copy_text('qq_commands.retry_failed.attempts', 'Attempts')}: {result['attempts']}",
                (
                    f"{copy_text('qq_commands.retry_failed.message_id', 'Message ID')}: "
                    f"{result.get('message_id') or copy_text('qq_commands.retry_failed.message_id_unknown', 'unknown')}"
                ),
            ]
        )
        return await self._send_text(event, text=text, detail=str(result.get("status") or "sent"))

    async def _handle_push_test(
        self,
        event: QQInboundEvent,
        normalized_command: str,
    ) -> QQCommandDispatchResult:
        tokens = normalized_command.split()
        preset_key = tokens[2].casefold() if len(tokens) >= 3 else "doors"
        fixtures = get_test_card_fixtures()
        if preset_key not in fixtures:
            supported = ", ".join(sorted(fixtures))
            return await self._send_text(
                event,
                text=copy_format(
                    "qq_commands.push_test.unsupported_preset",
                    "Unsupported test preset: {preset_key}. Available: {supported}",
                    preset_key=preset_key,
                    supported=supported,
                ),
            )

        try:
            result = await self.operations_service.push_test(
                preset_key=preset_key,
                theme="light",
                target_type="group" if event.group_id else "private",
                target_id=event.group_id or event.user_id,
            )
        except ValueError as exc:
            return await self._send_text(event, text=str(exc), detail=str(exc))

        if result.get("status") == "rendered_html_only":
            return await self._send_text(
                event,
                text=copy_format(
                    "qq_commands.push_test.image_unavailable",
                    "Test card rendered, but no PNG is available for NapCat.\nHTML: {html_path}",
                    html_path=result["html_path"],
                ),
                detail="image_unavailable",
            )
        text = copy_format(
            "qq_commands.push_test.queued",
            "Test card queued: {preset_key} -> {target_type}:{target_id} (trace {trace_id})",
            preset_key=preset_key,
            target_type=str(result.get("target_type") or "group"),
            target_id=str(result.get("target_id") or ""),
            trace_id=str(result.get("trace_id") or ""),
        )
        return await self._send_text(event, text=text, detail=str(result.get("status") or "queued"))

    async def _send_text(
        self,
        event: QQInboundEvent,
        *,
        text: str,
        command: str | None = None,
        authorized: bool | None = None,
        detail: str | None = None,
    ) -> QQCommandDispatchResult:
        result = QQCommandDispatchResult(
            handled=True,
            command=command,
            authorized=authorized,
            detail=detail,
        )
        target = self._resolve_reply_target(event)
        if target is None:
            result.detail = "missing_reply_target"
            return result

        try:
            result.response_message_id = await self.bot_adapter.send_text(target, text)
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            logger.warning("Failed to send QQ text response: %s", exc)
            result.detail = str(exc)
        return result

    @staticmethod
    def _extract_message_text(event: QQInboundEvent) -> str:
        if event.raw_message:
            return event.raw_message.strip()
        parts: list[str] = []
        for segment in event.segments:
            if segment.get("type") != "text":
                continue
            data = segment.get("data") or {}
            text = str(data.get("text") or "").strip()
            if text:
                parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _resolve_reply_target(event: QQInboundEvent) -> str | None:
        if event.group_id:
            return f"group:{event.group_id}"
        if event.user_id:
            return f"private:{event.user_id}"
        return None

    @staticmethod
    def _shorten(value: str, *, limit: int = 42) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 3]}..."

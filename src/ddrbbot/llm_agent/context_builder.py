from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..copybook import copy_text
from ..database import SQLiteRepository
from ..models import (
    ChatKnowledgeItem,
    ChatMessageRecord,
    ChatProfile,
    ChatSession,
    CustomPersonaPayload,
)
from .persona_store import ActivePersona, PersonaStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ContextBuildConfig:
    history_limit: int = 12
    knowledge_limit: int = 4
    include_knowledge: bool = True
    max_prompt_chars: int = 8000


@dataclass(slots=True)
class BuiltContext:
    messages: list[dict[str, Any]]
    persona: ActivePersona
    session: ChatSession
    history: list[ChatMessageRecord] = field(default_factory=list)
    profile: ChatProfile | None = None
    knowledge: list[ChatKnowledgeItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class ChatContextBuilder:
    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        persona_store: PersonaStore,
        config: ContextBuildConfig | None = None,
    ) -> None:
        self.repository = repository
        self.persona_store = persona_store
        self.config = config or ContextBuildConfig()

    def build(
        self,
        *,
        session: ChatSession,
        user_message: str,
        profile_scope: str | None = None,
        profile_user_id: str | None = None,
        override_persona_id: str | None = None,
        override_custom_persona: CustomPersonaPayload | None = None,
        override_history_limit: int | None = None,
        include_knowledge: bool | None = None,
    ) -> BuiltContext:
        persona = self.persona_store.resolve_active(
            session,
            override_persona_id=override_persona_id,
            override_custom=override_custom_persona,
        )
        history_limit = override_history_limit or self.config.history_limit
        history = self.repository.list_chat_messages(session.id, limit=history_limit)

        profile: ChatProfile | None = None
        if profile_scope and profile_user_id:
            profile = self.repository.get_chat_profile(
                scope=profile_scope,
                user_id=profile_user_id,
            )

        knowledge_items: list[ChatKnowledgeItem] = []
        use_knowledge = include_knowledge
        if use_knowledge is None:
            use_knowledge = self.config.include_knowledge
        if use_knowledge and user_message.strip():
            try:
                knowledge_items = self.repository.search_chat_knowledge_items(
                    user_message,
                    limit=self.config.knowledge_limit,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("knowledge search failed: %s", exc)
                knowledge_items = []

        messages = self._assemble_messages(
            persona=persona,
            session=session,
            history=history,
            profile=profile,
            knowledge_items=knowledge_items,
            user_message=user_message,
        )

        notes: list[str] = []
        total_chars = sum(len(str(m.get("content") or "")) for m in messages)
        if total_chars > self.config.max_prompt_chars:
            messages = self._trim_to_budget(messages, self.config.max_prompt_chars)
            notes.append(
                f"trimmed_to_budget:{self.config.max_prompt_chars}"
            )

        return BuiltContext(
            messages=messages,
            persona=persona,
            session=session,
            history=history,
            profile=profile,
            knowledge=knowledge_items,
            notes=notes,
        )

    def _assemble_messages(
        self,
        *,
        persona: ActivePersona,
        session: ChatSession,
        history: list[ChatMessageRecord],
        profile: ChatProfile | None,
        knowledge_items: list[ChatKnowledgeItem],
        user_message: str,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        persona_prompt = self.persona_store.compose_system_prompt(persona)
        messages.append({"role": "system", "content": persona_prompt})

        if profile is not None:
            profile_text = self._render_profile(profile)
            if profile_text:
                header = copy_text(
                    "llm_agent.context_profile_header",
                    "以下是当前用户的长期画像与偏好：",
                )
                messages.append(
                    {"role": "system", "content": f"{header}\n{profile_text}"}
                )

        if session.summary.strip():
            header = copy_text(
                "llm_agent.context_session_summary_header",
                "以下是此前对话的滚动摘要：",
            )
            messages.append(
                {"role": "system", "content": f"{header}\n{session.summary.strip()}"}
            )

        if knowledge_items:
            header = copy_text(
                "llm_agent.context_knowledge_header",
                "以下是可能相关的背景资料，请仅在有用时引用：",
            )
            lines: list[str] = []
            for item in knowledge_items:
                tags = ", ".join(item.tags) if item.tags else ""
                body = item.content.strip()
                prefix = f"[{item.topic}]"
                if tags:
                    prefix = f"{prefix} ({tags})"
                lines.append(f"{prefix} {body}")
            messages.append(
                {"role": "system", "content": f"{header}\n" + "\n".join(lines)}
            )

        if history:
            for record in history:
                payload = self._history_record_to_message(record)
                if payload is not None:
                    messages.append(payload)

        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _history_record_to_message(record: ChatMessageRecord) -> dict[str, Any] | None:
        if record.role == "system":
            return None
        payload: dict[str, Any] = {"role": record.role}
        if record.role == "assistant":
            payload["content"] = record.content or None
            if record.tool_calls:
                payload["tool_calls"] = record.tool_calls
            return payload
        if record.role == "tool":
            if not record.tool_call_id:
                return None
            payload["content"] = record.content or ""
            payload["tool_call_id"] = record.tool_call_id
            if record.name:
                payload["name"] = record.name
            return payload
        payload["content"] = record.content
        return payload

    @staticmethod
    def _render_profile(profile: ChatProfile) -> str:
        parts: list[str] = []
        if profile.display_name:
            parts.append(f"称呼: {profile.display_name}")
        if profile.preferences:
            try:
                dumped = json.dumps(profile.preferences, ensure_ascii=False)
            except Exception:
                dumped = str(profile.preferences)
            parts.append(f"偏好: {dumped}")
        if profile.notes.strip():
            parts.append(f"备注: {profile.notes.strip()}")
        return "\n".join(parts)

    @staticmethod
    def _trim_to_budget(
        messages: list[dict[str, Any]],
        budget: int,
    ) -> list[dict[str, Any]]:
        if not messages:
            return messages
        system_messages: list[dict[str, Any]] = []
        conversation: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_messages.append(msg)
            else:
                conversation.append(msg)

        total = sum(len(str(m.get("content") or "")) for m in system_messages)
        total += sum(len(str(m.get("content") or "")) for m in conversation)
        while total > budget and len(conversation) > 1:
            removed = conversation.pop(0)
            total -= len(str(removed.get("content") or ""))
        return [*system_messages, *conversation]


def extra_messages_for_history(history: list[ChatMessageRecord]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for record in history:
        payload = ChatContextBuilder._history_record_to_message(record)
        if payload is not None:
            payloads.append(payload)
    return payloads

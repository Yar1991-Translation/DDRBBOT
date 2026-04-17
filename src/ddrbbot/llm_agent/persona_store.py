from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..copybook import copy_get, copy_text
from ..database import SQLiteRepository
from ..models import ChatPersona, ChatSession, CustomPersonaPayload

logger = logging.getLogger(__name__)

DEFAULT_PERSONA_KEY = "default"


@dataclass(slots=True)
class ActivePersona:
    key: str
    label: str
    description: str
    system_prompt: str
    allow_tools: bool
    tone: str | None
    is_custom: bool

    @classmethod
    def from_persona(cls, persona: ChatPersona) -> "ActivePersona":
        return cls(
            key=persona.persona_key,
            label=persona.label,
            description=persona.description,
            system_prompt=persona.system_prompt,
            allow_tools=persona.allow_tools,
            tone=persona.tone,
            is_custom=not persona.is_builtin,
        )

    @classmethod
    def from_custom(cls, payload: CustomPersonaPayload) -> "ActivePersona":
        return cls(
            key="custom",
            label=payload.label.strip() or "自定义角色",
            description=payload.description.strip(),
            system_prompt=payload.system_prompt.strip(),
            allow_tools=bool(payload.allow_tools),
            tone=payload.tone,
            is_custom=True,
        )


class PersonaStore:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def seed_defaults(self) -> None:
        raw = copy_get("personas.defaults", [])
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            persona_key = str(item.get("persona_key") or "").strip()
            system_prompt = str(item.get("system_prompt") or "").strip()
            if not persona_key or not system_prompt:
                continue
            persona = ChatPersona(
                persona_key=persona_key,
                label=str(item.get("label") or persona_key),
                description=str(item.get("description") or ""),
                system_prompt=system_prompt,
                is_builtin=True,
                allow_tools=bool(item.get("allow_tools", True)),
                tone=(str(item.get("tone")) if item.get("tone") else None),
            )
            try:
                self.repository.upsert_chat_persona(persona)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to seed persona %s: %s", persona_key, exc)

    def list_personas(self) -> list[ChatPersona]:
        return self.repository.list_chat_personas()

    def get_persona(self, persona_id_or_key: str) -> ChatPersona | None:
        return self.repository.get_chat_persona(persona_id_or_key)

    def resolve_active(
        self,
        session: ChatSession | None,
        *,
        override_persona_id: str | None = None,
        override_custom: CustomPersonaPayload | None = None,
    ) -> ActivePersona:
        if override_custom and override_custom.system_prompt.strip():
            return ActivePersona.from_custom(override_custom)
        if override_persona_id:
            persona = self.repository.get_chat_persona(override_persona_id)
            if persona:
                return ActivePersona.from_persona(persona)
        if session is not None:
            if session.custom_persona and session.custom_persona.system_prompt.strip():
                return ActivePersona.from_custom(session.custom_persona)
            if session.persona_id:
                persona = self.repository.get_chat_persona(session.persona_id)
                if persona:
                    return ActivePersona.from_persona(persona)
        default_persona = self.repository.get_chat_persona(DEFAULT_PERSONA_KEY)
        if default_persona:
            return ActivePersona.from_persona(default_persona)
        return ActivePersona(
            key=DEFAULT_PERSONA_KEY,
            label=copy_text("chat_commands.persona_active_default", "默认助手"),
            description="",
            system_prompt=copy_text(
                "llm_agent.chat_system_prompt",
                "你是 DDRBBOT 的默认助手。",
            ),
            allow_tools=True,
            tone=None,
            is_custom=False,
        )

    def update_session_persona(
        self,
        session: ChatSession,
        *,
        persona_id_or_key: str | None = None,
        custom: CustomPersonaPayload | None = None,
        reset: bool = False,
    ) -> ChatSession:
        if reset:
            self.repository.update_chat_session(
                session.id,
                persona_id=None,
                custom_persona=None,
            )
            session.persona_id = None
            session.custom_persona = None
            return session
        if custom is not None:
            self.repository.update_chat_session(
                session.id,
                persona_id=None,
                custom_persona=custom.model_dump(),
            )
            session.persona_id = None
            session.custom_persona = custom
            return session
        if persona_id_or_key is not None:
            persona = self.repository.get_chat_persona(persona_id_or_key)
            if persona is None:
                raise ValueError(f"persona_not_found: {persona_id_or_key}")
            self.repository.update_chat_session(
                session.id,
                persona_id=persona.id,
                custom_persona=None,
            )
            session.persona_id = persona.id
            session.custom_persona = None
        return session

    def compose_system_prompt(self, persona: ActivePersona) -> str:
        base = persona.system_prompt.strip()
        suffix = copy_text("llm_agent.persona_safety_suffix", "")
        if suffix:
            return f"{base}\n{suffix}".strip()
        return base


def session_key_for_event(
    *,
    origin: str,
    group_id: str | None,
    user_id: str | None,
    explicit_session_id: str | None = None,
) -> str:
    if explicit_session_id:
        return f"explicit:{explicit_session_id}"
    if group_id:
        target_user = user_id or "anon"
        return f"{origin}:group:{group_id}:user:{target_user}"
    if user_id:
        return f"{origin}:private:{user_id}"
    return f"{origin}:anon"


def session_scope(*, group_id: str | None) -> str:
    return "qq_group" if group_id else "qq_private"


def profile_scope_for(*, group_id: str | None) -> str:
    return "qq_group" if group_id else "qq_private"


def coerce_custom_persona(value: Any) -> CustomPersonaPayload | None:
    if value is None:
        return None
    if isinstance(value, CustomPersonaPayload):
        return value
    if isinstance(value, dict):
        try:
            return CustomPersonaPayload.model_validate(value)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("custom_persona payload invalid: %s", exc)
            return None
    return None

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..database import SQLiteRepository
from ..models import (
    ChatMessageRecord,
    ChatSession,
    CustomPersonaPayload,
)
from ..utils import utc_now
from .agent import AgentContext, AgentRunResult, LLMAgent
from .context_builder import BuiltContext, ChatContextBuilder, ContextBuildConfig
from .persona_store import (
    PersonaStore,
    profile_scope_for,
    session_key_for_event,
    session_scope,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatTurnRequest:
    origin: str
    user_message: str
    group_id: str | None = None
    user_id: str | None = None
    explicit_session_id: str | None = None
    override_persona_id: str | None = None
    override_custom_persona: CustomPersonaPayload | None = None
    history_limit: int | None = None
    include_knowledge: bool | None = None
    reset_session: bool = False
    agent_context: AgentContext | None = None


@dataclass(slots=True)
class ChatTurnResult:
    session: ChatSession
    built_context: BuiltContext
    run_result: AgentRunResult
    appended_messages: list[ChatMessageRecord] = field(default_factory=list)


class ChatService:
    def __init__(
        self,
        *,
        repository: SQLiteRepository,
        llm_agent: LLMAgent,
        persona_store: PersonaStore,
        context_builder: ChatContextBuilder | None = None,
        trim_keep_latest: int = 60,
    ) -> None:
        self.repository = repository
        self.llm_agent = llm_agent
        self.persona_store = persona_store
        self.context_builder = context_builder or ChatContextBuilder(
            repository=repository,
            persona_store=persona_store,
        )
        self.trim_keep_latest = trim_keep_latest

    def ensure_session(
        self,
        *,
        origin: str,
        group_id: str | None,
        user_id: str | None,
        explicit_session_id: str | None = None,
    ) -> ChatSession:
        session_key = session_key_for_event(
            origin=origin,
            group_id=group_id,
            user_id=user_id,
            explicit_session_id=explicit_session_id,
        )
        scope = session_scope(group_id=group_id)
        return self.repository.get_or_create_chat_session(
            session_key=session_key,
            origin=origin,
            scope=scope,
            group_id=group_id,
            user_id=user_id,
        )

    async def run_turn(self, request: ChatTurnRequest) -> ChatTurnResult:
        session = self.ensure_session(
            origin=request.origin,
            group_id=request.group_id,
            user_id=request.user_id,
            explicit_session_id=request.explicit_session_id,
        )

        if request.reset_session:
            self.repository.clear_chat_messages(session.id)
            self.repository.update_chat_session(
                session.id,
                summary="",
                touch_summary=True,
            )
            session = self.repository.get_chat_session(session.id) or session

        built = self.context_builder.build(
            session=session,
            user_message=request.user_message,
            profile_scope=profile_scope_for(group_id=request.group_id),
            profile_user_id=request.user_id,
            override_persona_id=request.override_persona_id,
            override_custom_persona=request.override_custom_persona,
            override_history_limit=request.history_limit,
            include_knowledge=request.include_knowledge,
        )

        agent_context = request.agent_context or AgentContext(origin=request.origin)

        user_record = ChatMessageRecord(
            session_id=session.id,
            role="user",
            content=request.user_message,
            created_at=utc_now(),
        )
        self.repository.append_chat_message(user_record)

        baseline = len(built.messages)
        run_result = await self.llm_agent.run(agent_context, built.messages)

        new_messages = run_result.messages[baseline:]
        appended = [user_record]
        appended.extend(
            self._persist_agent_output(
                session_id=session.id,
                messages=new_messages,
            )
        )

        self.repository.update_chat_session(
            session.id,
            last_message_at=utc_now(),
        )

        total = self.repository.count_chat_messages(session.id)
        if total > self.trim_keep_latest:
            self.repository.trim_chat_messages(
                session.id,
                keep_latest=self.trim_keep_latest,
            )

        return ChatTurnResult(
            session=session,
            built_context=built,
            run_result=run_result,
            appended_messages=appended,
        )

    def _persist_agent_output(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> list[ChatMessageRecord]:
        records: list[ChatMessageRecord] = []
        for msg in messages:
            role = msg.get("role")
            if role == "assistant":
                record = ChatMessageRecord(
                    session_id=session_id,
                    role="assistant",
                    content=str(msg.get("content") or "") if msg.get("content") is not None else "",
                    tool_calls=list(msg.get("tool_calls")) if msg.get("tool_calls") else None,
                    created_at=utc_now(),
                )
                records.append(record)
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id")
                if not tool_call_id:
                    continue
                record = ChatMessageRecord(
                    session_id=session_id,
                    role="tool",
                    content=str(msg.get("content") or ""),
                    name=str(msg.get("name") or "") or None,
                    tool_call_id=str(tool_call_id),
                    created_at=utc_now(),
                )
                records.append(record)
        if records:
            self.repository.append_chat_messages_batch(records)
        return records

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ddrbbot.config import Settings
from ddrbbot.database import SQLiteRepository
from ddrbbot.llm_agent.agent import AgentContext, LLMAgent
from ddrbbot.llm_agent.chat_service import ChatService, ChatTurnRequest
from ddrbbot.llm_agent.context_builder import ChatContextBuilder, ContextBuildConfig
from ddrbbot.llm_agent.persona_store import PersonaStore, session_key_for_event
from ddrbbot.llm_agent.tools import build_default_registry
from ddrbbot.models import (
    ChatKnowledgeItem,
    ChatMessageRecord,
    ChatPersona,
    ChatProfile,
    CustomPersonaPayload,
)
from ddrbbot.rendering import NewsCardRenderer


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_name="DDRBBOT",
        database_path=tmp_path / "chat.db",
        artifacts_dir=tmp_path / "artifacts",
        screenshot_enabled=False,
        worker_concurrency=1,
        queue_maxsize=10,
        auto_deliver_enabled=False,
        default_qq_group_id=None,
        qq_admin_user_ids=frozenset(),
        qq_admin_group_ids=frozenset(),
        qq_news_card_max_bytes=10 * 1024 * 1024,
        delivery_retry_delays_seconds=(0.0,),
        napcat_base_url="http://127.0.0.1:3000",
        napcat_access_token=None,
        napcat_timeout_seconds=10.0,
        napcat_ws_url=None,
        napcat_ws_reconnect_base_seconds=2.0,
        napcat_ws_reconnect_max_seconds=60.0,
        delivery_worker_poll_seconds=2.0,
        delivery_worker_enabled=False,
        delivery_dead_letter_max_attempts=6,
        delivery_alert_consecutive_failures=5,
        llm_base_url="http://fake-llm",
        llm_api_key=None,
        llm_model="fake-model",
        llm_timeout_seconds=30.0,
        llm_agent_enabled=True,
        llm_agent_max_tool_steps=4,
        llm_agent_schedule_interval_minutes=60.0,
        llm_agent_schedule_enabled=False,
        llm_agent_temperature=0.0,
        llm_agent_max_reply_chars=2000,
        rsshub_host_markers=frozenset({"rsshub"}),
        rsshub_extra_hosts=frozenset({"localhost"}),
        qq_image_fail_text_fallback_enabled=True,
        llm_agent_shell_enabled=False,
        llm_agent_shell_timeout_seconds=30.0,
        llm_agent_shell_output_limit=20000,
        llm_agent_shell_workdir=None,
        google_custom_search_api_key=None,
        google_custom_search_engine_id=None,
        bing_search_api_key=None,
    )


class _FakeBotAdapter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, route: str, text: str) -> str:
        self.sent.append((route, text))
        return f"msg-{len(self.sent)}"


class _FakePipeline:
    def __init__(self) -> None:
        self.queued: list[str] = []

    async def enqueue(self, event_id: str) -> None:
        self.queued.append(event_id)


def _build_env(tmp_path: Path) -> tuple[SQLiteRepository, LLMAgent, PersonaStore, ChatService]:
    settings = _settings(tmp_path)
    repository = SQLiteRepository(settings.database_path)
    repository.initialize()
    renderer = NewsCardRenderer(settings)
    registry = build_default_registry(
        settings=settings,
        repository=repository,
        renderer=renderer,
        pipeline=_FakePipeline(),
        bot_adapter=_FakeBotAdapter(),
    )
    agent = LLMAgent(settings=settings, tool_registry=registry)
    persona_store = PersonaStore(repository=repository)
    persona_store.seed_defaults()
    chat_service = ChatService(
        repository=repository,
        llm_agent=agent,
        persona_store=persona_store,
    )
    return repository, agent, persona_store, chat_service


def _patch_chat(agent: LLMAgent, scripted: list[dict]) -> list[list[dict]]:
    observed: list[list[dict]] = []
    iterator = iter(scripted)

    async def fake_chat(messages: list[dict], tools: list[dict]) -> dict:
        observed.append([dict(m) for m in messages])
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError("no more scripted chat completions") from exc

    agent._chat_once = fake_chat  # type: ignore[attr-defined]
    return observed


def test_persona_store_seeds_defaults(tmp_path: Path) -> None:
    repository, _, persona_store, _ = _build_env(tmp_path)
    personas = persona_store.list_personas()
    keys = {p.persona_key for p in personas}
    assert "default" in keys
    assert any(p.is_builtin for p in personas)


def test_session_key_stable(tmp_path: Path) -> None:
    key_a = session_key_for_event(origin="qq_chat", group_id=None, user_id="42")
    key_b = session_key_for_event(origin="qq_chat", group_id=None, user_id="42")
    key_group = session_key_for_event(origin="qq_chat", group_id="100", user_id="42")
    assert key_a == key_b
    assert key_a != key_group


def test_context_builder_assembles_all_sections(tmp_path: Path) -> None:
    repository, _, persona_store, chat_service = _build_env(tmp_path)
    session = chat_service.ensure_session(
        origin="qq_chat", group_id=None, user_id="u1"
    )
    repository.upsert_chat_profile(
        ChatProfile(scope="qq_private", user_id="u1", display_name="Alice")
    )
    repository.upsert_chat_knowledge_item(
        ChatKnowledgeItem(topic="ddrb_help", content="ddrb 是项目代号", tags=["ddrb"])
    )
    repository.append_chat_message(
        ChatMessageRecord(session_id=session.id, role="user", content="之前的问题")
    )
    repository.append_chat_message(
        ChatMessageRecord(session_id=session.id, role="assistant", content="之前的答案")
    )
    repository.update_chat_session(session.id, summary="此前聊过项目概况。")
    session = repository.get_chat_session(session.id)
    assert session is not None

    builder = ChatContextBuilder(repository=repository, persona_store=persona_store)
    built = builder.build(
        session=session,
        user_message="请介绍一下 ddrb",
        profile_scope="qq_private",
        profile_user_id="u1",
    )
    roles = [m["role"] for m in built.messages]
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert built.messages[-1]["content"] == "请介绍一下 ddrb"
    all_content = "\n".join(str(m.get("content") or "") for m in built.messages)
    assert "Alice" in all_content
    assert "滚动摘要" in all_content
    assert "ddrb 是项目代号" in all_content
    assert any(m.get("role") == "assistant" for m in built.messages)


def test_context_builder_trims_history_limit(tmp_path: Path) -> None:
    repository, _, persona_store, chat_service = _build_env(tmp_path)
    session = chat_service.ensure_session(
        origin="qq_chat", group_id=None, user_id="u2"
    )
    for idx in range(20):
        repository.append_chat_message(
            ChatMessageRecord(
                session_id=session.id,
                role="user" if idx % 2 == 0 else "assistant",
                content=f"msg-{idx}",
            )
        )
    session = repository.get_chat_session(session.id)
    assert session is not None

    builder = ChatContextBuilder(
        repository=repository,
        persona_store=persona_store,
        config=ContextBuildConfig(history_limit=4, include_knowledge=False),
    )
    built = builder.build(session=session, user_message="现在")
    history_msgs = [m for m in built.messages if m["role"] in {"user", "assistant"}]
    assert history_msgs[-1]["content"] == "现在"
    assert len(built.history) == 4


def test_context_builder_custom_persona_override(tmp_path: Path) -> None:
    repository, _, persona_store, chat_service = _build_env(tmp_path)
    session = chat_service.ensure_session(
        origin="qq_chat", group_id=None, user_id="u3"
    )
    builder = ChatContextBuilder(repository=repository, persona_store=persona_store)
    custom = CustomPersonaPayload(
        label="喵娘",
        description="可爱风",
        system_prompt="你是一只温柔的小猫娘。",
        allow_tools=True,
    )
    built = builder.build(
        session=session,
        user_message="hi",
        override_custom_persona=custom,
    )
    assert built.persona.is_custom
    assert "小猫娘" in built.messages[0]["content"]


def test_chat_service_persists_history_and_no_duplicates(tmp_path: Path) -> None:
    repository, agent, _, chat_service = _build_env(tmp_path)
    scripted = [{"role": "assistant", "content": "你好 Alice"}]
    observed = _patch_chat(agent, scripted)

    request = ChatTurnRequest(
        origin="qq_chat",
        user_message="第一条",
        group_id=None,
        user_id="u10",
        agent_context=AgentContext(origin="qq_chat", reply_target_type="private", reply_target_id="u10"),
    )
    turn = asyncio.run(chat_service.run_turn(request))
    assert turn.run_result.final_text == "你好 Alice"

    messages = repository.list_chat_messages(turn.session.id, limit=10)
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant"]
    assert messages[0].content == "第一条"
    assert messages[1].content == "你好 Alice"

    scripted_second = [{"role": "assistant", "content": "收到第二条"}]
    _patch_chat(agent, scripted_second)
    turn2 = asyncio.run(
        chat_service.run_turn(
            ChatTurnRequest(
                origin="qq_chat",
                user_message="第二条",
                group_id=None,
                user_id="u10",
            )
        )
    )
    assert turn2.session.id == turn.session.id
    messages2 = repository.list_chat_messages(turn.session.id, limit=10)
    assert [m.content for m in messages2] == [
        "第一条",
        "你好 Alice",
        "第二条",
        "收到第二条",
    ]
    first_call_payload = observed[0]
    history_user_msgs = [m for m in first_call_payload if m["role"] == "user"]
    assert len(history_user_msgs) == 1


def test_chat_service_reset_session_clears_history(tmp_path: Path) -> None:
    repository, agent, _, chat_service = _build_env(tmp_path)
    _patch_chat(agent, [{"role": "assistant", "content": "ok"}])
    asyncio.run(
        chat_service.run_turn(
            ChatTurnRequest(
                origin="qq_chat",
                user_message="old",
                group_id=None,
                user_id="u11",
            )
        )
    )
    _patch_chat(agent, [{"role": "assistant", "content": "fresh"}])
    turn = asyncio.run(
        chat_service.run_turn(
            ChatTurnRequest(
                origin="qq_chat",
                user_message="start over",
                group_id=None,
                user_id="u11",
                reset_session=True,
            )
        )
    )
    messages = repository.list_chat_messages(turn.session.id, limit=10)
    assert [m.content for m in messages] == ["start over", "fresh"]


def test_persona_switch_and_reset(tmp_path: Path) -> None:
    repository, _, persona_store, chat_service = _build_env(tmp_path)
    session = chat_service.ensure_session(
        origin="qq_chat", group_id=None, user_id="u20"
    )
    persona_store.update_session_persona(session, persona_id_or_key="assistant_pro")
    refreshed = repository.get_chat_session(session.id)
    assert refreshed is not None
    assert refreshed.persona_id is not None

    persona_store.update_session_persona(session, reset=True)
    cleared = repository.get_chat_session(session.id)
    assert cleared is not None
    assert cleared.persona_id is None
    assert cleared.custom_persona is None

    with pytest.raises(ValueError):
        persona_store.update_session_persona(session, persona_id_or_key="does_not_exist")


def test_persona_custom_payload_applied(tmp_path: Path) -> None:
    repository, _, persona_store, chat_service = _build_env(tmp_path)
    session = chat_service.ensure_session(
        origin="qq_chat", group_id=None, user_id="u21"
    )
    custom = CustomPersonaPayload(
        label="cat",
        description="",
        system_prompt="你是一只猫娘。",
        allow_tools=True,
    )
    persona_store.update_session_persona(session, custom=custom)
    refreshed = repository.get_chat_session(session.id)
    assert refreshed is not None
    assert refreshed.custom_persona is not None
    assert refreshed.custom_persona.system_prompt == "你是一只猫娘。"


def test_knowledge_search_matches_tokens(tmp_path: Path) -> None:
    repository, _, _, _ = _build_env(tmp_path)
    repository.upsert_chat_knowledge_item(
        ChatKnowledgeItem(topic="roblox_intro", content="Roblox 平台简介", tags=["roblox"])
    )
    repository.upsert_chat_knowledge_item(
        ChatKnowledgeItem(topic="doors_notes", content="DOORS 游戏要点", tags=["doors"])
    )
    hits = repository.search_chat_knowledge_items("Roblox 更新")
    topics = {item.topic for item in hits}
    assert "roblox_intro" in topics
    empty_hits = repository.search_chat_knowledge_items("   ")
    assert empty_hits == []


def test_trim_chat_messages_keeps_latest(tmp_path: Path) -> None:
    repository, _, _, chat_service = _build_env(tmp_path)
    session = chat_service.ensure_session(
        origin="qq_chat", group_id=None, user_id="u30"
    )
    for idx in range(10):
        repository.append_chat_message(
            ChatMessageRecord(
                session_id=session.id,
                role="user" if idx % 2 == 0 else "assistant",
                content=f"m{idx}",
            )
        )
    removed = repository.trim_chat_messages(session.id, keep_latest=3)
    assert removed == 7
    remaining = repository.list_chat_messages(session.id, limit=10)
    assert [m.content for m in remaining] == ["m7", "m8", "m9"]


def test_upsert_chat_persona_respects_builtin_flag(tmp_path: Path) -> None:
    repository, _, _, _ = _build_env(tmp_path)
    persona = ChatPersona(
        persona_key="custom_one",
        label="Custom",
        system_prompt="hi",
        is_builtin=False,
    )
    saved = repository.upsert_chat_persona(persona)
    assert saved.is_builtin is False
    removed = repository.delete_chat_persona("custom_one")
    assert removed is True
    # 内置角色不应被删除
    removed_builtin = repository.delete_chat_persona("default")
    assert removed_builtin is False

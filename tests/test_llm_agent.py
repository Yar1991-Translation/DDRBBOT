from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ddrbbot.config import Settings
from ddrbbot.database import SQLiteRepository
from ddrbbot.llm_agent.agent import AgentContext, AgentRunResult, LLMAgent
from ddrbbot.llm_agent.tools import build_default_registry
from ddrbbot.models import RawEvent
from ddrbbot.rendering import NewsCardRenderer
from ddrbbot.utils import utc_now


def _settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    return Settings(
        app_name="DDRBBOT",
        database_path=tmp_path / "agent.db",
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
        llm_base_url="http://fake-llm" if enabled else None,
        llm_api_key=None,
        llm_model="fake-model" if enabled else None,
        llm_timeout_seconds=30.0,
        llm_agent_enabled=enabled,
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


def _build_agent(
    tmp_path: Path,
    *,
    enabled: bool = True,
) -> tuple[LLMAgent, SQLiteRepository, _FakeBotAdapter, NewsCardRenderer]:
    settings = _settings(tmp_path, enabled=enabled)
    repository = SQLiteRepository(settings.database_path)
    repository.initialize()
    renderer = NewsCardRenderer(settings)
    bot_adapter = _FakeBotAdapter()
    pipeline = _FakePipeline()
    registry = build_default_registry(
        settings=settings,
        repository=repository,
        renderer=renderer,
        pipeline=pipeline,
        bot_adapter=bot_adapter,
    )
    agent = LLMAgent(settings=settings, tool_registry=registry)
    return agent, repository, bot_adapter, renderer


def _patch_agent_chat(agent: LLMAgent, scripted: list[dict]) -> None:
    iterator = iter(scripted)

    async def fake_chat(messages: list[dict], tools: list[dict]) -> dict:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise AssertionError("no more scripted chat completions") from exc

    agent._chat_once = fake_chat  # type: ignore[attr-defined]


def test_agent_disabled_returns_disabled_reply(tmp_path: Path) -> None:
    agent, _, _, _ = _build_agent(tmp_path, enabled=False)
    result = asyncio.run(agent.run(AgentContext(), "hi"))
    assert result.stopped_reason == "error"
    assert result.error == "agent_disabled"
    assert result.final_text


def test_agent_tool_call_roundtrip(tmp_path: Path) -> None:
    agent, repository, _, _ = _build_agent(tmp_path)
    scripted = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "list_review_items",
                        "arguments": json.dumps({"status": "open", "limit": 3}),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "no items"},
    ]
    _patch_agent_chat(agent, scripted)
    result = asyncio.run(
        agent.run(
            AgentContext(origin="api"),
            "Check review queue",
        )
    )
    assert isinstance(result, AgentRunResult)
    assert result.stopped_reason == "final"
    assert result.final_text == "no items"
    assert result.tool_steps == 1
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["ok"] is True
    assert payload["data"] == []


def test_agent_send_reply_text_blocked_outside_qq_chat(tmp_path: Path) -> None:
    agent, _, bot_adapter, _ = _build_agent(tmp_path)
    scripted = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-send",
                    "type": "function",
                    "function": {
                        "name": "send_reply_text",
                        "arguments": json.dumps({"text": "hi"}),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "done"},
    ]
    _patch_agent_chat(agent, scripted)
    result = asyncio.run(agent.run(AgentContext(origin="scheduler"), "tick"))
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert tool_msgs
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["ok"] is False
    assert "qq_chat" in payload["error"]
    assert bot_adapter.sent == []


def test_agent_send_reply_text_allowed_in_qq_chat(tmp_path: Path) -> None:
    agent, _, bot_adapter, _ = _build_agent(tmp_path)
    scripted = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-send",
                    "type": "function",
                    "function": {
                        "name": "send_reply_text",
                        "arguments": json.dumps({"text": "hello back"}),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "ok"},
    ]
    _patch_agent_chat(agent, scripted)
    ctx = AgentContext(
        origin="qq_chat",
        reply_target_type="group",
        reply_target_id="123456",
        is_private=False,
        at_self=True,
    )
    asyncio.run(agent.run(ctx, "hi"))
    assert bot_adapter.sent == [("group:123456", "hello back")]


def test_render_card_for_review_enqueues_review_pending(tmp_path: Path) -> None:
    agent, repository, _, _ = _build_agent(tmp_path)
    scripted = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-render",
                    "type": "function",
                    "function": {
                        "name": "render_card_for_review",
                        "arguments": json.dumps(
                            {
                                "title": "Agent draft",
                                "summary": "Short summary",
                                "highlights": ["A", "B"],
                                "category": "announcement",
                                "preset_key": "roblox",
                                "orientation": "vertical",
                                "source_name": "AgentTest",
                            }
                        ),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "queued for review"},
    ]
    _patch_agent_chat(agent, scripted)
    result = asyncio.run(agent.run(AgentContext(origin="api"), "make one"))
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["ok"] is True
    processed_event_id = payload["data"]["processed_event_id"]
    processed = repository.get_processed_event(processed_event_id)
    assert processed is not None
    assert processed.delivery_status == "review_pending"

from __future__ import annotations

import asyncio
import logging

from ..config import Settings
from ..copybook import copy_text
from .agent import AgentContext, LLMAgent

logger = logging.getLogger(__name__)


class AgentScheduler:
    def __init__(self, *, settings: Settings, agent: LLMAgent) -> None:
        self.settings = settings
        self.agent = agent
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return (
            self.settings.llm_agent_enabled
            and self.settings.llm_agent_schedule_enabled
            and self.settings.llm_agent_schedule_interval_minutes > 0
        )

    async def start(self) -> None:
        if self._task is not None or not self.enabled:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="llm-agent-scheduler")

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _loop(self) -> None:
        interval_seconds = float(self.settings.llm_agent_schedule_interval_minutes) * 60.0
        while not self._stopped.is_set():
            try:
                await self._run_scout_tick()
            except Exception as exc:  # pragma: no cover - safety
                logger.exception("Agent scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval_seconds)
                break
            except asyncio.TimeoutError:
                continue

    async def _run_scout_tick(self) -> None:
        user_msg = copy_text(
            "llm_agent.scheduler_user_prompt",
            (
                "Scout tick: review list_sources, pick at most one interesting source to "
                "collect, and if a new item appears, render a card for review. Otherwise "
                "reply with a short status line."
            ),
        )
        context = AgentContext(origin="scheduler")
        logger.info("Agent scheduler tick starting")
        result = await self.agent.run(context, user_msg)
        logger.info(
            "Agent scheduler tick done: reason=%s tool_calls=%s",
            result.stopped_reason,
            result.tool_calls_total,
        )

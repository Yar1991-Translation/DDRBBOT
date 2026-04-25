from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings
from ..copybook import copy_text

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    origin: str = "api"  # qq_chat | scheduler | api
    reply_target_type: str | None = None  # "group" | "private"
    reply_target_id: str | None = None
    initiator_user_id: str | None = None
    is_private: bool = False
    at_self: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def can_send_reply(self) -> bool:
        return self.origin == "qq_chat" and bool(self.reply_target_type and self.reply_target_id)


@dataclass
class AgentRunResult:
    final_text: str | None
    messages: list[dict[str, Any]]
    tool_steps: int
    stopped_reason: str  # "final" | "max_steps" | "error"
    error: str | None = None

    @property
    def tool_calls_total(self) -> int:
        return self.tool_steps


class LLMAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        tool_registry: Any = None,
        registry: Any = None,
        provider_store: Any = None,
    ) -> None:
        self.settings = settings
        self._provider_store = provider_store
        chosen = tool_registry if tool_registry is not None else registry
        if chosen is None:
            raise ValueError("LLMAgent requires tool_registry (or registry=) argument")
        self.tool_registry = chosen

    def _resolve_llm_config(self) -> tuple[str | None, str | None, str | None]:
        """Returns (base_url, model, api_key) from active provider or settings fallback."""
        if self._provider_store is not None:
            active = self._provider_store.get_active()
            if active and active.base_url and active.model:
                return active.base_url, active.model, (active.api_key or None)
        return (
            self.settings.llm_base_url,
            self.settings.llm_model,
            self.settings.llm_api_key,
        )

    @property
    def enabled(self) -> bool:
        if not self.settings.llm_agent_enabled:
            return False
        base_url, model, _ = self._resolve_llm_config()
        return bool(base_url and model)

    async def run(
        self,
        context: AgentContext,
        prompt_or_messages: "str | list[dict[str, Any]]",
        *,
        system_prompt: str | None = None,
        extra_messages: list[dict[str, Any]] | None = None,
    ) -> AgentRunResult:
        if not self.enabled:
            return AgentRunResult(
                final_text=copy_text(
                    "llm_agent.disabled",
                    "LLM Agent 未启用。请设置 LLM_AGENT_ENABLED=true 并配置 LLM_BASE_URL/LLM_MODEL。",
                ),
                messages=[],
                tool_steps=0,
                stopped_reason="error",
                error="agent_disabled",
            )

        if isinstance(prompt_or_messages, str):
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt or self._default_system_prompt(context)}
            ]
            if extra_messages:
                messages.extend(extra_messages)
            messages.append({"role": "user", "content": prompt_or_messages})
        else:
            messages = list(prompt_or_messages)

        tools_payload = self.tool_registry.openai_tool_specs()
        total_tool_calls = 0

        for step in range(self.settings.llm_agent_max_tool_steps):
            try:
                assistant_message = await self._chat_once(messages, tools_payload)
            except Exception as exc:  # pragma: no cover - network dependent
                logger.exception("LLM agent chat failed: %s", exc)
                return AgentRunResult(
                    final_text=copy_text(
                        "llm_agent.run_failed",
                        "LLM Agent 调用失败，请稍后再试。",
                    ),
                    messages=messages,
                    tool_steps=total_tool_calls,
                    stopped_reason="error",
                    error=str(exc),
                )
            messages.append(assistant_message)
            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                return AgentRunResult(
                    final_text=str(assistant_message.get("content") or "").strip() or None,
                    messages=messages,
                    tool_steps=total_tool_calls,
                    stopped_reason="final",
                )
            for call in tool_calls:
                total_tool_calls += 1
                tool_result = await self._execute_tool_call(context, call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": (call.get("function") or {}).get("name", ""),
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )

        return AgentRunResult(
            final_text=copy_text(
                "llm_agent.tool_steps_exceeded",
                "（已达到工具调用上限，终止循环。）",
            ),
            messages=messages,
            tool_steps=total_tool_calls,
            stopped_reason="max_steps",
        )

    async def _chat_once(
        self,
        messages: list[dict[str, Any]],
        tools_payload: list[dict[str, Any]],
    ) -> dict[str, Any]:
        base_url, model, api_key = self._resolve_llm_config()
        body: dict[str, Any] = {
            "model": model or self.settings.llm_model,
            "temperature": self.settings.llm_agent_temperature,
            "messages": messages,
        }
        if tools_payload:
            body["tools"] = tools_payload
            body["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            response = await client.post(
                f"{str(base_url or self.settings.llm_base_url).rstrip('/')}/chat/completions",
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM returned empty choices")
        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        normalized: dict[str, Any] = {
            "role": "assistant",
            "content": msg.get("content"),
        }
        if tool_calls:
            normalized["tool_calls"] = tool_calls
        return normalized

    async def _execute_tool_call(
        self,
        context: AgentContext,
        call: dict[str, Any],
    ) -> dict[str, Any]:
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"invalid_arguments: {exc}"}
        handler = self.tool_registry.get(name)
        if handler is None:
            return {"ok": False, "error": f"unknown_tool: {name}"}
        try:
            return await handler(context, arguments)
        except Exception as exc:  # pragma: no cover - handler errors
            logger.exception("Tool %s crashed: %s", name, exc)
            return {"ok": False, "error": f"tool_crashed: {exc}"}

    def _default_system_prompt(self, context: AgentContext) -> str:
        if context.origin == "scheduler":
            return copy_text(
                "llm_agent.scheduler_system_prompt",
                "你是 DDRBBOT 的后台巡查助手。请最多做一轮必要的采集与渲染，不要调用 send_reply_text。",
            )
        if context.origin == "api":
            return copy_text(
                "llm_agent.api_system_prompt",
                "你是 DDRBBOT 的调试 Agent。当前没有 QQ 会话上下文，不要调用 send_reply_text。",
            )
        return copy_text(
            "llm_agent.chat_system_prompt",
            "你是 DDRBBOT 的 QQ 助手。最终通过 send_reply_text 向当前会话回复。",
        )

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from ..config import Settings
from ..database import SQLiteRepository
from ..delivery import QQDeliveryService
from ..models import (
    MediaAsset,
    ProcessedEvent,
    QQSendNewsCardRequest,
    RawEvent,
    RenderPreviewRequest,
    RSSHubCollectRequest,
)
from ..rendering import NewsCardRenderer
from ..review_presenter import review_statuses
from ..rss import RSSCollector, collect_and_enqueue_rss
from ..rsshub import validate_rsshub_feed_url
from ..utils import make_external_id, utc_now
from .agent import AgentContext

logger = logging.getLogger(__name__)

ToolHandler = Callable[[AgentContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolHandler | None:
        spec = self._tools.get(name)
        return spec.handler if spec else None

    def openai_tool_specs(self) -> list[dict[str, Any]]:
        return [spec.to_openai() for spec in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())


def build_default_registry(
    *,
    settings: Settings,
    repository: SQLiteRepository,
    renderer: NewsCardRenderer,
    pipeline: Any = None,
    bot_adapter: Any = None,
    bot_adapter_sender: Callable[[str, str], Awaitable[str]] | None = None,
    pipeline_enqueue: Callable[[str], Awaitable[None]] | None = None,
    delivery_service: QQDeliveryService | None = None,
) -> ToolRegistry:
    """Build the default tool set.

    Prefer passing the live `pipeline` and `bot_adapter` service instances.
    `bot_adapter_sender` / `pipeline_enqueue` callables are supported for tests.
    """
    if pipeline_enqueue is None and pipeline is not None:
        pipeline_enqueue = pipeline.enqueue
    if bot_adapter_sender is None and bot_adapter is not None:
        bot_adapter_sender = bot_adapter.send_text
    if pipeline_enqueue is None or bot_adapter_sender is None:
        raise ValueError(
            "build_default_registry requires pipeline and bot_adapter (or explicit senders)."
        )

    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="list_sources",
            description="List registered source feeds from the DDRBBOT repository.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}
                },
                "additionalProperties": False,
            },
            handler=_tool_list_sources(repository),
        )
    )
    registry.register(
        ToolSpec(
            name="list_review_items",
            description=(
                "List processed events in the review queue. "
                "status: open | failed | sent | rejected | all."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["open", "failed", "sent", "rejected", "all"],
                        "default": "open",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "additionalProperties": False,
            },
            handler=_tool_list_review_items(repository),
        )
    )
    registry.register(
        ToolSpec(
            name="get_processed_event",
            description="Fetch a single processed event (title/summary/highlights/media).",
            parameters={
                "type": "object",
                "properties": {
                    "processed_event_id": {"type": "string"},
                },
                "required": ["processed_event_id"],
                "additionalProperties": False,
            },
            handler=_tool_get_processed_event(repository),
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_url",
            description=(
                "HTTP GET a public URL and return up to max_bytes of the decoded text body. "
                "Use this to skim an article or API before summarizing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full http(s) URL"},
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1024,
                        "maximum": 200000,
                        "default": 65536,
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 60,
                        "default": 15,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_tool_fetch_url,
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_x_tweets",
            description=(
                "Fetch recent tweets from an X (Twitter) user. "
                "Provide the X user ID (screen name, e.g. 'Roblox' or 'DoorGame'). "
                "Fetches via Jina AI reader over x.com."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "x_user_id": {
                        "type": "string",
                        "description": "X/Twitter screen name (without @), e.g. 'Roblox'",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1024,
                        "maximum": 200000,
                        "default": 65536,
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 60,
                        "default": 15,
                    },
                },
                "required": ["x_user_id"],
                "additionalProperties": False,
            },
            handler=_tool_fetch_x_tweets,
        )
    )
    registry.register(
        ToolSpec(
            name="web_search",
            description=(
                "Search the web using Google Custom Search. "
                "Returns titles, links, and snippets for each result. "
                "Use this to find up-to-date information, news, or facts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "num": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                        "description": "Number of results (1-10)",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 15,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=_tool_web_search(
                settings.google_custom_search_api_key,
                settings.google_custom_search_engine_id,
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="bing_search",
            description=(
                "Search the web using Bing Web Search API. "
                "Returns titles, links, and snippets for each result."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                        "description": "Number of results (1-10)",
                    },
                    "market": {
                        "type": "string",
                        "default": "zh-CN",
                        "description": "Market code (e.g. zh-CN, en-US)",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 15,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=_tool_bing_search(settings.bing_search_api_key),
        )
    )
    registry.register(
        ToolSpec(
            name="collect_rss",
            description=(
                "Pull a generic RSS/Atom feed, insert new raw events and enqueue them "
                "for the analyzer pipeline."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_name": {"type": "string"},
                    "feed_url": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["source_name", "feed_url"],
                "additionalProperties": False,
            },
            handler=_tool_collect_rss(repository, pipeline_enqueue, rsshub=False, settings=settings),
        )
    )
    registry.register(
        ToolSpec(
            name="collect_rsshub",
            description=(
                "Pull an RSSHub feed (host must match RSSHUB_HOST_MARKERS / RSSHUB_EXTRA_HOSTS). "
                "Used for X / other social sources exposed via RSSHub."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_name": {"type": "string"},
                    "feed_url": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["source_name", "feed_url"],
                "additionalProperties": False,
            },
            handler=_tool_collect_rss(repository, pipeline_enqueue, rsshub=True, settings=settings),
        )
    )
    registry.register(
        ToolSpec(
            name="register_source",
            description="Upsert a source entry in the sources registry.",
            parameters={
                "type": "object",
                "properties": {
                    "source_type": {"type": "string", "default": "rss"},
                    "name": {"type": "string"},
                    "feed_url": {"type": "string"},
                    "credibility_level": {
                        "type": "string",
                        "enum": ["official", "community", "unverified"],
                        "default": "unverified",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=_tool_register_source(repository),
        )
    )
    registry.register(
        ToolSpec(
            name="render_card_for_review",
            description=(
                "Render a news card and place it into the review queue "
                "(delivery_status=review_pending). Returns processed_event_id, html_path "
                "and image_path. The card will NOT be sent automatically."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "highlights": {"type": "array", "items": {"type": "string"}},
                    "category": {
                        "type": "string",
                        "enum": ["announcement", "teaser", "patch", "maintenance"],
                        "default": "announcement",
                    },
                    "game": {"type": "string"},
                    "preset_key": {
                        "type": "string",
                        "enum": ["roblox", "doors", "forsaken", "pressure"],
                        "default": "roblox",
                    },
                    "orientation": {
                        "type": "string",
                        "enum": ["vertical", "horizontal"],
                        "default": "vertical",
                    },
                    "theme": {"type": "string", "enum": ["light", "dark"], "default": "light"},
                    "source_name": {"type": "string"},
                    "channel_name": {"type": "string"},
                    "author": {"type": "string"},
                    "source_credibility": {
                        "type": "string",
                        "enum": ["official", "community", "unverified"],
                        "default": "unverified",
                    },
                    "need_translation": {"type": "boolean", "default": False},
                    "media": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "description": {"type": "string"},
                                "reference_url": {"type": "string"},
                                "reference_label": {"type": "string"},
                            },
                            "required": ["url"],
                            "additionalProperties": True,
                        },
                    },
                    "discovered_sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "summary", "highlights"],
                "additionalProperties": False,
            },
            handler=_tool_render_card_for_review(repository, renderer),
        )
    )
    registry.register(
        ToolSpec(
            name="call_ddrbbot_api",
            description=(
                "Call any DDRBBOT HTTP API endpoint on the local service. "
                "Use for endpoints not exposed as a dedicated tool "
                "(review actions, dead-letter, delivery retry, preview PNG, etc.). "
                "method is GET or POST; path starts with /api/... ."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST"], "default": "GET"},
                    "path": {
                        "type": "string",
                        "description": "Path starting with /api/, e.g. /api/review/items",
                    },
                    "query": {
                        "type": "object",
                        "description": "Optional query params (string values only).",
                        "additionalProperties": {"type": "string"},
                    },
                    "payload": {
                        "type": "object",
                        "description": "Optional JSON body for POST.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Override base URL; defaults to http://127.0.0.1:8000.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=_tool_call_ddrbbot_api(settings),
        )
    )
    registry.register(
        ToolSpec(
            name="send_reply_text",
            description=(
                "Send a plain-text message via NapCat. "
                "Without target_type/target_id it replies to the current QQ chat. "
                "With them it can send to any group or private user."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_type": {"type": "string", "enum": ["group", "private"]},
                    "target_id": {"type": "string"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=_tool_send_reply_text(bot_adapter_sender),
        )
    )
    if settings.llm_agent_shell_enabled:
        registry.register(
            ToolSpec(
                name="run_shell",
                description=(
                    "Execute a shell command on the DDRBBOT host and return stdout/stderr/exit_code. "
                    "Runs via /bin/sh -c (or cmd.exe on Windows). "
                    "High privilege: use for maintenance / diagnostics only. "
                    "Output and runtime are bounded by server settings."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The raw shell command line."},
                        "timeout_seconds": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 600,
                            "description": "Optional override of default timeout (seconds).",
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Optional working directory for the command.",
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
                handler=_tool_run_shell(settings),
            )
        )
    if delivery_service is not None:
        registry.register(
            ToolSpec(
                name="send_news_card_now",
                description=(
                    "Enqueue an already-rendered news card for immediate delivery via NapCat. "
                    "Requires processed_event_id (from render_card_for_review) OR explicit image_path. "
                    "Bypasses the human review step."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "processed_event_id": {"type": "string"},
                        "image_path": {"type": "string"},
                        "target_type": {"type": "string", "enum": ["group", "private"], "default": "group"},
                        "target_id": {"type": "string"},
                        "caption": {"type": "string"},
                    },
                    "required": ["target_id"],
                    "additionalProperties": False,
                },
                handler=_tool_send_news_card_now(repository, delivery_service),
            )
        )
    return registry


# ---------- handler implementations ----------


def _tool_list_sources(repository: SQLiteRepository) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = int(arguments.get("limit") or 50)
        rows = repository.list_sources(limit=max(1, min(limit, 200)))
        return {
            "ok": True,
            "data": [dict(r) if not isinstance(r, dict) else r for r in rows],
        }

    return handler


def _tool_list_review_items(repository: SQLiteRepository) -> ToolHandler:
    base = review_statuses("open")
    status_map = {
        "open": base + ("queued",) if base else ("queued",),
        "failed": ("failed",),
        "sent": ("sent",),
        "rejected": ("rejected",),
        "all": None,
    }

    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        status = str(arguments.get("status") or "open")
        limit = int(arguments.get("limit") or 10)
        statuses = status_map.get(status, status_map["open"])
        items = repository.list_processed_events(
            delivery_statuses=statuses,
            limit=max(1, min(limit, 50)),
        )
        return {
            "ok": True,
            "data": [
                {
                    "id": it.id,
                    "title": it.title,
                    "game": it.game,
                    "category": it.category,
                    "delivery_status": it.delivery_status,
                    "render_status": it.render_status,
                    "published_at": it.published_at.isoformat(),
                }
                for it in items
            ],
        }

    return handler


def _tool_get_processed_event(repository: SQLiteRepository) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        pid = str(arguments.get("processed_event_id") or "").strip()
        if not pid:
            return {"ok": False, "error": "processed_event_id is required"}
        item = repository.get_processed_event(pid)
        if item is None:
            return {"ok": False, "error": "not_found"}
        return {
            "ok": True,
            "data": {
                "id": item.id,
                "title": item.title,
                "summary": item.summary,
                "highlights": item.highlights,
                "category": item.category,
                "game": item.game,
                "source_credibility": item.source_credibility,
                "delivery_status": item.delivery_status,
                "render_status": item.render_status,
                "media": [m.model_dump() for m in item.media],
                "discovered_sources": item.discovered_sources,
                "published_at": item.published_at.isoformat(),
            },
        }

    return handler


async def _tool_fetch_url(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
    url = str(arguments.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must be http(s)"}
    max_bytes = int(arguments.get("max_bytes") or 65536)
    timeout = float(arguments.get("timeout_seconds") or 15)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
    except Exception as exc:  # pragma: no cover - network dependent
        return {"ok": False, "error": f"fetch_failed: {exc}"}
    text = response.text[: max(1024, min(max_bytes, 200000))]
    return {
        "ok": True,
        "data": {
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "text": text,
            "truncated": len(response.text) > len(text),
        },
    }


async def _tool_fetch_x_tweets(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
    x_user_id = str(arguments.get("x_user_id") or "").strip()
    if not x_user_id:
        return {"ok": False, "error": "x_user_id is required"}
    url = f"https://r.jina.ai/https://x.com/{x_user_id}"
    max_bytes = int(arguments.get("max_bytes") or 65536)
    timeout = float(arguments.get("timeout_seconds") or 15)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
    except Exception as exc:
        return {"ok": False, "error": f"fetch_failed: {exc}"}
    text = response.text[: max(1024, min(max_bytes, 200000))]
    return {
        "ok": True,
        "data": {
            "status_code": response.status_code,
            "url": url,
            "text": text,
            "truncated": len(response.text) > len(text),
        },
    }


def _tool_web_search(api_key: str | None, cx: str | None) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        if not api_key or not cx:
            return {"ok": False, "error": "google_custom_search not configured (set GOOGLE_CUSTOM_SEARCH_API_KEY and GOOGLE_CUSTOM_SEARCH_ENGINE_ID)"}
        num = int(arguments.get("num") or 5)
        num = max(1, min(num, 10))
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "cx": cx, "q": query, "num": str(num)}
        timeout = float(arguments.get("timeout_seconds") or 15)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return {"ok": False, "error": f"search_failed: {exc}"}
        items = (data.get("items") or [])[:num]
        results = [
            {"title": it.get("title"), "link": it.get("link"), "snippet": it.get("snippet")}
            for it in items
        ]
        return {"ok": True, "data": {"query": query, "total_results": len(results), "results": results}}
    return handler


def _tool_bing_search(api_key: str | None) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query is required"}
        if not api_key:
            return {"ok": False, "error": "bing_search not configured (set BING_SEARCH_API_KEY)"}
        count = int(arguments.get("count") or 5)
        count = max(1, min(count, 10))
        market = str(arguments.get("market") or "zh-CN")
        timeout = float(arguments.get("timeout_seconds") or 15)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    "https://api.bing.microsoft.com/v7.0/search",
                    params={"q": query, "count": str(count), "mkt": market},
                    headers={"Ocp-Apim-Subscription-Key": api_key},
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            return {"ok": False, "error": f"search_failed: {exc}"}
        items = (data.get("webPages", {}).get("value") or [])[:count]
        results = [
            {"title": it.get("name"), "link": it.get("url"), "snippet": it.get("snippet")}
            for it in items
        ]
        return {"ok": True, "data": {"query": query, "total_results": len(results), "results": results}}
    return handler


def _tool_collect_rss(
    repository: SQLiteRepository,
    pipeline_enqueue: Callable[[str], Awaitable[None]],
    *,
    rsshub: bool,
    settings: Settings,
) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        source_name = str(arguments.get("source_name") or "").strip()
        feed_url = str(arguments.get("feed_url") or "").strip()
        limit = int(arguments.get("limit") or 5)
        if not source_name or not feed_url:
            return {"ok": False, "error": "source_name and feed_url required"}
        if rsshub:
            try:
                validate_rsshub_feed_url(
                    feed_url,
                    host_markers=settings.rsshub_host_markers,
                    extra_hosts=settings.rsshub_extra_hosts,
                )
            except ValueError as exc:
                return {"ok": False, "error": f"rsshub_invalid: {exc}"}
        try:
            events = await RSSCollector().collect(source_name, feed_url, limit=max(1, min(limit, 20)))
        except Exception as exc:
            return {"ok": False, "error": f"collect_failed: {exc}"}
        result = await collect_and_enqueue_rss(
            events,
            insert_raw_event=repository.insert_raw_event,
            enqueue=pipeline_enqueue,
            touch_source_feed=repository.touch_source_feed,
            source_name=source_name,
            feed_url=feed_url,
            rsshub=rsshub,
        )
        return {
            "ok": True,
            "data": result,
        }

    return handler


def _tool_register_source(repository: SQLiteRepository) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name required"}
        source = repository.upsert_source_registration(
            source_type=str(arguments.get("source_type") or "rss").strip(),
            name=name,
            feed_url=(str(arguments.get("feed_url") or "").strip() or None),
            credibility_level=str(arguments.get("credibility_level") or "unverified"),
        )
        return {"ok": True, "data": source if isinstance(source, dict) else dict(source)}

    return handler


def _tool_render_card_for_review(
    repository: SQLiteRepository,
    renderer: NewsCardRenderer,
) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            preview = RenderPreviewRequest(
                title=str(arguments.get("title") or "").strip() or "Untitled",
                summary=str(arguments.get("summary") or "").strip(),
                highlights=[
                    str(x).strip()
                    for x in (arguments.get("highlights") or [])
                    if str(x).strip()
                ],
                category=str(arguments.get("category") or "announcement"),
                theme=str(arguments.get("theme") or "light"),
                preset_key=(str(arguments.get("preset_key") or "").strip() or None),
                orientation=str(arguments.get("orientation") or "vertical"),
                game=(str(arguments.get("game") or "").strip() or None),
                source_name=str(arguments.get("source_name") or "AgentDraft"),
                channel_name=(str(arguments.get("channel_name") or "").strip() or None),
                author=(str(arguments.get("author") or "").strip() or None),
                source_credibility=str(arguments.get("source_credibility") or "unverified"),
                need_translation=bool(arguments.get("need_translation") or False),
                media=[
                    MediaAsset.model_validate(m)
                    for m in (arguments.get("media") or [])
                    if isinstance(m, dict)
                ],
                discovered_sources=[
                    str(x).strip()
                    for x in (arguments.get("discovered_sources") or [])
                    if str(x).strip()
                ],
            )
        except Exception as exc:
            return {"ok": False, "error": f"invalid_payload: {exc}"}

        now = utc_now()
        raw_event = RawEvent(
            source_type="llm_agent",
            source_name=preview.source_name,
            channel_name=preview.channel_name,
            author=preview.author,
            content="\n".join([preview.title, preview.summary, *preview.highlights]),
            attachments=[m.url for m in preview.media],
            external_id=make_external_id(
                "llm-agent-card",
                preview.source_name,
                preview.title,
                now.isoformat(),
            ),
            published_at=preview.published_at or now,
            raw_payload=preview.model_dump(mode="json"),
        )
        if not repository.insert_raw_event(raw_event):
            existing = repository.get_raw_event(raw_event.id)
            if existing is not None:
                raw_event = existing
        processed_event = ProcessedEvent(
            raw_event_id=raw_event.id,
            title=preview.title,
            summary=preview.summary,
            highlights=preview.highlights,
            category=preview.category,
            game=preview.game,
            need_translation=preview.need_translation,
            source_credibility=preview.source_credibility,
            media=preview.media,
            discovered_sources=preview.discovered_sources,
            language="zh",
            render_status="pending",
            delivery_status="review_pending",
            published_at=preview.published_at or now,
        )
        repository.upsert_processed_event(processed_event)
        try:
            artifact = await renderer.render(raw_event, processed_event, theme=preview.theme)
        except Exception as exc:
            repository.update_processed_event_status(processed_event.id, render_status="failed")
            return {"ok": False, "error": f"render_failed: {exc}"}
        repository.save_render_artifact(artifact)
        repository.update_processed_event_status(
            processed_event.id,
            render_status="image_ready" if artifact.image_path else "html_ready",
            delivery_status="review_pending",
        )
        return {
            "ok": True,
            "data": {
                "processed_event_id": processed_event.id,
                "raw_event_id": raw_event.id,
                "html_path": artifact.html_path,
                "image_path": artifact.image_path,
                "review_url": f"/review?processed_event_id={processed_event.id}",
            },
        }

    return handler


def _tool_call_ddrbbot_api(settings: Settings) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path") or "").strip()
        method = str(arguments.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return {"ok": False, "error": "method must be GET/POST/PUT/PATCH/DELETE"}
        if not path.startswith("/"):
            return {"ok": False, "error": "path must start with /"}
        base_url = str(arguments.get("base_url") or "http://127.0.0.1:8000").rstrip("/")
        query_raw = arguments.get("query") or {}
        payload = arguments.get("payload")
        params = {str(k): str(v) for k, v in query_raw.items()} if isinstance(query_raw, dict) else None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if method == "GET":
                    response = await client.get(base_url + path, params=params)
                else:
                    response = await client.request(
                        method,
                        base_url + path,
                        params=params,
                        json=payload or {},
                    )
        except Exception as exc:
            return {"ok": False, "error": f"call_failed: {exc}"}
        body_text = response.text[:60000]
        try:
            body: Any = response.json()
        except Exception:
            body = body_text
        return {
            "ok": 200 <= response.status_code < 300,
            "data": {
                "status_code": response.status_code,
                "body": body,
            },
        }

    return handler


def _tool_send_reply_text(
    sender: Callable[[str, str], Awaitable[str]],
) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        text = str(arguments.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text is empty"}
        target_type = str(arguments.get("target_type") or "").strip() or context.reply_target_type
        target_id = str(arguments.get("target_id") or "").strip() or context.reply_target_id
        if not target_type or not target_id:
            return {
                "ok": False,
                "error": "target_type/target_id required when not inside a qq_chat context",
            }
        route = f"{target_type}:{target_id}"
        try:
            message_id = await sender(route, text)
        except Exception as exc:  # pragma: no cover - network dependent
            return {"ok": False, "error": f"send_failed: {exc}"}
        return {"ok": True, "data": {"route": route, "message_id": message_id}}

    return handler


def _tool_run_shell(settings: Settings) -> ToolHandler:
    import asyncio as _asyncio
    import os as _os

    default_timeout = float(settings.llm_agent_shell_timeout_seconds)
    output_limit = int(settings.llm_agent_shell_output_limit)
    default_workdir = settings.llm_agent_shell_workdir

    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        command = str(arguments.get("command") or "").strip()
        if not command:
            return {"ok": False, "error": "command is empty"}
        timeout = float(arguments.get("timeout_seconds") or default_timeout)
        timeout = max(1.0, min(timeout, 600.0))
        workdir = str(arguments.get("workdir") or "").strip() or default_workdir
        cwd = workdir if workdir and _os.path.isdir(workdir) else None
        try:
            process = await _asyncio.create_subprocess_shell(
                command,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except Exception as exc:
            return {"ok": False, "error": f"spawn_failed: {exc}"}
        try:
            stdout_bytes, stderr_bytes = await _asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except _asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            return {"ok": False, "error": f"timeout after {timeout:.1f}s"}

        def _decode(data: bytes) -> str:
            text = data.decode("utf-8", errors="replace")
            if len(text) > output_limit:
                return text[:output_limit] + f"\n...[truncated {len(text) - output_limit} chars]"
            return text

        return {
            "ok": (process.returncode == 0),
            "data": {
                "exit_code": process.returncode,
                "stdout": _decode(stdout_bytes),
                "stderr": _decode(stderr_bytes),
                "cwd": cwd,
                "command": command,
            },
        }

    return handler


def _tool_send_news_card_now(
    repository: SQLiteRepository,
    delivery_service: QQDeliveryService,
) -> ToolHandler:
    async def handler(context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        target_type = str(arguments.get("target_type") or "group").strip()
        target_id = str(arguments.get("target_id") or "").strip()
        if target_type not in {"group", "private"} or not target_id:
            return {"ok": False, "error": "target_type/target_id invalid"}
        image_path = str(arguments.get("image_path") or "").strip()
        processed_event_id = str(arguments.get("processed_event_id") or "").strip() or None
        caption = str(arguments.get("caption") or "").strip() or None
        if not image_path and processed_event_id:
            artifact = repository.get_latest_render_artifact(processed_event_id)
            if artifact and artifact.image_path:
                image_path = artifact.image_path
        if not image_path:
            return {"ok": False, "error": "image_path required (or processed_event_id with rendered image)"}
        try:
            result = delivery_service.enqueue_delivery(
                QQSendNewsCardRequest(
                    processed_event_id=processed_event_id,
                    target_type=target_type,  # type: ignore[arg-type]
                    target_id=target_id,
                    image_path=image_path,
                    caption=caption,
                )
            )
        except Exception as exc:
            return {"ok": False, "error": f"enqueue_failed: {exc}"}
        return {"ok": True, "data": result.to_dict()}

    return handler


build_default_tool_registry = build_default_registry


# noinspection PyUnusedLocal
def _unused(x: Any = json) -> None:
    """Keep json import when the module is statically analyzed without callers."""

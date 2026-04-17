from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .config import Settings
from .copybook import copy_list, copy_text
from .models import LLMAnalysisOutput, MediaAsset, ProcessedEvent, RawEvent

logger = logging.getLogger(__name__)


class EventAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze(self, raw_event: RawEvent) -> ProcessedEvent:
        if self._llm_enabled:
            try:
                return await self._analyze_with_llm(raw_event)
            except Exception as exc:  # pragma: no cover - graceful fallback
                logger.warning("LLM analysis failed, falling back to heuristic mode: %s", exc)
        return self._analyze_heuristically(raw_event)

    @property
    def _llm_enabled(self) -> bool:
        return bool(self.settings.llm_base_url and self.settings.llm_model)

    async def _analyze_with_llm(self, raw_event: RawEvent) -> ProcessedEvent:
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_name": raw_event.source_name,
                            "channel_name": raw_event.channel_name,
                            "author": raw_event.author,
                            "content": raw_event.content,
                            "attachments": raw_event.attachments,
                            "published_at": raw_event.published_at.isoformat(),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        draft = LLMAnalysisOutput.model_validate(parsed)
        return ProcessedEvent(
            raw_event_id=raw_event.id,
            title=draft.title or self._fallback_title(raw_event.content),
            summary=draft.summary or self._fallback_summary(raw_event.content),
            highlights=self._normalize_highlights(draft.highlights, raw_event.content),
            category=draft.category or self._infer_category(raw_event.content),
            game=draft.game or self._infer_game(raw_event),
            need_translation=bool(draft.need_translation),
            source_credibility=draft.source_credibility or self._infer_credibility(raw_event),
            media=self._normalize_media(draft.media, raw_event.attachments),
            discovered_sources=self._normalize_discovered_sources(draft.discovered_sources),
            language=draft.language or self._detect_language(raw_event.content),
            published_at=raw_event.published_at,
        )

    def _analyze_heuristically(self, raw_event: RawEvent) -> ProcessedEvent:
        return ProcessedEvent(
            raw_event_id=raw_event.id,
            title=self._fallback_title(raw_event.content),
            summary=self._fallback_summary(raw_event.content),
            highlights=self._normalize_highlights(None, raw_event.content),
            category=self._infer_category(raw_event.content),
            game=self._infer_game(raw_event),
            need_translation=self._detect_language(raw_event.content) != "zh",
            source_credibility=self._infer_credibility(raw_event),
            media=[MediaAsset(url=url) for url in raw_event.attachments[:4]],
            discovered_sources=self._extract_handles(raw_event.content),
            language=self._detect_language(raw_event.content),
            published_at=raw_event.published_at,
        )

    def _fallback_title(self, content: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if lines:
            return lines[0][:48]
        return copy_text("analyzer.fallback_title", "未命名 Roblox 资讯")

    def _fallback_summary(self, content: str) -> str:
        compact = " ".join(part.strip() for part in content.splitlines() if part.strip())
        return compact[:180] if compact else copy_text(
            "analyzer.fallback_summary",
            "暂无详细内容，建议查看原始事件。",
        )

    def _normalize_highlights(self, value: Any, content: str) -> list[str]:
        if isinstance(value, list):
            normalized = [str(item).strip() for item in value if str(item).strip()]
            if normalized:
                return normalized[:5]
        parts = re.split(r"[。！？.!?\n]+", content)
        normalized = [part.strip(" -•\t") for part in parts if part.strip(" -•\t")]
        return normalized[:4] or copy_list(
            "analyzer.fallback_highlights",
            ["等待补充更多公告内容。"],
        )

    def _normalize_media(self, value: Any, fallback_urls: list[str]) -> list[MediaAsset]:
        items: list[MediaAsset] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("url"):
                    items.append(
                        MediaAsset(
                            type=str(item.get("type") or "image"),
                            url=str(item["url"]),
                            description=str(item.get("description") or "").strip() or None,
                            reference_url=str(item.get("reference_url") or "").strip() or None,
                            reference_label=str(item.get("reference_label") or "").strip() or None,
                        )
                    )
        if items:
            return items[:4]
        return [MediaAsset(url=url) for url in fallback_urls[:4]]

    @staticmethod
    def _system_prompt() -> str:
        return copy_text(
            "analyzer.system_prompt",
            (
                "你是一个 Roblox 资讯分析机器人。\n"
                "请把输入整理成 JSON，字段必须包含：\n"
                "title, summary, highlights, category, game, need_translation, "
                "source_credibility, media, discovered_sources, language\n"
                "其中 media 为数组；每项尽量提供 type, url, description, reference_url, reference_label。\n"
                "要求：\n"
                "- 只能输出合法 JSON\n"
                "- 不要输出 Markdown\n"
                "- highlights 保持 3 到 5 条\n"
                "- media 最多返回 4 项\n"
                "- 如果运行环境支持联网检索，可优先补充官方或一手来源的图片链接\n"
                "- 如信息不足，不要编造"
            ),
        )

    def _normalize_discovered_sources(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:6]

    def _infer_category(self, content: str) -> str:
        lowered = content.lower()
        mapping = {
            "maintenance": ["maintenance", "downtime", "维护", "停机"],
            "patch": ["patch", "update log", "patch notes", "平衡", "修复", "更新日志"],
            "teaser": ["teaser", "preview", "coming soon", "预告", "爆料"],
            "announcement": ["announce", "announcement", "公告", "发布"],
        }
        for category, keywords in mapping.items():
            if any(keyword in lowered for keyword in keywords):
                return category
        return "announcement"

    def _infer_game(self, raw_event: RawEvent) -> str | None:
        pool = f"{raw_event.source_name} {raw_event.channel_name or ''} {raw_event.content}"
        matches = re.findall(r"\b[A-Z][A-Z0-9!]{2,}\b", pool)
        if matches:
            return matches[0]
        for candidate in ("DOORS", "PHIGHTING", "Forsaken", "Block Tales", "Pressure"):
            if candidate.lower() in pool.lower():
                return candidate
        return None

    def _infer_credibility(self, raw_event: RawEvent) -> str:
        name = f"{raw_event.source_name} {raw_event.channel_name or ''} {raw_event.author or ''}".lower()
        if any(token in name for token in ("official", "announcement", "news", "公告", "官方")):
            return "official"
        return "community"

    def _detect_language(self, content: str) -> str:
        chinese = len(re.findall(r"[\u4e00-\u9fff]", content))
        latin = len(re.findall(r"[A-Za-z]", content))
        if chinese and latin:
            return "mixed"
        if chinese:
            return "zh"
        if latin:
            return "en"
        return "unknown"

    def _extract_handles(self, content: str) -> list[str]:
        handles = re.findall(r"@[\w\.-]+", content)
        deduplicated: list[str] = []
        for handle in handles:
            if handle not in deduplicated:
                deduplicated.append(handle)
        return deduplicated[:6]

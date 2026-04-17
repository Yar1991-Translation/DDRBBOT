from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import struct_time

import feedparser

from .models import RawEvent
from .utils import make_external_id, utc_now


class RSSCollector:
    async def collect(self, source_name: str, feed_url: str, *, limit: int = 10) -> list[RawEvent]:
        parsed = await asyncio.to_thread(feedparser.parse, feed_url)
        events: list[RawEvent] = []
        for entry in parsed.entries[:limit]:
            title = str(entry.get("title") or "").strip()
            summary = str(entry.get("summary") or entry.get("description") or "").strip()
            content = "\n".join(part for part in [title, summary] if part)
            attachments = [
                enclosure.get("href")
                for enclosure in entry.get("enclosures", [])
                if enclosure.get("href")
            ]
            published_at = self._entry_datetime(entry.get("published_parsed"))
            external_id = str(entry.get("id") or entry.get("guid") or entry.get("link") or "")
            events.append(
                RawEvent(
                    source_type="rss",
                    source_name=source_name,
                    author=str(entry.get("author") or "") or None,
                    content=content or title or "RSS entry without content",
                    attachments=attachments,
                    external_id=external_id or make_external_id(source_name, title, summary),
                    published_at=published_at,
                    raw_payload={
                        "title": title,
                        "summary": summary,
                        "link": str(entry.get("link") or ""),
                        "id": external_id,
                    },
                )
            )
        return events

    @staticmethod
    def _entry_datetime(value: struct_time | None) -> datetime:
        if value is None:
            return utc_now()
        return datetime(*value[:6], tzinfo=timezone.utc)

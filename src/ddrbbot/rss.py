from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import struct_time
from typing import Awaitable, Callable

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


async def collect_and_enqueue_rss(
    events: list[RawEvent],
    *,
    insert_raw_event: Callable[[RawEvent], bool],
    enqueue: Callable[[str], Awaitable[None]],
    touch_source_feed: Callable[..., None],
    source_name: str,
    feed_url: str,
    rsshub: bool = False,
) -> dict[str, int | list[str]]:
    accepted = 0
    deduplicated = 0
    queued_event_ids: list[str] = []
    for event in events:
        if rsshub:
            base = dict(event.raw_payload) if event.raw_payload else {}
            event.raw_payload = {**base, "collector": "rsshub", "feed_url": feed_url}
        inserted = insert_raw_event(event)
        if not inserted:
            deduplicated += 1
            continue
        accepted += 1
        queued_event_ids.append(event.id)
        await enqueue(event.id)
    touch_source_feed(source_type="rss", source_name=source_name, feed_url=feed_url)
    return {"accepted": accepted, "deduplicated": deduplicated, "queued_event_ids": queued_event_ids}

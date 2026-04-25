from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException

from .copybook import copy_dict, copy_format, copy_list, copy_text
from .database import _RAW_PATCH_MISSING
from .delivery import DeliveryError
from .models import ProcessedEvent, RawEvent, ReviewEditRequest
from .services import AppServices
from .utils import utc_now


def review_statuses(status: str) -> tuple[str, ...] | None:
    mapping = {
        "open": ("pending", "skipped", "review_pending", "failed", "approved"),
        "failed": ("failed",),
        "sent": ("sent",),
        "rejected": ("rejected",),
        "all": None,
    }
    return mapping.get(status, mapping["open"])


def normalize_review_status(status: str) -> str:
    if status in {"open", "failed", "sent", "rejected", "all"}:
        return status
    return "open"


def select_review_item(
    items: list[ProcessedEvent],
    processed_event_id: str | None,
) -> ProcessedEvent | None:
    if not items:
        return None
    if processed_event_id:
        for item in items:
            if item.id == processed_event_id:
                return item
    return items[0]


def build_review_list_item(
    processed_event: ProcessedEvent,
    *,
    source_name: str,
    selected_id: str | None,
) -> dict[str, Any]:
    return {
        "id": processed_event.id,
        "title": processed_event.title,
        "game": processed_event.game,
        "source_name": source_name,
        "published_at": processed_event.published_at.strftime("%m-%d %H:%M"),
        "delivery_status_label": _review_delivery_status_label(processed_event.delivery_status),
        "delivery_status_tone": _review_delivery_status_tone(processed_event.delivery_status),
        "render_status": processed_event.render_status,
        "render_status_label": _review_render_status_label(processed_event.render_status),
        "category_label": _review_category_label(processed_event.category),
        "active": processed_event.id == selected_id,
    }


def build_review_detail(services: AppServices, processed_event: ProcessedEvent) -> dict[str, Any]:
    raw_event = services.repository.get_raw_event(processed_event.raw_event_id)
    latest_artifact = services.repository.get_latest_render_artifact(processed_event.id)
    delivery_records = services.repository.list_delivery_records(
        processed_event_id=processed_event.id,
        limit=5,
    )
    source_name = raw_event.source_name if raw_event else (
        processed_event.game or copy_text("rendering.unknown_source", "Unknown Source")
    )
    preview_seed = review_preview_seed(
        raw_event=raw_event,
        processed_event=processed_event,
        theme=latest_artifact.theme if latest_artifact else "light",
    )
    structured_meta = copy_format(
        "review_panel.supporting.structured_meta",
        "语言 {language} · 可信度 {credibility} · {translation}",
        language=processed_event.language,
        credibility=_review_credibility_label(processed_event.source_credibility),
        translation=copy_text("review_panel.supporting.translation_true", "含翻译")
        if processed_event.need_translation
        else copy_text("review_panel.supporting.translation_false", "原文可直接使用"),
    )
    return {
        "id": processed_event.id,
        "title": processed_event.title,
        "summary": processed_event.summary,
        "highlights_text": "\n".join(processed_event.highlights),
        "category": processed_event.category,
        "game": processed_event.game or "",
        "theme": latest_artifact.theme if latest_artifact else "light",
        "source_name": source_name,
        "channel_name": raw_event.channel_name if raw_event else None,
        "author": raw_event.author if raw_event else None,
        "published_at": processed_event.published_at.strftime("%Y-%m-%d %H:%M UTC"),
        "source_credibility": processed_event.source_credibility,
        "source_credibility_label": _review_credibility_label(processed_event.source_credibility),
        "need_translation": processed_event.need_translation,
        "language": processed_event.language,
        "render_status": processed_event.render_status,
        "render_status_label": _review_render_status_label(processed_event.render_status),
        "delivery_status_label": _review_delivery_status_label(processed_event.delivery_status),
        "delivery_status_tone": _review_delivery_status_tone(processed_event.delivery_status),
        "structured_meta": structured_meta,
        "discovered_sources": processed_event.discovered_sources,
        "raw_content": raw_event.content
        if raw_event
        else copy_text("rendering.raw_event_missing", "Raw event payload is missing."),
        "raw_payload_json": json.dumps(
            raw_event.raw_payload if raw_event else {},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        "latest_html_path": latest_artifact.html_path if latest_artifact else None,
        "latest_image_path": latest_artifact.image_path if latest_artifact else None,
        "preview_link": build_preview_link(preview_seed, processed_event),
        "preview_seed_json": json.dumps(preview_seed, ensure_ascii=False),
        "recent_deliveries": [
            {
                "trace_id": record.trace_id,
                "status": record.status,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "updated_at": record.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for record in delivery_records
        ],
        "raw_event_id": processed_event.raw_event_id,
        "preset_key": preview_seed.get("preset_key") or "",
        "orientation": preview_seed.get("orientation") or "vertical",
        "custom_css": preview_seed.get("custom_css") or "",
        "media": [m.model_dump() for m in processed_event.media],
    }


def load_review_entities(
    services: AppServices,
    processed_event_id: str,
) -> tuple[RawEvent, ProcessedEvent]:
    processed_event = services.repository.get_processed_event(processed_event_id)
    if processed_event is None:
        raise HTTPException(
            status_code=404,
            detail=copy_format(
                "review_api.errors.processed_not_found",
                "Processed event not found: {processed_event_id}",
                processed_event_id=processed_event_id,
            ),
        )
    raw_event = services.repository.get_raw_event(processed_event.raw_event_id)
    if raw_event is None:
        raise HTTPException(
            status_code=404,
            detail=copy_format(
                "review_api.errors.raw_not_found",
                "Raw event not found: {raw_event_id}",
                raw_event_id=processed_event.raw_event_id,
            ),
        )
    return raw_event, processed_event


def apply_review_edits(
    services: AppServices,
    raw_event: RawEvent,
    processed_event: ProcessedEvent,
    payload: ReviewEditRequest,
) -> ProcessedEvent:
    fs = payload.model_fields_set
    title = payload.title.strip() or processed_event.title
    summary = payload.summary.strip() or processed_event.summary
    highlights = [item.strip() for item in payload.highlights if item.strip()]
    if not highlights:
        highlights = processed_event.highlights or copy_list(
            "rendering.fallback_review_highlights",
            ["待补充重点摘要。"],
        )
    category = payload.category or processed_event.category
    game = (payload.game or "").strip()
    need_translation_update: bool | None = None
    if "need_translation" in fs:
        need_translation_update = bool(payload.need_translation)
    source_credibility_update: str | None = None
    if "source_credibility" in fs:
        sc = (payload.source_credibility or "").strip()
        source_credibility_update = sc or processed_event.source_credibility
    media_update = list(payload.media or []) if "media" in fs else None
    discovered_update: list[str] | None = None
    if "discovered_sources" in fs:
        discovered_update = [
            line.strip()
            for line in (payload.discovered_sources or [])
            if isinstance(line, str) and line.strip()
        ]
    services.repository.update_processed_event_review_fields(
        processed_event.id,
        title=title,
        summary=summary,
        highlights=highlights,
        category=category,
        game=game,
        delivery_status="review_pending",
        need_translation=need_translation_update,
        source_credibility=source_credibility_update,
        media=media_update,
        discovered_sources=discovered_update,
    )
    processed_event.title = title
    processed_event.summary = summary
    processed_event.highlights = highlights
    processed_event.category = category
    processed_event.game = game or None
    processed_event.delivery_status = "review_pending"
    if need_translation_update is not None:
        processed_event.need_translation = need_translation_update
    if source_credibility_update is not None:
        processed_event.source_credibility = source_credibility_update
    if media_update is not None:
        processed_event.media = media_update
    if discovered_update is not None:
        processed_event.discovered_sources = discovered_update
    raw_payload_merge: dict[str, Any] = {}
    if "preset_key" in fs:
        raw_payload_merge["preset_key"] = (payload.preset_key or "").strip()
    if "orientation" in fs:
        raw_payload_merge["orientation"] = payload.orientation or "vertical"
    if "custom_css" in fs:
        raw_payload_merge["custom_css"] = payload.custom_css or ""
    ch: Any = _RAW_PATCH_MISSING
    au: Any = _RAW_PATCH_MISSING
    if "channel_name" in fs:
        ch = payload.channel_name
    if "author" in fs:
        au = payload.author
    if raw_payload_merge or ch is not _RAW_PATCH_MISSING or au is not _RAW_PATCH_MISSING:
        services.repository.patch_raw_event(
            raw_event.id,
            channel_name=ch,
            author=au,
            raw_payload_merge=raw_payload_merge if raw_payload_merge else None,
        )
        if raw_payload_merge:
            raw_event.raw_payload.update(raw_payload_merge)
        if ch is not _RAW_PATCH_MISSING:
            raw_event.channel_name = ch
        if au is not _RAW_PATCH_MISSING:
            raw_event.author = au
    return processed_event


def review_preview_seed(
    *,
    raw_event: RawEvent | None,
    processed_event: ProcessedEvent,
    theme: str,
) -> dict[str, Any]:
    preset_key = ""
    orientation = "vertical"
    custom_css = ""
    if raw_event:
        preset_key = str(raw_event.raw_payload.get("preset_key") or "")
        orientation = str(raw_event.raw_payload.get("orientation") or "vertical")
        if orientation not in {"vertical", "horizontal"}:
            orientation = "vertical"
        custom_css = str(raw_event.raw_payload.get("custom_css") or "")
    return {
        "source_name": raw_event.source_name
        if raw_event
        else (processed_event.game or copy_text("rendering.preview_source", "Preview Source")),
        "channel_name": raw_event.channel_name if raw_event and raw_event.channel_name else "",
        "author": raw_event.author
        if raw_event and raw_event.author
        else copy_text("rendering.unknown_author", "Unknown"),
        "source_credibility": processed_event.source_credibility,
        "need_translation": processed_event.need_translation,
        "hero_image_url": processed_event.media[0].url if processed_event.media else "",
        "hero_image_description": processed_event.media[0].description if processed_event.media else "",
        "hero_image_reference_url": processed_event.media[0].reference_url if processed_event.media else "",
        "hero_image_reference_label": processed_event.media[0].reference_label if processed_event.media else "",
        "discovered_sources": processed_event.discovered_sources,
        "preset_key": preset_key,
        "orientation": orientation,
        "custom_css": custom_css,
        "theme": theme,
    }


def build_preview_link(preview_seed: dict[str, Any], processed_event: ProcessedEvent) -> str:
    params = {
        "title": processed_event.title,
        "summary": processed_event.summary,
        "highlights": "\n".join(processed_event.highlights),
        "category": processed_event.category,
        "theme": preview_seed.get("theme") or "light",
        "preset_key": preview_seed.get("preset_key") or "",
        "orientation": preview_seed.get("orientation") or "vertical",
        "custom_css": preview_seed.get("custom_css") or "",
        "game": processed_event.game or "",
        "source_name": preview_seed.get("source_name")
        or copy_text("rendering.preview_source", "Preview Source"),
        "channel_name": preview_seed.get("channel_name") or "",
        "author": preview_seed.get("author") or "",
        "source_credibility": preview_seed.get("source_credibility") or "unverified",
        "hero_image_url": preview_seed.get("hero_image_url") or "",
        "hero_image_description": preview_seed.get("hero_image_description") or "",
        "hero_image_reference_url": preview_seed.get("hero_image_reference_url") or "",
        "hero_image_reference_label": preview_seed.get("hero_image_reference_label") or "",
        "discovered_sources": "\n".join(preview_seed.get("discovered_sources") or []),
        "need_translation": "true" if preview_seed.get("need_translation") else "false",
    }
    return f"/preview/md3/card?{urlencode(params)}"


async def send_review_artifact(
    services: AppServices,
    *,
    processed_event: ProcessedEvent,
    raw_event: RawEvent,
    image_path: str,
    action: str,
    target_type: str | None,
    target_id: str | None,
) -> dict[str, Any]:
    caption_prefix = processed_event.game or raw_event.source_name
    caption = copy_format(
        "delivery.review_caption",
        "{prefix} / {title}",
        prefix=caption_prefix,
        title=processed_event.title,
    )
    trace_id = (
        f"review:{action}:{processed_event.id}:{target_type or 'default'}:"
        f"{target_id or services.settings.default_qq_group_id or 'unset'}:{utc_now().timestamp():.6f}"
    )
    return await services.operations_service.send_news_card(
        image_path=image_path,
        caption=caption[:120],
        trace_id=trace_id,
        processed_event_id=processed_event.id,
        target_type=target_type,
        target_id=target_id,
    )


def _review_delivery_status_label(status: str) -> str:
    mapping = copy_dict(
        "review_panel.delivery_status_labels",
        {
            "pending": "待处理",
            "skipped": "待审核",
            "review_pending": "待审核",
            "approved": "已批准",
            "sent": "已发送",
            "failed": "发送失败",
            "rejected": "已拒绝",
        },
    )
    return mapping.get(status, status)


def _review_delivery_status_tone(status: str) -> str:
    if status in {"sent"}:
        return "sent"
    if status in {"failed", "rejected"}:
        return "failed"
    return "pending"


def _review_category_label(category: str) -> str:
    mapping = copy_dict(
        "rendering.category_labels",
        {
            "maintenance": "通知内容",
            "patch": "更新内容",
            "teaser": "预告内容",
            "announcement": "公告内容",
        },
    )
    return mapping.get(category, category)


def _review_credibility_label(credibility: str) -> str:
    mapping = copy_dict(
        "rendering.credibility_labels",
        {
            "official": "官方来源",
            "community": "社区来源",
            "unverified": "待核实",
        },
    )
    return mapping.get(credibility, credibility)


def _review_render_status_label(status: str) -> str:
    mapping = copy_dict(
        "rendering.render_status_labels",
        {
            "pending": "待渲染",
            "html_ready": "HTML 就绪",
            "image_ready": "图片就绪",
            "failed": "渲染失败",
            "skipped": "已跳过",
        },
    )
    return mapping.get(status, status)

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Settings
from .copybook import copy_dict, copy_format, copy_list, copy_text, load_copy
from .models import ProcessedEvent, RawEvent, RenderArtifact
from .presets import list_game_card_presets, resolve_game_card_preset
from .utils import ensure_directory, slugify, utc_now

logger = logging.getLogger(__name__)

_DOORS_FONT_FILE = Path(__file__).resolve().parent.parent / "font" / "DOORS" / "Doors-Regular.ttf"
_DOORS_FONT_SRC = 'url("/font/DOORS/Doors-Regular.ttf")'
_FORSAKEN_BORDER_FILE = (
    Path(__file__).resolve().parent.parent / "assent" / "forsaken" / "SideDescriptionUI.png"
)
_FORSAKEN_BORDER_SRC = 'url("/assent/forsaken/SideDescriptionUI.png")'


def _inline_doors_font_in_css(css: str) -> str:
    if _DOORS_FONT_SRC not in css or not _DOORS_FONT_FILE.is_file():
        return css
    encoded = base64.b64encode(_DOORS_FONT_FILE.read_bytes()).decode("ascii")
    return css.replace(_DOORS_FONT_SRC, f'url("data:font/ttf;base64,{encoded}")')


def _inline_forsaken_border_in_css(css: str) -> str:
    if _FORSAKEN_BORDER_SRC not in css or not _FORSAKEN_BORDER_FILE.is_file():
        return css
    encoded = base64.b64encode(_FORSAKEN_BORDER_FILE.read_bytes()).decode("ascii")
    return css.replace(_FORSAKEN_BORDER_SRC, f'url("data:image/png;base64,{encoded}")')


class NewsCardRenderer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._template_dir = Path(__file__).resolve().parent / "templates"
        self._env = Environment(
            loader=FileSystemLoader(self._template_dir),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._news_card_css = (self._template_dir / "news_card.css").read_text(encoding="utf-8")
        self._preview_console_css = (self._template_dir / "preview_console.css").read_text(
            encoding="utf-8"
        )
        self._review_panel_css = (self._template_dir / "review_panel.css").read_text(
            encoding="utf-8"
        )

    async def render(
        self,
        raw_event: RawEvent,
        processed_event: ProcessedEvent,
        *,
        theme: str = "light",
        force_screenshot: bool = False,
    ) -> RenderArtifact:
        target_dir = ensure_directory(
            self.settings.artifacts_dir / "rendered" / utc_now().date().isoformat()
        )
        base_name = slugify(
            f"{processed_event.game or raw_event.source_name}-{processed_event.category}-{processed_event.id[:8]}",
            fallback=processed_event.id[:8],
        )
        html_path = target_dir / f"{base_name}.html"
        image_path = target_dir / f"{base_name}.png"
        html = self.render_card_html(raw_event, processed_event, theme=theme)
        html_path.write_text(html, encoding="utf-8")

        orientation = str(raw_event.raw_payload.get("orientation") or "vertical").lower()
        if orientation not in {"vertical", "horizontal"}:
            orientation = "vertical"

        image_file: str | None = None
        if self.settings.screenshot_enabled or force_screenshot:
            image_file = await self._try_capture_screenshot(html_path, image_path, orientation=orientation)

        return RenderArtifact(
            processed_event_id=processed_event.id,
            theme=theme,
            html_path=str(html_path),
            image_path=image_file,
        )

    def render_card_html(
        self,
        raw_event: RawEvent,
        processed_event: ProcessedEvent,
        *,
        theme: str = "light",
    ) -> str:
        copy_data = load_copy()
        preset_key = str(raw_event.raw_payload.get("preset_key") or "")
        orientation = str(raw_event.raw_payload.get("orientation") or "vertical").lower()
        if orientation not in {"vertical", "horizontal"}:
            orientation = "vertical"
        custom_css = str(raw_event.raw_payload.get("custom_css") or "").strip()
        preset = resolve_game_card_preset(processed_event.game, preset_key or None)
        effective_custom_css = custom_css or preset.default_custom_css
        final_css = "\n\n".join(
            block for block in [self._news_card_css, preset.css, effective_custom_css] if block.strip()
        )
        final_css = _inline_doors_font_in_css(final_css)
        final_css = _inline_forsaken_border_in_css(final_css)
        context_note = self._context_note(preset.key, raw_event, processed_event)
        if "尚未开发" in preset.description:
            context_note = f"{preset.label} · {preset.description}"
        media_payload = self._build_media_payload(processed_event, copy_data)
        context = {
            "css": final_css,
            "copy": copy_data,
            "theme": theme,
            "preset_key": preset.key,
            "orientation": orientation,
            "preset_label": copy_text(("presets", preset.key, "label"), preset.label),
            "context_note": context_note,
            "preset_description": preset.description,
            "source_name": raw_event.source_name,
            "channel_name": raw_event.channel_name,
            "author": raw_event.author or copy_text("rendering.unknown_author", "\u672a\u77e5"),
            "published_at": processed_event.published_at.strftime("%Y-%m-%d %H:%M UTC"),
            "category": processed_event.category,
            "category_label": self._category_label(processed_event.category),
            "credibility_label": self._credibility_label(processed_event.source_credibility),
            "title": processed_event.title,
            "summary": processed_event.summary,
            "highlights_title": self._content_section_title(processed_event.category),
            "highlights": processed_event.highlights or copy_list(
                "rendering.fallback_review_highlights",
                ["\u5f85\u8865\u5145\u91cd\u70b9\u6458\u8981\u3002"],
            ),
            "game": processed_event.game,
            "need_translation": processed_event.need_translation,
            "primary_media": media_payload["primary_media"],
            "secondary_media": media_payload["secondary_media"],
            "reference_materials": media_payload["reference_materials"],
            "use_reference_materials_section": media_payload["use_reference_materials_section"],
            "media_count": len(processed_event.media),
            "discovered_sources": processed_event.discovered_sources,
            "generated_at": utc_now().strftime("%Y-%m-%d %H:%M UTC"),
        }
        return self._env.get_template("news_card.html").render(**context)

    def render_preview_console(self, *, defaults: dict[str, str], card_url: str) -> str:
        copy_data = load_copy()
        presets = list_game_card_presets()
        return self._env.get_template("preview_console.html").render(
            css=self._preview_console_css,
            copy=copy_data,
            defaults=defaults,
            card_url=card_url,
            presets=presets,
            preset_map_json=json.dumps(
                {item["key"]: item for item in presets},
                ensure_ascii=False,
            ),
        )

    def render_review_panel(
        self,
        *,
        items: list[dict[str, Any]],
        selected_item: dict[str, Any] | None,
        status_filter: str,
        default_group_id: str | None,
        queue_size: int,
        failed_deliveries: int,
        total_processed_events: int,
    ) -> str:
        copy_data = load_copy()
        return self._env.get_template("review_panel.html").render(
            css=self._review_panel_css,
            copy=copy_data,
            review_copy_json=json.dumps(copy_dict("review_panel", {}), ensure_ascii=False),
            items=items,
            selected_item=selected_item,
            status_filter=status_filter,
            default_group_id=default_group_id,
            queue_size=queue_size,
            failed_deliveries=failed_deliveries,
            total_processed_events=total_processed_events,
        )

    async def _try_capture_screenshot(
        self, html_path: Path, image_path: Path, *, orientation: str = "vertical",
    ) -> str | None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:  # pragma: no cover - optional dependency at runtime
            logger.warning("Playwright is not installed, skipping screenshot generation.")
            return None

        if orientation == "horizontal":
            vp = {"width": 1320, "height": 760}
        else:
            vp = {"width": 860, "height": 1280}

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                page = await browser.new_page(
                    viewport=vp,
                    device_scale_factor=2,
                )
                await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
                clip = await self._news_card_clip(page, orientation=orientation)
                if clip is not None:
                    await page.screenshot(path=str(image_path), type="png", clip=clip)
                else:
                    await page.screenshot(path=str(image_path), type="png", full_page=True)
                await browser.close()
        except Exception as exc:  # pragma: no cover - depends on system browser runtime
            logger.warning("Failed to capture screenshot for %s: %s", html_path, exc)
            return None
        return str(image_path)

    async def _news_card_clip(self, page: Any, *, orientation: str = "vertical") -> dict[str, float] | None:
        card = page.locator(".news-card").first
        box = await card.bounding_box()
        if box is None:
            return None

        if orientation == "horizontal":
            return {
                "x": box["x"],
                "y": box["y"],
                "width": box["width"],
                "height": box["height"],
            }

        document_box = await page.evaluate(
            """() => ({
                width: Math.ceil(document.documentElement.scrollWidth),
                height: Math.ceil(document.documentElement.scrollHeight),
            })"""
        )
        padding = 24
        x = max(box["x"] - padding, 0)
        y = max(box["y"] - padding, 0)
        right = min(box["x"] + box["width"] + padding, document_box["width"])
        bottom = min(box["y"] + box["height"] + padding, document_box["height"])
        width = right - x
        height = bottom - y
        if width <= 0 or height <= 0:
            return None
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    @staticmethod
    def _category_label(category: str) -> str:
        mapping = copy_dict(
            "rendering.category_labels",
            {
                "maintenance": "通知内容",
                "patch": "更新内容",
                "teaser": "预告内容",
                "announcement": "公告内容",
            },
        )
        return mapping.get(category, "\u8d44\u8baf\u5feb\u62a5")

    @staticmethod
    def _content_section_title(category: str) -> str:
        mapping = copy_dict(
            "news_card.content_section_titles",
            {
                "announcement": "公告内容",
                "teaser": "预告内容",
                "patch": "更新内容",
                "maintenance": "通知内容",
            },
        )
        return mapping.get(category, copy_text("news_card.highlights_title", "内容要点"))

    @staticmethod
    def _credibility_label(credibility: str) -> str:
        mapping = copy_dict(
            "rendering.credibility_labels",
            {
                "official": "\u5b98\u65b9\u6765\u6e90",
                "community": "\u793e\u533a\u6765\u6e90",
                "unverified": "\u5f85\u6838\u5b9e",
            },
        )
        return mapping.get(credibility, "\u5f85\u6838\u5b9e")

    @staticmethod
    def _context_note(preset_key: str, raw_event: RawEvent, processed_event: ProcessedEvent) -> str:
        category_mapping = copy_dict(
            "news_card.short_category_labels",
            {
                "announcement": "公告内容",
                "teaser": "预告内容",
                "patch": "更新内容",
                "maintenance": "通知内容",
            },
        )
        credibility_mapping = copy_dict(
            "news_card.credibility_prefixes",
            {
                "official": "官方",
                "community": "社区",
                "unverified": "待核实",
            },
        )
        category_label = category_mapping.get(processed_event.category, "资讯")
        credibility_prefix = credibility_mapping.get(processed_event.source_credibility, "待核实")
        source_display = raw_event.source_name or copy_text("rendering.unknown_source", "未知来源")
        channel_display = raw_event.channel_name or "unknown-channel"
        author_display = raw_event.author or copy_text("rendering.unknown_author", "未知")
        sources = "、".join(processed_event.discovered_sources[:3])
        preset_templates = copy_dict("news_card.preset_context_templates", {})
        selected_templates = preset_templates.get(preset_key, {})
        with_sources_template = str(
            selected_templates.get("with_sources")
            or copy_text(
                "news_card.context_templates.with_sources",
                "{credibility_prefix}{category_label}，来自 {source_display}，补充线索：{sources}。",
            )
        )
        source_only_template = str(
            selected_templates.get("source_only")
            or copy_text(
                "news_card.context_templates.source_only",
                "{credibility_prefix}{category_label}，来自 {source_display}。",
            )
        )
        if sources:
            return copy_format(
                with_sources_template,
                with_sources_template,
                credibility_prefix=credibility_prefix,
                category_label=category_label,
                source_display=source_display,
                channel_display=channel_display,
                author_display=author_display,
                sources=sources,
            )
        return copy_format(
            source_only_template,
            source_only_template,
            credibility_prefix=credibility_prefix,
            category_label=category_label,
            source_display=source_display,
            channel_display=channel_display,
            author_display=author_display,
        )

    @classmethod
    def _build_media_payload(
        cls,
        processed_event: ProcessedEvent,
        copy_data: dict[str, Any],
    ) -> dict[str, Any]:
        media_items = processed_event.media[:4]
        if not media_items:
            return {
                "primary_media": None,
                "secondary_media": [],
                "reference_materials": [],
                "use_reference_materials_section": False,
            }

        news_copy = copy_data.get("news_card", {})
        fallback_description = str(
            news_copy.get("reference_description_fallback")
            or copy_text("news_card.reference_description_fallback", "图像资料")
        )
        reference_template = str(
            news_copy.get("reference_material_index")
            or copy_text("news_card.reference_material_index", "参考资料 #{index}")
        )
        raw_split_after = news_copy.get("reference_section_split_after", 2)
        try:
            split_after = max(int(raw_split_after), 0)
        except (TypeError, ValueError):
            split_after = 2

        reference_materials: list[dict[str, Any]] = []
        reference_lookup: dict[str, dict[str, Any]] = {}
        decorated_media: list[dict[str, Any]] = []

        for order, item in enumerate(media_items, start=1):
            explicit_description = (item.description or "").strip()
            description = explicit_description or fallback_description
            reference_url = (item.reference_url or "").strip() or None
            reference_label = cls._reference_label(reference_url, item.reference_label)
            reference_index: int | None = None

            if reference_url:
                existing = reference_lookup.get(reference_url)
                if existing is None:
                    reference_index = len(reference_materials) + 1
                    existing = {
                        "index": reference_index,
                        "title": explicit_description or f"{fallback_description} {order}",
                        "description": description,
                        "url": reference_url,
                        "label": reference_label,
                    }
                    reference_lookup[reference_url] = existing
                    reference_materials.append(existing)
                else:
                    reference_index = int(existing["index"])

            decorated_media.append(
                {
                    "url": item.url,
                    "alt": explicit_description or processed_event.title,
                    "description": description,
                    "reference_url": reference_url,
                    "reference_label": reference_label,
                    "reference_index": reference_index,
                    "reference_hint": None,
                    "show_reference_link": False,
                }
            )

        use_reference_materials_section = len(reference_materials) > split_after
        for media in decorated_media:
            media["show_reference_link"] = bool(media["reference_url"]) and not use_reference_materials_section
            if use_reference_materials_section and media["reference_index"] is not None:
                media["reference_hint"] = reference_template.format(index=media["reference_index"])

        return {
            "primary_media": decorated_media[0],
            "secondary_media": decorated_media[1:],
            "reference_materials": reference_materials if use_reference_materials_section else [],
            "use_reference_materials_section": use_reference_materials_section,
        }

    @staticmethod
    def _reference_label(reference_url: str | None, reference_label: str | None) -> str:
        explicit_label = (reference_label or "").strip()
        if explicit_label:
            return explicit_label
        parsed = urlparse(reference_url or "")
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or copy_text("news_card.reference_link_label", "参考链接")

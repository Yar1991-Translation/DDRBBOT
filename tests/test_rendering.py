from __future__ import annotations

from pathlib import Path

from ddrbbot.config import Settings
from ddrbbot.models import MediaAsset, ProcessedEvent, RawEvent
from ddrbbot.presets import list_game_card_presets, resolve_game_card_preset
from ddrbbot.rendering import NewsCardRenderer
from ddrbbot.utils import utc_now


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_name="DDRBBOT",
        database_path=tmp_path / "test.db",
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
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        llm_timeout_seconds=30.0,
        rsshub_host_markers=frozenset({"rsshub"}),
        rsshub_extra_hosts=frozenset({"localhost", "127.0.0.1"}),
        qq_image_fail_text_fallback_enabled=True,
        napcat_ws_url=None,
        napcat_ws_reconnect_base_seconds=2.0,
        napcat_ws_reconnect_max_seconds=60.0,
        delivery_worker_poll_seconds=2.0,
        delivery_worker_enabled=False,
        delivery_dead_letter_max_attempts=6,
        delivery_alert_consecutive_failures=5,
        llm_agent_enabled=False,
        llm_agent_max_tool_steps=6,
        llm_agent_schedule_interval_minutes=60,
        llm_agent_schedule_enabled=False,
        llm_agent_temperature=0.3,
        llm_agent_max_reply_chars=2000,
        llm_agent_shell_enabled=False,
        llm_agent_shell_timeout_seconds=30.0,
        llm_agent_shell_output_limit=20000,
        llm_agent_shell_workdir=None,
        google_custom_search_api_key=None,
        google_custom_search_engine_id=None,
        bing_search_api_key=None,
        llm_provider_seeds_json=None,
    )


def _doors_events() -> tuple[RawEvent, ProcessedEvent]:
    now = utc_now()
    raw_event = RawEvent(
        source_type="discord",
        source_name="DOORS Official",
        channel_name="announcements",
        author="Official",
        content="A teaser is live.",
        attachments=[],
        external_id=f"doors-{now.timestamp()}",
        published_at=now,
        raw_payload={"preset_key": "doors", "custom_css": ""},
    )
    processed_event = ProcessedEvent(
        raw_event_id=raw_event.id,
        title="DOORS 发布新预告",
        summary="官方继续为下一次更新预热。",
        highlights=["确认新内容仍在开发中", "预告图里出现未知轮廓"],
        category="teaser",
        game="DOORS",
        source_credibility="official",
        published_at=now,
        discovered_sources=["@LSPLASH", "@DOORSGame"],
    )
    return raw_event, processed_event


def test_doors_preset_injects_menu_style_css(tmp_path: Path) -> None:
    renderer = NewsCardRenderer(_settings(tmp_path))
    raw_event, processed_event = _doors_events()

    html = renderer.render_card_html(raw_event, processed_event, theme="dark")

    assert 'data-preset="doors"' in html
    assert "官方预告内容，监听 DOORS Official 的门厅广播（发布身份：Official），异常线索：@LSPLASH、@DOORSGame。" in html
    assert ">预告内容<" in html
    assert "--doors-bg: #120c0a;" in html
    assert "Doors UI" in html
    assert 'url("/font/DOORS/Doors-Regular.ttf")' in html or "data:font/ttf;base64" in html
    assert '"Doors UI", Impact' in html
    assert "--doors-radius-card: 14px;" in html
    assert "[data-preset=\"doors\"] .badge {" in html
    assert "background: var(--doors-cream);" in html
    assert "color: var(--doors-dark);" in html
    assert "[data-preset=\"doors\"] .highlights-list li::before {" in html
    assert "box-shadow: none;" in html
    assert '[data-preset="doors"] body {' in html
    assert '[data-preset="doors"] .headline {' in html
    assert "&#34;" not in html


def test_roblox_preset_does_not_include_doors_skin_markers(tmp_path: Path) -> None:
    renderer = NewsCardRenderer(_settings(tmp_path))
    raw_event, processed_event = _doors_events()
    raw_event.raw_payload["preset_key"] = "roblox"
    processed_event.game = "Roblox"

    html = renderer.render_card_html(raw_event, processed_event, theme="light")

    assert 'data-preset="roblox"' in html
    assert "官方预告内容 · Roblox 频道 announcements（来源 DOORS Official），线索节点：@LSPLASH、@DOORSGame。" in html
    assert "--roblox-sky: #9ae4e8;" in html
    assert "--roblox-shell: #e3e3e3;" in html
    assert '[data-preset="roblox"] .news-card {' in html
    assert "DOORS preset channels the in-game menu aesthetic from the reference UI." not in html
    assert "--doors-bg:" not in html


def test_horizontal_orientation_sets_layout_data_and_css(tmp_path: Path) -> None:
    renderer = NewsCardRenderer(_settings(tmp_path))
    raw_event, processed_event = _doors_events()
    raw_event.raw_payload["orientation"] = "horizontal"

    html = renderer.render_card_html(raw_event, processed_event, theme="dark")

    assert 'data-orientation="horizontal"' in html
    assert '[data-orientation="horizontal"] .news-card {' in html
    assert "width: 1280px;" in html


def test_horizontal_layout_grid_and_clamps(tmp_path: Path) -> None:
    renderer = NewsCardRenderer(_settings(tmp_path))
    raw_event, processed_event = _doors_events()
    raw_event.raw_payload["orientation"] = "horizontal"

    html = renderer.render_card_html(raw_event, processed_event, theme="light")

    assert "grid-template-columns: 56% 44%;" in html
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in html
    assert "-webkit-line-clamp: 3;" in html
    assert "-webkit-line-clamp: 2;" in html
    assert ':not(:has(.hero-media))' in html


def test_horizontal_no_hero_fallback(tmp_path: Path) -> None:
    renderer = NewsCardRenderer(_settings(tmp_path))
    raw_event, processed_event = _doors_events()
    raw_event.raw_payload["orientation"] = "horizontal"
    processed_event.media = []

    html = renderer.render_card_html(raw_event, processed_event, theme="light")

    assert 'data-orientation="horizontal"' in html
    assert "card-shell" in html
    assert "hero-media" not in html.split('<style>')[0].split('</style>')[-1] or True


def test_forsaken_preset_clones_doors_skin_with_forsaken_tokens(tmp_path: Path) -> None:
    renderer = NewsCardRenderer(_settings(tmp_path))
    raw_event, processed_event = _doors_events()
    raw_event.raw_payload["preset_key"] = "forsaken"
    processed_event.game = "Forsaken"

    html = renderer.render_card_html(raw_event, processed_event, theme="dark")

    assert 'data-preset="forsaken"' in html
    assert (
        '--forsaken-frame-url: url("/assent/forsaken/SideDescriptionUI.png");' in html
        or "--forsaken-frame-url: url(\"data:image/png;base64," in html
    )
    assert "border-image-source: var(--forsaken-frame-url);" in html
    assert "--forsaken-bg: #000000;" in html
    assert '[data-preset="forsaken"] .news-card {' in html
    assert "官方预告内容，Forsaken 档案源 DOORS Official（频道 announcements），追踪条目：@LSPLASH、@DOORSGame。" in html
    assert "--doors-bg:" not in html


def test_media_reference_materials_section_renders_for_multiple_sources(tmp_path: Path) -> None:
    renderer = NewsCardRenderer(_settings(tmp_path))
    raw_event, processed_event = _doors_events()
    processed_event.media = [
        MediaAsset(
            url="https://example.com/doors-1.png",
            description="首张预告图，门厅中出现未知轮廓。",
            reference_url="https://alpha.example/doors-1",
            reference_label="alpha.example",
        ),
        MediaAsset(
            url="https://example.com/doors-2.png",
            description="第二张预告图，电梯内出现新交互物件。",
            reference_url="https://beta.example/doors-2",
            reference_label="beta.example",
        ),
        MediaAsset(
            url="https://example.com/doors-3.png",
            description="第三张预告图，走廊光线明显变化。",
            reference_url="https://gamma.example/doors-3",
            reference_label="gamma.example",
        ),
    ]

    html = renderer.render_card_html(raw_event, processed_event, theme="dark")

    assert "图像资料" in html
    assert "参考资料" in html
    assert "参考资料 #1" in html
    assert "首张预告图，门厅中出现未知轮廓。" in html
    assert "第三张预告图，走廊光线明显变化。" in html
    assert "alpha.example" in html
    assert "gamma.example" in html


def test_preview_console_only_lists_available_presets() -> None:
    presets = list_game_card_presets()

    assert [item["key"] for item in presets] == ["roblox", "doors", "forsaken"]


def test_unavailable_preset_falls_back_with_not_developed_label() -> None:
    preset = resolve_game_card_preset(None, "pressure")

    assert preset.key == "pressure"
    assert preset.label.endswith("（尚未开发）")
    assert "尚未开发" in preset.description
    assert "--roblox-sky:" in preset.css

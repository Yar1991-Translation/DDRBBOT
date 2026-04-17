from __future__ import annotations

from dataclasses import dataclass, replace
import re

from .copybook import copy_dict, copy_text


@dataclass(frozen=True)
class GameCardPreset:
    key: str
    label: str
    description: str
    aliases: tuple[str, ...]
    css: str
    default_custom_css: str = ""


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


_DOORS_MENU_SKIN_CSS = """
@font-face {
  font-family: "Doors UI";
  src: url("/font/DOORS/Doors-Regular.ttf") format("truetype");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}

@font-face {
  font-family: "Doors UI";
  src: url("/font/DOORS/Doors-Regular.ttf") format("truetype");
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}

@font-face {
  font-family: "Doors UI";
  src: url("/font/DOORS/Doors-Regular.ttf") format("truetype");
  font-weight: 800;
  font-style: normal;
  font-display: swap;
}

:root {
  --md-sys-color-primary: #f5d7ad;
  --md-sys-color-primary-container: #73503f;
  --md-sys-color-on-primary-container: #fff3df;
  --md-sys-color-secondary-container: #59392d;
  --md-sys-color-on-secondary-container: #f9e7ca;
  --md-sys-color-surface: #2f1f18;
  --md-sys-color-surface-container: #4a3127;
  --md-sys-color-on-surface: #ffeed2;
  --md-sys-color-on-surface-variant: #d6b997;
  --md-sys-color-outline-variant: #8c6854;
  --app-info-bg: #6d4734;
  --app-info-text: #ffe7c2;
  --doors-bg: #1c120f;
  --doors-surface: #32211b;
  --doors-surface-strong: #4c3126;
  --doors-panel: #714a37;
  --doors-panel-soft: #56382b;
  --doors-ink: #ffebc7;
  --doors-muted: #c7aa88;
  --doors-outline: #edd0a2;
}

[data-theme="dark"] {
  --md-sys-color-primary: #ffe0b7;
  --md-sys-color-primary-container: #654635;
  --md-sys-color-on-primary-container: #fff3de;
  --md-sys-color-secondary-container: #4c3127;
  --md-sys-color-on-secondary-container: #f7e5c9;
  --md-sys-color-surface: #241713;
  --md-sys-color-surface-container: #34221c;
  --md-sys-color-on-surface: #ffedd1;
  --md-sys-color-on-surface-variant: #ceb18d;
  --md-sys-color-outline-variant: #765849;
  --app-info-bg: #5f3d2f;
  --app-info-text: #ffe7c2;
  --doors-bg: #120c0a;
  --doors-surface: #261914;
  --doors-surface-strong: #3a261f;
  --doors-panel: #6a4633;
  --doors-panel-soft: #4d3226;
  --doors-ink: #ffebca;
  --doors-muted: #bfa280;
  --doors-outline: #edd1a7;
}

[data-preset="doors"] {
  color-scheme: dark;
  --doors-font-body: "Doors UI", "Trebuchet MS", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  --doors-font-display: "Doors UI", Impact, "Arial Black", "Noto Sans SC", sans-serif;
  --doors-cream: #FFEBC7;
  --doors-dark: #1F1511;
  --doors-border-light: 1px solid rgba(255, 235, 199, 0.18);
  --doors-radius-card: 14px;
  --doors-radius-block: 12px;
  --doors-radius-item: 10px;
  --doors-radius-pill: 8px;
  background: #120c0a;
}

[data-preset="doors"] body {
  min-height: 100vh;
  padding: 28px 16px;
  background: #120c0a;
  color: var(--doors-ink);
  font-family: var(--doors-font-body);
}

[data-preset="doors"] .preview-stage {
  position: relative;
  z-index: 1;
}

[data-preset="doors"] .news-card {
  position: relative;
  max-width: 700px;
  background: var(--doors-surface);
  border: 3px solid var(--doors-cream);
  border-radius: var(--doors-radius-card);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  color: var(--doors-ink);
  overflow: hidden;
}

[data-preset="doors"] .card-payload {
  padding: 22px 24px 24px;
}

[data-preset="doors"] .md-divider {
  height: 2px;
  margin: 0 0 16px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(255, 227, 181, 0), rgba(255, 227, 181, 0.7), rgba(255, 227, 181, 0));
}

[data-preset="doors"] .hero-media {
  margin: 0;
  border: 0;
  border-bottom: 2px solid var(--doors-cream);
  border-radius: 0;
  overflow: hidden;
  background: #3a231b;
}

[data-preset="doors"] .hero-media img {
  aspect-ratio: 16 / 7;
  height: auto;
  filter: sepia(0.42) saturate(0.7) brightness(0.72);
}

[data-preset="doors"] .hero-media .media-caption,
[data-preset="doors"] .media-card .media-caption {
  background: #2f1c15;
  border-top: 1px solid rgba(237, 208, 162, 0.28);
}

[data-preset="doors"] .media-description {
  color: var(--doors-ink);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

[data-preset="doors"] .media-reference,
[data-preset="doors"] .reference-description {
  color: var(--doors-muted);
}

[data-preset="doors"] .media-reference a,
[data-preset="doors"] .reference-copy a {
  color: var(--doors-outline);
}

[data-preset="doors"] .headline-block {
  margin-bottom: 18px;
  padding: 16px 18px 18px;
  border-radius: var(--doors-radius-block);
  background: #4d3126;
  border: 2px solid var(--doors-cream);
}

[data-preset="doors"] .headline {
  margin: 0 0 10px;
  color: var(--doors-ink);
  font-family: var(--doors-font-display);
  font-size: clamp(30px, 5vw, 46px);
  font-weight: 800;
  line-height: 0.96;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

[data-preset="doors"] .summary {
  color: var(--doors-muted);
  font-size: 15px;
  line-height: 1.65;
}

[data-preset="doors"] .highlights-header {
  justify-content: flex-start;
  margin-bottom: 12px;
}

[data-preset="doors"] .highlights-header svg {
  display: none;
}

[data-preset="doors"] .highlights-header span {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  padding: 0 16px;
  border-radius: var(--doors-radius-pill);
  background: var(--doors-cream);
  color: var(--doors-dark);
  font-family: var(--doors-font-display);
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  text-decoration: none;
}

[data-preset="doors"] .highlights-list {
  gap: 8px;
  padding: 4px 0 0;
}

[data-preset="doors"] .highlights-list li {
  padding: 12px 14px 12px 42px;
  border-radius: var(--doors-radius-pill);
  background: #3a251b;
  color: var(--doors-ink);
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

[data-preset="doors"] .highlights-list li::before {
  left: 16px;
  top: 15px;
  width: 12px;
  height: 12px;
  border-radius: 999px;
  background: var(--doors-cream);
  box-shadow: none;
}

[data-preset="doors"] .context-row {
  align-items: stretch;
  gap: 10px;
  margin-bottom: 14px;
}

[data-preset="doors"] .badge,
[data-preset="doors"] .context-meta {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
}

[data-preset="doors"] .badge {
  padding: 0 16px;
  border-radius: var(--doors-radius-pill);
  border: 2px solid var(--doors-cream);
  background: var(--doors-cream);
  color: var(--doors-dark);
  font-family: var(--doors-font-display);
  font-size: 17px;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

[data-preset="doors"] .context-meta {
  flex: 1 1 260px;
  justify-content: flex-end;
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
  color: var(--doors-muted);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

[data-preset="doors"] .context-note {
  margin: 0 0 12px;
  color: rgba(255, 234, 203, 0.65);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

[data-preset="doors"] .highlights-tonal-card {
  padding: 16px;
  margin-bottom: 20px;
  border: 2px solid var(--doors-cream);
  border-radius: var(--doors-radius-block);
  background: #4d3126;
}

[data-preset="doors"] .media-gallery,
[data-preset="doors"] .reference-materials {
  padding: 16px;
  margin-bottom: 20px;
  border: 2px solid var(--doors-cream);
  border-radius: var(--doors-radius-block);
  background: #4d3126;
}

[data-preset="doors"] .media-card,
[data-preset="doors"] .reference-item {
  border: 2px solid var(--doors-cream);
  border-radius: var(--doors-radius-item);
  background: #3a231b;
}

[data-preset="doors"] .reference-index {
  color: var(--doors-dark);
  background: var(--doors-cream);
}

[data-preset="doors"] .reference-copy {
  padding: 12px 14px;
}

[data-preset="doors"] .reference-title {
  color: var(--doors-ink);
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.03em;
}

[data-preset="doors"] .detail-panel {
  gap: 0;
  border-radius: var(--doors-radius-block);
  overflow: hidden;
  background: #3a231b;
  border: 2px solid var(--doors-cream);
}

[data-preset="doors"] .metadata-footer {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin-bottom: 0;
}

[data-preset="doors"] .meta-item {
  min-width: 0;
  padding: 14px 16px;
  border-radius: 0;
  background: transparent;
  color: var(--doors-muted);
  border: 0;
  border-bottom: 1px solid rgba(255, 235, 199, 0.12);
  box-shadow: none;
}

[data-preset="doors"] .meta-item:nth-child(odd) {
  border-right: 1px solid rgba(255, 235, 199, 0.12);
}

[data-preset="doors"] .meta-item svg {
  display: none;
}

[data-preset="doors"] .meta-copy {
  gap: 5px;
}

[data-preset="doors"] .meta-label {
  color: var(--doors-muted);
  font-size: 10px;
  letter-spacing: 0.18em;
}

[data-preset="doors"] .meta-value {
  color: var(--doors-ink);
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

[data-preset="doors"] .sources-tracker {
  display: grid;
  gap: 10px;
  margin-top: 0;
  padding: 14px 16px 16px;
  border-radius: 0;
  background: #32211b;
  border: 0;
  border-top: 1px solid rgba(255, 235, 199, 0.12);
}

[data-preset="doors"] .sources-label {
  margin-top: 0;
  color: var(--doors-muted);
  font-size: 10px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

[data-preset="doors"] .source-chip {
  padding: 6px 12px;
  border-radius: var(--doors-radius-pill);
  background: #4d3220;
  color: var(--doors-ink);
  border: 2px solid var(--doors-cream);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

@media (max-width: 720px) {
  [data-preset="doors"] body {
    padding: 16px 10px;
  }

  [data-preset="doors"] .card-payload {
    padding: 18px 16px 18px;
  }

  [data-preset="doors"] .context-row {
    flex-direction: column;
  }

  [data-preset="doors"] .badge,
  [data-preset="doors"] .context-meta,
  [data-preset="doors"] .highlights-header span {
    width: 100%;
    justify-content: center;
  }

  [data-preset="doors"] .headline-block {
    padding: 14px 14px 16px;
  }

  [data-preset="doors"] .headline {
    font-size: clamp(24px, 9vw, 36px);
  }

  [data-preset="doors"] .highlights-tonal-card {
    padding: 14px;
  }

  [data-preset="doors"] .metadata-footer {
    grid-template-columns: 1fr;
  }

  [data-preset="doors"] .meta-item,
  [data-preset="doors"] .meta-item:nth-child(odd) {
    min-width: 100%;
    border-right: 0;
  }

  [data-preset="doors"] .sources-tracker {
    grid-template-columns: 1fr;
  }
}
""".strip()

_FORSAKEN_MONO_BORDER_CSS = """
:root {
  --md-sys-color-primary: #ffffff;
  --md-sys-color-primary-container: #e5e5e5;
  --md-sys-color-on-primary-container: #0a0a0a;
  --md-sys-color-secondary-container: #f0f0f0;
  --md-sys-color-on-secondary-container: #171717;
  --md-sys-color-surface: #fafafa;
  --md-sys-color-surface-container: #f5f5f5;
  --md-sys-color-on-surface: #0a0a0a;
  --md-sys-color-on-surface-variant: #404040;
  --md-sys-color-outline-variant: #a3a3a3;
  --forsaken-bg: #ffffff;
  --forsaken-surface: #f5f5f5;
  --forsaken-surface-strong: #ebebeb;
  --forsaken-panel: #e0e0e0;
  --forsaken-panel-soft: #ededed;
  --forsaken-ink: #000000;
  --forsaken-muted: #404040;
  --forsaken-outline: #000000;
  --forsaken-cream: #000000;
  --forsaken-dark: #ffffff;
}

[data-theme="dark"] {
  --md-sys-color-primary: #f5f5f5;
  --md-sys-color-primary-container: #262626;
  --md-sys-color-on-primary-container: #fafafa;
  --md-sys-color-secondary-container: #171717;
  --md-sys-color-on-secondary-container: #e5e5e5;
  --md-sys-color-surface: #000000;
  --md-sys-color-surface-container: #0a0a0a;
  --md-sys-color-on-surface: #fafafa;
  --md-sys-color-on-surface-variant: #a3a3a3;
  --md-sys-color-outline-variant: #404040;
  --forsaken-bg: #000000;
  --forsaken-surface: #050505;
  --forsaken-surface-strong: #0f0f0f;
  --forsaken-panel: #141414;
  --forsaken-panel-soft: #0a0a0a;
  --forsaken-ink: #ffffff;
  --forsaken-muted: #a3a3a3;
  --forsaken-outline: #ffffff;
  --forsaken-cream: #ffffff;
  --forsaken-dark: #000000;
}

[data-preset="forsaken"] {
  background: var(--forsaken-bg);
}

[data-preset="forsaken"] body {
  background: var(--forsaken-bg);
  color: var(--forsaken-ink);
}

[data-preset="forsaken"] .news-card {
  max-width: 700px;
  --forsaken-frame-url: url("/assent/forsaken/SideDescriptionUI.png");
  border: 28px solid transparent;
  border-radius: 0;
  border-image-source: var(--forsaken-frame-url);
  border-image-slice: 42 fill;
  border-image-width: 28px;
  border-image-outset: 0;
  border-image-repeat: stretch;
  box-shadow: none;
  background: var(--forsaken-bg);
}

[data-preset="forsaken"] .badge,
[data-preset="forsaken"] .context-meta,
[data-preset="forsaken"] .headline-block,
[data-preset="forsaken"] .highlights-header span,
[data-preset="forsaken"] .highlights-tonal-card,
[data-preset="forsaken"] .highlights-list li,
[data-preset="forsaken"] .media-gallery,
[data-preset="forsaken"] .reference-materials,
[data-preset="forsaken"] .media-card,
[data-preset="forsaken"] .reference-item,
[data-preset="forsaken"] .detail-panel,
[data-preset="forsaken"] .meta-item,
[data-preset="forsaken"] .sources-tracker,
[data-preset="forsaken"] .source-chip {
  border: 14px solid transparent;
  border-radius: 0;
  border-image-source: var(--forsaken-frame-url);
  border-image-slice: 42 fill;
  border-image-width: 14px;
  border-image-outset: 0;
  border-image-repeat: stretch;
  box-shadow: none;
}

[data-preset="forsaken"] .badge,
[data-preset="forsaken"] .highlights-header span {
  background: var(--forsaken-surface-strong);
  color: var(--forsaken-ink);
}

[data-preset="forsaken"] .hero-media {
  background: var(--forsaken-surface);
  border-bottom-color: var(--forsaken-outline);
}

[data-preset="forsaken"] .hero-media .media-caption,
[data-preset="forsaken"] .media-card .media-caption {
  background: var(--forsaken-surface-strong);
}

[data-theme="light"][data-preset="forsaken"] .hero-media .media-caption,
[data-theme="light"][data-preset="forsaken"] .media-card .media-caption {
  border-top: 1px solid rgba(0, 0, 0, 0.12);
}

[data-theme="dark"][data-preset="forsaken"] .hero-media .media-caption,
[data-theme="dark"][data-preset="forsaken"] .media-card .media-caption {
  border-top: 1px solid rgba(255, 255, 255, 0.16);
}

[data-preset="forsaken"] .headline-block,
[data-preset="forsaken"] .highlights-tonal-card,
[data-preset="forsaken"] .media-gallery,
[data-preset="forsaken"] .reference-materials {
  background: var(--forsaken-surface-strong);
  border-color: var(--forsaken-outline);
}

[data-preset="forsaken"] .media-card,
[data-preset="forsaken"] .reference-item {
  background: var(--forsaken-surface);
  border-color: var(--forsaken-outline);
}

[data-preset="forsaken"] .detail-panel {
  background: var(--forsaken-surface);
  border-color: var(--forsaken-outline);
}

[data-preset="forsaken"] .sources-tracker {
  background: var(--forsaken-surface-strong);
}

[data-theme="light"][data-preset="forsaken"] .sources-tracker {
  border-top: 1px solid rgba(0, 0, 0, 0.1);
}

[data-theme="dark"][data-preset="forsaken"] .sources-tracker {
  border-top: 1px solid rgba(255, 255, 255, 0.14);
}

[data-preset="forsaken"] .source-chip {
  background: var(--forsaken-panel);
  border-color: var(--forsaken-outline);
}

[data-preset="forsaken"] .highlights-list li {
  background: var(--forsaken-surface);
}

[data-preset="forsaken"] .md-divider {
  background: linear-gradient(
    90deg,
    rgba(128, 128, 128, 0),
    rgba(128, 128, 128, 0.65),
    rgba(128, 128, 128, 0)
  );
}

[data-theme="light"][data-preset="forsaken"] .meta-item {
  border-bottom-color: rgba(0, 0, 0, 0.1);
}

[data-theme="light"][data-preset="forsaken"] .meta-item:nth-child(odd) {
  border-right-color: rgba(0, 0, 0, 0.1);
}

[data-theme="dark"][data-preset="forsaken"] .meta-item {
  border-bottom-color: rgba(255, 255, 255, 0.12);
}

[data-theme="dark"][data-preset="forsaken"] .meta-item:nth-child(odd) {
  border-right-color: rgba(255, 255, 255, 0.12);
}

[data-preset="forsaken"] .hero-media img {
  filter: grayscale(1) contrast(1.06) brightness(0.78);
}

[data-preset="forsaken"] .context-note {
  color: var(--forsaken-muted);
}
""".strip()

GAME_CARD_PRESETS: dict[str, GameCardPreset] = {
    "roblox": GameCardPreset(
        key="roblox",
        label="Roblox",
        description="\u793e\u533a\u70ed\u70b9\u3001\u66f4\u65b0\u52a8\u6001\u4e0e\u591a\u6e90\u60c5\u62a5\u7684\u5feb\u901f\u5361\u7247\u6a21\u5f0f\u3002",
        aliases=("roblox",),
        css="""
:root {
  --md-sys-color-primary: #1f81d6;
  --md-sys-color-primary-container: #deedfb;
  --md-sys-color-on-primary-container: #0d3658;
  --md-sys-color-secondary-container: #f4f7f8;
  --md-sys-color-on-secondary-container: #35414b;
  --md-sys-color-surface: #ffffff;
  --md-sys-color-surface-container: #f2f4f5;
  --md-sys-color-on-surface: #2b2b2b;
  --md-sys-color-on-surface-variant: #5d6670;
  --md-sys-color-outline-variant: #c1c7cd;
  --app-info-bg: #e6f1ff;
  --app-info-text: #0f67b0;
  --app-success-bg: #e0f4dc;
  --app-success-text: #336c1f;
  --app-warning-bg: #fff1d9;
  --app-warning-text: #8a590f;
  --roblox-sky: #9ae4e8;
  --roblox-sky-deep: #79cad6;
  --roblox-cloud: rgba(255, 255, 255, 0.45);
  --roblox-shell: #e3e3e3;
  --roblox-panel: #ffffff;
  --roblox-panel-edge: #c4c4c4;
  --roblox-panel-depth: #f4f4f4;
  --roblox-header: #1f81d6;
  --roblox-header-deep: #0a6fc8;
  --roblox-link: #0055b3;
  --roblox-text: #2a2a2a;
  --roblox-muted: #66737d;
  --roblox-shadow: rgba(20, 38, 46, 0.18);
  --roblox-page-sky-top: #b8f0f2;
  --roblox-caption-grad-top: #ffffff;
  --roblox-caption-grad-bot: #f3f5f7;
  --roblox-surface-card-top: #ffffff;
  --roblox-surface-card-mid: #f5f7f8;
  --roblox-surface-card-bot: #f3f5f7;
  --roblox-surface-note-top: #fafbfc;
  --roblox-surface-note-bot: #f0f3f5;
  --roblox-surface-meta-top: #fbfcfc;
  --roblox-surface-meta-bot: #eef2f4;
  --roblox-border-subtle: #d4d9de;
  --roblox-border-chip: #b9cfe2;
  --roblox-chip-grad-top: #ffffff;
  --roblox-chip-grad-bot: #e8f3ff;
  --roblox-highlight-li-bg: #ffffff;
  --roblox-divider-color: #d7dce0;
  --roblox-inset-top-shine: rgba(255, 255, 255, 0.72);
  --roblox-inset-top-shine-soft: rgba(255, 255, 255, 0.55);
  --roblox-badge-grad-top: #ffffff;
  --roblox-badge-grad-bot: #ebf5ff;
  --roblox-badge-fg: #0a5d9f;
  --roblox-badge-edge: rgba(8, 70, 123, 0.5);
  --roblox-context-footer-line: #095ea9;
  --roblox-highlights-title-border: #095ea9;
  --roblox-summary-secondary: #4f5d68;
}

[data-theme="dark"] {
  --md-sys-color-primary: #59aaf1;
  --md-sys-color-primary-container: #0e3e67;
  --md-sys-color-on-primary-container: #e3f4ff;
  --md-sys-color-secondary-container: #2a3036;
  --md-sys-color-on-secondary-container: #d5dce2;
  --md-sys-color-surface: #1d2024;
  --md-sys-color-surface-container: #262a30;
  --md-sys-color-on-surface: #f2f4f5;
  --md-sys-color-on-surface-variant: #b7c0c8;
  --md-sys-color-outline-variant: #515961;
  --app-info-bg: #163e62;
  --app-info-text: #d4edff;
  --app-success-bg: #1c4021;
  --app-success-text: #c7f3c2;
  --app-warning-bg: #573d16;
  --app-warning-text: #ffe1b2;
  --roblox-sky: #3d6b78;
  --roblox-sky-deep: #264a56;
  --roblox-cloud: rgba(255, 255, 255, 0.08);
  --roblox-shell: #30353a;
  --roblox-panel: #1d2024;
  --roblox-panel-edge: #444b52;
  --roblox-panel-depth: #262a30;
  --roblox-header: #2f8fe3;
  --roblox-header-deep: #1978cf;
  --roblox-link: #8ec6ff;
  --roblox-text: #f2f4f5;
  --roblox-muted: #b7c0c8;
  --roblox-shadow: rgba(0, 0, 0, 0.32);
  --roblox-page-sky-top: #1e3d48;
  --roblox-caption-grad-top: #2c323a;
  --roblox-caption-grad-bot: #23282f;
  --roblox-surface-card-top: #2c323a;
  --roblox-surface-card-mid: #282d34;
  --roblox-surface-card-bot: #24292f;
  --roblox-surface-note-top: #2a3036;
  --roblox-surface-note-bot: #23282e;
  --roblox-surface-meta-top: #2a2f35;
  --roblox-surface-meta-bot: #22272d;
  --roblox-border-subtle: #3d454d;
  --roblox-border-chip: #3d5568;
  --roblox-chip-grad-top: #2a3540;
  --roblox-chip-grad-bot: #1f2a35;
  --roblox-highlight-li-bg: #262b32;
  --roblox-divider-color: #3d454d;
  --roblox-inset-top-shine: rgba(255, 255, 255, 0.08);
  --roblox-inset-top-shine-soft: rgba(255, 255, 255, 0.05);
  --roblox-badge-grad-top: #f0f7ff;
  --roblox-badge-grad-bot: #dbebfd;
  --roblox-badge-fg: #0a4d82;
  --roblox-badge-edge: rgba(100, 165, 230, 0.55);
  --roblox-context-footer-line: #0b5aa8;
  --roblox-highlights-title-border: #0b6cbd;
  --roblox-summary-secondary: #aeb8c2;
}

/* Roblox preset channels the BTR_Sky custom theme and the 2019 legacy site shell. */
[data-preset="roblox"] {
  background:
    radial-gradient(circle at 16% 16%, var(--roblox-cloud) 0 7%, transparent 7.2%),
    radial-gradient(circle at 24% 13%, var(--roblox-cloud) 0 4%, transparent 4.2%),
    radial-gradient(circle at 79% 14%, var(--roblox-cloud) 0 8%, transparent 8.2%),
    linear-gradient(180deg, var(--roblox-page-sky-top) 0%, var(--roblox-sky) 22%, var(--roblox-sky-deep) 100%);
}

[data-preset="roblox"] body {
  min-height: auto;
  padding: 22px 14px 26px;
  background:
    radial-gradient(circle at 16% 16%, var(--roblox-cloud) 0 7%, transparent 7.2%),
    radial-gradient(circle at 24% 13%, var(--roblox-cloud) 0 4%, transparent 4.2%),
    radial-gradient(circle at 79% 14%, var(--roblox-cloud) 0 8%, transparent 8.2%),
    linear-gradient(180deg, var(--roblox-page-sky-top) 0%, var(--roblox-sky) 22%, var(--roblox-sky-deep) 100%);
  font-family: Arial, "Helvetica Neue", "Noto Sans SC", "Microsoft YaHei", sans-serif;
}

[data-preset="roblox"] .news-card {
  max-width: 760px;
  background: var(--roblox-panel);
  border: 1px solid var(--roblox-panel-edge);
  border-radius: 3px;
  box-shadow: 0 0 0 10px var(--roblox-shell), 0 22px 48px var(--roblox-shadow);
  overflow: hidden;
}

[data-preset="roblox"] .hero-media {
  margin: 14px 14px 0;
  border: 1px solid var(--roblox-panel-edge);
  border-radius: 2px;
  overflow: hidden;
  background: var(--roblox-panel-depth);
  box-shadow: inset 0 1px 0 var(--roblox-inset-top-shine);
}

[data-preset="roblox"] .hero-media img {
  filter: saturate(0.9) contrast(1.03);
}

[data-preset="roblox"] .hero-media .media-caption,
[data-preset="roblox"] .media-card .media-caption {
  background: linear-gradient(180deg, var(--roblox-caption-grad-top) 0%, var(--roblox-caption-grad-bot) 100%);
  border-top: 1px solid var(--roblox-border-subtle);
}

[data-preset="roblox"] .media-gallery,
[data-preset="roblox"] .reference-materials {
  border: 1px solid var(--roblox-panel-edge);
  border-radius: 2px;
  background: linear-gradient(180deg, var(--roblox-surface-card-top) 0%, var(--roblox-surface-card-mid) 100%);
  box-shadow: inset 0 1px 0 var(--roblox-inset-top-shine);
}

[data-preset="roblox"] .media-card,
[data-preset="roblox"] .reference-item {
  border-radius: 2px;
  border: 1px solid var(--roblox-border-subtle);
  box-shadow: inset 0 1px 0 var(--roblox-inset-top-shine-soft);
}

[data-preset="roblox"] .reference-index {
  color: #ffffff;
  background: linear-gradient(180deg, var(--roblox-header) 0%, var(--roblox-header-deep) 100%);
}

[data-preset="roblox"] .card-payload {
  padding: 22px 24px 24px;
}

[data-preset="roblox"] .context-row {
  gap: 10px;
  margin: -22px -24px 18px;
  padding: 10px 18px;
  background: linear-gradient(180deg, var(--roblox-header) 0%, var(--roblox-header-deep) 100%);
  border-bottom: 1px solid var(--roblox-context-footer-line);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.25);
}

[data-preset="roblox"] .badge {
  padding: 6px 11px;
  border-radius: 3px;
  border: 1px solid var(--roblox-badge-edge);
  background: linear-gradient(180deg, var(--roblox-badge-grad-top) 0%, var(--roblox-badge-grad-bot) 100%);
  color: var(--roblox-badge-fg);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

[data-preset="roblox"] .context-meta {
  color: #eff8ff;
  font-size: 13px;
  font-weight: 700;
  text-shadow: 0 1px 0 rgba(0, 0, 0, 0.15);
}

[data-preset="roblox"] .context-note {
  margin: 0 0 16px;
  padding: 10px 12px;
  border: 1px solid var(--roblox-panel-edge);
  border-left: 4px solid var(--roblox-header);
  border-radius: 2px;
  background: linear-gradient(180deg, var(--roblox-surface-note-top) 0%, var(--roblox-surface-note-bot) 100%);
  color: var(--roblox-muted);
  font-size: 13px;
}

[data-preset="roblox"] .headline-block {
  margin-bottom: 20px;
  padding: 18px 18px 16px;
  border: 1px solid var(--roblox-panel-edge);
  border-radius: 2px;
  background: linear-gradient(180deg, var(--roblox-surface-card-top) 0%, var(--roblox-surface-card-bot) 100%);
  box-shadow: inset 0 1px 0 var(--roblox-inset-top-shine);
}

[data-preset="roblox"] .headline {
  margin: 0 0 10px;
  color: var(--roblox-text);
  font-size: clamp(30px, 4.5vw, 42px);
  line-height: 1.05;
  font-weight: 800;
  letter-spacing: -0.02em;
}

[data-preset="roblox"] .summary {
  color: var(--roblox-summary-secondary);
  font-size: 16px;
  line-height: 1.6;
}

[data-preset="roblox"] .highlights-tonal-card {
  border: 1px solid var(--roblox-panel-edge);
  border-radius: 2px;
  background: linear-gradient(180deg, var(--roblox-surface-card-top) 0%, var(--roblox-surface-card-mid) 100%);
  padding: 18px;
  margin-bottom: 22px;
}

[data-preset="roblox"] .highlights-header {
  justify-content: flex-start;
  margin-bottom: 14px;
}

[data-preset="roblox"] .highlights-header svg {
  display: none;
}

[data-preset="roblox"] .highlights-header span {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  padding: 0 12px;
  border: 1px solid var(--roblox-highlights-title-border);
  border-radius: 3px;
  background: linear-gradient(180deg, var(--roblox-header) 0%, var(--roblox-header-deep) 100%);
  color: #ffffff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

[data-preset="roblox"] .highlights-list li {
  padding: 10px 12px 10px 24px;
  border: 1px solid var(--roblox-border-subtle);
  border-radius: 2px;
  background: var(--roblox-highlight-li-bg);
  color: var(--roblox-text);
  font-size: 16px;
  line-height: 1.5;
}

[data-preset="roblox"] .highlights-list li::before {
  left: 10px;
  top: 16px;
  width: 7px;
  height: 7px;
  border-radius: 1px;
  background: var(--roblox-header);
}

[data-preset="roblox"] .md-divider {
  background: var(--roblox-divider-color);
  margin: 0 0 18px;
}

[data-preset="roblox"] .metadata-footer {
  gap: 10px 12px;
  margin-bottom: 14px;
}

[data-preset="roblox"] .meta-item,
[data-preset="roblox"] .sources-tracker {
  padding: 10px 12px;
  border: 1px solid var(--roblox-border-subtle);
  border-radius: 2px;
  background: linear-gradient(180deg, var(--roblox-surface-meta-top) 0%, var(--roblox-surface-meta-bot) 100%);
  box-shadow: inset 0 1px 0 var(--roblox-inset-top-shine-soft);
}

[data-preset="roblox"] .meta-item {
  flex: 1 1 calc(50% - 12px);
  min-width: 220px;
  gap: 8px;
  font-size: 13px;
  color: var(--roblox-muted);
}

[data-preset="roblox"] .meta-item span {
  color: var(--roblox-text);
  font-weight: 700;
}

[data-preset="roblox"] .sources-tracker {
  gap: 12px;
  margin-top: 0;
}

[data-preset="roblox"] .sources-label {
  color: var(--roblox-muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

[data-preset="roblox"] .source-chip {
  padding: 6px 10px;
  border-radius: 3px;
  border: 1px solid var(--roblox-border-chip);
  background: linear-gradient(180deg, var(--roblox-chip-grad-top) 0%, var(--roblox-chip-grad-bot) 100%);
  color: var(--roblox-link);
  font-size: 12px;
  font-weight: 700;
}

@media (max-width: 600px) {
  [data-preset="roblox"] body {
    padding: 16px 10px 20px;
  }

  [data-preset="roblox"] .news-card {
    box-shadow: 0 0 0 6px var(--roblox-shell), 0 16px 32px var(--roblox-shadow);
  }

  [data-preset="roblox"] .card-payload {
    padding: 18px 16px 18px;
  }

  [data-preset="roblox"] .context-row {
    margin: -18px -16px 16px;
    padding: 10px 12px;
  }
}
""".strip(),
        default_custom_css="",
    ),
    "doors": GameCardPreset(
        key="doors",
        label="DOORS",
        description="\u6050\u6016\u63a2\u7d22\u3001\u5b9e\u4f53\u9884\u8b66\u4e0e\u697c\u5c42\u66f4\u65b0\u60c5\u62a5\u7684\u6df1\u8272\u98ce\u683c\u9884\u8bbe\u3002",
        aliases=("doors", "lsplash"),
        css=_DOORS_MENU_SKIN_CSS,
        default_custom_css="",
    ),
    "pressure": GameCardPreset(
        key="pressure",
        label="Pressure",
        description="\u6df1\u6d77\u65bd\u8bbe\u3001\u9ad8\u538b\u751f\u5b58\u4e0e\u5c01\u9501\u533a\u57df\u5f02\u52a8\u7684\u51b7\u8c03\u9884\u8bbe\u3002",
        aliases=("pressure", "urbanshade"),
        css="""
:root {
  --md-sys-color-primary: #006781;
  --md-sys-color-primary-container: #bbe9ff;
  --md-sys-color-on-primary-container: #001f28;
  --md-sys-color-secondary-container: #d2edf6;
  --md-sys-color-on-secondary-container: #0d3440;
  --app-info-bg: #d7f3ff;
  --app-info-text: #005167;
}

[data-theme="dark"] {
  --md-sys-color-primary: #6cd2f5;
  --md-sys-color-primary-container: #004d61;
  --md-sys-color-on-primary-container: #bbe9ff;
  --md-sys-color-secondary-container: #143946;
  --md-sys-color-on-secondary-container: #d2edf6;
  --app-info-bg: #003f51;
  --app-info-text: #8ee1ff;
}
""".strip(),
        default_custom_css="",
    ),
    "forsaken": GameCardPreset(
        key="forsaken",
        label="Forsaken",
        description="\u6cbf\u7528 DOORS \u540c\u6b3e\u5361\u7247\u5916\u58f3\uff08\u6df1\u8272\u7c97\u63cf\u8fb9\u3001\u5706\u89d2\u5757\u4e0e\u6e38\u620f\u5b57\u4f53\u6808\uff09\uff0c\u9002\u7528\u4e8e Forsaken \u60c5\u62a5\u3001\u7248\u672c\u8282\u70b9\u4e0e\u793e\u7fa4\u516c\u544a\u3002",
        aliases=("forsaken",),
        css=_DOORS_MENU_SKIN_CSS.replace('[data-preset="doors"]', '[data-preset="forsaken"]').replace(
            "--doors-", "--forsaken-"
        )
        + _FORSAKEN_MONO_BORDER_CSS,
        default_custom_css="",
    ),
}

DEFAULT_GAME_CARD_PRESET_KEY = "roblox"


def available_game_card_preset_keys() -> tuple[str, ...]:
    configured = copy_dict(
        "preset_availability",
        {
            "available_keys": ["roblox", "doors", "forsaken"],
        },
    ).get("available_keys", ["roblox", "doors", "forsaken"])
    keys = [str(item) for item in configured if str(item) in GAME_CARD_PRESETS]
    if not keys:
        return ("roblox", "doors", "forsaken")
    return tuple(keys)


def game_card_preset_fallback_key() -> str:
    configured = copy_text("preset_availability.fallback_key", DEFAULT_GAME_CARD_PRESET_KEY)
    if configured in GAME_CARD_PRESETS:
        return configured
    return DEFAULT_GAME_CARD_PRESET_KEY


def is_game_card_preset_available(preset_key: str | None) -> bool:
    return bool(preset_key and preset_key in available_game_card_preset_keys())


def resolve_game_card_preset(game: str | None, preset_key: str | None = None) -> GameCardPreset:
    if preset_key and preset_key in GAME_CARD_PRESETS:
        if is_game_card_preset_available(preset_key):
            return GAME_CARD_PRESETS[preset_key]
        unavailable = GAME_CARD_PRESETS[preset_key]
        fallback = GAME_CARD_PRESETS[game_card_preset_fallback_key()]
        suffix = copy_text("preset_availability.unavailable_suffix", "（尚未开发）")
        return replace(
            fallback,
            key=unavailable.key,
            label=f"{copy_text(('presets', unavailable.key, 'label'), unavailable.label)}{suffix}",
            description=copy_text(
                "preview_console.supporting.preset_unavailable",
                "这个预设尚未开发，当前已临时回退到 Roblox 预设。",
            ),
        )

    normalized_game = _normalize(game)
    for preset in GAME_CARD_PRESETS.values():
        if (
            normalized_game in {_normalize(alias) for alias in preset.aliases}
            and is_game_card_preset_available(preset.key)
        ):
            return preset

    return GAME_CARD_PRESETS[game_card_preset_fallback_key()]


def list_game_card_presets() -> list[dict[str, str]]:
    return [
        {
            "key": preset.key,
            "label": copy_text(("presets", preset.key, "label"), preset.label),
            "description": copy_text(("presets", preset.key, "description"), preset.description),
            "default_custom_css": preset.default_custom_css,
        }
        for preset in GAME_CARD_PRESETS.values()
        if preset.key in available_game_card_preset_keys()
    ]

# DDRBBOT API Endpoints

## Base URL

- Resolution order:
  1. `--base-url`
  2. current working directory `.env`
  3. process environment
  4. fallback `http://127.0.0.1:8000`
- Supported keys: `DDRBBOT_API`, `DDRBBOT_API_BASE_URL`, `DDRBBOT_API_URL`
- Recommended `.env` entry:

```env
DDRBBOT_API=http://127.0.0.1:8000
```

## Endpoint index

| Group | Method | Path |
|---|---|---|
| Health | GET | `/api/health` |
| Render | POST | `/api/render/preview` |
| Render | POST | `/api/render/preview-image` |
| Ingest | POST | `/api/webhook/discord` |
| Ingest | POST | `/api/collect/rss` |
| Ingest | POST | `/api/collect/rsshub` |
| Sources | GET / POST | `/api/sources` |
| Review | GET | `/api/review/items` |
| Review | GET | `/api/review/items/{processed_event_id}` |
| Review | POST | `/api/review/{processed_event_id}/rerender` |
| Review | POST | `/api/review/{processed_event_id}/approve-send` |
| Review | POST | `/api/review/{processed_event_id}/reject` |
| Review | POST | `/api/review/{processed_event_id}/resend` |
| Delivery | POST | `/api/qq/send-news-card` |
| Delivery | GET | `/api/qq/delivery/review-queue` |
| Delivery | POST | `/api/qq/delivery/retry-failed` |
| Delivery | GET | `/api/delivery/dead-letter` |
| Delivery | POST | `/api/delivery/dead-letter/{record_id}/retry` |
| QQ | GET | `/api/qq/adapter/status` |
| QQ | POST | `/api/events/qq` |
| AI Agent | POST | `/api/ai/chat` |

## Core endpoint map

### `GET /api/health`

Confirm the FastAPI app, pipeline queue depth, repository stats and NapCat connectivity before heavier operations. Returns `{ ok, queue_size, stats, napcat_connected }`.

### `POST /api/render/preview`

Render an MD3 news card from structured fields. Returns `html_path` and an optional `image_path` (PNG) when Playwright is available.

Minimal payload:

```json
{
  "title": "DOORS 发布新预告",
  "summary": "官方继续为下一次更新预热。",
  "highlights": [
    "确认新内容仍在开发中",
    "预告图里出现未知轮廓",
    "近期还会公布更多信息"
  ],
  "category": "teaser",
  "theme": "light",
  "orientation": "vertical",
  "preset_key": "doors",
  "game": "DOORS",
  "source_name": "DOORS 官方账号",
  "channel_name": "announcements",
  "author": "Official",
  "source_credibility": "official",
  "need_translation": false,
  "media": [
    {
      "type": "image",
      "url": "https://example.com/doors-teaser.png",
      "description": "官方预告图，中央区域出现新实体轮廓。",
      "reference_url": "https://www.roblox.com/games/example",
      "reference_label": "roblox.com"
    }
  ],
  "discovered_sources": [
    "@LSPLASH",
    "@DOORSGame"
  ]
}
```

Field rules:

- `category`: `announcement | teaser | patch | maintenance`
- `theme`: `light | dark`
- `orientation`: `vertical | horizontal`
- `preset_key`: `roblox | doors | forsaken | pressure`
- `source_credibility`: `official | community | unverified`
- Prefer official or primary image sources; populate `description` plus `reference_url` for each image.

### `POST /api/render/preview-image`

Same payload as `/api/render/preview` but forces a PNG screenshot and returns an error when Playwright runtime is unavailable. Expect `html_path` AND `image_path`.

### `POST /api/webhook/discord`

Feed raw Discord-style content into the analyzer/queue pipeline. Auto-delivery still obeys `AUTO_DELIVER_ENABLED` + `DEFAULT_QQ_GROUP_ID`.

Minimal payload:

```json
{
  "id": "discord-001",
  "source_name": "PHIGHTING Discord",
  "channel_name": "announcements",
  "content": "New patch notes are live.",
  "author": {"username": "Official"}
}
```

Returns `accepted`, `deduplicated`, `raw_event_id`.

### `POST /api/collect/rss`

Pull a generic RSS/Atom feed. Inserts new `raw_events`, enqueues them for the analyzer pipeline, and stamps `sources.last_checked_at` + `url`.

```json
{
  "source_name": "Doors Fansite",
  "feed_url": "https://example.com/feed.xml",
  "limit": 5
}
```

### `POST /api/collect/rsshub`

Same payload as `/api/collect/rss`, but validates the feed host against `RSSHUB_HOST_MARKERS` + `RSSHUB_EXTRA_HOSTS`. Use this for X / other social sources exposed through an RSSHub instance you trust.

### `GET /api/sources`

List registered source feeds. Query: `limit` (default 50).

### `POST /api/sources`

Upsert a source registration.

```json
{
  "source_type": "rss",
  "name": "DOORS RSSHub",
  "feed_url": "http://rsshub.app/x/doors_game",
  "credibility_level": "official"
}
```

### `GET /api/review/items`

JSON list mirroring `GET /review`. Query:

- `status`: `open` (pending/skipped/review_pending/failed/approved/queued) | `failed` | `sent` | `rejected` | `all`
- `limit`: 1-50
- `processed_event_id`: pin selection

Response includes `items`, `selected_id`, and `selected` (full detail).

### `GET /api/review/items/{processed_event_id}`

Single-item detail JSON (same shape as `selected`).

### `POST /api/review/{processed_event_id}/rerender`

Apply edits and re-render. Only keys present in the JSON body are persisted.

```json
{
  "title": "Updated title",
  "summary": "Updated summary",
  "highlights": ["Point A", "Point B", "Point C"],
  "category": "announcement",
  "game": "Roblox",
  "theme": "light",
  "orientation": "horizontal",
  "preset_key": "doors",
  "custom_css": "",
  "channel_name": "announcements",
  "author": "Official",
  "source_credibility": "official",
  "need_translation": false,
  "media": [
    {
      "type": "image",
      "url": "https://example.com/card.png",
      "description": "Hero art",
      "reference_url": null,
      "reference_label": null
    }
  ],
  "discovered_sources": ["@DOORSGame"]
}
```

#### Horizontal card tips

When `orientation` is `horizontal` (16:9, ~1280×720):

- `highlights`: ≤ 4 items, each ≤ 22 characters (2-column grid, 2-line clamp per item).
- `summary`: ≤ 80 characters (3-line clamp).
- `media`: at most 1 entry (secondary gallery is hidden).
- Without `media`, the card falls back to a single-column text layout.

### `POST /api/review/{processed_event_id}/approve-send`

Save edits, rerender, and enqueue to the delivery queue. Payload = rerender payload + optional `target_type` / `target_id`. If omitted and `DEFAULT_QQ_GROUP_ID` is set, the default group is used. Response returns `status="queued"` because delivery is now asynchronous via `DeliveryWorker`; monitor the record via `/api/review/items/{id}` or `/api/qq/delivery/review-queue`.

### `POST /api/review/{processed_event_id}/reject`

Mark the item as rejected. Empty body accepted.

### `POST /api/review/{processed_event_id}/resend`

Enqueue a fresh delivery using the latest screenshot artifact. Payload:

```json
{"target_type": "group", "target_id": "123456"}
```

### `POST /api/qq/send-news-card`

Enqueue a delivery directly (by path + trace_id). Uses the same `DeliveryWorker` as the pipeline.

```json
{
  "trace_id": "manual-send-001",
  "target_type": "group",
  "target_id": "123456",
  "image_path": "D:/Code/DDRBBOT/artifacts/rendered/2026-04-17/example.png",
  "caption": "DOORS / 公告预览"
}
```

Returns `{ok, trace_id, status: "queued"|"duplicate", ...}`. `duplicate` means the record is already `sent`.

### `GET /api/qq/delivery/review-queue`

Inspect pipeline queue size + failed delivery records. Query: `limit` (default 5).

### `POST /api/qq/delivery/retry-failed`

Re-enqueue a failed or dead-letter record. Payload `{trace_id?}`; omitting `trace_id` picks the most recent failed one.

### `GET /api/delivery/dead-letter`

List delivery records that exhausted retries (`status="dead_letter"`). Query: `limit` (default 20).

### `POST /api/delivery/dead-letter/{record_id}/retry`

Re-enqueue a specific dead-letter record by its internal `id` (from the dead-letter list).

### `GET /api/qq/adapter/status`

NapCat health snapshot. Query: `include_groups` (default false), `limit` (default 20). Returns `connected`, `login_info`, `version_info`, `groups_count`, `default_group_id`, `default_group_found`, and (when requested) `groups`.

### `POST /api/events/qq`

Inbound NapCat-style event endpoint. Accepts either a normalized `QQInboundEvent` JSON or a raw OneBot payload; always persists a `platform_events` row and dispatches commands via `QQCommandRouter`. Also available through the `NapCatWSClient` when `NAPCAT_WS_URL` is configured.

### `POST /api/ai/chat`

Call the built-in LLM agent with tool calling. The agent can query the DB, collect sources, render cards into the review queue, and (only inside a QQ chat context) reply via `send_reply_text`.

Payload:

```json
{"message": "查一下最近的 DOORS 审核队列", "origin": "api"}
```

`origin` must be one of `api` (default, debug), `qq_chat`, or `scheduler`. Response:

```json
{
  "ok": true,
  "final_text": "…",
  "tool_steps": 2,
  "error": null,
  "messages": [
    {"role": "system", "content": "…"},
    {"role": "user", "content": "…"},
    {"role": "assistant", "tool_calls": [...]},
    {"role": "tool", "name": "list_review_items", "content": "…"},
    {"role": "assistant", "content": "…"}
  ]
}
```

Errors 409 when `LLM_AGENT_ENABLED=false` or `LLM_BASE_URL` / `LLM_MODEL` missing.

## Selection rules

- Prefer `render-preview` when the user is asking for a fresh card artifact from structured fields.
- Prefer `webhook-discord` when the user wants the analyzer / queue to decide the final processed result.
- Prefer `GET /api/review/items` to discover `processed_event_id`, then review POST endpoints.
- Prefer `collect-rsshub` over `collect-rss` for RSSHub hosts to benefit from whitelist validation.
- Do not use QQ delivery endpoints to generate content; they only send existing artifacts through `DeliveryWorker`.
- Use `ai-chat` when you want the LLM to orchestrate tools (search, summarize, render-to-review) in one shot.

---
name: ddrbbot-api-content
description: Drive DDRBBOT's local FastAPI endpoints to generate news-card content, render preview artifacts, ingest source announcements (Discord webhook / RSS / RSSHub), operate review + delivery flows, inspect dead-letter records, and talk to the built-in LLM Agent. Use when Codex needs to turn source text or structured fields into DDRBBOT card output through the running API instead of editing templates directly, or when a task involves `/api/render/preview*`, `/api/webhook/discord`, `/api/collect/{rss,rsshub}`, `/api/sources`, `/api/review/*`, `/api/qq/*`, `/api/delivery/dead-letter*`, or `/api/ai/chat`.
---

# DDRBBOT API Content

Use this skill to operate the local DDRBBOT service as a content-generation, delivery and LLM-agent backend.

## Service model

- FastAPI app (`uvicorn ddrbbot.main:create_app --factory --reload`) persists data in SQLite (`DATABASE_PATH`).
- `PipelineCoordinator` (asyncio `Queue`) analyzes raw events and renders cards; `DeliveryWorker` polls `delivery_records` and sends to NapCat asynchronously with retry / dead-letter / text fallback.
- `NapCatAdapter` talks to NapCat OneBot HTTP; optional `NapCatWSClient` subscribes to `NAPCAT_WS_URL` for push events with exponential-backoff reconnect.
- Optional `LLMAgent` (OpenAI-compatible `chat/completions` with tool calling) exposes auto-scout scheduler and a debug HTTP chat endpoint.

## Workflow

1. Confirm the service is reachable.
   - Run `python skills/ddrbbot-api-content/scripts/ddrbbot_api.py health`.
   - If the API is down, start `uvicorn ddrbbot.main:create_app --factory --reload` from the repo root.
   - Base URL resolution: `--base-url` > `./.env` (`DDRBBOT_API` / `DDRBBOT_API_BASE_URL` / `DDRBBOT_API_URL`) > process env > `http://127.0.0.1:8000`.

2. Choose the narrowest entrypoint.
   - `render-preview` — structured card generation (HTML + optional PNG).
   - `render-preview-image` — require PNG in response (error if Playwright missing).
   - `webhook-discord` — raw announcement → pipeline (analyzer + render + optional auto-deliver).
   - `collect-rss` / `collect-rsshub` — feed ingestion; RSSHub variant enforces host whitelist.
   - `sources-list` / `sources-upsert` — source registry bookkeeping.
   - `review-items` / `review-item` — list or fetch a single processed event.
   - `review-rerender`, `review-approve-send`, `review-reject`, `review-resend` — act on a `processed_event_id`.
   - `qq-send` — pure delivery when PNG already exists.
   - `review-queue`, `retry-failed` — delivery queue overview + manual retry.
   - `dead-letter-list` / `dead-letter-retry` — inspect and re-enqueue records exhausted by `DeliveryWorker`.
   - `adapter-status` — NapCat login/version/group snapshot.
   - `ai-chat` — drive the LLM Agent with a natural-language instruction (debug mode; agent cannot send QQ messages outside `qq_chat` origin).

3. Normalize the payload before calling the API.
   - Keep `highlights` to 3-5 concrete items (≤ 4 and ≤ 22 chars each for `horizontal`).
   - `category` ∈ `announcement | teaser | patch | maintenance` (UI labels: 公告内容 / 预告内容 / 更新内容 / 通知内容).
   - `source_credibility` ∈ `official | community | unverified`.
   - `theme` ∈ `light | dark`; `orientation` ∈ `vertical | horizontal`.
   - Prefer explicit `preset_key` when the user names a style (`doors`, `roblox`, `pressure`, `forsaken`).
   - For `horizontal` cards (16:9): `summary` ≤ 80 chars; `media` ≤ 1; without hero media the card becomes a single-column text layout.
   - Populate `media[].url`, `description`, optional `reference_url` + `reference_label` when the card benefits from artwork. Search the web for official / primary sources before calling the API.
   - Text polish lives in `copy.json`; the API payload should describe the event, not skin-level boilerplate.

4. Call the API through the bundled script.
   - `--file <payload.json>` for non-trivial bodies.
   - `--json '{...}'` for short one-off payloads.
   - `--stdin` when another command already emits JSON.
   - Run from the repo root so `.env` is picked up for `DDRBBOT_API`.

5. Verify the returned artifact or status.
   - `render-preview*` returns `html_path` and optional `image_path`.
   - Review mutation endpoints now return `status="queued"` because delivery is asynchronous via `DeliveryWorker`; poll `/api/review/items/{id}` or `/api/qq/delivery/review-queue` to confirm `sent` / `dead_letter`.
   - If visual inspection matters, open the returned HTML artifact or `/preview/md3`.
   - Dead-letter failures surface at `GET /api/delivery/dead-letter`; re-enqueue via `POST /api/delivery/dead-letter/{record_id}/retry` once NapCat is healthy.

## Quick Commands

```powershell
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py health
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py adapter-status --include-groups --limit 20

python skills/ddrbbot-api-content/scripts/ddrbbot_api.py render-preview --file payload.json
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py render-preview-image --file payload.json

python skills/ddrbbot-api-content/scripts/ddrbbot_api.py webhook-discord --file discord-event.json
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py collect-rss --file rss.json
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py collect-rsshub --file rsshub.json
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py sources-list --limit 50
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py sources-upsert --file source.json

python skills/ddrbbot-api-content/scripts/ddrbbot_api.py review-items --status open --limit 24
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py review-item <processed_event_id>
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py review-rerender <processed_event_id> --file review-edit.json
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py review-approve-send <processed_event_id> --file approve-send.json
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py review-reject <processed_event_id>
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py review-resend <processed_event_id> --file resend.json

python skills/ddrbbot-api-content/scripts/ddrbbot_api.py qq-send --file qq-send.json
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py review-queue --limit 10
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py retry-failed --json "{\"trace_id\":\"...\"}"
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py dead-letter-list --limit 20
python skills/ddrbbot-api-content/scripts/ddrbbot_api.py dead-letter-retry <record_id>

python skills/ddrbbot-api-content/scripts/ddrbbot_api.py ai-chat --message "最近有没有新的 DOORS 公告？"
```

## Task Guide

### Generate a card from structured content

- Build a `RenderPreviewRequest` payload.
- If the request should include artwork, add `media[]` with image URL, description and source reference link.
- Call `render-preview` (HTML-first) or `render-preview-image` (PNG mandatory).

### Turn raw source text into a processed event

- Wrap the content as a Discord-style webhook payload.
- Call `webhook-discord`.
- Poll `review-items` / `review-queue` after enqueue.

### Poll a community feed

- Call `collect-rsshub` for an RSSHub URL (validated against `RSSHUB_HOST_MARKERS` + `RSSHUB_EXTRA_HOSTS`), or `collect-rss` for any Atom/RSS feed.
- Each new entry goes through the analyzer pipeline.
- After ingestion, inspect `sources-list` to confirm `last_checked_at` updated.

### Edit and resend an existing processed event

- Use the review endpoints, not `render-preview`.
- Keep the payload aligned with `ReviewEditRequest` or `ReviewApproveSendRequest` (optional: `preset_key`, `orientation`, `custom_css`, `channel_name`, `author`, `source_credibility`, `need_translation`, `media`, `discovered_sources`; only sent keys are persisted).
- `review-resend` uses the latest artifact PNG when no edits are needed.
- Delivery is async: expect `status="queued"` and verify later via `review-queue` / `review-item`.

### Clear a delivery failure

- `review-queue` for a quick summary of pending / failed.
- `retry-failed` (by `trace_id`) to re-enqueue a failed or dead-letter record from the queue tail.
- `dead-letter-list` + `dead-letter-retry <record_id>` when you want to target a specific exhausted record.

### Let the LLM Agent orchestrate

- Call `ai-chat` with a natural-language message.
- The agent can invoke tools: `list_sources`, `list_review_items`, `get_processed_event`, `fetch_url`, `collect_rss`, `collect_rsshub`, `register_source`, `render_card_for_review` (drops into review queue), `call_ddrbbot_api` (covered endpoints only) and, only inside a QQ chat context, `send_reply_text`.
- The agent CANNOT send QQ cards directly; `render_card_for_review` always lands as `delivery_status=review_pending` for human approval.
- Require `LLM_AGENT_ENABLED=true`, `LLM_BASE_URL`, `LLM_MODEL` to be set.

## Resources

- [references/endpoints.md](references/endpoints.md) — per-endpoint payloads, response shapes, selection rules.
- [scripts/ddrbbot_api.py](scripts/ddrbbot_api.py) — deterministic local CLI.

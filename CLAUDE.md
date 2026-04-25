# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

DDRBBOT is a Roblox news aggregation bot. It collects news/updates from RSS, RSSHub, and Discord webhooks; renders them as Material Design 3 news card images (Jinja2 + Playwright/Chromium); offers a web review panel for human approval; and delivers approved cards to QQ groups/channels via NapCat (a OneBot-compatible QQ bot framework). It also includes an optional LLM agent that responds to QQ chat commands and runs periodic autonomous scouting tasks.

## Development commands

```bash
# Install (editable, with test deps)
pip install -e ".[dev]"

# Run the FastAPI server
export PYTHONPATH=src
uvicorn ddrbbot.main:create_app --factory --host 0.0.0.0 --port 8000

# Run all tests (pythonpath is set in pyproject.toml)
pytest tests/ -q

# Run a single test file
pytest tests/test_rendering.py -q

# Run a specific test
pytest tests/test_rendering.py::test_preset_injects_correct_css -q

# Optional: install TUI log viewer
pip install -e ".[tui]"
ddrbbot-logs --file ./artifacts/logs/ddrbbot.log
```

Configuration is loaded from environment variables (see `.env.example`). Copy `.env.example` to `.env` and fill in values before running.

## Architecture

**Entry point**: `src/ddrbbot/main.py` — `create_app()` factory assembles all services into a `FastAPI` app with lifespan hooks for startup/shutdown. Service wiring lives in `_build_services()` and returns an `AppServices` dataclass (`src/ddrbbot/services.py`), which is the single DI container accessible via `request.app.state.services`.

**Configuration**: `src/ddrbbot/config.py` — `load_settings()` reads env vars into a frozen `Settings` dataclass (all config is declarative, no config files beyond `.env`).

**Data flow (the core pipeline)**:

```
RawEvent (from RSS/Discord webhook)
  → PipelineCoordinator (asyncio worker pool, src/ddrbbot/pipeline.py)
    → EventAnalyzer.analyze() → ProcessedEvent (LLM or heuristic fallback)
    → NewsCardRenderer.render() → RenderArtifact (HTML + optional PNG screenshot)
    → QQDeliveryService.enqueue_delivery() → DeliveryWorker polls & sends via NapCat
```

**Key modules**:

| Module | Purpose |
|--------|---------|
| `src/ddrbbot/models.py` | All Pydantic v2 models: `RawEvent`, `ProcessedEvent`, `RenderArtifact`, `DeliveryRecord`, `ChatSession`, `ChatMessageRecord`, `LLMAnalysisOutput`, etc. |
| `src/ddrbbot/database.py` | `SQLiteRepository` — single class wrapping all SQLite access with thread-safe RLock, WAL mode, and incremental column migrations |
| `src/ddrbbot/rendering.py` | `NewsCardRenderer` — Jinja2 HTML templates + Playwright Chromium screenshot to PNG |
| `src/ddrbbot/presets.py` | Per-game CSS presets (DOORS, Pressure, Forsaken, Roblox themes) |
| `src/ddrbbot/analyzer.py` | `EventAnalyzer` — sends raw content to OpenAI-compatible LLM for structured analysis; falls back to heuristics (keyword matching, regex, character counting) |
| `src/ddrbbot/delivery.py` | `QQDeliveryService` — enqueues delivery records, sends via NapCat with retry, manages dead-letter queue |
| `src/ddrbbot/delivery_worker.py` | `DeliveryWorker` — background asyncio task polling for due delivery records |
| `src/ddrbbot/rss.py` | `RSSCollector` — feedparser wrapper, converts RSS entries to `RawEvent`, deduplicates by SHA-256 external_id |
| `src/ddrbbot/rsshub.py` | RSSHub URL hostname validation (whitelist-based) |
| `src/ddrbbot/copybook.py` | `RuntimeCopyStore` — loads i18n/prompt strings from `copy.json` at runtime |
| `src/ddrbbot/review_presenter.py` | Builds HTML/JSON for the human review panel at `/review` |
| `src/ddrbbot/logging_setup.py` | Centralized logging: text or JSON format, stdout + optional file rotation, per-library level overrides |
| `src/ddrbbot/tui_logs.py` | Optional Textual-based terminal log viewer |

**QQ integration** (`src/ddrbbot/qq/`): Lazy-loaded subpackage (`__getattr__` proxy in `__init__.py` to break circular imports). `NapCatAdapter` implements the `BotAdapter` protocol over HTTP. `NapCatWSClient` optionally receives events via WebSocket. `QQCommandRouter` parses `#`/`/` prefixed commands and dispatches to handlers.

**LLM agent** (`src/ddrbbot/llm_agent/`): `LLMAgent` wraps OpenAI-compatible chat completion with a tool-calling loop. `ToolRegistry` holds named tool handlers. `ChatService` orchestrates sessions, personas, knowledge injection, and message history. `AgentScheduler` runs periodic autonomous scouting tasks.

**Deduplication** happens at three levels: `raw_events` (UNIQUE on source_type+external_id), `processed_events` (UNIQUE on raw_event_id), and `delivery_records` (UNIQUE on trace_id).

**Database**: SQLite via `SQLiteRepository`. Tables include `sources`, `raw_events`, `processed_events`, `render_artifacts`, `delivery_records`, `delivery_logs`, `chat_sessions`, `chat_messages`, `chat_profiles`, `chat_personas`, `chat_knowledge_items`. Schema is defined in the `SCHEMA` module-level constant; column additions happen automatically in `_run_migrations_locked()` via `PRAGMA table_info`.

## Important patterns

- **No ORM**: All database access goes through `SQLiteRepository` using raw SQL with parameterized queries. Thread safety via `threading.RLock()`.
- **Factory pattern**: `create_app()` is a factory (not a module-level app), used with `uvicorn --factory`. This ensures settings are loaded before app construction.
- **Pydantic v2**: All models use `pydantic>=2.7`. Field validators use `@field_validator` (not v1 `@validator`).
- **Artifacts**: All generated files (DB, HTML, PNG, logs) go under `ARTIFACTS_DIR` (default `./artifacts/`). This directory is gitignored.
- **copy.json**: The large `copy.json` file is the single source for all user-facing text, LLM prompts, and i18n strings. Use `RuntimeCopyStore` to read it — never hardcode strings that should be configurable.
- **Tests use `fastapi.testclient.TestClient`** against the full app. They do NOT mock the database — tests hit a real SQLite database (in-memory or temp file via `tmp_path`).

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    ChatKnowledgeItem,
    ChatMessageRecord,
    ChatPersona,
    ChatProfile,
    ChatSession,
    DeliveryLog,
    DeliveryRecord,
    LLMProviderRecord,
    MediaAsset,
    ProcessedEvent,
    ProviderConfig,
    QQInboundEvent,
    RawEvent,
    RenderArtifact,
)
from .utils import isoformat_z, utc_now

_RAW_PATCH_MISSING = object()


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  name TEXT NOT NULL,
  url TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  last_checked_at TEXT,
  credibility_level TEXT DEFAULT 'unverified',
  created_at TEXT NOT NULL,
  UNIQUE(source_type, name)
);

CREATE TABLE IF NOT EXISTS raw_events (
  id TEXT PRIMARY KEY,
  source_id TEXT,
  source_type TEXT NOT NULL,
  source_name TEXT NOT NULL,
  channel_name TEXT,
  author TEXT,
  content TEXT NOT NULL,
  attachments_json TEXT NOT NULL,
  external_id TEXT NOT NULL,
  published_at TEXT NOT NULL,
  raw_payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(source_type, external_id)
);

CREATE TABLE IF NOT EXISTS processed_events (
  id TEXT PRIMARY KEY,
  raw_event_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  highlights_json TEXT NOT NULL,
  category TEXT NOT NULL,
  game TEXT,
  need_translation INTEGER NOT NULL DEFAULT 0,
  source_credibility TEXT NOT NULL,
  media_json TEXT NOT NULL,
  discovered_sources_json TEXT NOT NULL,
  language TEXT NOT NULL,
  render_status TEXT NOT NULL,
  delivery_status TEXT NOT NULL,
  published_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS render_artifacts (
  id TEXT PRIMARY KEY,
  processed_event_id TEXT NOT NULL,
  template_name TEXT NOT NULL,
  theme TEXT NOT NULL,
  html_path TEXT NOT NULL,
  image_path TEXT,
  width INTEGER NOT NULL,
  height INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_logs (
  id TEXT PRIMARY KEY,
  processed_event_id TEXT,
  channel_name TEXT NOT NULL,
  target_id TEXT NOT NULL,
  delivery_result TEXT NOT NULL,
  message_id TEXT,
  error_message TEXT,
  delivered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_records (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL UNIQUE,
  processed_event_id TEXT,
  platform TEXT NOT NULL,
  adapter TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  status TEXT NOT NULL,
  message_id TEXT,
  error_code TEXT,
  error_message TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  request_payload_json TEXT NOT NULL DEFAULT '{}',
  next_retry_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_events (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  adapter TEXT NOT NULL,
  event_type TEXT NOT NULL,
  group_id TEXT,
  user_id TEXT,
  raw_message TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id TEXT PRIMARY KEY,
  session_key TEXT NOT NULL UNIQUE,
  origin TEXT NOT NULL,
  scope TEXT NOT NULL,
  group_id TEXT,
  user_id TEXT,
  persona_id TEXT,
  custom_persona_json TEXT,
  summary TEXT NOT NULL DEFAULT '',
  summary_updated_at TEXT,
  last_message_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  name TEXT,
  tool_call_id TEXT,
  tool_calls_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
  ON chat_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS chat_profiles (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  user_id TEXT NOT NULL,
  display_name TEXT,
  preferences_json TEXT NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(scope, user_id)
);

CREATE TABLE IF NOT EXISTS chat_personas (
  id TEXT PRIMARY KEY,
  persona_key TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL,
  is_builtin INTEGER NOT NULL DEFAULT 0,
  allow_tools INTEGER NOT NULL DEFAULT 1,
  tone TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_knowledge_items (
  id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  content TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  priority INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_knowledge_priority
  ON chat_knowledge_items(priority DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS llm_providers (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL DEFAULT '',
  base_url TEXT NOT NULL,
  api_key TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class SQLiteRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def _get_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            conn = sqlite3.connect(self.database_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._connection = conn
        return self._connection

    def close(self) -> None:
        conn = self._connection
        if conn is not None:
            self._connection = None
            conn.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = self._get_connection()
            connection.executescript(SCHEMA)
            self._run_migrations_locked(connection)
            connection.commit()

    def insert_raw_event(self, event: RawEvent) -> bool:
        with self._lock:
            connection = self._get_connection()
            source_id = self._ensure_source_locked(
                connection,
                source_type=event.source_type,
                source_name=event.source_name,
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO raw_events (
                  id, source_id, source_type, source_name, channel_name, author, content,
                  attachments_json, external_id, published_at, raw_payload_json, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    source_id,
                    event.source_type,
                    event.source_name,
                    event.channel_name,
                    event.author,
                    event.content,
                    json.dumps(event.attachments, ensure_ascii=False),
                    event.external_id,
                    isoformat_z(event.published_at),
                    json.dumps(event.raw_payload, ensure_ascii=False),
                    event.status,
                    isoformat_z(event.created_at),
                ),
            )
            connection.commit()
            return cursor.rowcount > 0

    def get_raw_event(self, raw_event_id: str) -> RawEvent | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM raw_events WHERE id = ?",
                (raw_event_id,),
            ).fetchone()
        return self._row_to_raw_event(row) if row else None

    def update_raw_event_status(self, raw_event_id: str, status: str) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                "UPDATE raw_events SET status = ? WHERE id = ?",
                (status, raw_event_id),
            )
            connection.commit()

    def upsert_processed_event(self, event: ProcessedEvent) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                """
                INSERT INTO processed_events (
                  id, raw_event_id, title, summary, highlights_json, category, game,
                  need_translation, source_credibility, media_json, discovered_sources_json,
                  language, render_status, delivery_status, published_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(raw_event_id) DO UPDATE SET
                  title = excluded.title,
                  summary = excluded.summary,
                  highlights_json = excluded.highlights_json,
                  category = excluded.category,
                  game = excluded.game,
                  need_translation = excluded.need_translation,
                  source_credibility = excluded.source_credibility,
                  media_json = excluded.media_json,
                  discovered_sources_json = excluded.discovered_sources_json,
                  language = excluded.language,
                  render_status = excluded.render_status,
                  delivery_status = excluded.delivery_status,
                  published_at = excluded.published_at
                """,
                (
                    event.id,
                    event.raw_event_id,
                    event.title,
                    event.summary,
                    json.dumps(event.highlights, ensure_ascii=False),
                    event.category,
                    event.game,
                    int(event.need_translation),
                    event.source_credibility,
                    json.dumps([asset.model_dump() for asset in event.media], ensure_ascii=False),
                    json.dumps(event.discovered_sources, ensure_ascii=False),
                    event.language,
                    event.render_status,
                    event.delivery_status,
                    isoformat_z(event.published_at),
                    isoformat_z(event.created_at),
                ),
            )
            connection.commit()

    def update_processed_event_status(
        self,
        processed_event_id: str,
        *,
        render_status: str | None = None,
        delivery_status: str | None = None,
    ) -> None:
        updates: list[str] = []
        parameters: list[Any] = []
        if render_status is not None:
            updates.append("render_status = ?")
            parameters.append(render_status)
        if delivery_status is not None:
            updates.append("delivery_status = ?")
            parameters.append(delivery_status)
        if not updates:
            return
        parameters.append(processed_event_id)
        statement = f"UPDATE processed_events SET {', '.join(updates)} WHERE id = ?"
        with self._lock:
            connection = self._get_connection()
            connection.execute(statement, tuple(parameters))
            connection.commit()

    def get_processed_event(self, processed_event_id: str) -> ProcessedEvent | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM processed_events WHERE id = ?",
                (processed_event_id,),
            ).fetchone()
        return self._row_to_processed_event(row) if row else None

    def touch_source_feed(
        self,
        *,
        source_type: str,
        source_name: str,
        feed_url: str | None = None,
    ) -> None:
        now = isoformat_z(utc_now())
        with self._lock:
            connection = self._get_connection()
            self._ensure_source_locked(connection, source_type=source_type, source_name=source_name)
            if feed_url:
                connection.execute(
                    """
                    UPDATE sources
                    SET last_checked_at = ?, url = COALESCE(?, url)
                    WHERE source_type = ? AND name = ?
                    """,
                    (now, feed_url, source_type, source_name),
                )
            else:
                connection.execute(
                    "UPDATE sources SET last_checked_at = ? WHERE source_type = ? AND name = ?",
                    (now, source_type, source_name),
                )
            connection.commit()

    def list_sources(self, *, limit: int = 50) -> list[dict[str, Any]]:
        cap = max(1, min(limit, 200))
        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(
                """
                SELECT id, source_type, name, url, status, credibility_level, last_checked_at, created_at
                FROM sources
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_source_registration(
        self,
        *,
        source_type: str,
        name: str,
        feed_url: str | None,
        credibility_level: str,
    ) -> dict[str, Any]:
        now = isoformat_z(utc_now())
        source_id = f"src_{abs(hash((source_type, name)))}"
        with self._lock:
            connection = self._get_connection()
            existing = connection.execute(
                "SELECT id FROM sources WHERE source_type = ? AND name = ?",
                (source_type, name),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE sources
                    SET url = COALESCE(?, url),
                        credibility_level = ?,
                        status = 'active'
                    WHERE source_type = ? AND name = ?
                    """,
                    (feed_url, credibility_level, source_type, name),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO sources (id, source_type, name, url, status, credibility_level, created_at)
                    VALUES (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (source_id, source_type, name, feed_url, credibility_level, now),
                )
            connection.commit()
            row = connection.execute(
                "SELECT id, source_type, name, url, status, credibility_level, last_checked_at, created_at "
                "FROM sources WHERE source_type = ? AND name = ?",
                (source_type, name),
            ).fetchone()
        return dict(row) if row else {}

    def list_processed_events(
        self,
        *,
        delivery_statuses: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[ProcessedEvent]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if delivery_statuses:
            placeholders = ", ".join("?" for _ in delivery_statuses)
            clauses.append(f"delivery_status IN ({placeholders})")
            parameters.extend(delivery_statuses)

        query = "SELECT * FROM processed_events"
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY published_at DESC, created_at DESC LIMIT ?"
        parameters.append(max(limit, 1))

        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [self._row_to_processed_event(row) for row in rows]

    def get_raw_events_batch(self, raw_event_ids: list[str]) -> dict[str, RawEvent]:
        """Fetch multiple raw events in a single query. Returns {id: RawEvent}."""
        if not raw_event_ids:
            return {}
        deduplicated = list(dict.fromkeys(raw_event_ids))
        placeholders = ", ".join("?" for _ in deduplicated)
        query = f"SELECT * FROM raw_events WHERE id IN ({placeholders})"
        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(query, tuple(deduplicated)).fetchall()
        return {str(row["id"]): self._row_to_raw_event(row) for row in rows}

    def update_processed_event_review_fields(
        self,
        processed_event_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        highlights: list[str] | None = None,
        category: str | None = None,
        game: str | None = None,
        need_translation: bool | None = None,
        source_credibility: str | None = None,
        media: list[MediaAsset] | None = None,
        discovered_sources: list[str] | None = None,
        render_status: str | None = None,
        delivery_status: str | None = None,
    ) -> None:
        updates: list[str] = []
        parameters: list[Any] = []
        if title is not None:
            updates.append("title = ?")
            parameters.append(title)
        if summary is not None:
            updates.append("summary = ?")
            parameters.append(summary)
        if highlights is not None:
            updates.append("highlights_json = ?")
            parameters.append(json.dumps(highlights, ensure_ascii=False))
        if category is not None:
            updates.append("category = ?")
            parameters.append(category)
        if game is not None:
            updates.append("game = ?")
            parameters.append(game)
        if need_translation is not None:
            updates.append("need_translation = ?")
            parameters.append(1 if need_translation else 0)
        if source_credibility is not None:
            updates.append("source_credibility = ?")
            parameters.append(source_credibility)
        if media is not None:
            updates.append("media_json = ?")
            parameters.append(
                json.dumps(
                    [item.model_dump() for item in media],
                    ensure_ascii=False,
                )
            )
        if discovered_sources is not None:
            updates.append("discovered_sources_json = ?")
            parameters.append(json.dumps(discovered_sources, ensure_ascii=False))
        if render_status is not None:
            updates.append("render_status = ?")
            parameters.append(render_status)
        if delivery_status is not None:
            updates.append("delivery_status = ?")
            parameters.append(delivery_status)
        if not updates:
            return
        parameters.append(processed_event_id)
        statement = f"UPDATE processed_events SET {', '.join(updates)} WHERE id = ?"
        with self._lock:
            connection = self._get_connection()
            connection.execute(statement, tuple(parameters))
            connection.commit()

    def patch_raw_event(
        self,
        raw_event_id: str,
        *,
        channel_name: Any = _RAW_PATCH_MISSING,
        author: Any = _RAW_PATCH_MISSING,
        raw_payload_merge: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT channel_name, author, raw_payload_json FROM raw_events WHERE id = ?",
                (raw_event_id,),
            ).fetchone()
            if row is None:
                return
            ch = row["channel_name"]
            au = row["author"]
            payload = json.loads(row["raw_payload_json"])
            if channel_name is not _RAW_PATCH_MISSING:
                ch = channel_name
            if author is not _RAW_PATCH_MISSING:
                au = author
            if raw_payload_merge:
                payload.update(raw_payload_merge)
            connection.execute(
                """
                UPDATE raw_events
                SET channel_name = ?, author = ?, raw_payload_json = ?
                WHERE id = ?
                """,
                (
                    ch,
                    au,
                    json.dumps(payload, ensure_ascii=False),
                    raw_event_id,
                ),
            )
            connection.commit()

    def save_render_artifact(self, artifact: RenderArtifact) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                """
                INSERT INTO render_artifacts (
                  id, processed_event_id, template_name, theme, html_path, image_path, width, height, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.processed_event_id,
                    artifact.template_name,
                    artifact.theme,
                    artifact.html_path,
                    artifact.image_path,
                    artifact.width,
                    artifact.height,
                    isoformat_z(artifact.created_at),
                ),
            )
            connection.commit()

    def get_latest_render_artifact(self, processed_event_id: str) -> RenderArtifact | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                """
                SELECT * FROM render_artifacts
                WHERE processed_event_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (processed_event_id,),
            ).fetchone()
        return self._row_to_render_artifact(row) if row else None

    def save_delivery_log(self, log: DeliveryLog) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                """
                INSERT INTO delivery_logs (
                  id, processed_event_id, channel_name, target_id, delivery_result, message_id, error_message, delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log.id,
                    log.processed_event_id,
                    log.channel_name,
                    log.target_id,
                    log.delivery_result,
                    log.message_id,
                    log.error_message,
                    isoformat_z(log.delivered_at),
                ),
            )
            connection.commit()

    def reserve_delivery_record(self, record: DeliveryRecord) -> tuple[DeliveryRecord, bool]:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM delivery_records WHERE trace_id = ?",
                (record.trace_id,),
            ).fetchone()
            if row:
                return self._row_to_delivery_record(row), False
            connection.execute(
                """
                INSERT INTO delivery_records (
                  id, trace_id, processed_event_id, platform, adapter, target_type, target_id,
                  status, message_id, error_code, error_message, attempts, request_payload_json,
                  next_retry_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.trace_id,
                    record.processed_event_id,
                    record.platform,
                    record.adapter,
                    record.target_type,
                    record.target_id,
                    record.status,
                    record.message_id,
                    record.error_code,
                    record.error_message,
                    record.attempts,
                    json.dumps(record.request_payload, ensure_ascii=False),
                    isoformat_z(record.next_retry_at) if record.next_retry_at else None,
                    isoformat_z(record.created_at),
                    isoformat_z(record.updated_at),
                ),
            )
            connection.commit()
            return record, True

    def get_delivery_record(self, trace_id: str) -> DeliveryRecord | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM delivery_records WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        return self._row_to_delivery_record(row) if row else None

    def update_delivery_record(
        self,
        trace_id: str,
        *,
        processed_event_id: str | None = None,
        status: str | None = None,
        message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        attempts: int | None = None,
        request_payload: dict[str, Any] | None = None,
        next_retry_at: datetime | None | object = _RAW_PATCH_MISSING,
    ) -> None:
        updates: list[str] = []
        parameters: list[Any] = []
        if processed_event_id is not None:
            updates.append("processed_event_id = ?")
            parameters.append(processed_event_id)
        if status is not None:
            updates.append("status = ?")
            parameters.append(status)
        if message_id is not None:
            updates.append("message_id = ?")
            parameters.append(message_id)
        if error_code is not None:
            updates.append("error_code = ?")
            parameters.append(error_code)
        if error_message is not None:
            updates.append("error_message = ?")
            parameters.append(error_message)
        if attempts is not None:
            updates.append("attempts = ?")
            parameters.append(attempts)
        if request_payload is not None:
            updates.append("request_payload_json = ?")
            parameters.append(json.dumps(request_payload, ensure_ascii=False))
        if next_retry_at is not _RAW_PATCH_MISSING:
            updates.append("next_retry_at = ?")
            parameters.append(isoformat_z(next_retry_at) if next_retry_at else None)
        updates.append("updated_at = ?")
        parameters.append(isoformat_z(utc_now()))
        parameters.append(trace_id)
        statement = f"UPDATE delivery_records SET {', '.join(updates)} WHERE trace_id = ?"
        with self._lock:
            connection = self._get_connection()
            connection.execute(statement, tuple(parameters))
            connection.commit()

    def list_due_delivery_records(
        self,
        *,
        statuses: tuple[str, ...] = ("pending", "retry"),
        now: datetime | None = None,
        limit: int = 10,
    ) -> list[DeliveryRecord]:
        now_iso = isoformat_z(now or utc_now())
        placeholders = ", ".join("?" for _ in statuses)
        query = (
            f"SELECT * FROM delivery_records "
            f"WHERE status IN ({placeholders}) "
            f"AND (next_retry_at IS NULL OR next_retry_at <= ?) "
            f"ORDER BY next_retry_at IS NULL DESC, next_retry_at ASC, updated_at ASC "
            f"LIMIT ?"
        )
        parameters: list[Any] = list(statuses) + [now_iso, max(limit, 1)]
        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [self._row_to_delivery_record(row) for row in rows]

    def get_delivery_record_by_id(self, record_id: str) -> DeliveryRecord | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM delivery_records WHERE id = ?",
                (record_id,),
            ).fetchone()
        return self._row_to_delivery_record(row) if row else None

    def list_delivery_records(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        processed_event_id: str | None = None,
        limit: int = 20,
    ) -> list[DeliveryRecord]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(statuses)
        if processed_event_id is not None:
            clauses.append("processed_event_id = ?")
            parameters.append(processed_event_id)

        query = "SELECT * FROM delivery_records"
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY updated_at DESC LIMIT ?"
        parameters.append(max(limit, 1))

        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [self._row_to_delivery_record(row) for row in rows]

    def count_delivery_records(self, *, statuses: tuple[str, ...] | None = None) -> int:
        clauses: list[str] = []
        parameters: list[Any] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(statuses)

        query = "SELECT COUNT(*) FROM delivery_records"
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"

        with self._lock:
            connection = self._get_connection()
            row = connection.execute(query, tuple(parameters)).fetchone()
        return int(row[0]) if row else 0

    def save_platform_event(self, event: QQInboundEvent) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                """
                INSERT INTO platform_events (
                  id, platform, adapter, event_type, group_id, user_id, raw_message, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"evt_{utc_now().timestamp():.6f}".replace(".", ""),
                    event.platform,
                    event.adapter,
                    event.event_type,
                    event.group_id,
                    event.user_id,
                    event.raw_message,
                    json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                    isoformat_z(utc_now()),
                ),
            )
            connection.commit()

    def get_or_create_chat_session(
        self,
        *,
        session_key: str,
        origin: str,
        scope: str,
        group_id: str | None = None,
        user_id: str | None = None,
    ) -> ChatSession:
        now = utc_now()
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM chat_sessions WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            if row:
                return self._row_to_chat_session(row)
            session = ChatSession(
                session_key=session_key,
                origin=origin,
                scope=scope,
                group_id=group_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO chat_sessions (
                  id, session_key, origin, scope, group_id, user_id,
                  persona_id, custom_persona_json, summary, summary_updated_at,
                  last_message_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.session_key,
                    session.origin,
                    session.scope,
                    session.group_id,
                    session.user_id,
                    session.persona_id,
                    None,
                    session.summary,
                    None,
                    None,
                    isoformat_z(session.created_at),
                    isoformat_z(session.updated_at),
                ),
            )
            connection.commit()
            return session

    def get_chat_session(self, session_id: str) -> ChatSession | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return self._row_to_chat_session(row) if row else None

    def get_chat_session_by_key(self, session_key: str) -> ChatSession | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM chat_sessions WHERE session_key = ?",
                (session_key,),
            ).fetchone()
        return self._row_to_chat_session(row) if row else None

    def update_chat_session(
        self,
        session_id: str,
        *,
        persona_id: Any = _RAW_PATCH_MISSING,
        custom_persona: Any = _RAW_PATCH_MISSING,
        summary: str | None = None,
        touch_summary: bool = False,
        last_message_at: datetime | None = None,
    ) -> None:
        updates: list[str] = []
        parameters: list[Any] = []
        if persona_id is not _RAW_PATCH_MISSING:
            updates.append("persona_id = ?")
            parameters.append(persona_id)
        if custom_persona is not _RAW_PATCH_MISSING:
            updates.append("custom_persona_json = ?")
            parameters.append(
                json.dumps(custom_persona, ensure_ascii=False)
                if custom_persona is not None
                else None
            )
        if summary is not None:
            updates.append("summary = ?")
            parameters.append(summary)
        if touch_summary:
            updates.append("summary_updated_at = ?")
            parameters.append(isoformat_z(utc_now()))
        if last_message_at is not None:
            updates.append("last_message_at = ?")
            parameters.append(isoformat_z(last_message_at))
        if not updates:
            return
        updates.append("updated_at = ?")
        parameters.append(isoformat_z(utc_now()))
        parameters.append(session_id)
        statement = f"UPDATE chat_sessions SET {', '.join(updates)} WHERE id = ?"
        with self._lock:
            connection = self._get_connection()
            connection.execute(statement, tuple(parameters))
            connection.commit()

    def clear_chat_messages(self, session_id: str) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                "DELETE FROM chat_messages WHERE session_id = ?",
                (session_id,),
            )
            connection.commit()

    def append_chat_message(self, record: ChatMessageRecord) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                """
                INSERT INTO chat_messages (
                  id, session_id, role, content, name, tool_call_id,
                  tool_calls_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.session_id,
                    record.role,
                    record.content,
                    record.name,
                    record.tool_call_id,
                    json.dumps(record.tool_calls, ensure_ascii=False)
                    if record.tool_calls
                    else None,
                    isoformat_z(record.created_at),
                ),
            )
            connection.commit()

    def append_chat_messages_batch(self, records: list[ChatMessageRecord]) -> None:
        if not records:
            return
        with self._lock:
            connection = self._get_connection()
            rows = []
            for record in records:
                rows.append((
                    record.id,
                    record.session_id,
                    record.role,
                    record.content,
                    record.name,
                    record.tool_call_id,
                    json.dumps(record.tool_calls, ensure_ascii=False)
                    if record.tool_calls
                    else None,
                    isoformat_z(record.created_at),
                ))
            connection.executemany(
                """
                INSERT INTO chat_messages (
                  id, session_id, role, content, name, tool_call_id,
                  tool_calls_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()

    def list_chat_messages(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> list[ChatMessageRecord]:
        capped = max(1, min(limit, 200))
        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = ?
                ORDER BY datetime(created_at) DESC, rowid DESC
                LIMIT ?
                """,
                (session_id, capped),
            ).fetchall()
        records = [self._row_to_chat_message(row) for row in rows]
        records.reverse()
        return records

    def count_chat_messages(self, session_id: str) -> int:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def trim_chat_messages(self, session_id: str, *, keep_latest: int) -> int:
        kept = max(keep_latest, 0)
        with self._lock:
            connection = self._get_connection()
            cursor = connection.execute(
                """
                DELETE FROM chat_messages
                WHERE session_id = ?
                  AND rowid NOT IN (
                    SELECT rowid FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY datetime(created_at) DESC, rowid DESC
                    LIMIT ?
                  )
                """,
                (session_id, session_id, kept),
            )
            connection.commit()
            return int(cursor.rowcount or 0)

    def upsert_chat_profile(self, profile: ChatProfile) -> ChatProfile:
        now = utc_now()
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT id, created_at FROM chat_profiles WHERE scope = ? AND user_id = ?",
                (profile.scope, profile.user_id),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE chat_profiles
                    SET display_name = ?, preferences_json = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        profile.display_name,
                        json.dumps(profile.preferences, ensure_ascii=False),
                        profile.notes,
                        isoformat_z(now),
                        row["id"],
                    ),
                )
                profile.id = str(row["id"])
                profile.created_at = datetime.fromisoformat(
                    str(row["created_at"]).replace("Z", "+00:00")
                )
                profile.updated_at = now
            else:
                connection.execute(
                    """
                    INSERT INTO chat_profiles (
                      id, scope, user_id, display_name, preferences_json,
                      notes, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.id,
                        profile.scope,
                        profile.user_id,
                        profile.display_name,
                        json.dumps(profile.preferences, ensure_ascii=False),
                        profile.notes,
                        isoformat_z(profile.created_at),
                        isoformat_z(now),
                    ),
                )
                profile.updated_at = now
            connection.commit()
        return profile

    def get_chat_profile(self, *, scope: str, user_id: str) -> ChatProfile | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM chat_profiles WHERE scope = ? AND user_id = ?",
                (scope, user_id),
            ).fetchone()
        return self._row_to_chat_profile(row) if row else None

    def upsert_chat_persona(self, persona: ChatPersona) -> ChatPersona:
        now = utc_now()
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT id, created_at FROM chat_personas WHERE persona_key = ?",
                (persona.persona_key,),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE chat_personas
                    SET label = ?, description = ?, system_prompt = ?, is_builtin = ?,
                        allow_tools = ?, tone = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        persona.label,
                        persona.description,
                        persona.system_prompt,
                        1 if persona.is_builtin else 0,
                        1 if persona.allow_tools else 0,
                        persona.tone,
                        isoformat_z(now),
                        row["id"],
                    ),
                )
                persona.id = str(row["id"])
                persona.created_at = datetime.fromisoformat(
                    str(row["created_at"]).replace("Z", "+00:00")
                )
                persona.updated_at = now
            else:
                connection.execute(
                    """
                    INSERT INTO chat_personas (
                      id, persona_key, label, description, system_prompt,
                      is_builtin, allow_tools, tone, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        persona.id,
                        persona.persona_key,
                        persona.label,
                        persona.description,
                        persona.system_prompt,
                        1 if persona.is_builtin else 0,
                        1 if persona.allow_tools else 0,
                        persona.tone,
                        isoformat_z(persona.created_at),
                        isoformat_z(now),
                    ),
                )
                persona.updated_at = now
            connection.commit()
        return persona

    def get_chat_persona(self, persona_id_or_key: str) -> ChatPersona | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM chat_personas WHERE id = ? OR persona_key = ? LIMIT 1",
                (persona_id_or_key, persona_id_or_key),
            ).fetchone()
        return self._row_to_chat_persona(row) if row else None

    def list_chat_personas(self, *, include_custom: bool = True) -> list[ChatPersona]:
        with self._lock:
            connection = self._get_connection()
            if include_custom:
                rows = connection.execute(
                    "SELECT * FROM chat_personas ORDER BY is_builtin DESC, persona_key ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM chat_personas WHERE is_builtin = 1 ORDER BY persona_key ASC"
                ).fetchall()
        return [self._row_to_chat_persona(row) for row in rows]

    def delete_chat_persona(self, persona_id_or_key: str) -> bool:
        with self._lock:
            connection = self._get_connection()
            cursor = connection.execute(
                "DELETE FROM chat_personas WHERE (id = ? OR persona_key = ?) AND is_builtin = 0",
                (persona_id_or_key, persona_id_or_key),
            )
            connection.commit()
            return (cursor.rowcount or 0) > 0

    def upsert_chat_knowledge_item(self, item: ChatKnowledgeItem) -> ChatKnowledgeItem:
        now = utc_now()
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT id, created_at FROM chat_knowledge_items WHERE id = ?",
                (item.id,),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE chat_knowledge_items
                    SET topic = ?, content = ?, tags_json = ?, priority = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        item.topic,
                        item.content,
                        json.dumps(item.tags, ensure_ascii=False),
                        item.priority,
                        isoformat_z(now),
                        row["id"],
                    ),
                )
                item.created_at = datetime.fromisoformat(
                    str(row["created_at"]).replace("Z", "+00:00")
                )
                item.updated_at = now
            else:
                connection.execute(
                    """
                    INSERT INTO chat_knowledge_items (
                      id, topic, content, tags_json, priority, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id,
                        item.topic,
                        item.content,
                        json.dumps(item.tags, ensure_ascii=False),
                        item.priority,
                        isoformat_z(item.created_at),
                        isoformat_z(now),
                    ),
                )
                item.updated_at = now
            connection.commit()
        return item

    def delete_chat_knowledge_item(self, item_id: str) -> bool:
        with self._lock:
            connection = self._get_connection()
            cursor = connection.execute(
                "DELETE FROM chat_knowledge_items WHERE id = ?",
                (item_id,),
            )
            connection.commit()
            return (cursor.rowcount or 0) > 0

    def list_chat_knowledge_items(self, *, limit: int = 100) -> list[ChatKnowledgeItem]:
        capped = max(1, min(limit, 500))
        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(
                """
                SELECT * FROM chat_knowledge_items
                ORDER BY priority DESC, datetime(updated_at) DESC
                LIMIT ?
                """,
                (capped,),
            ).fetchall()
        return [self._row_to_chat_knowledge_item(row) for row in rows]

    def search_chat_knowledge_items(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> list[ChatKnowledgeItem]:
        tokens = [token.strip() for token in query.split() if token.strip()]
        if not tokens:
            return []
        clauses: list[str] = []
        parameters: list[Any] = []
        for token in tokens[:5]:
            clauses.append("(topic LIKE ? OR content LIKE ? OR tags_json LIKE ?)")
            like = f"%{token}%"
            parameters.extend([like, like, like])
        statement = (
            "SELECT * FROM chat_knowledge_items "
            f"WHERE {' OR '.join(clauses)} "
            "ORDER BY priority DESC, datetime(updated_at) DESC LIMIT ?"
        )
        parameters.append(max(1, min(limit, 20)))
        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        return [self._row_to_chat_knowledge_item(row) for row in rows]

    # ------------------------------------------------------------------
    # llm_providers
    # ------------------------------------------------------------------

    def _row_to_llm_provider_record(self, row: Any) -> LLMProviderRecord:
        return LLMProviderRecord(
            id=row["id"],
            label=row["label"] or "",
            base_url=row["base_url"] or "",
            api_key=row["api_key"] or "",
            model=row["model"] or "",
            is_active=bool(row["is_active"]),
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def _row_to_provider_config(self, row: Any) -> ProviderConfig:
        return ProviderConfig(
            key=row["id"],
            label=row["label"] or "",
            base_url=row["base_url"] or "",
            api_key=row["api_key"] or "",
            model=row["model"] or "",
            is_active=bool(row["is_active"]),
        )

    def upsert_llm_provider(self, record: LLMProviderRecord) -> None:
        with self._lock:
            connection = self._get_connection()
            connection.execute(
                "INSERT OR REPLACE INTO llm_providers "
                "(id, label, base_url, api_key, model, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id, record.label, record.base_url, record.api_key,
                    record.model, int(record.is_active), record.created_at, record.updated_at,
                ),
            )

    def list_llm_providers(self) -> list[LLMProviderRecord]:
        with self._lock:
            connection = self._get_connection()
            rows = connection.execute(
                "SELECT * FROM llm_providers ORDER BY label"
            ).fetchall()
        return [self._row_to_llm_provider_record(row) for row in rows]

    def get_active_llm_provider(self) -> ProviderConfig | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM llm_providers WHERE is_active = 1 LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self._row_to_provider_config(row)

    def set_active_llm_provider(self, provider_id: str) -> bool:
        with self._lock:
            connection = self._get_connection()
            exists = connection.execute(
                "SELECT id FROM llm_providers WHERE id = ?", (provider_id,)
            ).fetchone()
            if not exists:
                return False
            connection.execute("UPDATE llm_providers SET is_active = 0")
            connection.execute(
                "UPDATE llm_providers SET is_active = 1, updated_at = ? WHERE id = ?",
                (isoformat_z(utc_now()), provider_id),
            )
        return True

    def insert_llm_provider(self, record: LLMProviderRecord) -> bool:
        """Insert a new provider if id doesn't exist. Returns True if inserted."""
        with self._lock:
            connection = self._get_connection()
            existing = connection.execute(
                "SELECT id FROM llm_providers WHERE id = ?", (record.id,)
            ).fetchone()
            if existing:
                return False
            connection.execute(
                "INSERT INTO llm_providers (id, label, base_url, api_key, model, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id, record.label, record.base_url, record.api_key,
                    record.model, int(record.is_active), record.created_at, record.updated_at,
                ),
            )
        return True

    def update_llm_provider_api_key(self, provider_id: str, api_key: str) -> bool:
        with self._lock:
            connection = self._get_connection()
            existing = connection.execute(
                "SELECT id FROM llm_providers WHERE id = ?", (provider_id,)
            ).fetchone()
            if not existing:
                return False
            connection.execute(
                "UPDATE llm_providers SET api_key = ?, updated_at = ? WHERE id = ?",
                (api_key, isoformat_z(utc_now()), provider_id),
            )
        return True

    def update_llm_provider_model(self, provider_id: str, model: str) -> bool:
        with self._lock:
            connection = self._get_connection()
            existing = connection.execute(
                "SELECT id FROM llm_providers WHERE id = ?", (provider_id,)
            ).fetchone()
            if not existing:
                return False
            connection.execute(
                "UPDATE llm_providers SET model = ?, updated_at = ? WHERE id = ?",
                (model, isoformat_z(utc_now()), provider_id),
            )
        return True

    def update_llm_provider_base_url(self, provider_id: str, base_url: str) -> bool:
        with self._lock:
            connection = self._get_connection()
            existing = connection.execute(
                "SELECT id FROM llm_providers WHERE id = ?", (provider_id,)
            ).fetchone()
            if not existing:
                return False
            connection.execute(
                "UPDATE llm_providers SET base_url = ?, updated_at = ? WHERE id = ?",
                (base_url, isoformat_z(utc_now()), provider_id),
            )
        return True

    def get_llm_provider(self, provider_id: str) -> LLMProviderRecord | None:
        with self._lock:
            connection = self._get_connection()
            row = connection.execute(
                "SELECT * FROM llm_providers WHERE id = ?", (provider_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_llm_provider_record(row)

    def get_stats(self) -> dict[str, int]:
        with self._lock:
            connection = self._get_connection()
            def count(query: str) -> int:
                row = connection.execute(query).fetchone()
                return int(row[0]) if row else 0

            return {
                "raw_events": count("SELECT COUNT(*) FROM raw_events"),
                "processed_events": count("SELECT COUNT(*) FROM processed_events"),
                "render_artifacts": count("SELECT COUNT(*) FROM render_artifacts"),
                "delivery_logs": count("SELECT COUNT(*) FROM delivery_logs"),
                "delivery_records": count("SELECT COUNT(*) FROM delivery_records"),
                "platform_events": count("SELECT COUNT(*) FROM platform_events"),
                "chat_sessions": count("SELECT COUNT(*) FROM chat_sessions"),
                "chat_messages": count("SELECT COUNT(*) FROM chat_messages"),
                "chat_personas": count("SELECT COUNT(*) FROM chat_personas"),
                "chat_profiles": count("SELECT COUNT(*) FROM chat_profiles"),
                "chat_knowledge_items": count("SELECT COUNT(*) FROM chat_knowledge_items"),
                "llm_providers": count("SELECT COUNT(*) FROM llm_providers"),
            }

    def _ensure_source_locked(
        self,
        connection: sqlite3.Connection,
        *,
        source_type: str,
        source_name: str,
    ) -> str:
        row = connection.execute(
            "SELECT id FROM sources WHERE source_type = ? AND name = ?",
            (source_type, source_name),
        ).fetchone()
        if row:
            return str(row["id"])
        source_id = f"src_{abs(hash((source_type, source_name)))}"
        connection.execute(
            """
            INSERT INTO sources (id, source_type, name, status, created_at)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (source_id, source_type, source_name, isoformat_z(utc_now())),
        )
        return source_id

    @staticmethod
    def _run_migrations_locked(connection: sqlite3.Connection) -> None:
        SQLiteRepository._ensure_column_locked(
            connection,
            table_name="delivery_records",
            column_name="request_payload_json",
            column_sql="TEXT NOT NULL DEFAULT '{}'",
        )
        SQLiteRepository._ensure_column_locked(
            connection,
            table_name="delivery_records",
            column_name="next_retry_at",
            column_sql="TEXT",
        )

    @staticmethod
    def _ensure_column_locked(
        connection: sqlite3.Connection,
        *,
        table_name: str,
        column_name: str,
        column_sql: str,
    ) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {str(row["name"]) for row in rows}
        if column_name in existing_columns:
            return
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    @staticmethod
    def _row_to_raw_event(row: sqlite3.Row) -> RawEvent:
        return RawEvent(
            id=row["id"],
            source_id=row["source_id"],
            source_type=row["source_type"],
            source_name=row["source_name"],
            channel_name=row["channel_name"],
            author=row["author"],
            content=row["content"],
            attachments=json.loads(row["attachments_json"]),
            external_id=row["external_id"],
            published_at=datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00")),
            raw_payload=json.loads(row["raw_payload_json"]),
            status=row["status"],
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    @staticmethod
    def _row_to_delivery_record(row: sqlite3.Row) -> DeliveryRecord:
        request_payload_json = row["request_payload_json"] if "request_payload_json" in row.keys() else "{}"
        next_retry_raw = row["next_retry_at"] if "next_retry_at" in row.keys() else None
        next_retry_at = (
            datetime.fromisoformat(str(next_retry_raw).replace("Z", "+00:00"))
            if next_retry_raw
            else None
        )
        return DeliveryRecord(
            id=row["id"],
            trace_id=row["trace_id"],
            processed_event_id=row["processed_event_id"],
            platform=row["platform"],
            adapter=row["adapter"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            status=row["status"],
            message_id=row["message_id"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            attempts=int(row["attempts"]),
            request_payload=json.loads(request_payload_json or "{}"),
            next_retry_at=next_retry_at,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    @staticmethod
    def _row_to_processed_event(row: sqlite3.Row) -> ProcessedEvent:
        media_items = [
            MediaAsset.model_validate(item)
            for item in json.loads(row["media_json"])
        ]
        return ProcessedEvent(
            id=row["id"],
            raw_event_id=row["raw_event_id"],
            title=row["title"],
            summary=row["summary"],
            highlights=json.loads(row["highlights_json"]),
            category=row["category"],
            game=row["game"],
            need_translation=bool(row["need_translation"]),
            source_credibility=row["source_credibility"],
            media=media_items,
            discovered_sources=json.loads(row["discovered_sources_json"]),
            language=row["language"],
            render_status=row["render_status"],
            delivery_status=row["delivery_status"],
            published_at=datetime.fromisoformat(str(row["published_at"]).replace("Z", "+00:00")),
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    @staticmethod
    def _row_to_chat_session(row: sqlite3.Row) -> ChatSession:
        custom_persona_raw = row["custom_persona_json"] if "custom_persona_json" in row.keys() else None
        custom_persona_data = None
        if custom_persona_raw:
            try:
                data = json.loads(custom_persona_raw)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                from .models import CustomPersonaPayload

                custom_persona_data = CustomPersonaPayload.model_validate(data)
        return ChatSession(
            id=str(row["id"]),
            session_key=str(row["session_key"]),
            origin=str(row["origin"]),
            scope=str(row["scope"]),
            group_id=row["group_id"],
            user_id=row["user_id"],
            persona_id=row["persona_id"],
            custom_persona=custom_persona_data,
            summary=str(row["summary"] or ""),
            summary_updated_at=SQLiteRepository._parse_datetime(row["summary_updated_at"]),
            last_message_at=SQLiteRepository._parse_datetime(row["last_message_at"]),
            created_at=SQLiteRepository._parse_datetime(row["created_at"]) or utc_now(),
            updated_at=SQLiteRepository._parse_datetime(row["updated_at"]) or utc_now(),
        )

    @staticmethod
    def _row_to_chat_message(row: sqlite3.Row) -> ChatMessageRecord:
        tool_calls_raw = row["tool_calls_json"] if "tool_calls_json" in row.keys() else None
        tool_calls: list[dict[str, Any]] | None = None
        if tool_calls_raw:
            try:
                parsed = json.loads(tool_calls_raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                tool_calls = parsed
        return ChatMessageRecord(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            role=str(row["role"]),
            content=str(row["content"] or ""),
            name=row["name"],
            tool_call_id=row["tool_call_id"],
            tool_calls=tool_calls,
            created_at=SQLiteRepository._parse_datetime(row["created_at"]) or utc_now(),
        )

    @staticmethod
    def _row_to_chat_profile(row: sqlite3.Row) -> ChatProfile:
        preferences: dict[str, Any] = {}
        raw_prefs = row["preferences_json"] if "preferences_json" in row.keys() else "{}"
        try:
            parsed = json.loads(raw_prefs or "{}")
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            preferences = parsed
        return ChatProfile(
            id=str(row["id"]),
            scope=str(row["scope"]),
            user_id=str(row["user_id"]),
            display_name=row["display_name"],
            preferences=preferences,
            notes=str(row["notes"] or ""),
            created_at=SQLiteRepository._parse_datetime(row["created_at"]) or utc_now(),
            updated_at=SQLiteRepository._parse_datetime(row["updated_at"]) or utc_now(),
        )

    @staticmethod
    def _row_to_chat_persona(row: sqlite3.Row) -> ChatPersona:
        return ChatPersona(
            id=str(row["id"]),
            persona_key=str(row["persona_key"]),
            label=str(row["label"]),
            description=str(row["description"] or ""),
            system_prompt=str(row["system_prompt"]),
            is_builtin=bool(row["is_builtin"]),
            allow_tools=bool(row["allow_tools"]),
            tone=row["tone"],
            created_at=SQLiteRepository._parse_datetime(row["created_at"]) or utc_now(),
            updated_at=SQLiteRepository._parse_datetime(row["updated_at"]) or utc_now(),
        )

    @staticmethod
    def _row_to_chat_knowledge_item(row: sqlite3.Row) -> ChatKnowledgeItem:
        tags: list[str] = []
        raw_tags = row["tags_json"] if "tags_json" in row.keys() else "[]"
        try:
            parsed = json.loads(raw_tags or "[]")
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            tags = [str(item) for item in parsed if isinstance(item, (str, int, float))]
        return ChatKnowledgeItem(
            id=str(row["id"]),
            topic=str(row["topic"]),
            content=str(row["content"]),
            tags=tags,
            priority=int(row["priority"] or 0),
            created_at=SQLiteRepository._parse_datetime(row["created_at"]) or utc_now(),
            updated_at=SQLiteRepository._parse_datetime(row["updated_at"]) or utc_now(),
        )

    @staticmethod
    def _row_to_render_artifact(row: sqlite3.Row) -> RenderArtifact:
        return RenderArtifact(
            id=row["id"],
            processed_event_id=row["processed_event_id"],
            template_name=row["template_name"],
            theme=row["theme"],
            html_path=row["html_path"],
            image_path=row["image_path"],
            width=int(row["width"]),
            height=int(row["height"]) if row["height"] is not None else None,
            created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
        )

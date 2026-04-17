from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> frozenset[str]:
    value = os.getenv(name, "")
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _env_float_tuple(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return default
    return tuple(float(part) for part in parts)


@dataclass(frozen=True)
class Settings:
    app_name: str
    database_path: Path
    artifacts_dir: Path
    screenshot_enabled: bool
    worker_concurrency: int
    queue_maxsize: int
    auto_deliver_enabled: bool
    default_qq_group_id: str | None
    qq_admin_user_ids: frozenset[str]
    qq_admin_group_ids: frozenset[str]
    qq_news_card_max_bytes: int
    delivery_retry_delays_seconds: tuple[float, ...]
    napcat_base_url: str
    napcat_access_token: str | None
    napcat_timeout_seconds: float
    napcat_ws_url: str | None
    napcat_ws_reconnect_base_seconds: float
    napcat_ws_reconnect_max_seconds: float
    delivery_worker_poll_seconds: float
    delivery_worker_enabled: bool
    delivery_dead_letter_max_attempts: int
    delivery_alert_consecutive_failures: int
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    llm_timeout_seconds: float
    llm_agent_enabled: bool
    llm_agent_max_tool_steps: int
    llm_agent_schedule_interval_minutes: float
    llm_agent_schedule_enabled: bool
    llm_agent_temperature: float
    llm_agent_max_reply_chars: int
    rsshub_host_markers: frozenset[str]
    rsshub_extra_hosts: frozenset[str]
    qq_image_fail_text_fallback_enabled: bool


def load_settings() -> Settings:
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "./artifacts")).resolve()
    database_path = Path(os.getenv("DATABASE_PATH", str(artifacts_dir / "ddrbbot.db"))).resolve()
    return Settings(
        app_name=os.getenv("APP_NAME", "DDRBBOT"),
        database_path=database_path,
        artifacts_dir=artifacts_dir,
        screenshot_enabled=_env_bool("SCREENSHOT_ENABLED", True),
        worker_concurrency=max(int(os.getenv("WORKER_CONCURRENCY", "1")), 1),
        queue_maxsize=max(int(os.getenv("QUEUE_MAXSIZE", "200")), 1),
        auto_deliver_enabled=_env_bool("AUTO_DELIVER_ENABLED", False),
        default_qq_group_id=os.getenv("DEFAULT_QQ_GROUP_ID") or None,
        qq_admin_user_ids=_env_csv("QQ_ADMIN_USER_IDS"),
        qq_admin_group_ids=_env_csv("QQ_ADMIN_GROUP_IDS"),
        qq_news_card_max_bytes=max(int(os.getenv("QQ_NEWS_CARD_MAX_BYTES", "10485760")), 1),
        delivery_retry_delays_seconds=_env_float_tuple(
            "DELIVERY_RETRY_DELAYS_SECONDS",
            (10.0, 30.0, 120.0),
        ),
        napcat_base_url=os.getenv("NAPCAT_BASE_URL", "http://127.0.0.1:3000").rstrip("/"),
        napcat_access_token=os.getenv("NAPCAT_ACCESS_TOKEN") or None,
        napcat_timeout_seconds=float(os.getenv("NAPCAT_TIMEOUT_SECONDS", "10")),
        napcat_ws_url=(os.getenv("NAPCAT_WS_URL") or "").strip() or None,
        napcat_ws_reconnect_base_seconds=max(
            float(os.getenv("NAPCAT_WS_RECONNECT_BASE_SECONDS", "2")), 0.1
        ),
        napcat_ws_reconnect_max_seconds=max(
            float(os.getenv("NAPCAT_WS_RECONNECT_MAX_SECONDS", "60")), 1.0
        ),
        delivery_worker_poll_seconds=max(
            float(os.getenv("DELIVERY_WORKER_POLL_SECONDS", "2")), 0.2
        ),
        delivery_worker_enabled=_env_bool("DELIVERY_WORKER_ENABLED", True),
        delivery_dead_letter_max_attempts=max(
            int(os.getenv("DELIVERY_DEAD_LETTER_MAX_ATTEMPTS", "6")), 1
        ),
        delivery_alert_consecutive_failures=max(
            int(os.getenv("DELIVERY_ALERT_CONSECUTIVE_FAILURES", "5")), 1
        ),
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL") or None,
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        llm_agent_enabled=_env_bool("LLM_AGENT_ENABLED", False),
        llm_agent_max_tool_steps=max(int(os.getenv("LLM_AGENT_MAX_TOOL_STEPS", "6")), 1),
        llm_agent_schedule_interval_minutes=max(
            float(os.getenv("LLM_AGENT_SCHEDULE_INTERVAL_MINUTES", "60")), 0.0
        ),
        llm_agent_schedule_enabled=_env_bool("LLM_AGENT_SCHEDULE_ENABLED", False),
        llm_agent_temperature=float(os.getenv("LLM_AGENT_TEMPERATURE", "0.3")),
        llm_agent_max_reply_chars=max(int(os.getenv("LLM_AGENT_MAX_REPLY_CHARS", "2000")), 100),
        rsshub_host_markers=_env_csv("RSSHUB_HOST_MARKERS") or frozenset({"rsshub"}),
        rsshub_extra_hosts=_env_csv("RSSHUB_EXTRA_HOSTS") or frozenset({"localhost", "127.0.0.1"}),
        qq_image_fail_text_fallback_enabled=_env_bool("QQ_IMAGE_FAIL_TEXT_FALLBACK", True),
    )

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..copybook import copy_text
from ..database import SQLiteRepository
from ..models import LLMProviderRecord, ProviderConfig
from ..utils import utc_now, isoformat_z

logger = logging.getLogger(__name__)


class ProviderStore:
    """Manage LLM provider configurations in the database.

    Seeds default providers from a JSON list (e.g. from LLM_PROVIDER_SEEDS_JSON env var),
    then provides get_active / set_active / list_all for runtime switching.
    """

    def __init__(self, repository: SQLiteRepository, seed_providers: list[dict[str, str]] | None = None) -> None:
        self._repository = repository
        self._seed_providers = seed_providers or []

    # ------------------------------------------------------------------
    # seeding
    # ------------------------------------------------------------------

    def seed_defaults(self) -> int:
        """Idempotent: insert seed providers that don't exist yet. Returns count seeded."""
        seeded = 0
        now = utc_now()
        now_str = isoformat_z(now)
        for entry in self._seed_providers:
            key = (entry.get("key") or entry.get("id") or "").strip()
            if not key or not entry.get("base_url"):
                continue
            # Don't overwrite existing provider
            existing = self._repository.list_llm_providers()
            if any(p.id == key for p in existing):
                continue
            record = LLMProviderRecord(
                id=key,
                label=entry.get("label") or key,
                base_url=entry.get("base_url") or "",
                api_key=entry.get("api_key") or "",
                model=entry.get("model") or "",
                is_active=bool(entry.get("is_active")) or len(existing) == 0,
                created_at=now_str,
                updated_at=now_str,
            )
            self._repository.upsert_llm_provider(record)
            seeded += 1

        # If no provider is active and we have at least one, activate the first
        active = self._repository.get_active_llm_provider()
        if active is None:
            all_providers = self._repository.list_llm_providers()
            if all_providers:
                first = all_providers[0]
                self._repository.set_active_llm_provider(first.id)
                logger.info("No active provider; activated %s", first.id)

        if seeded:
            logger.info("Seeded %d new LLM provider(s)", seeded)
        return seeded

    # ------------------------------------------------------------------
    # runtime access
    # ------------------------------------------------------------------

    def get_active(self) -> ProviderConfig | None:
        return self._repository.get_active_llm_provider()

    def set_active(self, provider_key: str) -> bool:
        ok = self._repository.set_active_llm_provider(provider_key)
        if ok:
            logger.info("Switched LLM provider to %s", provider_key)
        return ok

    def list_all(self) -> list[ProviderConfig]:
        rows = self._repository.list_llm_providers()
        return [
            self._repository._row_to_provider_config(r) if hasattr(r, "keys") else ProviderConfig(
                key=r.id, label=r.label, base_url=r.base_url,
                api_key=r.api_key, model=r.model, is_active=r.is_active,
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # management
    # ------------------------------------------------------------------

    def get_provider(self, provider_key: str) -> ProviderConfig | None:
        row = self._repository.get_llm_provider(provider_key)
        if row is None:
            return None
        return ProviderConfig(
            key=row.id, label=row.label, base_url=row.base_url,
            api_key=row.api_key, model=row.model, is_active=row.is_active,
        )

    def update_api_key(self, provider_key: str, api_key: str) -> bool:
        return self._repository.update_llm_provider_api_key(provider_key, api_key)

    def update_model(self, provider_key: str, model: str) -> bool:
        return self._repository.update_llm_provider_model(provider_key, model)

    def update_base_url(self, provider_key: str, base_url: str) -> bool:
        return self._repository.update_llm_provider_base_url(provider_key, base_url)

    def add_provider(self, key: str, label: str, base_url: str,
                     api_key: str = "", model: str = "") -> bool:
        now = utc_now()
        now_str = isoformat_z(now)
        existing = self._repository.list_llm_providers()
        record = LLMProviderRecord(
            id=key, label=label, base_url=base_url, api_key=api_key,
            model=model, is_active=len(existing) == 0,
            created_at=now_str, updated_at=now_str,
        )
        ok = self._repository.insert_llm_provider(record)
        if ok:
            logger.info("Added LLM provider %s (%s)", key, label)
        return ok

    # ------------------------------------------------------------------
    # model discovery
    # ------------------------------------------------------------------

    @staticmethod
    async def fetch_models_via_http(base_url: str, api_key: str,
                                    timeout: float = 15.0) -> list[str]:
        """Fetch available model IDs from an OpenAI-compatible /models endpoint."""
        url = f"{base_url.rstrip('/')}/models"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        models = data.get("data") or []
        return [m.get("id", "") for m in models if m.get("id")]

    # ------------------------------------------------------------------
    # copybook helpers
    # ------------------------------------------------------------------

    @staticmethod
    def switch_success(provider_key: str, label: str) -> str:
        return copy_text(
            "switch.success",
            "已切换到 {label} ({key})。",
            label=label,
            key=provider_key,
        )

    @staticmethod
    def switch_not_found(provider_key: str) -> str:
        return copy_text(
            "switch.not_found",
            "没有找到 provider：{key}。用 /switch list 查看可用列表。",
            key=provider_key,
        )

    @staticmethod
    def switch_list_header() -> str:
        return copy_text("switch.list_header", "可用的 LLM Provider：")

    @staticmethod
    def switch_list_item(key: str, label: str, active: bool) -> str:
        marker = " [当前]" if active else ""
        return copy_text(
            "switch.list_item",
            "- {key}: {label}{marker}",
            key=key,
            label=label,
            marker=marker,
        )

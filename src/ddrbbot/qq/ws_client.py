from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from ..config import Settings
from ..database import SQLiteRepository
from ..models import QQInboundEvent
from .commands import QQCommandRouter
from .napcat import normalize_inbound_event

logger = logging.getLogger(__name__)

EventHandler = Callable[[QQInboundEvent], Awaitable[Any]]


async def handle_inbound_event(
    payload: dict[str, Any],
    *,
    repository: SQLiteRepository,
    command_router: QQCommandRouter,
) -> tuple[QQInboundEvent, Any]:
    event = normalize_inbound_event(payload)
    repository.save_platform_event(event)
    result = await command_router.dispatch(event)
    return event, result


class NapCatWSClient:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: SQLiteRepository,
        command_router: QQCommandRouter,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.command_router = command_router
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._connected = False

    @property
    def enabled(self) -> bool:
        return bool(self.settings.napcat_ws_url)

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run_loop(), name="napcat-ws-client")

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run_loop(self) -> None:
        try:
            import websockets
        except ImportError:  # pragma: no cover - dependency missing
            logger.error("websockets package is not installed; NapCat WS disabled.")
            return

        url = self.settings.napcat_ws_url
        if not url:
            return
        headers: list[tuple[str, str]] = []
        if self.settings.napcat_access_token:
            headers.append(("Authorization", f"Bearer {self.settings.napcat_access_token}"))

        attempt = 0
        base = self.settings.napcat_ws_reconnect_base_seconds
        cap = self.settings.napcat_ws_reconnect_max_seconds

        while not self._stopped.is_set():
            try:
                logger.info("NapCat WS connecting to %s", url)
                connect_kwargs: dict[str, Any] = {"ping_interval": 30, "ping_timeout": 20}
                if headers:
                    connect_kwargs["additional_headers"] = headers
                async with websockets.connect(url, **connect_kwargs) as ws:
                    self._connected = True
                    attempt = 0
                    logger.info("NapCat WS connected")
                    async for message in ws:
                        if self._stopped.is_set():
                            break
                        await self._handle_message(message)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("NapCat WS connection error: %s", exc)
            finally:
                self._connected = False

            if self._stopped.is_set():
                break
            attempt += 1
            delay = min(base * (2 ** (attempt - 1)), cap)
            logger.info("NapCat WS reconnecting in %.1fs (attempt %d)", delay, attempt)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                continue

    async def _handle_message(self, message: Any) -> None:
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8")
            except UnicodeDecodeError:
                logger.debug("Dropping non-UTF8 binary WS frame")
                return
        if not isinstance(message, str):
            return
        text = message.strip()
        if not text or not text.startswith("{"):
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("NapCat WS invalid JSON: %s", exc)
            return
        if not isinstance(payload, dict):
            return
        try:
            await handle_inbound_event(
                payload,
                repository=self.repository,
                command_router=self.command_router,
            )
        except Exception as exc:  # pragma: no cover - handler errors
            logger.exception("NapCat WS event handling failed: %s", exc)

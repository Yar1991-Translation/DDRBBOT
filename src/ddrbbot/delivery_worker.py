from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from .config import Settings
from .database import SQLiteRepository
from .delivery import QQDeliveryService
from .models import DeliveryRecord
from .utils import utc_now

logger = logging.getLogger(__name__)


class DeliveryWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: SQLiteRepository,
        delivery_service: QQDeliveryService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.delivery_service = delivery_service
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._consecutive_failures = 0
        self._alerts_emitted = 0

    async def start(self) -> None:
        if self._task is not None or not self.settings.delivery_worker_enabled:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._poll_loop(), name="delivery-worker")

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

    async def _poll_loop(self) -> None:
        interval = self.settings.delivery_worker_poll_seconds
        while not self._stopped.is_set():
            try:
                await self.tick()
            except Exception as exc:  # pragma: no cover - db errors
                logger.exception("Delivery worker poll error: %s", exc)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                continue

    async def tick(self, *, limit: int = 10) -> int:
        """Process all currently-due delivery records once. Returns count processed."""
        records = self.repository.list_due_delivery_records(
            statuses=("pending", "retry"),
            limit=limit,
        )
        processed = 0
        for record in records:
            if self._stopped.is_set():
                break
            try:
                await self._process_one(record)
                processed += 1
            except Exception as exc:  # pragma: no cover - unexpected
                logger.exception(
                    "Delivery worker crashed on trace_id=%s: %s",
                    record.trace_id,
                    exc,
                )
        return processed

    async def drain(self, *, max_rounds: int = 20) -> int:
        """Repeatedly tick until no more due records are found or max_rounds reached."""
        total = 0
        for _ in range(max_rounds):
            processed = await self.tick()
            if processed == 0:
                break
            total += processed
        return total

    async def _process_one(self, record: DeliveryRecord) -> None:
        trace_id = record.trace_id
        try:
            request = self.delivery_service.request_from_record(record)
        except Exception as exc:
            logger.warning(
                "Delivery worker: invalid payload for trace_id=%s: %s",
                trace_id,
                exc,
            )
            self.repository.update_delivery_record(
                trace_id,
                status="dead_letter",
                error_code="invalid_payload",
                error_message=str(exc),
                next_retry_at=None,
            )
            self._record_failure()
            return

        self.repository.update_delivery_record(trace_id, status="processing")
        ok, message_id, error = await self.delivery_service.send_once(
            request,
            trace_id=trace_id,
        )
        if ok:
            self.repository.update_delivery_record(
                trace_id,
                status="sent",
                message_id=message_id or "",
                error_code="",
                error_message="",
                attempts=record.attempts + 1,
                next_retry_at=None,
            )
            self._record_success()
            return

        current_attempt = record.attempts + 1
        max_attempts = self.settings.delivery_dead_letter_max_attempts
        if current_attempt < max_attempts:
            delay = self._delay_for_attempt(current_attempt)
            next_retry = utc_now() + timedelta(seconds=delay)
            self.repository.update_delivery_record(
                trace_id,
                status="retry",
                error_code=error or "",
                error_message=error or "",
                attempts=current_attempt,
                next_retry_at=next_retry,
            )
            self._record_failure()
            return

        ok_text, text_message_id, text_err = await self.delivery_service.send_text_fallback_once(
            request,
        )
        if ok_text:
            self.repository.update_delivery_record(
                trace_id,
                status="sent",
                message_id=text_message_id or "",
                error_code="text_fallback",
                error_message=error or "",
                attempts=current_attempt + 1,
                next_retry_at=None,
            )
            self._record_success()
            return

        combined_error = error or ""
        if text_err:
            combined_error = f"{combined_error} | text_fallback: {text_err}".strip(" |")
        self.repository.update_delivery_record(
            trace_id,
            status="dead_letter",
            error_code="dead_letter",
            error_message=combined_error,
            attempts=current_attempt,
            next_retry_at=None,
        )
        self._record_failure()

    def _delay_for_attempt(self, attempt: int) -> float:
        schedule = self.settings.delivery_retry_delays_seconds
        if not schedule:
            return 10.0
        index = min(attempt - 1, len(schedule) - 1)
        return float(schedule[max(index, 0)])

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._alerts_emitted = 0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        threshold = self.settings.delivery_alert_consecutive_failures
        if self._consecutive_failures < threshold:
            return
        if self._consecutive_failures == threshold or (
            self._consecutive_failures - threshold
        ) % 10 == 0:
            self._alerts_emitted += 1
            logger.critical(
                "NapCat delivery alert: %d consecutive failures (alert #%d)",
                self._consecutive_failures,
                self._alerts_emitted,
            )

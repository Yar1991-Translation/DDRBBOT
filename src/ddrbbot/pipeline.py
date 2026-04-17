from __future__ import annotations

import asyncio
import logging

from .analyzer import EventAnalyzer
from .config import Settings
from .copybook import copy_format, copy_text
from .database import SQLiteRepository
from .delivery import QQDeliveryService
from .models import ProcessedEvent, QQSendNewsCardRequest
from .rendering import NewsCardRenderer

logger = logging.getLogger(__name__)


class PipelineCoordinator:
    def __init__(
        self,
        settings: Settings,
        repository: SQLiteRepository,
        analyzer: EventAnalyzer,
        renderer: NewsCardRenderer,
        delivery_service: QQDeliveryService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.analyzer = analyzer
        self.renderer = renderer
        self.delivery_service = delivery_service
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=settings.queue_maxsize)
        self._workers: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._workers:
            return
        for index in range(self.settings.worker_concurrency):
            task = asyncio.create_task(self._worker_loop(), name=f"pipeline-worker-{index + 1}")
            self._workers.append(task)

    async def stop(self) -> None:
        if not self._workers:
            return
        for _ in self._workers:
            await self.queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def enqueue(self, raw_event_id: str) -> None:
        self.repository.update_raw_event_status(raw_event_id, "queued")
        await self.queue.put(raw_event_id)

    def queue_size(self) -> int:
        return self.queue.qsize()

    async def _worker_loop(self) -> None:
        while True:
            raw_event_id = await self.queue.get()
            try:
                if raw_event_id is None:
                    return
                await self._process_event(raw_event_id)
            finally:
                self.queue.task_done()

    async def _process_event(self, raw_event_id: str) -> None:
        raw_event = self.repository.get_raw_event(raw_event_id)
        if raw_event is None:
            logger.warning("Raw event %s not found, skipping", raw_event_id)
            return

        processed_event: ProcessedEvent | None = None
        try:
            processed_event = await self.analyzer.analyze(raw_event)
            self.repository.upsert_processed_event(processed_event)
            self.repository.update_raw_event_status(raw_event.id, "analyzed")

            artifact = await self.renderer.render(raw_event, processed_event)
            self.repository.save_render_artifact(artifact)
            render_status = "image_ready" if artifact.image_path else "html_ready"
            self.repository.update_processed_event_status(processed_event.id, render_status=render_status)
            self.repository.update_raw_event_status(raw_event.id, "rendered")

            if self.settings.auto_deliver_enabled and self.settings.default_qq_group_id and artifact.image_path:
                self._deliver(processed_event, artifact.image_path)
                self.repository.update_processed_event_status(processed_event.id, delivery_status="queued")
                self.repository.update_raw_event_status(raw_event.id, "delivered")
            else:
                self.repository.update_processed_event_status(processed_event.id, delivery_status="skipped")
        except Exception as exc:  # pragma: no cover - integration path
            logger.exception("Pipeline failed for raw event %s: %s", raw_event.id, exc)
            self.repository.update_raw_event_status(raw_event.id, "failed")
            if processed_event:
                self.repository.update_processed_event_status(
                    processed_event.id,
                    render_status="failed",
                    delivery_status="failed",
                )

    def _deliver(self, processed_event: ProcessedEvent, image_path: str) -> None:
        self.delivery_service.enqueue_delivery(
            QQSendNewsCardRequest(
                trace_id=f"pipeline:{processed_event.id}:group:{self.settings.default_qq_group_id}",
                processed_event_id=processed_event.id,
                target_type="group",
                target_id=str(self.settings.default_qq_group_id),
                image_path=image_path,
                caption=self._build_caption(processed_event),
            )
        )

    @staticmethod
    def _build_caption(processed_event: ProcessedEvent) -> str:
        prefix = processed_event.game or copy_text("delivery.default_caption_prefix", "Roblox 资讯")
        return copy_format(
            "delivery.pipeline_caption",
            "{prefix} · {title}",
            prefix=prefix,
            title=processed_event.title,
        )

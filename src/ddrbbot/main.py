from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .analyzer import EventAnalyzer
from .config import Settings, load_settings
from .copybook import copy_dict, copy_format, copy_list, copy_text
from .database import SQLiteRepository, _RAW_PATCH_MISSING
from .delivery import DeliveryError, QQDeliveryService
from .models import (
    DiscordWebhookPayload,
    EnqueueResult,
    HealthResponse,
    ProcessedEvent,
    RetryFailedDeliveryRequest,
    ReviewApproveSendRequest,
    ReviewEditRequest,
    ReviewResendRequest,
    RSSCollectRequest,
    RSSCollectResponse,
    RSSHubCollectRequest,
    QQInboundEvent,
    QQSendNewsCardRequest,
    RawEvent,
    RenderPreviewRequest,
    SourcePublic,
    SourceRegisterRequest,
)
from .delivery_worker import DeliveryWorker
from .llm_agent import (
    AgentContext,
    AgentScheduler,
    ChatService,
    ChatTurnRequest,
    LLMAgent,
    PersonaStore,
    build_default_registry,
    coerce_custom_persona,
)
from .models import (
    AIChatRequest,
    ChatPersona,
    KnowledgeUpsertRequest,
    PersonaUpsertRequest,
    ProfileUpsertRequest,
    ChatKnowledgeItem,
    ChatProfile,
)
from .pipeline import PipelineCoordinator
from .qq.commands import QQCommandRouter
from .qq.napcat import NapCatAdapter, normalize_inbound_event
from .qq.operations import QQOperationsService
from .qq.ws_client import NapCatWSClient, handle_inbound_event
from .rendering import NewsCardRenderer
from .rss import RSSCollector
from .rsshub import validate_rsshub_feed_url
from .services import AppServices
from .logging_setup import configure_logging
from .utils import make_external_id, utc_now


def _rsshub2_seed_sources() -> list[dict[str, str]]:
    base = "https://rsshub2.asailor.org"
    return [
        {
            "name": "Roblox Forsaken Official X",
            "feed_url": f"{base}/x/user/forsaken2024",
            "credibility_level": "official",
        },
        {
            "name": "Roblox DOORS Official X",
            "feed_url": f"{base}/x/user/doorsgame",
            "credibility_level": "official",
        },
        {
            "name": "Forsaken Wiki",
            "feed_url": f"{base}/fandom/wiki/forsaken2024",
            "credibility_level": "community",
        },
        {
            "name": "DOORS Wiki",
            "feed_url": f"{base}/fandom/wiki/doors-game",
            "credibility_level": "community",
        },
    ]


def create_app() -> FastAPI:
    settings = load_settings()
    configure_logging(settings)
    services = _build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.repository.initialize()
        services.persona_store.seed_defaults()
        await services.pipeline.start()
        await services.delivery_worker.start()
        await services.ws_client.start()
        await services.agent_scheduler.start()
        app.state.services = services
        await _startup_selfcheck(services)
        yield
        await services.agent_scheduler.stop()
        await services.ws_client.stop()
        await services.delivery_worker.stop()
        await services.pipeline.stop()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    _register_routes(app)
    return app


def _build_services(settings: Settings) -> AppServices:
    repository = SQLiteRepository(settings.database_path)
    analyzer = EventAnalyzer(settings)
    renderer = NewsCardRenderer(settings)
    bot_adapter = NapCatAdapter(settings)
    delivery_service = QQDeliveryService(
        settings=settings,
        repository=repository,
        bot_adapter=bot_adapter,
    )
    delivery_worker = DeliveryWorker(
        settings=settings,
        repository=repository,
        delivery_service=delivery_service,
    )
    pipeline = PipelineCoordinator(
        settings=settings,
        repository=repository,
        analyzer=analyzer,
        renderer=renderer,
        delivery_service=delivery_service,
    )
    operations_service = QQOperationsService(
        settings=settings,
        repository=repository,
        renderer=renderer,
        bot_adapter=bot_adapter,
        delivery_service=delivery_service,
        pipeline=pipeline,
    )
    tool_registry = build_default_registry(
        settings=settings,
        repository=repository,
        renderer=renderer,
        pipeline=pipeline,
        bot_adapter=bot_adapter,
        delivery_service=delivery_service,
    )
    llm_agent = LLMAgent(settings=settings, registry=tool_registry)
    persona_store = PersonaStore(repository=repository)
    chat_service = ChatService(
        repository=repository,
        llm_agent=llm_agent,
        persona_store=persona_store,
    )
    agent_scheduler = AgentScheduler(settings=settings, agent=llm_agent)
    command_router = QQCommandRouter(
        settings=settings,
        repository=repository,
        bot_adapter=bot_adapter,
        pipeline=pipeline,
        operations_service=operations_service,
        llm_agent=llm_agent,
        chat_service=chat_service,
        persona_store=persona_store,
    )
    ws_client = NapCatWSClient(
        settings=settings,
        repository=repository,
        command_router=command_router,
    )
    return AppServices(
        settings=settings,
        repository=repository,
        analyzer=analyzer,
        renderer=renderer,
        bot_adapter=bot_adapter,
        delivery_service=delivery_service,
        delivery_worker=delivery_worker,
        pipeline=pipeline,
        operations_service=operations_service,
        command_router=command_router,
        ws_client=ws_client,
        llm_agent=llm_agent,
        agent_scheduler=agent_scheduler,
        persona_store=persona_store,
        chat_service=chat_service,
    )


def _register_routes(app: FastAPI) -> None:
    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        services = _services(request)
        napcat_connected = await services.bot_adapter.health_check()
        return HealthResponse(
            ok=True,
            queue_size=services.pipeline.queue_size(),
            stats=services.repository.get_stats(),
            napcat_connected=napcat_connected,
        )

    @app.post("/api/webhook/discord", response_model=EnqueueResult)
    async def discord_webhook(payload: DiscordWebhookPayload, request: Request) -> EnqueueResult:
        services = _services(request)
        raw_event = _discord_payload_to_raw_event(payload)
        inserted = services.repository.insert_raw_event(raw_event)
        if not inserted:
            return EnqueueResult(accepted=True, deduplicated=True, raw_event_id=raw_event.id)
        await services.pipeline.enqueue(raw_event.id)
        return EnqueueResult(accepted=True, deduplicated=False, raw_event_id=raw_event.id)

    @app.post("/api/render/preview")
    async def render_preview(payload: RenderPreviewRequest, request: Request) -> dict[str, Any]:
        services = _services(request)
        raw_event, processed_event = _preview_models_from_payload(payload)
        artifact = await services.renderer.render(raw_event, processed_event, theme=payload.theme)
        return {
            "ok": True,
            "html_path": artifact.html_path,
            "image_path": artifact.image_path,
        }

    @app.post("/api/render/preview-image")
    async def render_preview_image(payload: RenderPreviewRequest, request: Request) -> dict[str, Any]:
        services = _services(request)
        raw_event, processed_event = _preview_models_from_payload(payload)
        artifact = await services.renderer.render(
            raw_event,
            processed_event,
            theme=payload.theme,
            force_screenshot=True,
        )
        if not artifact.image_path:
            raise HTTPException(
                status_code=500,
                detail=copy_text(
                    "render_api.preview_image_failed",
                    "Preview HTML rendered, but PNG generation failed.",
                ),
            )
        return {
            "ok": True,
            "html_path": artifact.html_path,
            "image_path": artifact.image_path,
        }

    @app.get("/preview/md3", response_class=HTMLResponse)
    async def md3_preview_console(request: Request) -> HTMLResponse:
        services = _services(request)
        defaults = _default_preview_form_values()
        card_url = f"/preview/md3/card?{urlencode(defaults)}"
        html = services.renderer.render_preview_console(defaults=defaults, card_url=card_url)
        return HTMLResponse(html)

    @app.get("/preview/md3/card", response_class=HTMLResponse)
    async def md3_preview_card(request: Request) -> HTMLResponse:
        services = _services(request)
        defaults = _default_preview_form_values()
        query_params = dict(request.query_params)
        payload = RenderPreviewRequest(
            title=_preview_param(query_params.get("title"), defaults, "title"),
            summary=_preview_param(query_params.get("summary"), defaults, "summary"),
            highlights=_split_multiline(
                _preview_param(query_params.get("highlights"), defaults, "highlights")
            ),
            category=_preview_param(query_params.get("category"), defaults, "category"),
            theme=_preview_param(query_params.get("theme"), defaults, "theme"),
            preset_key=_preview_param(query_params.get("preset_key"), defaults, "preset_key") or None,
            orientation=(
                _preview_param(query_params.get("orientation"), defaults, "orientation") or "vertical"
            ),
            custom_css=_preview_param(query_params.get("custom_css"), defaults, "custom_css"),
            game=_preview_param(query_params.get("game"), defaults, "game") or None,
            source_name=_preview_param(query_params.get("source_name"), defaults, "source_name"),
            channel_name=_preview_param(query_params.get("channel_name"), defaults, "channel_name")
            or None,
            author=_preview_param(query_params.get("author"), defaults, "author") or None,
            source_credibility=_preview_param(
                query_params.get("source_credibility"), defaults, "source_credibility"
            ),
            need_translation=_parse_bool_like(
                _preview_param(query_params.get("need_translation"), defaults, "need_translation")
            ),
            media=_preview_media_from_params(query_params, defaults),
            discovered_sources=_split_multiline(
                _preview_param(query_params.get("discovered_sources"), defaults, "discovered_sources")
            ),
        )
        raw_event, processed_event = _preview_models_from_payload(payload)
        html = services.renderer.render_card_html(raw_event, processed_event, theme=payload.theme)
        return HTMLResponse(html)

    @app.get("/review", response_class=HTMLResponse)
    async def review_console(
        request: Request,
        processed_event_id: str | None = None,
        status: str = "open",
        limit: int = 24,
    ) -> HTMLResponse:
        services = _services(request)
        normalized_status = _normalize_review_status(status)
        items = services.repository.list_processed_events(
            delivery_statuses=_review_statuses(normalized_status),
            limit=min(max(limit, 1), 50),
        )
        selected = _select_review_item(items, processed_event_id)
        review_items: list[dict[str, Any]] = []
        for item in items:
            raw_event = services.repository.get_raw_event(item.raw_event_id)
            review_items.append(
                _build_review_list_item(
                    item,
                    source_name=raw_event.source_name
                    if raw_event
                    else (item.game or copy_text("rendering.unknown_source", "Unknown Source")),
                    selected_id=selected.id if selected else None,
                )
            )
        selected_item = _build_review_detail(services, selected) if selected else None
        html = services.renderer.render_review_panel(
            items=review_items,
            selected_item=selected_item,
            status_filter=normalized_status,
            default_group_id=services.settings.default_qq_group_id,
            queue_size=services.pipeline.queue_size(),
            failed_deliveries=services.delivery_service.failed_records_count(),
            total_processed_events=services.repository.get_stats()["processed_events"],
        )
        return HTMLResponse(html)

    @app.get("/api/review/items")
    async def review_items_api(
        request: Request,
        processed_event_id: str | None = None,
        status: str = "open",
        limit: int = 24,
    ) -> dict[str, Any]:
        services = _services(request)
        normalized_status = _normalize_review_status(status)
        items = services.repository.list_processed_events(
            delivery_statuses=_review_statuses(normalized_status),
            limit=min(max(limit, 1), 50),
        )
        selected = _select_review_item(items, processed_event_id)
        review_items: list[dict[str, Any]] = []
        for item in items:
            raw_event = services.repository.get_raw_event(item.raw_event_id)
            review_items.append(
                _build_review_list_item(
                    item,
                    source_name=raw_event.source_name
                    if raw_event
                    else (item.game or copy_text("rendering.unknown_source", "Unknown Source")),
                    selected_id=selected.id if selected else None,
                )
            )
        selected_detail = _build_review_detail(services, selected) if selected else None
        return {
            "ok": True,
            "status_filter": normalized_status,
            "items": review_items,
            "selected_id": selected.id if selected else None,
            "selected": selected_detail,
        }

    @app.get("/api/review/items/{processed_event_id}")
    async def review_item_api(processed_event_id: str, request: Request) -> dict[str, Any]:
        services = _services(request)
        processed_event = services.repository.get_processed_event(processed_event_id)
        if processed_event is None:
            raise HTTPException(
                status_code=404,
                detail=copy_format(
                    "review_api.errors.processed_not_found",
                    "Processed event not found: {processed_event_id}",
                    processed_event_id=processed_event_id,
                ),
            )
        return {"ok": True, "item": _build_review_detail(services, processed_event)}

    @app.post("/api/review/{processed_event_id}/rerender")
    async def review_rerender(
        processed_event_id: str,
        payload: ReviewEditRequest,
        request: Request,
    ) -> dict[str, Any]:
        services = _services(request)
        raw_event, processed_event = _load_review_entities(services, processed_event_id)
        processed_event = _apply_review_edits(services, raw_event, processed_event, payload)
        try:
            artifact = await services.renderer.render(raw_event, processed_event, theme=payload.theme)
        except Exception as exc:
            services.repository.update_processed_event_status(processed_event.id, render_status="failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        services.repository.save_render_artifact(artifact)
        render_status = "image_ready" if artifact.image_path else "html_ready"
        services.repository.update_processed_event_status(
            processed_event.id,
            render_status=render_status,
            delivery_status="review_pending",
        )
        return {
            "ok": True,
            "processed_event_id": processed_event.id,
            "render_status": render_status,
            "html_path": artifact.html_path,
            "image_path": artifact.image_path,
            "theme": artifact.theme,
            "message": copy_text("review_api.rerender_done", "已保存修改并重新渲染。"),
        }

    @app.post("/api/review/{processed_event_id}/approve-send")
    async def review_approve_and_send(
        processed_event_id: str,
        payload: ReviewApproveSendRequest,
        request: Request,
    ) -> dict[str, Any]:
        services = _services(request)
        raw_event, processed_event = _load_review_entities(services, processed_event_id)
        processed_event = _apply_review_edits(services, raw_event, processed_event, payload)
        try:
            artifact = await services.renderer.render(raw_event, processed_event, theme=payload.theme)
        except Exception as exc:
            services.repository.update_processed_event_status(processed_event.id, render_status="failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        services.repository.save_render_artifact(artifact)
        render_status = "image_ready" if artifact.image_path else "html_ready"
        services.repository.update_processed_event_status(processed_event.id, render_status=render_status)
        if not artifact.image_path:
            raise HTTPException(
                status_code=409,
                detail=copy_text(
                    "review_api.errors.preview_image_missing",
                    "Preview HTML rendered, but no screenshot image was produced for NapCat delivery.",
                ),
            )
        try:
            delivery = await _send_review_artifact(
                services,
                processed_event=processed_event,
                raw_event=raw_event,
                image_path=artifact.image_path,
                action="approve",
                target_type=payload.target_type,
                target_id=payload.target_id,
            )
        except DeliveryError as exc:
            services.repository.update_processed_event_status(processed_event.id, delivery_status="failed")
            raise HTTPException(
                status_code=502,
                detail={
                    "ok": False,
                    "trace_id": exc.trace_id,
                    "attempts": exc.attempts,
                    "error": exc.message,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        services.repository.update_processed_event_status(processed_event.id, delivery_status="queued")
        return {
            "ok": True,
            "processed_event_id": processed_event.id,
            "render_status": render_status,
            "html_path": artifact.html_path,
            "image_path": artifact.image_path,
            "message": copy_text(
                "review_api.approve_send_queued",
                "已批准，已加入投递队列，后台 DeliveryWorker 将自动发送。",
            ),
            **delivery,
        }

    @app.post("/api/review/{processed_event_id}/reject")
    async def review_reject(processed_event_id: str, request: Request) -> dict[str, Any]:
        services = _services(request)
        processed_event = services.repository.get_processed_event(processed_event_id)
        if processed_event is None:
            raise HTTPException(
                status_code=404,
                detail=copy_format(
                    "review_api.errors.processed_not_found",
                    "Processed event not found: {processed_event_id}",
                    processed_event_id=processed_event_id,
                ),
            )
        services.repository.update_processed_event_status(processed_event.id, delivery_status="rejected")
        return {
            "ok": True,
            "processed_event_id": processed_event.id,
            "message": copy_text("review_api.reject_done", "已标记为拒绝，不会进入批准发送流。"),
        }

    @app.post("/api/review/{processed_event_id}/resend")
    async def review_resend(
        processed_event_id: str,
        payload: ReviewResendRequest,
        request: Request,
    ) -> dict[str, Any]:
        services = _services(request)
        raw_event, processed_event = _load_review_entities(services, processed_event_id)
        artifact = services.repository.get_latest_render_artifact(processed_event.id)
        if artifact is None or not artifact.image_path:
            raise HTTPException(
                status_code=409,
                detail=copy_text(
                    "review_api.errors.manual_resend_missing",
                    "No screenshot artifact is available for manual resend.",
                ),
            )
        try:
            delivery = await _send_review_artifact(
                services,
                processed_event=processed_event,
                raw_event=raw_event,
                image_path=artifact.image_path,
                action="resend",
                target_type=payload.target_type,
                target_id=payload.target_id,
            )
        except DeliveryError as exc:
            services.repository.update_processed_event_status(processed_event.id, delivery_status="failed")
            raise HTTPException(
                status_code=502,
                detail={
                    "ok": False,
                    "trace_id": exc.trace_id,
                    "attempts": exc.attempts,
                    "error": exc.message,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        services.repository.update_processed_event_status(processed_event.id, delivery_status="queued")
        return {
            "ok": True,
            "processed_event_id": processed_event.id,
            "message": copy_text(
                "review_api.resend_queued",
                "已使用最新截图入队重新发送。",
            ),
            "html_path": artifact.html_path,
            "image_path": artifact.image_path,
            **delivery,
        }

    @app.post("/api/collect/rss", response_model=RSSCollectResponse)
    async def collect_rss(payload: RSSCollectRequest, request: Request) -> RSSCollectResponse:
        services = _services(request)
        collector = RSSCollector()
        events = await collector.collect(payload.source_name, payload.feed_url, limit=payload.limit)
        accepted = 0
        deduplicated = 0
        queued_event_ids: list[str] = []
        for event in events:
            inserted = services.repository.insert_raw_event(event)
            if not inserted:
                deduplicated += 1
                continue
            accepted += 1
            queued_event_ids.append(event.id)
            await services.pipeline.enqueue(event.id)
        services.repository.touch_source_feed(
            source_type="rss",
            source_name=payload.source_name,
            feed_url=payload.feed_url,
        )
        return RSSCollectResponse(
            accepted=accepted,
            deduplicated=deduplicated,
            queued_event_ids=queued_event_ids,
        )

    @app.post("/api/collect/rsshub", response_model=RSSCollectResponse)
    async def collect_rsshub(payload: RSSHubCollectRequest, request: Request) -> RSSCollectResponse:
        services = _services(request)
        settings = services.settings
        try:
            validate_rsshub_feed_url(
                payload.feed_url,
                host_markers=settings.rsshub_host_markers,
                extra_hosts=settings.rsshub_extra_hosts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        collector = RSSCollector()
        events = await collector.collect(payload.source_name, payload.feed_url, limit=payload.limit)
        accepted = 0
        deduplicated = 0
        queued_event_ids: list[str] = []
        for event in events:
            base = dict(event.raw_payload) if event.raw_payload else {}
            event.raw_payload = {
                **base,
                "collector": "rsshub",
                "feed_url": payload.feed_url,
            }
            inserted = services.repository.insert_raw_event(event)
            if not inserted:
                deduplicated += 1
                continue
            accepted += 1
            queued_event_ids.append(event.id)
            await services.pipeline.enqueue(event.id)
        services.repository.touch_source_feed(
            source_type="rss",
            source_name=payload.source_name,
            feed_url=payload.feed_url,
        )
        return RSSCollectResponse(
            accepted=accepted,
            deduplicated=deduplicated,
            queued_event_ids=queued_event_ids,
        )

    @app.get("/api/sources")
    async def list_sources(request: Request, limit: int = 50) -> dict[str, Any]:
        services = _services(request)
        rows = services.repository.list_sources(limit=limit)
        return {"ok": True, "sources": [SourcePublic.model_validate(r).model_dump() for r in rows]}

    @app.post("/api/sources")
    async def register_source(payload: SourceRegisterRequest, request: Request) -> dict[str, Any]:
        services = _services(request)
        row = services.repository.upsert_source_registration(
            source_type=payload.source_type.strip() or "rss",
            name=payload.name.strip(),
            feed_url=payload.feed_url.strip() if payload.feed_url else None,
            credibility_level=payload.credibility_level,
        )
        return {"ok": True, "source": SourcePublic.model_validate(row).model_dump()}

    @app.post("/api/sources/bootstrap-rsshub2")
    async def bootstrap_rsshub2_sources(request: Request) -> dict[str, Any]:
        services = _services(request)
        registered: list[dict[str, Any]] = []
        for item in _rsshub2_seed_sources():
            feed_url = validate_rsshub_feed_url(
                item["feed_url"],
                host_markers=services.settings.rsshub_host_markers,
                extra_hosts=services.settings.rsshub_extra_hosts,
            )
            row = services.repository.upsert_source_registration(
                source_type="rss",
                name=item["name"],
                feed_url=feed_url,
                credibility_level=item["credibility_level"],
            )
            registered.append(SourcePublic.model_validate(row).model_dump())
        return {"ok": True, "count": len(registered), "sources": registered}

    @app.post("/api/qq/send-news-card")
    async def send_news_card(payload: QQSendNewsCardRequest, request: Request) -> dict[str, Any]:
        services = _services(request)
        try:
            result = services.delivery_service.enqueue_delivery(payload)
        except DeliveryError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "ok": False,
                    "trace_id": exc.trace_id,
                    "attempts": exc.attempts,
                    "error": exc.message,
                },
            ) from exc
        return {"ok": True, **result.to_dict()}

    @app.get("/api/qq/adapter/status")
    async def qq_adapter_status(
        request: Request,
        include_groups: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        services = _services(request)
        return {
            "ok": True,
            **(
                await services.operations_service.adapter_snapshot(
                    include_groups=include_groups,
                    group_limit=limit,
                )
            ),
        }

    @app.get("/api/qq/delivery/review-queue")
    async def qq_delivery_review_queue(request: Request, limit: int = 5) -> dict[str, Any]:
        services = _services(request)
        return {"ok": True, **services.operations_service.review_queue(limit=limit)}

    @app.post("/api/qq/delivery/retry-failed")
    async def qq_retry_failed_delivery(
        payload: RetryFailedDeliveryRequest,
        request: Request,
    ) -> dict[str, Any]:
        services = _services(request)
        try:
            result = await services.operations_service.retry_failed(trace_id=payload.trace_id)
        except DeliveryError as exc:
            not_found_message = copy_text(
                "delivery.no_failed_record",
                "No failed delivery record found.",
            )
            raise HTTPException(
                status_code=404 if exc.message == not_found_message else 409,
                detail={
                    "ok": False,
                    "trace_id": exc.trace_id or payload.trace_id,
                    "attempts": exc.attempts,
                    "error": exc.message,
                },
            ) from exc
        return {"ok": True, **result}

    @app.post("/api/events/qq")
    async def receive_qq_event(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        services = _services(request)
        event, result = await handle_inbound_event(
            payload,
            repository=services.repository,
            command_router=services.command_router,
        )
        return {
            "ok": True,
            "received": event.event_type,
            "message_id": event.message_id,
            "platform_event_saved": True,
            **result.to_dict(),
        }

    @app.post("/api/ai/chat")
    async def ai_chat(payload: AIChatRequest, request: Request) -> dict[str, Any]:
        services = _services(request)
        if not services.llm_agent.enabled:
            raise HTTPException(
                status_code=409,
                detail=copy_text(
                    "llm_agent.disabled",
                    "LLM Agent 未启用。请设置 LLM_AGENT_ENABLED=true 并配置 LLM_BASE_URL/LLM_MODEL。",
                ),
            )
        origin = payload.origin if payload.origin in {"api", "scheduler", "qq_chat"} else "api"
        context = AgentContext(origin=origin, extras=dict(payload.extras))
        custom_persona = coerce_custom_persona(payload.custom_persona)

        turn = await services.chat_service.run_turn(
            ChatTurnRequest(
                origin=origin,
                user_message=payload.message,
                explicit_session_id=payload.session_id,
                user_id=str(payload.extras.get("user_id") or "") or None,
                group_id=str(payload.extras.get("group_id") or "") or None,
                override_persona_id=payload.persona_id,
                override_custom_persona=custom_persona,
                history_limit=payload.history_limit,
                include_knowledge=payload.include_knowledge,
                reset_session=payload.reset_session,
                agent_context=context,
            )
        )
        result = turn.run_result
        persona_info = {
            "key": turn.built_context.persona.key,
            "label": turn.built_context.persona.label,
            "is_custom": turn.built_context.persona.is_custom,
        }
        return {
            "ok": not bool(result.error),
            "final_text": result.final_text,
            "tool_steps": result.tool_steps,
            "error": result.error,
            "messages": result.messages,
            "session_id": turn.session.id,
            "session_key": turn.session.session_key,
            "persona": persona_info,
            "history_count": len(turn.built_context.history),
            "knowledge_count": len(turn.built_context.knowledge),
            "notes": turn.built_context.notes,
        }

    @app.get("/api/chat/personas")
    async def list_chat_personas(request: Request) -> dict[str, Any]:
        services = _services(request)
        personas = services.persona_store.list_personas()
        return {
            "ok": True,
            "personas": [persona.model_dump(mode="json") for persona in personas],
        }

    @app.post("/api/chat/personas")
    async def upsert_chat_persona(
        payload: PersonaUpsertRequest, request: Request
    ) -> dict[str, Any]:
        services = _services(request)
        persona = ChatPersona(
            persona_key=payload.persona_key.strip(),
            label=payload.label.strip() or payload.persona_key.strip(),
            description=payload.description.strip(),
            system_prompt=payload.system_prompt.strip(),
            is_builtin=False,
            allow_tools=payload.allow_tools,
            tone=payload.tone,
        )
        saved = services.repository.upsert_chat_persona(persona)
        return {"ok": True, "persona": saved.model_dump(mode="json")}

    @app.delete("/api/chat/personas/{persona_key}")
    async def delete_chat_persona(persona_key: str, request: Request) -> dict[str, Any]:
        services = _services(request)
        removed = services.repository.delete_chat_persona(persona_key)
        return {"ok": removed}

    @app.get("/api/chat/knowledge")
    async def list_chat_knowledge(
        request: Request,
        query: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        services = _services(request)
        if query:
            items = services.repository.search_chat_knowledge_items(query, limit=limit)
        else:
            items = services.repository.list_chat_knowledge_items(limit=limit)
        return {
            "ok": True,
            "items": [item.model_dump(mode="json") for item in items],
        }

    @app.post("/api/chat/knowledge")
    async def upsert_chat_knowledge(
        payload: KnowledgeUpsertRequest, request: Request
    ) -> dict[str, Any]:
        services = _services(request)
        tags = [str(tag).strip() for tag in payload.tags if str(tag).strip()]
        item = ChatKnowledgeItem(
            id=payload.id or ChatKnowledgeItem().id,
            topic=payload.topic.strip(),
            content=payload.content.strip(),
            tags=tags,
            priority=payload.priority,
        )
        saved = services.repository.upsert_chat_knowledge_item(item)
        return {"ok": True, "item": saved.model_dump(mode="json")}

    @app.delete("/api/chat/knowledge/{item_id}")
    async def delete_chat_knowledge(item_id: str, request: Request) -> dict[str, Any]:
        services = _services(request)
        removed = services.repository.delete_chat_knowledge_item(item_id)
        return {"ok": removed}

    @app.post("/api/chat/profiles")
    async def upsert_chat_profile(
        payload: ProfileUpsertRequest, request: Request
    ) -> dict[str, Any]:
        services = _services(request)
        profile = ChatProfile(
            scope=payload.scope.strip() or "qq_private",
            user_id=payload.user_id.strip(),
            display_name=payload.display_name,
            preferences=dict(payload.preferences),
            notes=payload.notes,
        )
        saved = services.repository.upsert_chat_profile(profile)
        return {"ok": True, "profile": saved.model_dump(mode="json")}

    @app.get("/api/chat/profiles/{scope}/{user_id}")
    async def get_chat_profile(
        scope: str, user_id: str, request: Request
    ) -> dict[str, Any]:
        services = _services(request)
        profile = services.repository.get_chat_profile(scope=scope, user_id=user_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        return {"ok": True, "profile": profile.model_dump(mode="json")}

    @app.get("/api/chat/sessions/{session_id}/messages")
    async def list_chat_session_messages(
        session_id: str, request: Request, limit: int = 50
    ) -> dict[str, Any]:
        services = _services(request)
        session = services.repository.get_chat_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        messages = services.repository.list_chat_messages(session_id, limit=limit)
        return {
            "ok": True,
            "session": session.model_dump(mode="json"),
            "messages": [m.model_dump(mode="json") for m in messages],
        }

    @app.delete("/api/chat/sessions/{session_id}/messages")
    async def clear_chat_session_messages(
        session_id: str, request: Request
    ) -> dict[str, Any]:
        services = _services(request)
        session = services.repository.get_chat_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        services.repository.clear_chat_messages(session_id)
        services.repository.update_chat_session(
            session_id, summary="", touch_summary=True
        )
        return {"ok": True}

    @app.get("/api/delivery/dead-letter")
    async def list_dead_letter(request: Request, limit: int = 20) -> dict[str, Any]:
        services = _services(request)
        records = services.delivery_service.list_dead_letter_records(limit=limit)
        return {
            "ok": True,
            "count": len(records),
            "records": [
                {
                    "id": r.id,
                    "trace_id": r.trace_id,
                    "processed_event_id": r.processed_event_id,
                    "target_type": r.target_type,
                    "target_id": r.target_id,
                    "attempts": r.attempts,
                    "error_code": r.error_code,
                    "error_message": r.error_message,
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in records
            ],
        }

    @app.post("/api/delivery/dead-letter/{record_id}/retry")
    async def retry_dead_letter(record_id: str, request: Request) -> dict[str, Any]:
        services = _services(request)
        record = services.repository.get_delivery_record_by_id(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="delivery record not found")
        result = services.delivery_service.requeue_record(record)
        return {"ok": True, **result.to_dict()}

    _mount_font_static(app)


async def _startup_selfcheck(services: AppServices) -> None:
    logger = logging.getLogger(__name__)
    settings = services.settings
    napcat_connected = False
    bot_uin: Any = None
    bot_nickname: Any = None
    napcat_version: Any = None
    default_group_found: bool | None = None
    groups_count: int | None = None
    try:
        napcat_connected = await services.bot_adapter.health_check()
    except Exception as exc:  # pragma: no cover - network dependent
        logger.error("Selfcheck NapCat health_check failed: %s", exc)
    if napcat_connected:
        try:
            info = await services.bot_adapter.get_login_info()
            bot_uin = info.get("user_id")
            bot_nickname = info.get("nickname")
        except Exception as exc:  # pragma: no cover
            logger.warning("Selfcheck get_login_info failed: %s", exc)
        try:
            ver = await services.bot_adapter.get_version_info()
            napcat_version = ver.get("app_name") or ver.get("version")
        except Exception as exc:  # pragma: no cover
            logger.warning("Selfcheck get_version_info failed: %s", exc)
        if settings.default_qq_group_id:
            try:
                groups = await services.bot_adapter.get_group_list()
                groups_count = len(groups)
                target = settings.default_qq_group_id
                default_group_found = any(
                    str(g.get("group_id") or "") == target for g in groups
                )
                if not default_group_found:
                    logger.warning(
                        "Selfcheck default group %s is not found in NapCat group list.",
                        target,
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning("Selfcheck get_group_list failed: %s", exc)
    stats = services.repository.get_stats()
    logger.info(
        "Startup selfcheck: napcat_connected=%s bot_uin=%s nickname=%s version=%s "
        "default_group_id=%s default_group_found=%s groups_count=%s ws_enabled=%s db_stats=%s",
        napcat_connected,
        bot_uin,
        bot_nickname,
        napcat_version,
        settings.default_qq_group_id,
        default_group_found,
        groups_count,
        services.ws_client.enabled,
        stats,
    )


def _mount_font_static(app: FastAPI) -> None:
    root = Path(__file__).resolve().parent.parent
    font_root = root / "font"
    if font_root.is_dir():
        app.mount("/font", StaticFiles(directory=str(font_root)), name="font")
    assent_root = root / "assent"
    if assent_root.is_dir():
        app.mount("/assent", StaticFiles(directory=str(assent_root)), name="assent")


def _services(request: Request) -> AppServices:
    return request.app.state.services


def _preview_models_from_payload(payload: RenderPreviewRequest) -> tuple[RawEvent, ProcessedEvent]:
    raw_event = RawEvent(
        source_type="preview",
        source_name=payload.source_name,
        channel_name=payload.channel_name,
        author=payload.author,
        content="\n".join([payload.title, payload.summary, *payload.highlights]),
        attachments=[item.url for item in payload.media],
        external_id=make_external_id(payload.source_name, payload.title, payload.summary),
        published_at=payload.published_at,
        raw_payload=payload.model_dump(),
    )
    processed_event = ProcessedEvent(
        raw_event_id=raw_event.id,
        title=payload.title,
        summary=payload.summary,
        highlights=payload.highlights,
        category=payload.category,
        game=payload.game,
        need_translation=payload.need_translation,
        source_credibility=payload.source_credibility,
        media=payload.media,
        discovered_sources=payload.discovered_sources,
        language="zh",
        published_at=payload.published_at,
    )
    return raw_event, processed_event


def _review_statuses(status: str) -> tuple[str, ...] | None:
    mapping = {
        "open": ("pending", "skipped", "review_pending", "failed", "approved"),
        "failed": ("failed",),
        "sent": ("sent",),
        "rejected": ("rejected",),
        "all": None,
    }
    return mapping.get(status, mapping["open"])


def _normalize_review_status(status: str) -> str:
    if status in {"open", "failed", "sent", "rejected", "all"}:
        return status
    return "open"


def _select_review_item(
    items: list[ProcessedEvent],
    processed_event_id: str | None,
) -> ProcessedEvent | None:
    if not items:
        return None
    if processed_event_id:
        for item in items:
            if item.id == processed_event_id:
                return item
    return items[0]


def _build_review_list_item(
    processed_event: ProcessedEvent,
    *,
    source_name: str,
    selected_id: str | None,
) -> dict[str, Any]:
    return {
        "id": processed_event.id,
        "title": processed_event.title,
        "game": processed_event.game,
        "source_name": source_name,
        "published_at": processed_event.published_at.strftime("%m-%d %H:%M"),
        "delivery_status_label": _review_delivery_status_label(processed_event.delivery_status),
        "delivery_status_tone": _review_delivery_status_tone(processed_event.delivery_status),
        "render_status": processed_event.render_status,
        "render_status_label": _review_render_status_label(processed_event.render_status),
        "category_label": _review_category_label(processed_event.category),
        "active": processed_event.id == selected_id,
    }


def _build_review_detail(services: AppServices, processed_event: ProcessedEvent) -> dict[str, Any]:
    raw_event = services.repository.get_raw_event(processed_event.raw_event_id)
    latest_artifact = services.repository.get_latest_render_artifact(processed_event.id)
    delivery_records = services.repository.list_delivery_records(
        processed_event_id=processed_event.id,
        limit=5,
    )
    source_name = raw_event.source_name if raw_event else (
        processed_event.game or copy_text("rendering.unknown_source", "Unknown Source")
    )
    preview_seed = _review_preview_seed(
        raw_event=raw_event,
        processed_event=processed_event,
        theme=latest_artifact.theme if latest_artifact else "light",
    )
    structured_meta = copy_format(
        "review_panel.supporting.structured_meta",
        "语言 {language} · 可信度 {credibility} · {translation}",
        language=processed_event.language,
        credibility=_review_credibility_label(processed_event.source_credibility),
        translation=copy_text("review_panel.supporting.translation_true", "含翻译")
        if processed_event.need_translation
        else copy_text("review_panel.supporting.translation_false", "原文可直接使用"),
    )
    return {
        "id": processed_event.id,
        "title": processed_event.title,
        "summary": processed_event.summary,
        "highlights_text": "\n".join(processed_event.highlights),
        "category": processed_event.category,
        "game": processed_event.game or "",
        "theme": latest_artifact.theme if latest_artifact else "light",
        "source_name": source_name,
        "channel_name": raw_event.channel_name if raw_event else None,
        "author": raw_event.author if raw_event else None,
        "published_at": processed_event.published_at.strftime("%Y-%m-%d %H:%M UTC"),
        "source_credibility": processed_event.source_credibility,
        "source_credibility_label": _review_credibility_label(processed_event.source_credibility),
        "need_translation": processed_event.need_translation,
        "language": processed_event.language,
        "render_status": processed_event.render_status,
        "render_status_label": _review_render_status_label(processed_event.render_status),
        "delivery_status_label": _review_delivery_status_label(processed_event.delivery_status),
        "delivery_status_tone": _review_delivery_status_tone(processed_event.delivery_status),
        "structured_meta": structured_meta,
        "discovered_sources": processed_event.discovered_sources,
        "raw_content": raw_event.content
        if raw_event
        else copy_text("rendering.raw_event_missing", "Raw event payload is missing."),
        "raw_payload_json": json.dumps(
            raw_event.raw_payload if raw_event else {},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        "latest_html_path": latest_artifact.html_path if latest_artifact else None,
        "latest_image_path": latest_artifact.image_path if latest_artifact else None,
        "preview_link": _build_preview_link(preview_seed, processed_event),
        "preview_seed_json": json.dumps(preview_seed, ensure_ascii=False),
        "recent_deliveries": [
            {
                "trace_id": record.trace_id,
                "status": record.status,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "updated_at": record.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for record in delivery_records
        ],
        "raw_event_id": processed_event.raw_event_id,
        "preset_key": preview_seed.get("preset_key") or "",
        "orientation": preview_seed.get("orientation") or "vertical",
        "custom_css": preview_seed.get("custom_css") or "",
        "media": [m.model_dump() for m in processed_event.media],
    }


def _load_review_entities(
    services: AppServices,
    processed_event_id: str,
) -> tuple[RawEvent, ProcessedEvent]:
    processed_event = services.repository.get_processed_event(processed_event_id)
    if processed_event is None:
        raise HTTPException(
            status_code=404,
            detail=copy_format(
                "review_api.errors.processed_not_found",
                "Processed event not found: {processed_event_id}",
                processed_event_id=processed_event_id,
            ),
        )
    raw_event = services.repository.get_raw_event(processed_event.raw_event_id)
    if raw_event is None:
        raise HTTPException(
            status_code=404,
            detail=copy_format(
                "review_api.errors.raw_not_found",
                "Raw event not found: {raw_event_id}",
                raw_event_id=processed_event.raw_event_id,
            ),
        )
    return raw_event, processed_event


def _apply_review_edits(
    services: AppServices,
    raw_event: RawEvent,
    processed_event: ProcessedEvent,
    payload: ReviewEditRequest,
) -> ProcessedEvent:
    fs = payload.model_fields_set
    title = payload.title.strip() or processed_event.title
    summary = payload.summary.strip() or processed_event.summary
    highlights = [item.strip() for item in payload.highlights if item.strip()]
    if not highlights:
        highlights = processed_event.highlights or copy_list(
            "rendering.fallback_review_highlights",
            ["待补充重点摘要。"],
        )
    category = payload.category or processed_event.category
    game = (payload.game or "").strip()
    need_translation_update: bool | None = None
    if "need_translation" in fs:
        need_translation_update = bool(payload.need_translation)
    source_credibility_update: str | None = None
    if "source_credibility" in fs:
        sc = (payload.source_credibility or "").strip()
        source_credibility_update = sc or processed_event.source_credibility
    media_update = list(payload.media or []) if "media" in fs else None
    discovered_update: list[str] | None = None
    if "discovered_sources" in fs:
        discovered_update = [
            line.strip()
            for line in (payload.discovered_sources or [])
            if isinstance(line, str) and line.strip()
        ]
    services.repository.update_processed_event_review_fields(
        processed_event.id,
        title=title,
        summary=summary,
        highlights=highlights,
        category=category,
        game=game,
        delivery_status="review_pending",
        need_translation=need_translation_update,
        source_credibility=source_credibility_update,
        media=media_update,
        discovered_sources=discovered_update,
    )
    processed_event.title = title
    processed_event.summary = summary
    processed_event.highlights = highlights
    processed_event.category = category
    processed_event.game = game or None
    processed_event.delivery_status = "review_pending"
    if need_translation_update is not None:
        processed_event.need_translation = need_translation_update
    if source_credibility_update is not None:
        processed_event.source_credibility = source_credibility_update
    if media_update is not None:
        processed_event.media = media_update
    if discovered_update is not None:
        processed_event.discovered_sources = discovered_update
    raw_payload_merge: dict[str, Any] = {}
    if "preset_key" in fs:
        raw_payload_merge["preset_key"] = (payload.preset_key or "").strip()
    if "orientation" in fs:
        raw_payload_merge["orientation"] = payload.orientation or "vertical"
    if "custom_css" in fs:
        raw_payload_merge["custom_css"] = payload.custom_css or ""
    ch: Any = _RAW_PATCH_MISSING
    au: Any = _RAW_PATCH_MISSING
    if "channel_name" in fs:
        ch = payload.channel_name
    if "author" in fs:
        au = payload.author
    if raw_payload_merge or ch is not _RAW_PATCH_MISSING or au is not _RAW_PATCH_MISSING:
        services.repository.patch_raw_event(
            raw_event.id,
            channel_name=ch,
            author=au,
            raw_payload_merge=raw_payload_merge if raw_payload_merge else None,
        )
        if raw_payload_merge:
            raw_event.raw_payload.update(raw_payload_merge)
        if ch is not _RAW_PATCH_MISSING:
            raw_event.channel_name = ch
        if au is not _RAW_PATCH_MISSING:
            raw_event.author = au
    return processed_event


def _review_preview_seed(
    *,
    raw_event: RawEvent | None,
    processed_event: ProcessedEvent,
    theme: str,
) -> dict[str, Any]:
    preset_key = ""
    orientation = "vertical"
    custom_css = ""
    if raw_event:
        preset_key = str(raw_event.raw_payload.get("preset_key") or "")
        orientation = str(raw_event.raw_payload.get("orientation") or "vertical")
        if orientation not in {"vertical", "horizontal"}:
            orientation = "vertical"
        custom_css = str(raw_event.raw_payload.get("custom_css") or "")
    return {
        "source_name": raw_event.source_name
        if raw_event
        else (processed_event.game or copy_text("rendering.preview_source", "Preview Source")),
        "channel_name": raw_event.channel_name if raw_event and raw_event.channel_name else "",
        "author": raw_event.author
        if raw_event and raw_event.author
        else copy_text("rendering.unknown_author", "Unknown"),
        "source_credibility": processed_event.source_credibility,
        "need_translation": processed_event.need_translation,
        "hero_image_url": processed_event.media[0].url if processed_event.media else "",
        "hero_image_description": processed_event.media[0].description if processed_event.media else "",
        "hero_image_reference_url": processed_event.media[0].reference_url if processed_event.media else "",
        "hero_image_reference_label": processed_event.media[0].reference_label if processed_event.media else "",
        "discovered_sources": processed_event.discovered_sources,
        "preset_key": preset_key,
        "orientation": orientation,
        "custom_css": custom_css,
        "theme": theme,
    }


def _build_preview_link(preview_seed: dict[str, Any], processed_event: ProcessedEvent) -> str:
    params = {
        "title": processed_event.title,
        "summary": processed_event.summary,
        "highlights": "\n".join(processed_event.highlights),
        "category": processed_event.category,
        "theme": preview_seed.get("theme") or "light",
        "preset_key": preview_seed.get("preset_key") or "",
        "orientation": preview_seed.get("orientation") or "vertical",
        "custom_css": preview_seed.get("custom_css") or "",
        "game": processed_event.game or "",
        "source_name": preview_seed.get("source_name")
        or copy_text("rendering.preview_source", "Preview Source"),
        "channel_name": preview_seed.get("channel_name") or "",
        "author": preview_seed.get("author") or "",
        "source_credibility": preview_seed.get("source_credibility") or "unverified",
        "hero_image_url": preview_seed.get("hero_image_url") or "",
        "hero_image_description": preview_seed.get("hero_image_description") or "",
        "hero_image_reference_url": preview_seed.get("hero_image_reference_url") or "",
        "hero_image_reference_label": preview_seed.get("hero_image_reference_label") or "",
        "discovered_sources": "\n".join(preview_seed.get("discovered_sources") or []),
        "need_translation": "true" if preview_seed.get("need_translation") else "false",
    }
    return f"/preview/md3/card?{urlencode(params)}"


async def _send_review_artifact(
    services: AppServices,
    *,
    processed_event: ProcessedEvent,
    raw_event: RawEvent,
    image_path: str,
    action: str,
    target_type: str | None,
    target_id: str | None,
) -> dict[str, Any]:
    caption_prefix = processed_event.game or raw_event.source_name
    caption = copy_format(
        "delivery.review_caption",
        "{prefix} / {title}",
        prefix=caption_prefix,
        title=processed_event.title,
    )
    trace_id = (
        f"review:{action}:{processed_event.id}:{target_type or 'default'}:"
        f"{target_id or services.settings.default_qq_group_id or 'unset'}:{utc_now().timestamp():.6f}"
    )
    return await services.operations_service.send_news_card(
        image_path=image_path,
        caption=caption[:120],
        trace_id=trace_id,
        processed_event_id=processed_event.id,
        target_type=target_type,
        target_id=target_id,
    )


def _review_delivery_status_label(status: str) -> str:
    mapping = copy_dict(
        "review_panel.delivery_status_labels",
        {
            "pending": "待处理",
            "skipped": "待审核",
            "review_pending": "待审核",
            "approved": "已批准",
            "sent": "已发送",
            "failed": "发送失败",
            "rejected": "已拒绝",
        },
    )
    return mapping.get(status, status)


def _review_delivery_status_tone(status: str) -> str:
    if status in {"sent"}:
        return "sent"
    if status in {"failed", "rejected"}:
        return "failed"
    return "pending"


def _review_category_label(category: str) -> str:
    mapping = copy_dict(
        "rendering.category_labels",
        {
            "maintenance": "通知内容",
            "patch": "更新内容",
            "teaser": "预告内容",
            "announcement": "公告内容",
        },
    )
    return mapping.get(category, category)


def _review_credibility_label(credibility: str) -> str:
    mapping = copy_dict(
        "rendering.credibility_labels",
        {
            "official": "官方来源",
            "community": "社区来源",
            "unverified": "待核实",
        },
    )
    return mapping.get(credibility, credibility)


def _review_render_status_label(status: str) -> str:
    mapping = copy_dict(
        "rendering.render_status_labels",
        {
            "pending": "待渲染",
            "html_ready": "HTML 就绪",
            "image_ready": "图片就绪",
            "failed": "渲染失败",
            "skipped": "已跳过",
        },
    )
    return mapping.get(status, status)


def _split_multiline(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _preview_param(value: str | None, defaults: dict[str, str], key: str) -> str:
    if value is not None:
        return value
    return defaults.get(key, "")


def _preview_media_from_params(
    query_params: dict[str, str],
    defaults: dict[str, str],
) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    hero_url = _preview_param(query_params.get("hero_image_url"), defaults, "hero_image_url").strip()
    if hero_url:
        media.append(
            {
                "type": "image",
                "url": hero_url,
                "description": _preview_param(
                    query_params.get("hero_image_description"), defaults, "hero_image_description"
                ).strip()
                or None,
                "reference_url": _preview_param(
                    query_params.get("hero_image_reference_url"),
                    defaults,
                    "hero_image_reference_url",
                ).strip()
                or None,
                "reference_label": _preview_param(
                    query_params.get("hero_image_reference_label"),
                    defaults,
                    "hero_image_reference_label",
                ).strip()
                or None,
            }
        )
    for index in range(1, 5):
        url = _preview_param(
            query_params.get(f"secondary_media_{index}_url"),
            defaults,
            f"secondary_media_{index}_url",
        ).strip()
        if not url:
            continue
        media.append(
            {
                "type": "image",
                "url": url,
                "description": _preview_param(
                    query_params.get(f"secondary_media_{index}_description"),
                    defaults,
                    f"secondary_media_{index}_description",
                ).strip()
                or None,
                "reference_url": _preview_param(
                    query_params.get(f"secondary_media_{index}_reference_url"),
                    defaults,
                    f"secondary_media_{index}_reference_url",
                ).strip()
                or None,
                "reference_label": _preview_param(
                    query_params.get(f"secondary_media_{index}_reference_label"),
                    defaults,
                    f"secondary_media_{index}_reference_label",
                ).strip()
                or None,
            }
        )
    return media


def _parse_bool_like(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _default_preview_form_values() -> dict[str, str]:
    defaults = copy_dict("preview_defaults", {})
    return {key: str(value) for key, value in defaults.items()}


def _discord_payload_to_raw_event(payload: DiscordWebhookPayload) -> RawEvent:
    external_id = payload.id or make_external_id(
        payload.source_name,
        payload.channel_name or "",
        payload.author.username if payload.author else "",
        payload.content,
    )
    author = None
    if payload.author:
        author = payload.author.display_name or payload.author.username
    return RawEvent(
        source_type="discord",
        source_name=payload.source_name,
        channel_name=payload.channel_name,
        author=author,
        content=payload.content,
        attachments=[attachment.url for attachment in payload.attachments],
        external_id=external_id,
        published_at=payload.timestamp or utc_now(),
        raw_payload=payload.model_dump(),
    )

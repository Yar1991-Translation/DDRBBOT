from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .copybook import copy_text
from .utils import utc_now


def new_id() -> str:
    return uuid4().hex


class DiscordAuthor(BaseModel):
    id: str | None = None
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


class DiscordAttachment(BaseModel):
    url: str
    filename: str | None = None
    content_type: str | None = None


class DiscordWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    source_name: str = Field(default_factory=lambda: copy_text("models.discord_source_name", "Discord"))
    channel_name: str | None = None
    author: DiscordAuthor | None = None
    content: str = ""
    attachments: list[DiscordAttachment] = Field(default_factory=list)
    timestamp: datetime | None = None


class MediaAsset(BaseModel):
    type: str = "image"
    url: str
    description: str | None = None
    reference_url: str | None = None
    reference_label: str | None = None


class RawEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    source_id: str | None = None
    source_type: str
    source_name: str
    channel_name: str | None = None
    author: str | None = None
    content: str
    attachments: list[str] = Field(default_factory=list)
    external_id: str
    published_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "received"
    created_at: datetime = Field(default_factory=utc_now)


class ProcessedEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    raw_event_id: str
    title: str
    summary: str
    highlights: list[str] = Field(default_factory=list)
    category: str = "announcement"
    game: str | None = None
    need_translation: bool = False
    source_credibility: str = "unverified"
    media: list[MediaAsset] = Field(default_factory=list)
    discovered_sources: list[str] = Field(default_factory=list)
    language: str = "unknown"
    render_status: str = "pending"
    delivery_status: str = "pending"
    published_at: datetime
    created_at: datetime = Field(default_factory=utc_now)


class RenderArtifact(BaseModel):
    id: str = Field(default_factory=new_id)
    processed_event_id: str
    template_name: str = "news_card"
    theme: str = "light"
    html_path: str
    image_path: str | None = None
    width: int = 860
    height: int | None = None
    created_at: datetime = Field(default_factory=utc_now)


class DeliveryLog(BaseModel):
    id: str = Field(default_factory=new_id)
    processed_event_id: str | None = None
    channel_name: str = "qq"
    target_id: str
    delivery_result: str
    message_id: str | None = None
    error_message: str | None = None
    delivered_at: datetime = Field(default_factory=utc_now)


class DeliveryRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    trace_id: str
    processed_event_id: str | None = None
    platform: str = "qq"
    adapter: str = "napcat"
    target_type: Literal["group", "private"] = "group"
    target_id: str
    status: str = "pending"
    message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempts: int = 0
    request_payload: dict[str, Any] = Field(default_factory=dict)
    next_retry_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class QQSendNewsCardRequest(BaseModel):
    trace_id: str | None = None
    processed_event_id: str | None = None
    target_type: Literal["group", "private"] = "group"
    target_id: str
    image_path: str
    caption: str | None = None


class RetryFailedDeliveryRequest(BaseModel):
    trace_id: str | None = None


class ReviewEditRequest(BaseModel):
    title: str
    summary: str
    highlights: list[str] = Field(default_factory=list)
    category: str = "announcement"
    game: str | None = None
    theme: str = "light"
    preset_key: str | None = None
    orientation: Literal["vertical", "horizontal"] | None = None
    custom_css: str | None = None
    channel_name: str | None = None
    author: str | None = None
    source_credibility: str | None = None
    need_translation: bool | None = None
    media: list[MediaAsset] | None = None
    discovered_sources: list[str] | None = None


class ReviewApproveSendRequest(ReviewEditRequest):
    target_type: Literal["group", "private"] | None = None
    target_id: str | None = None


class ReviewResendRequest(BaseModel):
    target_type: Literal["group", "private"] | None = None
    target_id: str | None = None


class QQInboundEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    platform: str = "qq"
    adapter: str = "napcat"
    event_type: str
    post_type: str | None = None
    sub_type: str | None = None
    group_id: str | None = None
    user_id: str | None = None
    message_id: str | None = None
    self_id: str | None = None
    raw_message: str | None = None
    segments: list[dict[str, Any]] = Field(default_factory=list)
    at_self: bool = False
    time: int | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class CustomPersonaPayload(BaseModel):
    label: str = ""
    description: str = ""
    system_prompt: str
    tone: str | None = None
    allow_tools: bool = True


class AIChatRequest(BaseModel):
    message: str
    origin: str = "api"
    extras: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    persona_id: str | None = None
    custom_persona: CustomPersonaPayload | None = None
    history_limit: int | None = None
    include_knowledge: bool = True
    reset_session: bool = False


class ChatSession(BaseModel):
    id: str = Field(default_factory=new_id)
    session_key: str
    origin: str
    scope: str
    group_id: str | None = None
    user_id: str | None = None
    persona_id: str | None = None
    custom_persona: CustomPersonaPayload | None = None
    summary: str = ""
    summary_updated_at: datetime | None = None
    last_message_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatMessageRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    session_id: str
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ChatProfile(BaseModel):
    id: str = Field(default_factory=new_id)
    scope: str
    user_id: str
    display_name: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatPersona(BaseModel):
    id: str = Field(default_factory=new_id)
    persona_key: str
    label: str
    description: str = ""
    system_prompt: str
    is_builtin: bool = False
    allow_tools: bool = True
    tone: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatKnowledgeItem(BaseModel):
    id: str = Field(default_factory=new_id)
    topic: str
    content: str
    tags: list[str] = Field(default_factory=list)
    priority: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PersonaUpsertRequest(BaseModel):
    persona_key: str
    label: str
    description: str = ""
    system_prompt: str
    allow_tools: bool = True
    tone: str | None = None


class KnowledgeUpsertRequest(BaseModel):
    topic: str
    content: str
    tags: list[str] = Field(default_factory=list)
    priority: int = 0
    id: str | None = None


class ProfileUpsertRequest(BaseModel):
    scope: str = "qq_private"
    user_id: str
    display_name: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class RenderPreviewRequest(BaseModel):
    title: str
    summary: str
    highlights: list[str] = Field(default_factory=list)
    category: str = "announcement"
    theme: str = "light"
    preset_key: str | None = None
    orientation: Literal["vertical", "horizontal"] = "vertical"
    custom_css: str = ""
    game: str | None = None
    source_name: str = Field(default_factory=lambda: copy_text("rendering.preview_source", "Preview Source"))
    channel_name: str | None = None
    author: str | None = Field(default_factory=lambda: copy_text("models.preview_author", "Preview Operator"))
    published_at: datetime = Field(default_factory=utc_now)
    source_credibility: str = "official"
    need_translation: bool = False
    media: list[MediaAsset] = Field(default_factory=list)
    discovered_sources: list[str] = Field(default_factory=list)


class RSSCollectRequest(BaseModel):
    source_name: str
    feed_url: str
    limit: int = 10


class RSSHubCollectRequest(RSSCollectRequest):
    pass


class SourceRegisterRequest(BaseModel):
    source_type: str = "rss"
    name: str
    feed_url: str | None = None
    credibility_level: Literal["official", "community", "unverified"] = "unverified"


class SourcePublic(BaseModel):
    id: str
    source_type: str
    name: str
    url: str | None
    status: str
    credibility_level: str | None
    last_checked_at: str | None
    created_at: str


class LLMAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    summary: str = ""
    highlights: list[Any] = Field(default_factory=list)
    category: str = "announcement"
    game: str | None = None
    need_translation: bool = False
    source_credibility: str = "unverified"
    media: list[Any] = Field(default_factory=list)
    discovered_sources: list[Any] = Field(default_factory=list)
    language: str = "unknown"

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: Any) -> str:
        text = str(value or "announcement").strip()
        if text in ("announcement", "teaser", "patch", "maintenance"):
            return text
        return "announcement"

    @field_validator("source_credibility", mode="before")
    @classmethod
    def _normalize_credibility(cls, value: Any) -> str:
        text = str(value or "unverified").strip()
        if text in ("official", "community", "unverified"):
            return text
        return "unverified"


class RSSCollectResponse(BaseModel):
    accepted: int
    deduplicated: int
    queued_event_ids: list[str] = Field(default_factory=list)


class EnqueueResult(BaseModel):
    accepted: bool
    deduplicated: bool
    raw_event_id: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    queue_size: int
    stats: dict[str, int]
    napcat_connected: bool

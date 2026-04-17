from __future__ import annotations

from dataclasses import dataclass

from .analyzer import EventAnalyzer
from .config import Settings
from .database import SQLiteRepository
from .delivery import QQDeliveryService
from .delivery_worker import DeliveryWorker
from .llm_agent import AgentScheduler, ChatService, LLMAgent, PersonaStore
from .pipeline import PipelineCoordinator
from .qq.commands import QQCommandRouter
from .qq.napcat import BotAdapter
from .qq.operations import QQOperationsService
from .qq.ws_client import NapCatWSClient
from .rendering import NewsCardRenderer


@dataclass
class AppServices:
    settings: Settings
    repository: SQLiteRepository
    analyzer: EventAnalyzer
    renderer: NewsCardRenderer
    bot_adapter: BotAdapter
    delivery_service: QQDeliveryService
    delivery_worker: DeliveryWorker
    pipeline: PipelineCoordinator
    operations_service: QQOperationsService
    command_router: QQCommandRouter
    ws_client: NapCatWSClient
    llm_agent: LLMAgent
    agent_scheduler: AgentScheduler
    persona_store: PersonaStore
    chat_service: ChatService

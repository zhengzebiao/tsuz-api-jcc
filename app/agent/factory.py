"""Runtime and LLM construction helpers."""

from __future__ import annotations

from app.agent.llm.base import LLMClient
from app.agent.llm.openai_compatible_client import OpenAICompatibleClient
from app.agent.runtime import ConversationRuntimeManager
from app.agent.tools import build_default_registry
from app.core.config import Settings
from app.core.database import SessionLocal


def build_llm_client(settings: Settings) -> LLMClient:
    return OpenAICompatibleClient(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
    )


def build_runtime(settings: Settings) -> ConversationRuntimeManager:
    return ConversationRuntimeManager(
        session_factory=SessionLocal,
        llm_client=build_llm_client(settings),
        queue_maxsize=settings.agent_queue_maxsize,
        execution_timeout_seconds=settings.agent_execution_timeout_seconds,
        context_messages=settings.agent_context_messages,
        tool_registry=build_default_registry(),
        max_tool_iterations=settings.agent_max_tool_iterations,
        tool_timeout_seconds=settings.agent_tool_timeout_seconds,
    )

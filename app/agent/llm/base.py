"""Provider-neutral asynchronous LLM contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None

    def add(self, other: LLMUsage | None) -> LLMUsage:
        if other is None:
            return self
        return LLMUsage(
            input_tokens=(self.input_tokens or 0) + other.input_tokens
            if self.input_tokens is not None or other.input_tokens is not None
            else None,
            output_tokens=(self.output_tokens or 0) + other.output_tokens
            if self.output_tokens is not None or other.output_tokens is not None
            else None,
        )


@dataclass(frozen=True)
class TextDelta:
    content: str
    usage: LLMUsage | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    usage: LLMUsage | None = None


class LLMError(Exception):
    """Base class for safe, provider-independent LLM failures."""

    code = "llm_error"


class LLMConfigurationError(LLMError):
    code = "llm_not_configured"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"


class LLMAuthenticationError(LLMError):
    code = "llm_authentication_failed"


class LLMProviderError(LLMError):
    code = "llm_provider_error"


class LLMClient:
    provider = "unknown"
    model = "unknown"

    async def stream(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        system: str,
    ) -> AsyncIterator[TextDelta]:
        raise NotImplementedError

    async def complete_with_tools(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        system: str,
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        raise NotImplementedError

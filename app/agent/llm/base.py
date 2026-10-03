"""Provider-neutral asynchronous streaming LLM contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TextDelta:
    content: str


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

"""Small OpenAI-compatible streaming client using the existing HTTP stack."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence

import httpx

from app.agent.llm.base import (
    LLMAuthenticationError,
    LLMClient,
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    TextDelta,
)


class OpenAICompatibleClient(LLMClient):
    provider = "openai_compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 120.0,
        max_tokens: int = 2048,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self._client = client
        self._owns_client = client is None

    async def stream(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        system: str,
    ) -> AsyncIterator[TextDelta]:
        if not self.model or not self.base_url or not self.api_key:
            raise LLMConfigurationError("LLM endpoint is not configured")
        request_messages = [{"role": "system", "content": system}, *messages]
        payload = {
            "model": self.model,
            "messages": request_messages,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code in (401, 403):
                    raise LLMAuthenticationError
                if response.status_code == 429:
                    raise LLMRateLimitError
                if response.status_code >= 400:
                    raise LLMProviderError(f"provider returned status {response.status_code}")
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        decoded = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = decoded.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield TextDelta(content=delta)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError from exc
        except asyncio.CancelledError:
            raise
        except (LLMAuthenticationError, LLMRateLimitError, LLMProviderError):
            raise
        except httpx.HTTPError as exc:
            raise LLMProviderError from exc
        finally:
            if self._owns_client:
                await client.aclose()

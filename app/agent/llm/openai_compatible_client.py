"""Small OpenAI-compatible streaming client using the existing HTTP stack."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence

import httpx

logger = logging.getLogger(__name__)
_MAX_ERROR_LOG = 500
_SECRET_PATTERN = re.compile(
    r"(?i)([\"']?(?:authorization|api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']?)[^\"',\s}]+"
)
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[^\s,\"']+")


def _parse_usage(value: object) -> LLMUsage | None:
    if not isinstance(value, dict):
        return None

    def integer(*keys: str) -> int | None:
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
                return candidate
        return None

    input_tokens = integer("input_tokens", "prompt_tokens")
    output_tokens = integer("output_tokens", "completion_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _safe_error_body(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                text = str(error.get("message") or error.get("type") or "provider error")
            else:
                text = str(error or payload.get("message") or "provider error")
        else:
            text = str(payload)
    except (ValueError, TypeError):
        text = response.text
    text = _SECRET_PATTERN.sub(r"\1[REDACTED]", text)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    return text[:_MAX_ERROR_LOG]


from app.agent.llm.base import (
    LLMAuthenticationError,
    LLMClient,
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
    TextDelta,
    ToolCall,
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

    async def complete_with_tools(
        self,
        *,
        messages: Sequence[Mapping[str, object]],
        system: str,
        tools: Sequence[Mapping[str, object]],
    ) -> LLMResponse:
        if not self.model or not self.base_url or not self.api_key:
            raise LLMConfigurationError("LLM endpoint is not configured")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": self.max_tokens,
            "tools": list(tools),
            "tool_choice": "auto",
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            if response.status_code in (401, 403):
                raise LLMAuthenticationError
            if response.status_code == 429:
                raise LLMRateLimitError
            if response.status_code >= 400:
                detail = _safe_error_body(response)
                logger.warning("LLM provider request failed status_code=%s detail=%s", response.status_code, detail)
                raise LLMProviderError(f"provider returned status {response.status_code}: {detail}")
            decoded = response.json()
            choice = (decoded.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            calls = []
            for raw in message.get("tool_calls") or []:
                function = raw.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    raise LLMProviderError("provider returned invalid tool arguments") from exc
                if not isinstance(arguments, dict):
                    raise LLMProviderError("provider returned invalid tool arguments")
                calls.append(ToolCall(id=str(raw.get("id") or ""), name=str(function.get("name") or ""), arguments=arguments))
            return LLMResponse(
                text=str(message.get("content") or ""),
                tool_calls=tuple(calls),
                finish_reason=choice.get("finish_reason"),
                usage=_parse_usage(decoded.get("usage")),
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError from exc
        except (LLMAuthenticationError, LLMRateLimitError, LLMProviderError):
            raise
        except httpx.HTTPError as exc:
            raise LLMProviderError from exc
        finally:
            if self._owns_client:
                await client.aclose()

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
                    detail = _safe_error_body(response)
                    logger.warning("LLM provider request failed status_code=%s detail=%s", response.status_code, detail)
                    raise LLMProviderError(f"provider returned status {response.status_code}: {detail}")
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
                    delta = (choices[0].get("delta") or {}).get("content") if choices else None
                    usage = _parse_usage(decoded.get("usage"))
                    if delta or usage is not None:
                        yield TextDelta(content=str(delta or ""), usage=usage)
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

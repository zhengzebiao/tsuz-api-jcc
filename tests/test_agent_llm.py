import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from app.agent.llm.base import LLMAuthenticationError, LLMClient, LLMConfigurationError, TextDelta
from app.agent.llm.openai_compatible_client import OpenAICompatibleClient


class FakeLLM(LLMClient):
    provider = "fake"
    model = "fake-model"

    async def stream(self, *, messages, system) -> AsyncIterator[TextDelta]:
        yield TextDelta("hello")
        yield TextDelta(" world")


def test_fake_client_streams_text() -> None:
    async def run() -> None:
        client = FakeLLM()
        chunks = [item async for item in client.stream(messages=[], system="system")]
        assert [item.content for item in chunks] == ["hello", " world"]

    asyncio.run(run())


def test_openai_compatible_client_parses_sse() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer secret"
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b'data: {"choices":[{"delta":{"content":"one"}}]}\n\ndata: [DONE]\n\n',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAICompatibleClient(
                model="model",
                base_url="https://llm.example.test/v1",
                api_key="secret",
                client=http_client,
            )
            chunks = [item async for item in client.stream(messages=[], system="system")]

        assert [item.content for item in chunks] == ["one"]

    asyncio.run(run())


def test_openai_compatible_client_rejects_missing_configuration() -> None:
    async def run() -> None:
        client = OpenAICompatibleClient(model="", base_url="", api_key="")
        with pytest.raises(LLMConfigurationError):
            [item async for item in client.stream(messages=[], system="system")]

    asyncio.run(run())


def test_openai_compatible_client_maps_authentication_error() -> None:
    async def run() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAICompatibleClient(
                model="model",
                base_url="https://llm.example.test/v1",
                api_key="secret",
                client=http_client,
            )
            with pytest.raises(LLMAuthenticationError):
                [item async for item in client.stream(messages=[], system="system")]

    asyncio.run(run())

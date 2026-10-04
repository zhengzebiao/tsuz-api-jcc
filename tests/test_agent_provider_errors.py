import asyncio

import httpx
import pytest

from app.agent.llm.base import LLMProviderError
from app.agent.llm.openai_compatible_client import OpenAICompatibleClient


def test_provider_400_includes_safe_error_detail_without_secret() -> None:
    async def run() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "invalid tool role; token=secret-value"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            llm = OpenAICompatibleClient(model="model", base_url="https://llm.example.test/v1", api_key="secret", client=client)
            with pytest.raises(LLMProviderError, match="invalid tool role") as error:
                await llm.complete_with_tools(messages=[], system="system", tools=[])
            assert "secret-value" not in str(error.value)

    asyncio.run(run())

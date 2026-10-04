import asyncio

import httpx

from app.agent.llm.openai_compatible_client import OpenAICompatibleClient


def test_openai_compatible_client_parses_tool_call_response() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode()
            assert '"tools"' in body
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {"id": "call-1", "type": "function", "function": {"name": "get_hero", "arguments": '{"external_id":"hero_1"}'}},
                                ],
                            },
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = OpenAICompatibleClient(model="model", base_url="https://llm.example.test/v1", api_key="secret", client=http_client)
            response = await client.complete_with_tools(messages=[{"role": "user", "content": "hero"}], system="system", tools=[])
        assert response.tool_calls[0].id == "call-1"
        assert response.tool_calls[0].name == "get_hero"
        assert response.tool_calls[0].arguments == {"external_id": "hero_1"}

    asyncio.run(run())

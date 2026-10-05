import asyncio

from app.agent.llm.base import LLMUsage
from app.agent.llm.openai_compatible_client import _parse_usage
from app.core.rate_limit import InProcessRateLimiter


def test_usage_parser_supports_openai_aliases() -> None:
    assert _parse_usage({"prompt_tokens": 12, "completion_tokens": 7}) == LLMUsage(12, 7)
    assert _parse_usage({"input_tokens": 3, "output_tokens": 2}) == LLMUsage(3, 2)
    assert _parse_usage({"prompt_tokens": -1}) is None


def test_usage_add_preserves_unknown_values() -> None:
    assert LLMUsage().add(LLMUsage(2, None)).add(LLMUsage(None, 4)) == LLMUsage(2, 4)


def test_in_process_rate_limiter_returns_retry_after() -> None:
    async def run() -> None:
        limiter = InProcessRateLimiter(limit=1, window_seconds=60)
        assert (await limiter.check("user:1")).allowed
        decision = await limiter.check("user:1")
        assert not decision.allowed
        assert decision.retry_after_seconds >= 1
        assert (await limiter.check("user:2")).allowed

    asyncio.run(run())

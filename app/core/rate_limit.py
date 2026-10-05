"""Small in-process rate limiter for the single-worker Agent deployment."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class InProcessRateLimiter:
    def __init__(self, *, limit: int, window_seconds: float) -> None:
        self.limit = max(0, limit)
        self.window_seconds = max(0.001, window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> RateLimitDecision:
        now = time.monotonic()
        async with self._lock:
            hits = self._hits[key]
            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = max(1, int(hits[0] + self.window_seconds - now + 0.999))
                return RateLimitDecision(False, retry)
            hits.append(now)
            return RateLimitDecision(True)

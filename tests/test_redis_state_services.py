from datetime import UTC, datetime

import pytest

import app.core.redis as redis_module
import app.services.blacklist_service as blacklist_module
import app.services.session_service as session_module
from app.core.config import settings
from app.services.blacklist_service import BlacklistService
from app.services.session_service import SessionService


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.expirations[key] = ttl

    def exists(self, key: str) -> bool:
        return key in self.values

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)


@pytest.fixture
def fake_main_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(blacklist_module, "get_main_redis", lambda: redis)
    monkeypatch.setattr(session_module, "get_main_redis", lambda: redis)
    return redis


def test_blacklist_service_uses_main_redis_and_configured_prefix(fake_main_redis: FakeRedis) -> None:
    exp = int(datetime.now(UTC).timestamp()) + 120

    BlacklistService().add_jti("jti-123", exp)

    key = f"{settings.token_blacklist_prefix}jti-123"
    assert fake_main_redis.values[key] == "1"
    assert 1 <= fake_main_redis.expirations[key] <= 120
    with pytest.raises(ValueError, match="blacklisted"):
        BlacklistService().ensure_not_blacklisted("jti-123")


def test_session_service_uses_main_redis_and_configured_prefix(fake_main_redis: FakeRedis) -> None:
    SessionService().revoke_session("sid-123")

    assert fake_main_redis.values[f"{settings.session_prefix}sid-123"] == "revoked"
    with pytest.raises(ValueError, match="session is revoked"):
        SessionService().ensure_session_active("sid-123")


def test_redis_clients_use_separate_configured_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool, object]] = []

    class FakeRedisFactory:
        @staticmethod
        def from_url(url: str, *, decode_responses: bool) -> object:
            client = object()
            calls.append((url, decode_responses, client))
            return client

    monkeypatch.setattr(redis_module, "Redis", FakeRedisFactory)
    monkeypatch.setattr(settings, "redis_url", "redis://jcc-redis:6379/0")
    monkeypatch.setattr(settings, "main_redis_url", "redis://main-redis:6379/0")
    redis_module.get_redis.cache_clear()
    redis_module.get_main_redis.cache_clear()

    try:
        jcc_redis = redis_module.get_redis()
        main_redis = redis_module.get_main_redis()
    finally:
        redis_module.get_redis.cache_clear()
        redis_module.get_main_redis.cache_clear()

    assert jcc_redis is not main_redis
    assert calls == [
        ("redis://jcc-redis:6379/0", True, jcc_redis),
        ("redis://main-redis:6379/0", True, main_redis),
    ]


def test_main_redis_falls_back_to_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    class FakeRedisFactory:
        @staticmethod
        def from_url(url: str, *, decode_responses: bool) -> object:
            assert decode_responses is True
            urls.append(url)
            return object()

    monkeypatch.setattr(redis_module, "Redis", FakeRedisFactory)
    monkeypatch.setattr(settings, "redis_url", "redis://shared-redis:6379/0")
    monkeypatch.setattr(settings, "main_redis_url", None)
    redis_module.get_main_redis.cache_clear()

    try:
        redis_module.get_main_redis()
    finally:
        redis_module.get_main_redis.cache_clear()

    assert urls == ["redis://shared-redis:6379/0"]

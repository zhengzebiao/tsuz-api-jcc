import json

import httpx
import pytest

from app.clients.main_client import MainClient, MainClientAuthenticationError, MainClientRequestError
from app.core.config import settings


def test_main_client_uses_basic_only_for_token_and_caches_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jcc_app_id", "app_jcc")
    monkeypatch.setattr(settings, "jcc_app_secret", "app_secret_example_value_123456")
    monkeypatch.setattr(settings, "main_app_id", "app_main")
    monkeypatch.setattr(settings, "main_token_url", "https://main.example/internal/oauth/token")
    monkeypatch.setattr(settings, "main_api_base_url", "https://main.example")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/internal/oauth/token":
            return httpx.Response(
                200,
                json={"access_token": "service-token", "expires_in": 300},
            )
        return httpx.Response(200, json={"app_id": "app_main", "name": "Main"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = MainClient(http_client)
        first = client.get_application("app_main", request_id="req-1")
        second = client.get_application("app_main")

    assert first["app_id"] == second["app_id"] == "app_main"
    assert [request.url.path for request in requests].count("/internal/oauth/token") == 1
    token_request = requests[0]
    assert token_request.headers["Authorization"].startswith("Basic ")
    assert b"app_secret" not in token_request.content
    resource_requests = requests[1:]
    assert all(request.headers["Authorization"] == "Bearer service-token" for request in resource_requests)
    assert resource_requests[0].headers["X-Request-ID"] == "req-1"


def test_main_client_raises_safe_fixed_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jcc_app_id", "app_jcc")
    monkeypatch.setattr(settings, "jcc_app_secret", "app_secret_example_value_123456")
    monkeypatch.setattr(settings, "main_app_id", "app_main")

    with (
        httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(401))) as http_client,
        pytest.raises(MainClientAuthenticationError) as exc_info,
    ):
        MainClient(http_client).get_application("app_main")
    assert str(exc_info.value) == "MAIN_CLIENT_AUTHENTICATION_ERROR"
    assert settings.jcc_app_secret not in str(exc_info.value)

    responses = iter(
        [
            httpx.Response(200, json={"access_token": "service-token", "expires_in": 300}),
            httpx.Response(500, content=json.dumps({"detail": "failed"})),
        ]
    )
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _request: next(responses))) as http_client,
        pytest.raises(MainClientRequestError) as exc_info,
    ):
        MainClient(http_client).get_application("app_main")
    assert str(exc_info.value) == "MAIN_CLIENT_REQUEST_ERROR"

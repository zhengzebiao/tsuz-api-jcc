import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("app.main_client")


class MainClientError(RuntimeError):
    code = "MAIN_CLIENT_ERROR"


class MainClientConfigurationError(MainClientError):
    code = "MAIN_CLIENT_CONFIGURATION_ERROR"


class MainClientAuthenticationError(MainClientError):
    code = "MAIN_CLIENT_AUTHENTICATION_ERROR"


class MainClientRequestError(MainClientError):
    code = "MAIN_CLIENT_REQUEST_ERROR"


@dataclass(frozen=True)
class CachedServiceToken:
    value: str
    refresh_at: float


class MainClient:
    REQUIRED_SCOPE = "main:application:read"
    REFRESH_MARGIN_SECONDS = 30

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=settings.internal_http_timeout_seconds)
        self._owns_client = client is None
        self._cached_token: CachedServiceToken | None = None
        self._cache_lock = threading.Lock()

    def get_application(
        self,
        app_id: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._service_token()}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            response = self._client.get(
                f"{settings.main_api_base_url.rstrip('/')}/internal/v1/applications/{app_id}",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("main request failed reason=upstream_error")
            raise MainClientRequestError(MainClientRequestError.code) from exc
        if not isinstance(payload, dict):
            raise MainClientRequestError(MainClientRequestError.code)
        return payload

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _service_token(self) -> str:
        now = time.monotonic()
        cached = self._cached_token
        if cached is not None and cached.refresh_at > now:
            return cached.value
        with self._cache_lock:
            cached = self._cached_token
            now = time.monotonic()
            if cached is not None and cached.refresh_at > now:
                return cached.value
            token = self._fetch_service_token(now)
            self._cached_token = token
            return token.value

    def _fetch_service_token(self, now: float) -> CachedServiceToken:
        if not settings.jcc_app_id or not settings.jcc_app_secret or not settings.main_app_id:
            raise MainClientConfigurationError(MainClientConfigurationError.code)
        try:
            response = self._client.post(
                settings.main_token_url,
                auth=httpx.BasicAuth(settings.jcc_app_id, settings.jcc_app_secret),
                data={
                    "grant_type": "client_credentials",
                    "audience": settings.main_app_id,
                    "scope": self.REQUIRED_SCOPE,
                },
            )
            response.raise_for_status()
            payload = response.json()
            token = payload["access_token"]
            expires_in = payload["expires_in"]
            if not isinstance(token, str) or not token:
                raise ValueError("invalid token response")
            if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
                raise ValueError("invalid expiry response")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("main token request failed reason=authentication_error")
            raise MainClientAuthenticationError(MainClientAuthenticationError.code) from exc
        refresh_in = max(0, expires_in - self.REFRESH_MARGIN_SECONDS)
        return CachedServiceToken(token, now + refresh_in)

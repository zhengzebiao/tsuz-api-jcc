from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.internal import router as internal_router
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.logging import RequestIdMiddleware
from app.models.sample_profile import SampleProfile
from tests.conftest import TEST_PRIVATE_KEY, TEST_PUBLIC_KEY


@pytest.fixture
def internal_context(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    db.add_all(
        [
            SampleProfile(slug="second", display_name="Second", is_active=True),
            SampleProfile(slug="inactive", display_name="Inactive", is_active=False),
            SampleProfile(slug="third", display_name="Third", is_active=True),
        ]
    )
    db.commit()

    monkeypatch.setattr(settings, "service_token_public_key", TEST_PUBLIC_KEY)
    monkeypatch.setattr(settings, "service_token_issuer", "tsuz-api-main-test")
    monkeypatch.setattr(settings, "service_token_audience", "app_jcc")
    monkeypatch.setattr(settings, "jcc_app_id", "app_jcc")
    monkeypatch.setattr(settings, "service_token_clock_skew_seconds", 5)

    def override_db() -> Iterator[DbSession]:
        yield db

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.include_router(internal_router)
    application.dependency_overrides[get_db] = override_db
    try:
        with TestClient(application) as client:
            yield client
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_service_token(**overrides) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": "tsuz-api-main-test",
        "sub": "app_main",
        "aud": "app_jcc",
        "token_use": "service",
        "scope": "jcc:record:read",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "jti": "jti-service",
    }
    payload.update(overrides)
    return jwt.encode(payload, TEST_PRIVATE_KEY, algorithm="RS256")


def test_internal_records_returns_only_active_safe_fields(internal_context: TestClient) -> None:
    response = internal_context.get(
        "/internal/v1/records",
        headers={"Authorization": f"Bearer {make_service_token()}", "X-Request-ID": "req-records"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-records"
    assert response.json() == [
        {"id": 1, "slug": "second", "display_name": "Second", "is_active": True},
        {"id": 3, "slug": "third", "display_name": "Third", "is_active": True},
    ]


def test_internal_records_requires_scope(internal_context: TestClient) -> None:
    response = internal_context.get(
        "/internal/v1/records",
        headers={"Authorization": f"Bearer {make_service_token(scope='jcc:other:read')}"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "insufficient_scope"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "wrong"},
        {"aud": "wrong"},
        {"aud": ["app_jcc"]},
        {"token_use": "access"},
        {"sub": ""},
        {"jti": None},
        {"scope": ["jcc:record:read"]},
        {"iat": "now"},
        {"nbf": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())},
        {"exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())},
    ],
)
def test_internal_records_rejects_invalid_service_claims(internal_context: TestClient, overrides) -> None:
    response = internal_context.get(
        "/internal/v1/records",
        headers={"Authorization": f"Bearer {make_service_token(**overrides)}"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid service token"}


def test_internal_records_rejects_missing_token_and_user_token(internal_context: TestClient) -> None:
    missing = internal_context.get("/internal/v1/records")
    now = datetime.now(UTC)
    user_token = jwt.encode(
        {
            "iss": "tsuz-api-main-test",
            "sub": "user-1",
            "aud": "app_jcc",
            "scope": "jcc:record:read",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": "user-jti",
            "sid": "sid",
        },
        TEST_PRIVATE_KEY,
        algorithm="RS256",
    )
    user = internal_context.get(
        "/internal/v1/records",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert missing.status_code == 401
    assert user.status_code == 401


def test_internal_openapi_uses_separate_service_scheme(internal_context: TestClient) -> None:
    schema = internal_context.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["ServiceBearer"]["scheme"] == "bearer"
    assert {"ServiceBearer": []} in schema["paths"]["/internal/v1/records"]["get"]["security"]

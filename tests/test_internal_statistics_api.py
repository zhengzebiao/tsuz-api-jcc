from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.internal import router
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.logging import RequestIdMiddleware
from app.jcc_data.models import JccCurrentSnapshot, JccHero, JccSnapshot
from tests.conftest import TEST_PRIVATE_KEY, TEST_PUBLIC_KEY


def make_token(scope: str = "jcc:stats:read") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "main-test",
            "sub": "app_main",
            "aud": "app_jcc",
            "token_use": "service",
            "scope": scope,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": "stats-jti",
        },
        TEST_PRIVATE_KEY,
        algorithm="RS256",
    )


def test_resource_statistics_returns_counts_for_one_snapshot() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    snapshot = JccSnapshot(
        mode="18", mode_name="Mode", season="S19", version="18.1", revision=1,
        set_id="19", raw_directory_name="18.1-S19", content_hash="a" * 64,
        source_manifest={"sources": {}},
    )
    db.add(snapshot)
    db.flush()
    db.add(JccCurrentSnapshot(mode="18", snapshot_id=snapshot.id))
    db.add(JccHero(snapshot_id=snapshot.id, external_id="1", name="Hero", source_attributes={}))
    db.commit()
    settings.service_token_public_key = TEST_PUBLIC_KEY
    settings.service_token_issuer = "main-test"
    settings.jcc_app_id = "app_jcc"
    settings.service_token_audience = "app_jcc"

    def override_db() -> Iterator[Session]:
        yield db

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.include_router(router)
    application.dependency_overrides[get_db] = override_db
    try:
        with TestClient(application) as client:
            response = client.get(
                "/internal/v1/resource-statistics",
                headers={"Authorization": f"Bearer {make_token()}", "X-Request-ID": "stats-1"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["snapshot"]["version"] == "18.1"
        assert {item["resource"]: item["count"] for item in body["items"]}["heroes"] == 1
        assert response.headers["X-Request-ID"] == "stats-1"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_resource_statistics_requires_scope() -> None:
    assert make_token("jcc:record:read")

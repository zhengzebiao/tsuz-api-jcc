from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.agent import router
from app.core.database import Base, get_db
from app.core.logging import RequestIdMiddleware
from tests.conftest import _make_access_token


@pytest.fixture
def agent_context() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = session_factory()

    def override_db() -> Iterator[Session]:
        yield db

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.include_router(router)
    application.dependency_overrides[get_db] = override_db
    try:
        with TestClient(application) as client:
            yield client
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def headers(*, user_id: str = "user-1", scope: str = "jcc:agent:chat") -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_access_token(user_id=user_id, scope=scope)}"}


def create_conversation(client: TestClient, *, user_id: str = "user-1", **payload) -> dict:
    response = client.post(
        "/jcc/agent/conversations",
        headers=headers(user_id=user_id),
        json={"title": "Test conversation", **payload},
    )
    assert response.status_code == 201
    return response.json()


def test_conversation_requires_scope_and_is_user_isolated(agent_context: TestClient) -> None:
    missing_scope = agent_context.post(
        "/jcc/agent/conversations",
        headers=headers(scope="user:read"),
        json={"title": "Test"},
    )
    assert missing_scope.status_code == 403

    conversation = create_conversation(agent_context, user_id="owner")
    response = agent_context.get(
        f"/jcc/agent/conversations/{conversation['id']}",
        headers=headers(user_id="other"),
    )
    assert response.status_code == 404


def test_conversation_crud_and_archive_is_idempotent(agent_context: TestClient) -> None:
    conversation = create_conversation(agent_context, strategy_mode="operation")
    assert conversation["strategy_mode"] == "operation"
    assert conversation["status"] == "active"

    updated = agent_context.patch(
        f"/jcc/agent/conversations/{conversation['id']}",
        headers=headers(),
        json={"title": "Updated", "strategy_mode": "gamble"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated"
    assert updated.json()["strategy_mode"] == "gamble"

    archived = agent_context.post(
        f"/jcc/agent/conversations/{conversation['id']}/archive",
        headers=headers(),
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    archived_again = agent_context.post(
        f"/jcc/agent/conversations/{conversation['id']}/archive",
        headers=headers(),
    )
    assert archived_again.status_code == 200
    assert archived_again.json()["archived_at"] == archived.json()["archived_at"]

    cannot_update = agent_context.patch(
        f"/jcc/agent/conversations/{conversation['id']}",
        headers=headers(),
        json={"title": "Nope"},
    )
    assert cannot_update.status_code == 409


def test_message_mode_sequence_pagination_and_idempotency(agent_context: TestClient) -> None:
    conversation = create_conversation(agent_context, strategy_mode="operation")
    url = f"/jcc/agent/conversations/{conversation['id']}/messages"

    first = agent_context.post(
        url,
        headers=headers(),
        json={"content": "first", "client_request_id": "request-1"},
    )
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert first.json()["strategy_mode"] == "operation"
    assert first.json()["sequence"] == 1

    replay = agent_context.post(
        url,
        headers=headers(),
        json={"content": "first", "client_request_id": "request-1"},
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["sequence"] == 1

    conflict = agent_context.post(
        url,
        headers=headers(),
        json={"content": "different", "client_request_id": "request-1"},
    )
    assert conflict.status_code == 409

    second = agent_context.post(
        url,
        headers=headers(),
        json={"content": "second", "strategy_mode": "gamble"},
    )
    assert second.status_code == 202
    assert second.json()["sequence"] == 2
    assert second.json()["strategy_mode"] == "gamble"

    page = agent_context.get(f"{url}?limit=1&offset=1", headers=headers())
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert [item["sequence"] for item in page.json()["items"]] == [2]


def test_empty_conversation_update_is_rejected(agent_context: TestClient) -> None:
    conversation = create_conversation(agent_context)
    response = agent_context.patch(
        f"/jcc/agent/conversations/{conversation['id']}",
        headers=headers(),
        json={},
    )
    assert response.status_code == 422


def test_archived_conversation_rejects_new_message(agent_context: TestClient) -> None:
    conversation = create_conversation(agent_context)
    archive = agent_context.post(
        f"/jcc/agent/conversations/{conversation['id']}/archive",
        headers=headers(),
    )
    assert archive.status_code == 200

    response = agent_context.post(
        f"/jcc/agent/conversations/{conversation['id']}/messages",
        headers=headers(),
        json={"content": "not accepted"},
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 256},
        {"title": "valid", "strategy_mode": "invalid"},
    ],
)
def test_conversation_validation(agent_context: TestClient, payload: dict) -> None:
    response = agent_context.post(
        "/jcc/agent/conversations",
        headers=headers(),
        json=payload,
    )
    assert response.status_code == 422


def test_message_validation_and_client_cannot_supply_server_fields(agent_context: TestClient) -> None:
    conversation = create_conversation(agent_context)
    url = f"/jcc/agent/conversations/{conversation['id']}/messages"
    too_long = agent_context.post(url, headers=headers(), json={"content": "x" * 8001})
    assert too_long.status_code == 422
    extra = agent_context.post(
        url,
        headers=headers(),
        json={"content": "hello", "role": "assistant", "sequence": 99, "status": "completed"},
    )
    assert extra.status_code == 422

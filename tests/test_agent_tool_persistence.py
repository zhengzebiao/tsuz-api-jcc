from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.models import AgentMessageSource, AgentToolCall
from app.conversations import repository, service
from app.core.database import Base


@pytest.fixture
def database() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_tool_call_and_source_state_are_auditable(database) -> None:
    factory = database
    db = factory()
    conversation = service.create_conversation(db, user_id="user", title="tools", strategy_mode="gamble")
    message = service.create_message(db, user_id="user", conversation_id=conversation.id, content="question", strategy_mode=None, client_request_id=None)
    run = service.ensure_run(db, message=message, provider="fake", model="fake")
    call = repository.create_tool_call(db, run=run, message=message, tool_use_id="call-1", tool_name="get_hero", input_json={"external_id": "hero"}, snapshot_id=1, snapshot_version="18.18.2")
    db.commit()
    assert repository.update_tool_call(db, call, status="succeeded", output_json={"ok": True}, completed_at=datetime.now(UTC))
    source = repository.create_message_source(db, message=message, run=run, tool_call_id=call.id, source_type="official_structured_data", snapshot_id=1, version="18.18.2", entity_type="hero", entity_id="hero")
    db.commit()
    assert db.get(AgentToolCall, call.id).status == "succeeded"
    assert db.get(AgentMessageSource, source.id).entity_id == "hero"
    db.close()

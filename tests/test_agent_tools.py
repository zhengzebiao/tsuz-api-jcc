import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.tools.registry import build_default_registry
from app.agent.tools.schemas import DeriveLineupInput, EmptyInput, SnapshotContext, ToolContext
from app.agent.tools.structured import get_snapshot_metadata
from app.core.database import Base
from app.jcc_data.models import JccCurrentSnapshot, JccHero, JccSnapshot


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


def context(factory, snapshot_id: int = 1) -> ToolContext:
    return ToolContext(
        snapshot=SnapshotContext(snapshot_id, "18", "S19", "18.18.2", 1, "hash"),
        session_factory=factory,
        cancel_event=asyncio.Event(),
    )


def test_registry_is_static_and_excludes_unsafe_tools() -> None:
    names = {item.name for item in build_default_registry().definitions()}
    assert "search_knowledge" in names
    assert not names.intersection({"execute_sql", "execute_shell", "write_jcc_data", "switch_snapshot"})


def test_tool_input_rejects_extra_fields() -> None:
    with pytest.raises(ValueError):
        DeriveLineupInput.model_validate({"goal": "frontline", "sql": "select 1"})


def test_snapshot_metadata_is_scoped_to_context(database) -> None:
    db = database()
    snapshot = JccSnapshot(
        id=1, mode="18", mode_name="test", season="S19", version="18.18.2", revision=1,
        set_id="set", raw_directory_name="raw", content_hash="hash", source_manifest={}
    )
    db.add(snapshot)
    db.flush()
    db.add(JccCurrentSnapshot(mode="18", snapshot_id=1))
    db.commit()
    db.close()

    result = get_snapshot_metadata(context(database), EmptyInput())
    assert result.output["snapshot_id"] == 1
    assert result.sources[0].source_type == "official_structured_data"


def test_lineup_marks_system_derived(database) -> None:
    db = database()
    db.add(
        JccSnapshot(
            id=1, mode="18", mode_name="test", season="S19", version="18.18.2", revision=1,
            set_id="set", raw_directory_name="raw", content_hash="hash", source_manifest={}
        )
    )
    db.flush()
    db.add(JccHero(snapshot_id=1, external_id="hero_1", name="Hero", source_attributes={}))
    db.commit()
    db.close()

    from app.agent.tools.lineup import derive_lineup_candidates

    result = derive_lineup_candidates(context(database), DeriveLineupInput(goal="持续输出"))
    assert result.output["is_system_derived"] is True
    assert result.output["source_type"] == "system_derived"

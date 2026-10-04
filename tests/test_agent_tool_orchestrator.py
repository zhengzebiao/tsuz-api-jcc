import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.llm.base import LLMClient, LLMResponse, TextDelta, ToolCall
from app.agent.models import AgentRun
from app.agent.orchestrator import AgentOrchestrator
from app.agent.tools.registry import ToolDefinition, ToolRegistry
from app.agent.tools.schemas import EmptyInput, ToolContext, ToolExecutionResult
from app.conversations import service
from app.conversations.models import AgentMessage
from app.core.database import Base
from app.jcc_data.models import JccCurrentSnapshot, JccSnapshot


class MultiTurnLLM(LLMClient):
    provider = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    async def stream(self, *, messages, system) -> AsyncIterator[TextDelta]:
        yield TextDelta("text")

    async def complete_with_tools(self, *, messages, system, tools) -> LLMResponse:
        self.calls += 1
        self.requests.append(list(messages))
        if self.calls == 1:
            return LLMResponse("", (ToolCall("call-1", "fixed_tool", {}),), "tool_calls")
        return LLMResponse("final", (), "stop")


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


def test_orchestrator_runs_tool_then_final_response(database) -> None:
    db = database()
    conversation = service.create_conversation(db, user_id="user", title="tools", strategy_mode="gamble")
    message = service.create_message(db, user_id="user", conversation_id=conversation.id, content="question", strategy_mode=None, client_request_id=None)
    run = service.ensure_run(db, message=message, provider="fake", model="fake")
    db.add(JccSnapshot(id=1, mode="18", mode_name="test", season="S19", version="18.18.2", revision=1, set_id="set", raw_directory_name="raw", content_hash="hash", source_manifest={}))
    db.flush()
    db.add(JccCurrentSnapshot(mode="18", snapshot_id=1))
    db.commit()
    conversation_id, message_id, run_id = conversation.id, message.id, run.id
    db.close()

    def fixed_tool(_context: ToolContext, _input: EmptyInput) -> ToolExecutionResult:
        return ToolExecutionResult({"value": "fixed"})

    registry = ToolRegistry((ToolDefinition("fixed_tool", "fixed", EmptyInput, fixed_tool),))
    llm = MultiTurnLLM()
    emitted = []

    async def sink(item) -> None:
        emitted.append(item)

    async def run_test() -> None:
        orchestrator = AgentOrchestrator(session_factory=database, llm_client=llm, tool_registry=registry)
        await orchestrator.execute(
            conversation_id=conversation_id,
            message_id=message_id,
            run_id=run_id,
            sink=sink,
            cancel_event=asyncio.Event(),
            timeout_seconds=2,
        )

    async def sinkless() -> None:
        await run_test()

    asyncio.run(sinkless())
    db = database()
    try:
        assert llm.calls == 2
        assert llm.requests[1][1]["content"] is None
        assert db.get(AgentRun, run_id).status == "completed"
        assert db.get(AgentMessage, message_id).status == "completed"
        assert any(item.name == "tool.completed" for item in emitted)
        assert any(item.name == "message.completed" for item in emitted)
    finally:
        db.close()

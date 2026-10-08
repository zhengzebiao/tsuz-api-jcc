import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.llm.base import LLMClient, TextDelta
from app.agent.models import AgentRun
from app.agent.runtime import ConversationRuntimeManager
from app.conversations import repository, service
from app.conversations.models import AgentMessage
from app.core.database import Base


class FakeLLM(LLMClient):
    provider = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[list[dict[str, str]]] = []

    async def stream(self, *, messages, system) -> AsyncIterator[TextDelta]:
        self.calls.append(list(messages))
        self.started.set()
        yield TextDelta("answer")
        await self.release.wait()
        yield TextDelta(" done")


@pytest.fixture
def database() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _create_message(factory, content: str) -> tuple[str, str, str]:
    db = factory()
    try:
        conversation = service.create_conversation(db, user_id="user-1", title="test", strategy_mode="gamble")
        message = service.create_message(
            db,
            user_id="user-1",
            conversation_id=conversation.id,
            content=content,
            strategy_mode=None,
            client_request_id=None,
        )
        run = service.ensure_run(db, message=message, provider="fake", model="fake-model")
        return conversation.id, message.id, run.id
    finally:
        db.close()


def test_runtime_executes_text_and_preserves_user_message(database) -> None:
    async def run_test() -> None:
        llm = FakeLLM()
        manager = ConversationRuntimeManager(session_factory=database, llm_client=llm, execution_timeout_seconds=2)
        conversation_id, message_id, run_id = _create_message(database, "question")
        await manager.start()
        await manager.enqueue(message_id=message_id, run_id=run_id, conversation_id=conversation_id)
        await asyncio.wait_for(llm.started.wait(), timeout=1)
        llm.release.set()
        runtime = manager._runtimes[conversation_id]
        await asyncio.wait_for(runtime.queue.join(), timeout=2)
        await asyncio.sleep(0)

        db = database()
        try:
            message = db.get(AgentMessage, message_id)
            agent_run = db.get(AgentRun, run_id)
            messages = repository.list_messages(db, conversation_id=conversation_id, limit=10, offset=0).items
            assert message.content == "question"
            assert message.status == "completed"
            assert agent_run.status == "completed"
            assert agent_run.output_content == "answer done"
            assert [(item.role, item.content) for item in messages] == [
                ("assistant", "answer done"),
                ("user", "question"),
            ]
        finally:
            db.close()
            await manager.stop()

    asyncio.run(run_test())


def test_cancel_only_cancels_matching_current_message(database) -> None:
    async def run_test() -> None:
        llm = FakeLLM()
        manager = ConversationRuntimeManager(session_factory=database, llm_client=llm, execution_timeout_seconds=2)
        conversation_id, message_id, run_id = _create_message(database, "question")
        await manager.start()
        await manager.enqueue(message_id=message_id, run_id=run_id, conversation_id=conversation_id)
        await asyncio.wait_for(llm.started.wait(), timeout=1)
        await manager.cancel(conversation_id=conversation_id, message_id=message_id, run_id=run_id)
        await asyncio.sleep(0.05)

        db = database()
        try:
            message = db.get(AgentMessage, message_id)
            assert message.status in {"cancelled", "cancelling"}
        finally:
            db.close()
            await manager.stop()

    asyncio.run(run_test())

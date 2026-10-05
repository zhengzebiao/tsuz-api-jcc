"""Single-process latest-wins runtime for Agent conversations."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.agent.events import AgentEvent
from app.agent.llm.base import LLMClient
from app.agent.models import AgentRun
from app.agent.orchestrator import AgentOrchestrator
from app.conversations import repository
from app.conversations.models import AgentConversation, AgentMessage
from app.core import database


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


@dataclass
class ConversationRuntime:
    queue: asyncio.Queue[tuple[str, str]]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_task: asyncio.Task | None = None
    current_message_id: str | None = None
    current_run_id: str | None = None
    replay_run_id: str | None = None
    cancel_event: asyncio.Event | None = None
    subscribers: set[asyncio.Queue[AgentEvent]] = field(default_factory=set)
    subscriber_users: dict[str, int] = field(default_factory=dict)
    event_history: deque[AgentEvent] = field(default_factory=deque)
    event_sequence: int = 0
    event_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event_persistence: object | None = None
    consumer_task: asyncio.Task | None = None


class ConversationRuntimeManager:
    def __init__(
        self,
        *,
        session_factory: SessionFactory = database.SessionLocal,
        llm_client: LLMClient,
        queue_maxsize: int = 1,
        execution_timeout_seconds: float = 180.0,
        context_messages: int = 10,
        tool_registry=None,
        max_tool_iterations: int = 8,
        tool_timeout_seconds: float = 15.0,
    ) -> None:
        self.session_factory = session_factory
        self.llm_client = llm_client
        self.queue_maxsize = queue_maxsize
        self.execution_timeout_seconds = execution_timeout_seconds
        self.orchestrator = AgentOrchestrator(
            llm_client=llm_client,
            session_factory=session_factory,
            context_messages=context_messages,
            tool_registry=tool_registry,
            max_tool_iterations=max_tool_iterations,
            tool_timeout_seconds=tool_timeout_seconds,
        )
        self._runtimes: dict[str, ConversationRuntime] = {}
        self._stopping = False
        self._manager_lock = asyncio.Lock()

    async def start(self) -> None:
        self._stopping = False
        recoverable = await asyncio.to_thread(self._list_recoverable_runs)
        for run in recoverable:
            await self._enqueue_existing(run)

    def _list_recoverable_runs(self) -> list[AgentRun]:
        db = self.session_factory()
        try:
            return repository.list_recoverable_runs(db)
        finally:
            db.close()

    async def stop(self, *, timeout_seconds: float = 10.0) -> None:
        self._stopping = True
        tasks: list[asyncio.Task] = []
        for runtime in self._runtimes.values():
            if runtime.current_task is not None:
                runtime.current_task.cancel()
                tasks.append(runtime.current_task)
            if runtime.consumer_task is not None:
                runtime.consumer_task.cancel()
                tasks.append(runtime.consumer_task)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
            for task in pending:
                task.cancel()
        self._runtimes.clear()

    async def enqueue(self, *, message_id: str, run_id: str, conversation_id: str) -> None:
        if self._stopping:
            raise RuntimeError("agent runtime is stopping")
        runtime = await self._runtime_for(conversation_id)
        async with runtime.lock:
            if runtime.cancel_event is not None:
                runtime.cancel_event.set()
            current_task = runtime.current_task
            if current_task is not None and not current_task.done():
                current_task.cancel()
                try:
                    await current_task
                except asyncio.CancelledError:
                    pass
            superseded = await asyncio.to_thread(self._cancel_queued, conversation_id, message_id)
            for old_message_id, old_run_id in superseded:
                await self._publish(
                    runtime,
                    _event("message.cancelled", old_message_id, old_run_id, status="cancelled", reason="superseded"),
                )
            while not runtime.queue.empty():
                try:
                    runtime.queue.get_nowait()
                    runtime.queue.task_done()
                except asyncio.QueueEmpty:
                    break
            runtime.queue.put_nowait((message_id, run_id))
            await self._publish(runtime, _event("message.queued", message_id, run_id, status="queued"))

    async def cancel(self, *, conversation_id: str, message_id: str, run_id: str) -> None:
        runtime = await self._runtime_for(conversation_id)
        if runtime.current_message_id == message_id:
            if runtime.cancel_event is not None:
                runtime.cancel_event.set()
            current_task = runtime.current_task
            if current_task is not None and not current_task.done():
                current_task.cancel()
            # Persist the terminal state immediately; the orchestrator also performs
            # its conditional cleanup, but cancellation must be observable without
            # waiting for that task's thread handoff to finish.
            await asyncio.to_thread(self._mark_cancelled, message_id, run_id)
            await self._publish(runtime, _event("message.cancelled", message_id, run_id, status="cancelled"))
        else:
            cancelled = await asyncio.to_thread(self._cancel_queued_message, message_id, run_id)
            if cancelled:
                await self._publish(runtime, _event("message.cancelled", message_id, run_id, status="cancelled"))

    async def subscribe(self, conversation_id: str, *, user_id: str = "") -> tuple[ConversationRuntime, asyncio.Queue[AgentEvent]]:
        runtime = await self._runtime_for(conversation_id)
        subscriber: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=100)
        runtime.subscribers.add(subscriber)
        runtime.subscriber_users[user_id] = runtime.subscriber_users.get(user_id, 0) + 1
        return runtime, subscriber

    @staticmethod
    async def unsubscribe(runtime: ConversationRuntime, subscriber: asyncio.Queue[AgentEvent], *, user_id: str = "") -> None:
        runtime.subscribers.discard(subscriber)
        if user_id in runtime.subscriber_users:
            runtime.subscriber_users[user_id] -= 1
            if runtime.subscriber_users[user_id] <= 0:
                runtime.subscriber_users.pop(user_id, None)

    @staticmethod
    def _persist_event(runtime: ConversationRuntime, emitted: AgentEvent) -> None:
        if runtime.event_persistence is None:
            return
        db = runtime.event_persistence()
        try:
            if db.get_bind().dialect.name == "sqlite":
                return
            run_id = str(emitted.data.get("run_id") or "")
            message_id = str(emitted.data.get("message_id") or "")
            run = db.get(AgentRun, run_id)
            message = db.get(AgentMessage, message_id)
            if run is not None and message is not None:
                repository.create_run_event(db, run=run, message=message, event_name=emitted.name, payload=emitted.data, sequence=int(emitted.event_id or "0"))
                db.commit()
        finally:
            db.close()

    @staticmethod
    async def replay(runtime: ConversationRuntime, last_event_id: str | None) -> list[AgentEvent]:
        try:
            cursor = int(last_event_id or "0")
        except ValueError as exc:
            raise ValueError("invalid event cursor") from exc
        if runtime.event_persistence is not None:
            persisted = await asyncio.to_thread(ConversationRuntimeManager._load_events, runtime, cursor)
            if persisted:
                return persisted
        return [item for item in runtime.event_history if int(item.event_id or "0") > cursor]

    @staticmethod
    def _load_events(runtime: ConversationRuntime, cursor: int) -> list[AgentEvent]:
        if runtime.event_persistence is None:
            return []
        db = runtime.event_persistence()
        try:
            events = repository.list_run_events(db, run_id=runtime.current_run_id or runtime.replay_run_id or "", after_sequence=cursor)
            return [AgentEvent(name=item.event_name, data=item.payload, event_id=str(item.sequence)) for item in events]
        finally:
            db.close()

    async def _runtime_for(self, conversation_id: str) -> ConversationRuntime:
        async with self._manager_lock:
            runtime = self._runtimes.get(conversation_id)
            if runtime is None:
                runtime = ConversationRuntime(queue=asyncio.Queue(maxsize=self.queue_maxsize), event_persistence=self.session_factory)
                runtime.consumer_task = asyncio.create_task(self._consume(conversation_id, runtime))
                self._runtimes[conversation_id] = runtime
            return runtime

    async def _consume(self, conversation_id: str, runtime: ConversationRuntime) -> None:
        while not self._stopping:
            message_id, run_id = await runtime.queue.get()
            try:
                db = self.session_factory()
                try:
                    message = db.get(AgentMessage, message_id)
                    run = db.get(AgentRun, run_id)
                    conversation = db.get(AgentConversation, conversation_id)
                    if message is None or run is None or conversation is None or run.status != "queued":
                        continue
                finally:
                    db.close()

                runtime.cancel_event = asyncio.Event()
                runtime.current_message_id = message_id
                runtime.current_run_id = run_id
                execution = asyncio.create_task(
                    self.orchestrator.execute(
                        conversation_id=conversation_id,
                        message_id=message_id,
                        run_id=run_id,
                        sink=lambda emitted: self._publish(runtime, emitted),
                        cancel_event=runtime.cancel_event,
                        timeout_seconds=self.execution_timeout_seconds,
                    )
                )
                runtime.current_task = execution
                try:
                    await execution
                except asyncio.CancelledError:
                    if not execution.done():
                        execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                finally:
                    runtime.current_task = None
                    runtime.current_message_id = None
                    runtime.current_run_id = None
                    runtime.cancel_event = None
            finally:
                runtime.queue.task_done()

    async def _enqueue_existing(self, run: AgentRun) -> None:
        if run.status in ("running", "cancelling"):
            await asyncio.to_thread(self._requeue_run, run.id, run.message_id)
        await self.enqueue(message_id=run.message_id, run_id=run.id, conversation_id=run.conversation_id)

    def _requeue_run(self, run_id: str, message_id: str) -> None:
        db = self.session_factory()
        try:
            run = db.get(AgentRun, run_id)
            message = db.get(AgentMessage, message_id)
            if run is not None:
                repository.update_run_status(
                    db,
                    run,
                    from_statuses=("running", "cancelling"),
                    status="queued",
                    cancel_requested=False,
                )
            if message is not None:
                repository.update_message_status(
                    db,
                    message,
                    from_statuses=("running", "streaming", "cancelling"),
                    status="queued",
                )
            db.commit()
        finally:
            db.close()

    def _mark_cancelled(self, message_id: str, run_id: str) -> None:
        db = self.session_factory()
        try:
            message = db.get(AgentMessage, message_id)
            run = db.get(AgentRun, run_id)
            if message is None or run is None:
                return
            now = datetime.now(UTC)
            repository.update_run_status(
                db,
                run,
                from_statuses=("queued", "running", "cancelling"),
                status="cancelled",
                cancel_requested=True,
                error_code="cancelled",
                completed_at=now,
            )
            repository.update_message_status(
                db,
                message,
                from_statuses=("queued", "running", "streaming", "cancelling"),
                status="cancelled",
                error_code="cancelled",
                completed_at=now,
            )
            db.commit()
        finally:
            db.close()

    def _cancel_queued_message(self, message_id: str, run_id: str) -> bool:
        db = self.session_factory()
        try:
            run = db.get(AgentRun, run_id)
            message = db.get(AgentMessage, message_id)
            run_updated = False
            message_updated = False
            if run is not None:
                run_updated = repository.update_run_status(
                    db, run, from_statuses=("queued", "cancelling"), status="cancelled", cancel_requested=True
                )
            if message is not None:
                message_updated = repository.update_message_status(
                    db, message, from_statuses=("queued", "cancelling"), status="cancelled"
                )
            db.commit()
            return run_updated or message_updated
        finally:
            db.close()

    def _cancel_queued(self, conversation_id: str, except_message_id: str) -> list[tuple[str, str]]:
        superseded: list[tuple[str, str]] = []
        db = self.session_factory()
        try:
            runs = list(
                db.query(AgentRun)
                .filter(AgentRun.conversation_id == conversation_id, AgentRun.status == "queued")
                .all()
            )
            for run in runs:
                if run.message_id == except_message_id:
                    continue
                superseded.append((run.message_id, run.id))
                message = db.get(AgentMessage, run.message_id)
                repository.update_run_status(
                    db,
                    run,
                    from_statuses=("queued",),
                    status="cancelled",
                    cancel_requested=True,
                    error_code="superseded",
                )
                if message is not None:
                    repository.update_message_status(
                        db, message, from_statuses=("queued",), status="cancelled", error_code="superseded"
                    )
            db.commit()
        finally:
            db.close()
        return superseded

    @staticmethod
    async def _publish(runtime: ConversationRuntime, emitted: AgentEvent) -> None:
        async with runtime.event_lock:
            runtime.event_sequence += 1
            emitted = AgentEvent(
                name=emitted.name,
                data=emitted.data,
                event_id=str(runtime.event_sequence),
            )
            runtime.event_history.append(emitted)
            asyncio.create_task(
                asyncio.to_thread(ConversationRuntimeManager._persist_event, runtime, emitted)
            )
            while len(runtime.event_history) > 100:
                runtime.event_history.popleft()
            for subscriber in tuple(runtime.subscribers):
                try:
                    subscriber.put_nowait(emitted)
                except asyncio.QueueFull:
                    continue


def _event(name: str, message_id: str, run_id: str, **data: object) -> AgentEvent:
    return AgentEvent(name=name, data={"message_id": message_id, "run_id": run_id, **data})

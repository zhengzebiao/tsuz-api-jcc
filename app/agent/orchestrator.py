"""Text-only orchestration for one persisted Agent run."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agent.events import AgentEvent, event
from app.agent.llm.base import LLMClient, LLMError
from app.agent.models import AgentRun
from app.agent.prompts import build_system_prompt
from app.conversations import repository
from app.conversations.models import AgentConversation, AgentMessage

EventSink = Callable[[AgentEvent], Awaitable[None]]
SessionFactory = Callable[[], Session]


class AgentCancelled(Exception):
    """The current run was explicitly superseded or cancelled."""


class AgentOrchestrator:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        session_factory: SessionFactory,
        context_messages: int = 10,
    ) -> None:
        self.llm_client = llm_client
        self.session_factory = session_factory
        self.context_messages = context_messages

    async def execute(
        self,
        *,
        conversation_id: str,
        message_id: str,
        run_id: str,
        sink: EventSink,
        cancel_event: asyncio.Event,
        timeout_seconds: float,
    ) -> None:
        started = datetime.now(UTC)
        started_ok = await asyncio.to_thread(self._mark_running, message_id, run_id, started)
        if not started_ok:
            return
        await sink(event("run.started", message_id=message_id, run_id=run_id, model=self.llm_client.model))
        history, strategy_mode = await asyncio.to_thread(self._history, conversation_id, message_id)
        system = build_system_prompt(strategy_mode)
        generated: list[str] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                async for delta in self.llm_client.stream(messages=history, system=system):
                    if cancel_event.is_set():
                        raise AgentCancelled
                    generated.append(delta.content)
                    text = "".join(generated)
                    if len(text) > 8000:
                        raise LLMError("output limit exceeded")
                    await asyncio.to_thread(self._save_streaming, message_id, run_id, text)
                    await sink(event("text.delta", message_id=message_id, run_id=run_id, content=delta.content))
            if cancel_event.is_set():
                raise AgentCancelled
            answer = "".join(generated)
            await asyncio.to_thread(self._complete, conversation_id, message_id, run_id, answer, strategy_mode, started)
            await sink(event("message.completed", message_id=message_id, run_id=run_id, status="completed"))
        except (AgentCancelled, asyncio.CancelledError):
            await asyncio.to_thread(self._cancel, message_id, run_id, started, "".join(generated))
            await sink(event("message.cancelled", message_id=message_id, run_id=run_id, status="cancelled"))
        except LLMError as exc:
            await asyncio.to_thread(self._fail, message_id, run_id, exc.code, started, "".join(generated))
            await sink(event("message.failed", message_id=message_id, run_id=run_id, status="failed", error_code=exc.code))
        except TimeoutError:
            await asyncio.to_thread(self._fail, message_id, run_id, "agent_timeout", started, "".join(generated))
            await sink(
                event("message.failed", message_id=message_id, run_id=run_id, status="failed", error_code="agent_timeout")
            )
        except Exception:  # noqa: BLE001 - persist an opaque provider failure
            await asyncio.to_thread(self._fail, message_id, run_id, "agent_error", started, "".join(generated))
            await sink(
                event("message.failed", message_id=message_id, run_id=run_id, status="failed", error_code="agent_error")
            )

    def _session(self) -> Session:
        return self.session_factory()

    def _history(self, conversation_id: str, current_message_id: str) -> tuple[list[dict[str, str]], str]:
        db = self._session()
        try:
            conversation = db.get(AgentConversation, conversation_id)
            recent = repository.list_recent_messages(
                db,
                conversation_id=conversation_id,
                limit=self.context_messages,
            )
            current = db.get(AgentMessage, current_message_id)
            messages = [item for item in recent if item.id != current_message_id]
            history = [
                {"role": item.role, "content": item.content}
                for item in messages
                if item.role in ("user", "assistant")
            ]
            return history, current.strategy_mode if current is not None else conversation.strategy_mode
        finally:
            db.close()

    def _mark_running(self, message_id: str, run_id: str, started: datetime) -> bool:
        db = self._session()
        try:
            run = db.get(AgentRun, run_id)
            message = db.get(AgentMessage, message_id)
            if run is None or message is None:
                return False
            run_updated = repository.update_run_status(
                db, run, from_statuses=("queued",), status="running", started_at=started, cancel_requested=False
            )
            message_updated = repository.update_message_status(
                db, message, from_statuses=("queued",), status="running", started_at=started
            )
            if not run_updated or not message_updated:
                db.rollback()
                return False
            db.commit()
            return True
        finally:
            db.close()

    def _save_streaming(self, message_id: str, run_id: str, content: str) -> None:
        db = self._session()
        try:
            run = db.get(AgentRun, run_id)
            message = db.get(AgentMessage, message_id)
            if run is None or message is None:
                return
            repository.update_run_status(
                db, run, from_statuses=("running",), status="running", output_content=content
            )
            db.commit()
        finally:
            db.close()

    def _complete(
        self,
        conversation_id: str,
        message_id: str,
        run_id: str,
        answer: str,
        strategy_mode: str,
        started: datetime,
    ) -> None:
        db = self._session()
        try:
            conversation = db.get(AgentConversation, conversation_id)
            message = db.get(AgentMessage, message_id)
            run = db.get(AgentRun, run_id)
            if conversation is None or message is None or run is None:
                return
            now = datetime.now(UTC)
            repository.update_run_status(
                db,
                run,
                from_statuses=("running",),
                status="completed",
                completed_at=now,
                duration_ms=int((now - started).total_seconds() * 1000),
                output_content=answer,
            )
            repository.update_message_status(
                db,
                message,
                from_statuses=("running", "streaming"),
                status="completed",
                completed_at=now,
            )
            repository.create_assistant_message(
                db, conversation=conversation, content=answer, strategy_mode=strategy_mode, status="completed"
            )
            db.commit()
        finally:
            db.close()

    def _cancel(self, message_id: str, run_id: str, started: datetime, content: str) -> None:
        self._finish(message_id, run_id, "cancelled", "cancelled", started, content)

    def _fail(self, message_id: str, run_id: str, error_code: str, started: datetime, content: str) -> None:
        self._finish(message_id, run_id, "failed", error_code, started, content)

    def _finish(
        self,
        message_id: str,
        run_id: str,
        status: str,
        error_code: str,
        started: datetime,
        content: str,
    ) -> None:
        db = self._session()
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
                status=status,
                cancel_requested=status == "cancelled",
                error_code=error_code,
                completed_at=now,
                duration_ms=int((now - started).total_seconds() * 1000),
                output_content=content,
            )
            repository.update_message_status(
                db,
                message,
                from_statuses=("queued", "running", "streaming", "cancelling"),
                status=status,
                error_code=error_code,
                completed_at=now,
            )
            db.commit()
        finally:
            db.close()

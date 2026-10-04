"""Text-only orchestration for one persisted Agent run."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agent.events import AgentEvent, event
from app.agent.llm.base import LLMClient, LLMError
from app.agent.models import AgentRun, AgentToolCall
from app.agent.prompts import build_system_prompt
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.schemas import SnapshotContext, ToolContext
from app.conversations import repository
from app.conversations.models import AgentConversation, AgentMessage
from app.core.config import settings

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
        tool_registry: ToolRegistry | None = None,
        max_tool_iterations: int = 8,
        tool_timeout_seconds: float = 15.0,
    ) -> None:
        self.llm_client = llm_client
        self.session_factory = session_factory
        self.context_messages = context_messages
        self.tool_registry = tool_registry
        self.max_tool_iterations = max_tool_iterations
        self.tool_timeout_seconds = tool_timeout_seconds

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
                if self.tool_registry is None:
                    await self._stream_text(
                        history, system, generated, message_id, run_id, sink, cancel_event
                    )
                    snapshot = None
                else:
                    snapshot = await asyncio.to_thread(self._resolve_snapshot, run_id)
                    if snapshot is None:
                        raise LLMError("jcc_snapshot_unavailable")
                    await self._run_tool_loop(
                        history=history,
                        system=system,
                        snapshot=snapshot,
                        generated=generated,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        run_id=run_id,
                        strategy_mode=strategy_mode,
                        started=started,
                        sink=sink,
                        cancel_event=cancel_event,
                    )
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

    async def _stream_text(self, history, system, generated, message_id, run_id, sink, cancel_event) -> None:
        async for delta in self.llm_client.stream(messages=history, system=system):
            if cancel_event.is_set():
                raise AgentCancelled
            generated.append(delta.content)
            text = "".join(generated)
            if len(text) > 8000:
                raise LLMError("output limit exceeded")
            await asyncio.to_thread(self._save_streaming, message_id, run_id, text)
            await sink(event("text.delta", message_id=message_id, run_id=run_id, content=delta.content))

    async def _run_tool_loop(
        self, *, history, system, snapshot, generated, conversation_id, message_id, run_id,
        strategy_mode, started, sink, cancel_event,
    ) -> None:
        messages = list(history)
        context = ToolContext(snapshot=snapshot, session_factory=self.session_factory, cancel_event=cancel_event)
        for iteration in range(self.max_tool_iterations + 1):
            if cancel_event.is_set():
                raise AgentCancelled
            response = await self.llm_client.complete_with_tools(
                messages=messages, system=system, tools=self.tool_registry.provider_definitions()
            )
            if response.text:
                generated.append(response.text)
                await asyncio.to_thread(self._save_streaming, message_id, run_id, "".join(generated))
                await sink(event("text.delta", message_id=message_id, run_id=run_id, content=response.text))
            if not response.tool_calls:
                return
            if iteration >= self.max_tool_iterations:
                raise LLMError("agent_tool_limit")
            assistant_calls = [{"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}} for call in response.tool_calls]
            messages.append({"role": "assistant", "content": response.text or "", "tool_calls": assistant_calls})
            results = []
            for call in response.tool_calls:
                audit_id = await asyncio.to_thread(
                    self._create_tool_audit, run_id, message_id, call.id, call.name, call.arguments, snapshot
                )
                if cancel_event.is_set():
                    raise AgentCancelled
                definition = self.tool_registry.get(call.name)
                if definition is None:
                    result = {"ok": False, "error": {"code": "tool_not_allowed"}}
                    await asyncio.to_thread(self._finish_tool_audit, audit_id, "rejected", result, "tool_not_allowed")
                    await sink(event("tool.failed", message_id=message_id, run_id=run_id, tool_name=call.name, tool_call_id=call.id, error_code="tool_not_allowed"))
                else:
                    await sink(event("tool.started", message_id=message_id, run_id=run_id, tool_name=call.name, tool_call_id=call.id))
                    try:
                        parsed = definition.input_model.model_validate(call.arguments)
                        execution = await asyncio.wait_for(asyncio.to_thread(definition.execute, context, parsed), self.tool_timeout_seconds)
                        encoded = json.dumps(execution.output, ensure_ascii=False)
                        if len(encoded.encode("utf-8")) > settings.agent_tool_output_max_bytes:
                            raise LLMError("tool_output_limit")
                        result = {"ok": True, **execution.output}
                        await asyncio.to_thread(self._finish_tool_audit, audit_id, "succeeded", result, None)
                        await asyncio.to_thread(self._save_tool_sources, message_id, run_id, audit_id, execution.sources)
                        await sink(event("tool.completed", message_id=message_id, run_id=run_id, tool_name=call.name, tool_call_id=call.id, status="succeeded"))
                        for source in execution.sources:
                            await sink(event("source", message_id=message_id, run_id=run_id, source_type=source.source_type, snapshot_id=source.snapshot_id, version=source.version, entity_type=source.entity_type, entity_id=source.entity_id))
                    except Exception:  # noqa: BLE001 - tool failures are returned safely
                        result = {"ok": False, "error": {"code": "tool_execution_failed"}}
                        await asyncio.to_thread(self._finish_tool_audit, audit_id, "failed", result, "tool_execution_failed")
                        await sink(event("tool.failed", message_id=message_id, run_id=run_id, tool_name=call.name, tool_call_id=call.id, error_code="tool_execution_failed"))
                results.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
            messages.extend(results)

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
            if current is not None and current.role == "user":
                history.append({"role": "user", "content": current.content})
            return history, current.strategy_mode if current is not None else conversation.strategy_mode
        finally:
            db.close()

    def _create_tool_audit(self, run_id, message_id, tool_use_id, tool_name, arguments, snapshot):
        db = self._session()
        try:
            run = db.get(AgentRun, run_id)
            message = db.get(AgentMessage, message_id)
            if run is None or message is None:
                return ""
            call = repository.create_tool_call(db, run=run, message=message, tool_use_id=tool_use_id, tool_name=tool_name, input_json=arguments, snapshot_id=snapshot.snapshot_id, snapshot_version=snapshot.version)
            call.status = "running"
            db.commit()
            return call.id
        finally:
            db.close()

    def _finish_tool_audit(self, audit_id, status, output, error_code):
        if not audit_id:
            return
        db = self._session()
        try:
            call = db.get(AgentToolCall, audit_id)
            if call is not None:
                repository.update_tool_call(db, call, status=status, output_json=output, error_code=error_code, completed_at=datetime.now(UTC))
                db.commit()
        finally:
            db.close()

    def _save_tool_sources(self, message_id, run_id, audit_id, sources):
        db = self._session()
        try:
            message = db.get(AgentMessage, message_id)
            run = db.get(AgentRun, run_id)
            if message is None or run is None:
                return
            for source in sources:
                repository.create_message_source(db, message=message, run=run, tool_call_id=audit_id, source_type=source.source_type, snapshot_id=source.snapshot_id, version=source.version, entity_type=source.entity_type, entity_id=source.entity_id, rank=source.rank, excerpt=source.excerpt, metadata=source.metadata)
            db.commit()
        finally:
            db.close()

    def _resolve_snapshot(self, run_id: str) -> SnapshotContext | None:
        from app.jcc_data import repository as jcc_repository
        db = self._session()
        try:
            run = db.get(AgentRun, run_id)
            if run is None:
                return None
            if run.snapshot_id is not None:
                snapshot = jcc_repository.get_snapshot(db, mode=run.snapshot_mode or "", snapshot_id=run.snapshot_id)
            else:
                conversation = db.get(AgentConversation, run.conversation_id)
                snapshot = jcc_repository.get_current_snapshot(db, conversation.strategy_mode if conversation else "18")
            if snapshot is None:
                return None
            context = SnapshotContext(snapshot.id, snapshot.mode, snapshot.season, snapshot.version, snapshot.revision, snapshot.content_hash)
            if run.snapshot_id is None:
                repository.update_run_status(db, run, from_statuses=("running",), status="running", snapshot_id=context.snapshot_id, snapshot_mode=context.mode, snapshot_season=context.season, snapshot_version=context.version, snapshot_revision=context.revision, snapshot_content_hash=context.content_hash)
                db.commit()
            return context
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

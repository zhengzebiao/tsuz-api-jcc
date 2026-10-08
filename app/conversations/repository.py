"""Persistence operations for user-owned conversations, messages, and runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.agent.models import AgentConversationSummary, AgentMessageSource, AgentRun, AgentRunEvent, AgentToolCall
from app.conversations.models import AgentConversation, AgentMessage


@dataclass(frozen=True)
class Page:
    items: list
    total: int


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_conversation(db: Session, conversation_id: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:namespace), hashtext(:conversation_id))"),
            {"namespace": "agent_conversations", "conversation_id": conversation_id},
        )


def get_conversation(db: Session, user_id: str, conversation_id: str) -> AgentConversation | None:
    return db.scalar(
        select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
    )


def list_conversations(db: Session, user_id: str, *, limit: int, offset: int) -> Page:
    base = select(AgentConversation).where(AgentConversation.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = list(
        db.scalars(
            base.order_by(AgentConversation.id.asc())
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(items=items, total=total)


def create_conversation(db: Session, *, user_id: str, title: str, strategy_mode: str) -> AgentConversation:
    conversation = AgentConversation(user_id=user_id, title=title, strategy_mode=strategy_mode)
    db.add(conversation)
    db.flush()
    return conversation


def update_conversation(
    db: Session,
    conversation: AgentConversation,
    *,
    title: str | None,
    strategy_mode: str | None,
) -> AgentConversation:
    if title is not None:
        conversation.title = title
    if strategy_mode is not None:
        conversation.strategy_mode = strategy_mode
    conversation.updated_at = _now()
    db.flush()
    return conversation


def archive_conversation(db: Session, conversation: AgentConversation) -> AgentConversation:
    if conversation.status != "archived":
        conversation.status = "archived"
        conversation.archived_at = _now()
        conversation.updated_at = _now()
        db.flush()
    return conversation


def get_message_by_request_id(
    db: Session, *, conversation_id: str, client_request_id: str
) -> AgentMessage | None:
    return db.scalar(
        select(AgentMessage).where(
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.client_request_id == client_request_id,
        )
    )


def get_message(db: Session, *, conversation_id: str, message_id: str) -> AgentMessage | None:
    return db.scalar(
        select(AgentMessage).where(
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.id == message_id,
        )
    )


def get_run(db: Session, *, conversation_id: str, message_id: str) -> AgentRun | None:
    return db.scalar(
        select(AgentRun).where(
            AgentRun.conversation_id == conversation_id,
            AgentRun.message_id == message_id,
        )
    )


def get_run_by_id(db: Session, *, conversation_id: str, run_id: str) -> AgentRun | None:
    return db.scalar(
        select(AgentRun).where(
            AgentRun.conversation_id == conversation_id,
            AgentRun.id == run_id,
        )
    )


def list_recoverable_runs(db: Session) -> list[AgentRun]:
    return list(
        db.scalars(
            select(AgentRun)
            .where(AgentRun.status.in_(("queued", "running", "cancelling")))
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
    )


def lock_conversation(db: Session, conversation: AgentConversation) -> None:
    """Serialize writes for one conversation on PostgreSQL."""
    _lock_conversation(db, conversation.id)


def create_message(
    db: Session,
    *,
    conversation: AgentConversation,
    user_id: str,
    content: str,
    strategy_mode: str,
    client_request_id: str | None,
) -> AgentMessage:
    # The caller must hold the conversation lock before checking idempotency and
    # allocating a sequence. The unique constraints remain the final guard.
    next_sequence = (
        db.scalar(
            select(func.coalesce(func.max(AgentMessage.sequence), 0) + 1).where(
                AgentMessage.conversation_id == conversation.id
            )
        )
        or 1
    )
    message = AgentMessage(
        conversation_id=conversation.id,
        user_id=user_id,
        role="user",
        content=content,
        status="queued",
        sequence=next_sequence,
        strategy_mode=strategy_mode,
        client_request_id=client_request_id,
    )
    db.add(message)
    conversation.updated_at = _now()
    db.flush()
    return message


def create_run(
    db: Session,
    *,
    message: AgentMessage,
    provider: str,
    model: str,
) -> AgentRun:
    run = AgentRun(
        message_id=message.id,
        conversation_id=message.conversation_id,
        provider=provider,
        model=model,
    )
    db.add(run)
    db.flush()
    return run


def create_tool_call(
    db: Session,
    *,
    run: AgentRun,
    message: AgentMessage,
    tool_use_id: str,
    tool_name: str,
    input_json: dict,
    snapshot_id: int | None = None,
    snapshot_version: str | None = None,
) -> AgentToolCall:
    call = AgentToolCall(
        run_id=run.id,
        message_id=message.id,
        conversation_id=message.conversation_id,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        input_json=input_json,
        snapshot_id=snapshot_id,
        snapshot_version=snapshot_version,
    )
    db.add(call)
    db.flush()
    return call


def create_run_event(db: Session, *, run: AgentRun, message: AgentMessage, event_name: str, payload: dict, sequence: int | None = None) -> AgentRunEvent:
    sequence = sequence or (db.scalar(select(func.coalesce(func.max(AgentRunEvent.sequence), 0) + 1).where(AgentRunEvent.run_id == run.id)) or 1)
    item = AgentRunEvent(run_id=run.id, conversation_id=run.conversation_id, message_id=message.id, sequence=sequence, event_name=event_name, payload=payload)
    db.add(item)
    db.flush()
    return item


def list_run_events(db: Session, *, run_id: str, after_sequence: int = 0) -> list[AgentRunEvent]:
    return list(db.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id, AgentRunEvent.sequence > after_sequence).order_by(AgentRunEvent.sequence.asc())))


def get_latest_summary(db: Session, *, conversation_id: str) -> AgentConversationSummary | None:
    return db.scalar(select(AgentConversationSummary).where(AgentConversationSummary.conversation_id == conversation_id).order_by(AgentConversationSummary.through_sequence.desc()).limit(1))


def save_summary(db: Session, *, conversation_id: str, through_sequence: int, summary: str, model: str | None = None) -> AgentConversationSummary:
    item = AgentConversationSummary(conversation_id=conversation_id, through_sequence=through_sequence, summary=summary, model=model)
    db.add(item)
    db.flush()
    return item


def update_tool_call(
    db: Session,
    call: AgentToolCall,
    *,
    status: str,
    output_json: dict | None = None,
    error_code: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
) -> bool:
    values: dict[str, object] = {"status": status}
    for name, value in (("output_json", output_json), ("error_code", error_code), ("started_at", started_at), ("completed_at", completed_at), ("duration_ms", duration_ms)):
        if value is not None:
            values[name] = value
    result = db.execute(update(AgentToolCall).where(AgentToolCall.id == call.id, AgentToolCall.status.in_(("requested", "running"))).values(**values))
    if result.rowcount:
        db.refresh(call)
        return True
    return False


def create_message_source(
    db: Session,
    *,
    message: AgentMessage,
    run: AgentRun,
    source_type: str,
    snapshot_id: int | None,
    version: str | None,
    tool_call_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    rank: int | None = None,
    excerpt: str | None = None,
    metadata: dict | None = None,
) -> AgentMessageSource:
    source = AgentMessageSource(message_id=message.id, run_id=run.id, tool_call_id=tool_call_id, source_type=source_type, snapshot_id=snapshot_id, version=version, entity_type=entity_type, entity_id=entity_id, rank=rank, excerpt=excerpt, metadata_=metadata)
    db.add(source)
    db.flush()
    return source


def update_run_status(
    db: Session,
    run: AgentRun,
    *,
    from_statuses: tuple[str, ...],
    status: str,
    cancel_requested: bool | None = None,
    error_code: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
    output_content: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost: float | None = None,
    pricing_key: str | None = None,
    snapshot_id: int | None = None,
    snapshot_mode: str | None = None,
    snapshot_season: str | None = None,
    snapshot_version: str | None = None,
    snapshot_revision: int | None = None,
    snapshot_content_hash: str | None = None,
) -> bool:
    values: dict[str, object] = {"status": status}
    if cancel_requested is not None:
        values["cancel_requested"] = cancel_requested
    if error_code is not None:
        values["error_code"] = error_code
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if duration_ms is not None:
        values["duration_ms"] = duration_ms
    if output_content is not None:
        values["output_content"] = output_content
    for name, value in (
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("estimated_cost", estimated_cost),
        ("pricing_key", pricing_key),
    ):
        if value is not None:
            values[name] = value
    for name, value in (
        ("snapshot_id", snapshot_id),
        ("snapshot_mode", snapshot_mode),
        ("snapshot_season", snapshot_season),
        ("snapshot_version", snapshot_version),
        ("snapshot_revision", snapshot_revision),
        ("snapshot_content_hash", snapshot_content_hash),
    ):
        if value is not None:
            values[name] = value
    result = db.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status.in_(from_statuses))
        .values(**values)
    )
    if result.rowcount:
        db.refresh(run)
        return True
    return False


def set_run_cancel_requested(db: Session, run: AgentRun) -> bool:
    result = db.execute(
        update(AgentRun)
        .where(AgentRun.id == run.id, AgentRun.status.in_(("queued", "running", "cancelling")))
        .values(status="cancelling", cancel_requested=True)
    )
    if result.rowcount:
        db.refresh(run)
        return True
    return False


def update_message_status(
    db: Session,
    message: AgentMessage,
    *,
    from_statuses: tuple[str, ...],
    status: str,
    content: str | None = None,
    error_code: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> bool:
    values: dict[str, object] = {"status": status}
    if content is not None:
        values["content"] = content
    if error_code is not None:
        values["error_code"] = error_code
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    result = db.execute(
        update(AgentMessage)
        .where(AgentMessage.id == message.id, AgentMessage.status.in_(from_statuses))
        .values(**values)
    )
    if result.rowcount:
        db.refresh(message)
        return True
    return False


def create_assistant_message(
    db: Session,
    *,
    conversation: AgentConversation,
    content: str,
    strategy_mode: str,
    status: str,
) -> AgentMessage:
    next_sequence = (
        db.scalar(
            select(func.coalesce(func.max(AgentMessage.sequence), 0) + 1).where(
                AgentMessage.conversation_id == conversation.id
            )
        )
        or 1
    )
    message = AgentMessage(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        role="assistant",
        content=content or " ",
        status=status,
        sequence=next_sequence,
        strategy_mode=strategy_mode,
        started_at=_now(),
        completed_at=_now(),
    )
    db.add(message)
    db.flush()
    return message


def list_messages(db: Session, *, conversation_id: str, limit: int, offset: int) -> Page:
    base = select(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = list(
        db.scalars(
            base.order_by(AgentMessage.sequence.desc(), AgentMessage.id.desc()).limit(limit).offset(offset)
        )
    )
    return Page(items=items, total=total)


def list_recent_messages(db: Session, *, conversation_id: str, limit: int) -> tuple[AgentMessage, ...]:
    rows = tuple(
        db.scalars(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation_id)
            .order_by(AgentMessage.sequence.desc(), AgentMessage.id.desc())
            .limit(limit)
        )
    )
    return tuple(reversed(rows))

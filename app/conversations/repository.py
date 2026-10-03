"""Persistence operations for user-owned conversations, messages, and runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.agent.models import AgentRun
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
            base.order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
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
            base.order_by(AgentMessage.sequence.asc(), AgentMessage.id.asc()).limit(limit).offset(offset)
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

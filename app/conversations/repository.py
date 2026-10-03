"""Persistence operations for user-owned conversations and messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

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
            base.order_by(
                AgentConversation.updated_at.desc(),
                AgentConversation.id.desc(),
            )
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


def list_messages(db: Session, *, conversation_id: str, limit: int, offset: int) -> Page:
    base = select(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = list(db.scalars(base.order_by(AgentMessage.sequence.asc(), AgentMessage.id.asc()).limit(limit).offset(offset)))
    return Page(items=items, total=total)

"""ORM models for user-owned AI agent conversations and messages."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        CheckConstraint("strategy_mode IN ('gamble', 'operation')", name="ck_agent_conversations_strategy_mode"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_agent_conversations_status"),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR (status = 'archived' AND archived_at IS NOT NULL)",
            name="ck_agent_conversations_archive_consistency",
        ),
        Index("ix_agent_conversations_user_updated", "user_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="gamble")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool', 'system')", name="ck_agent_messages_role"),
        CheckConstraint(
            "status IN ('queued', 'running', 'streaming', 'completed', 'failed', 'cancelling', 'cancelled')",
            name="ck_agent_messages_status",
        ),
        CheckConstraint("strategy_mode IN ('gamble', 'operation')", name="ck_agent_messages_strategy_mode"),
        CheckConstraint("sequence >= 1", name="ck_agent_messages_sequence_positive"),
        CheckConstraint("length(content) BETWEEN 1 AND 8000", name="ck_agent_messages_content_length"),
        UniqueConstraint("conversation_id", "sequence", name="uq_agent_messages_conversation_sequence"),
        UniqueConstraint("conversation_id", "client_request_id", name="uq_agent_messages_conversation_request"),
        Index("ix_agent_messages_conversation_created", "conversation_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    client_request_id: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

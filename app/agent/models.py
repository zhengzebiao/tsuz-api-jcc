"""Persistent records for one Agent execution."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelling', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        UniqueConstraint("message_id", name="uq_agent_runs_message_id"),
        Index("ix_agent_runs_conversation_status", "conversation_id", "status"),
        Index("ix_agent_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_messages.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(255))
    cancel_requested: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    output_content: Mapped[str] = mapped_column(String(), nullable=False, default="")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    snapshot_id: Mapped[int | None] = mapped_column(Integer)
    snapshot_mode: Mapped[str | None] = mapped_column(String(32))
    snapshot_season: Mapped[str | None] = mapped_column(String(32))
    snapshot_version: Mapped[str | None] = mapped_column(String(64))
    snapshot_revision: Mapped[int | None] = mapped_column(Integer)
    snapshot_content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'running', 'succeeded', 'failed', 'timeout', 'rejected', 'cancelled')",
            name="ck_agent_tool_calls_status",
        ),
        UniqueConstraint("run_id", "tool_use_id", name="uq_agent_tool_calls_run_tool_use"),
        Index("ix_agent_tool_calls_run_created", "run_id", "created_at"),
        Index("ix_agent_tool_calls_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_messages.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    tool_use_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    error_code: Mapped[str | None] = mapped_column(String(64))
    snapshot_id: Mapped[int | None] = mapped_column(Integer)
    snapshot_version: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AgentMessageSource(Base):
    __tablename__ = "agent_message_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('official_structured_data', 'system_derived', 'model_explanation')",
            name="ck_agent_message_sources_type",
        ),
        Index("ix_agent_message_sources_message_rank", "message_id", "rank"),
        Index("ix_agent_message_sources_run", "run_id"),
        Index("ix_agent_message_sources_snapshot", "snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_messages.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_tool_calls.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[str | None] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(255))
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column()
    excerpt: Mapped[str | None] = mapped_column(String(1000))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

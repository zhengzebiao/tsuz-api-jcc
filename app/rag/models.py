"""Persistence models for versioned RAG index generations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.core.database import Base


class Vector1024(UserDefinedType):
    """PostgreSQL vector(1024), importable by SQLite unit tests."""

    cache_ok = True


class SearchVector(UserDefinedType):
    """PostgreSQL tsvector, represented as text by SQLite tests."""

    cache_ok = True


@compiles(SearchVector, "postgresql")
def _compile_search_vector(element, compiler, **kwargs) -> str:
    return "TSVECTOR"


@compiles(SearchVector)
def _compile_search_vector_fallback(element, compiler, **kwargs) -> str:
    return "TEXT"


@compiles(Vector1024, "postgresql")
def _compile_vector(element, compiler, **kwargs) -> str:
    return "VECTOR(1024)"


@compiles(Vector1024)
def _compile_vector_fallback(element, compiler, **kwargs) -> str:
    return "TEXT"



def _now() -> datetime:
    return datetime.now(UTC)


class RagIndexRun(Base):
    __tablename__ = "rag_index_runs"
    __table_args__ = (
        CheckConstraint("status IN ('building', 'active', 'failed', 'retired')", name="ck_rag_index_runs_status"),
        Index("ix_rag_index_runs_mode_status", "mode", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("jcc_snapshots.id"), nullable=False)
    snapshot_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RagCurrentIndex(Base):
    __tablename__ = "rag_current_indexes"
    mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    index_id: Mapped[str] = mapped_column(String(36), ForeignKey("rag_index_runs.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class RagDocument(Base):
    __tablename__ = "rag_documents"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('hero', 'trait', 'equipment', 'augment', 'adventure', 'galaxy')",
            name="ck_rag_documents_entity_type",
        ),
        CheckConstraint("status IN ('pending', 'embedded', 'failed', 'retired')", name="ck_rag_documents_status"),
        UniqueConstraint("index_id", "document_id", name="uq_rag_documents_index_document"),
        Index("ix_rag_documents_index_snapshot", "index_id", "snapshot_id"),
        Index("ix_rag_documents_index_entity", "index_id", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    index_id: Mapped[str] = mapped_column(String(36), ForeignKey("rag_index_runs.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(255))
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    section: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("jcc_snapshots.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    season: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(String(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector: Mapped[str | None] = mapped_column(SearchVector())
    embedding: Mapped[list[float] | None] = mapped_column(Vector1024())
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    source_file: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


__all__ = ["RagCurrentIndex", "RagDocument", "RagIndexRun", "Vector1024"]

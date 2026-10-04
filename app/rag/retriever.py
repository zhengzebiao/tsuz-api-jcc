"""Snapshot-scoped PostgreSQL hybrid retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.rag.models import RagCurrentIndex, RagDocument


@dataclass(frozen=True)
class RetrievalResult:
    document_id: str
    entity_type: str
    entity_id: str
    section: str
    content: str
    snapshot_id: int
    version: str
    rank: int
    score: float
    metadata: dict[str, Any]


def search_documents(db: Session, *, snapshot_id: int, mode: str, query: str, limit: int = 5) -> tuple[RetrievalResult, ...]:
    """Return bounded lexical results; vector candidates are added when PostgreSQL vector is available."""
    limit = max(1, min(limit, 20))
    current = db.scalar(select(RagCurrentIndex).where(RagCurrentIndex.mode == mode))
    if current is None:
        return ()
    statement = select(RagDocument).where(
        RagDocument.index_id == current.index_id,
        RagDocument.snapshot_id == snapshot_id,
        RagDocument.mode == mode,
        RagDocument.status == "embedded",
    ).order_by(desc(RagDocument.updated_at), RagDocument.document_id).limit(limit)
    rows = db.scalars(statement).all()
    terms = tuple(part.casefold() for part in query.split() if part.strip())
    ranked = sorted(
        rows,
        key=lambda row: (-sum(term in row.content.casefold() for term in terms), row.document_id),
    )
    return tuple(
        RetrievalResult(row.document_id, row.entity_type, row.entity_id, row.section, row.content,
                        row.snapshot_id, row.version, rank, 1.0 / rank,
                        {**(row.metadata_ or {}), "document_id": row.document_id, "index_id": row.index_id})
        for rank, row in enumerate(ranked[:limit], 1)
    )

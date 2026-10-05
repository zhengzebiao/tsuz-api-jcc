"""Incremental, generation-based RAG indexing."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.rag.document_builder import NormalizedDocument, build_snapshot_documents
from app.rag.embedding import EmbeddingClient, EmbeddingError, validate_embeddings


def _embed_with_retry(embedding, texts, *, retries: int = 3, backoff_seconds: float = 2.0):
    last_error = None
    for attempt in range(retries + 1):
        try:
            return validate_embeddings(embedding.embed(texts), expected_count=len(texts), dimension=embedding.dimension)
        except EmbeddingError as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(backoff_seconds * (attempt + 1))
    raise last_error
from app.rag.models import RagCurrentIndex, RagDocument, RagIndexRun


def index_snapshot(db: Session, snapshot, embedding: EmbeddingClient, *, batch_size: int = 32, progress_callback=None) -> dict[str, int | str]:
    """Build and publish one generation; the current pointer changes only after success."""
    documents = build_snapshot_documents(db, snapshot)
    run = RagIndexRun(mode=snapshot.mode, snapshot_id=snapshot.id, snapshot_content_hash=snapshot.content_hash,
                      embedding_model=embedding.model_name, embedding_dimension=embedding.dimension, document_count=len(documents))
    db.add(run)
    db.flush()
    try:
        previous = db.scalar(select(RagCurrentIndex).where(RagCurrentIndex.mode == snapshot.mode))
        previous_id = previous.index_id if previous else None
        reusable: dict[tuple[str, str], RagDocument] = {}
        if previous_id:
            for row in db.scalars(select(RagDocument).where(RagDocument.index_id == previous_id, RagDocument.status == "embedded")):
                reusable[(row.document_id, row.content_hash)] = row
        pending: list[RagDocument] = []
        for item in documents:
            old = reusable.get((item.document_id, item.content_hash))
            row = RagDocument(index_id=run.id, document_id=item.document_id, entity_type=item.entity_type,
                              entity_id=item.entity_id, section=item.section, snapshot_id=item.snapshot_id,
                              mode=item.mode, season=item.season, version=item.version, revision=item.revision,
                              content=item.content, content_hash=item.content_hash, search_vector=func.to_tsvector("simple", item.content),
                              embedding=(old.embedding if old and old.embedding_model == embedding.model_name and old.embedding_dimension == embedding.dimension else None),
                              embedding_model=(embedding.model_name if old and old.embedding is not None and old.embedding_model == embedding.model_name and old.embedding_dimension == embedding.dimension else None),
                              embedding_dimension=(embedding.dimension if old and old.embedding is not None and old.embedding_model == embedding.model_name and old.embedding_dimension == embedding.dimension else None),
                              source_file=item.metadata.get("source_file"), metadata_=item.metadata,
                              status="embedded" if old and old.embedding is not None and old.embedding_model == embedding.model_name and old.embedding_dimension == embedding.dimension else "pending")
            db.add(row)
            if row.status != "embedded":
                pending.append(row)
        db.flush()
        embedded = len(documents) - len(pending)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            try:
                vectors = _embed_with_retry(embedding, [row.content for row in batch])
                completed = list(zip(batch, vectors, strict=True))
            except EmbeddingError:
                completed = []
                for row in batch:
                    try:
                        vector = _embed_with_retry(embedding, [row.content])[0]
                    except EmbeddingError as document_error:
                        if progress_callback:
                            progress_callback(row, embedded, len(documents), document_error)
                        continue
                    completed.append((row, vector))
            for row, vector in completed:
                row.embedding, row.embedding_model, row.embedding_dimension, row.status = vector, embedding.model_name, embedding.dimension, "embedded"
                embedded += 1
                if progress_callback:
                    progress_callback(row, embedded, len(documents), None)
            db.flush()
        if embedded != len(documents):
            raise RuntimeError("rag_index_incomplete")
        run.embedded_count = embedded
        run.status = "active"
        run.completed_at = datetime.now(UTC)
        if previous is not None:
            old_run = db.get(RagIndexRun, previous.index_id)
            if old_run is not None:
                old_run.status = "retired"
            previous.index_id = run.id
            previous.updated_at = datetime.now(UTC)
        else:
            db.add(RagCurrentIndex(mode=snapshot.mode, index_id=run.id))
        db.commit()
        return {"index_id": run.id, "document_count": len(documents), "reused_count": len(documents) - len(pending), "embedded_count": embedded, "status": "active"}
    except Exception as exc:
        db.rollback()
        run = db.get(RagIndexRun, run.id)
        if run is not None:
            run.status, run.error_code = "failed", str(exc)[:64]
            db.commit()
        raise


def index_documents(db: Session, snapshot, documents: Sequence[NormalizedDocument], embedding: EmbeddingClient) -> dict[str, int | str]:
    """Small injectable entry point used by tests and offline callers."""
    return index_snapshot(db, snapshot, embedding)


if __name__ == "__main__":
    raise SystemExit("Use scripts/index_rag.py with an explicitly configured embedding provider")

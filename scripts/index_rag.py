"""Run the offline RAG indexer with an explicitly selected local provider."""

from __future__ import annotations

import argparse

from app.core.config import settings
from app.core.database import SessionLocal
from app.jcc_data.repository import get_current_snapshot
from app.rag.embedding import DeterministicEmbeddingClient
from app.rag.indexer import index_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the snapshot-scoped RAG index")
    parser.add_argument("--mode", default=settings.jcc_data_mode)
    parser.add_argument("--batch-size", type=int, default=settings.rag_embedding_batch_size)
    parser.add_argument("--provider", choices=("fake",), default=settings.rag_embedding_provider)
    args = parser.parse_args()
    if args.provider != "fake":
        raise SystemExit("no production embedding provider is configured")
    db = SessionLocal()
    try:
        snapshot = get_current_snapshot(db, args.mode)
        if snapshot is None:
            raise SystemExit("no current snapshot")
        result = index_snapshot(db, snapshot, DeterministicEmbeddingClient(settings.rag_embedding_dimension), batch_size=args.batch_size)
        print({key: result[key] for key in ("index_id", "document_count", "reused_count", "embedded_count", "status")})
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

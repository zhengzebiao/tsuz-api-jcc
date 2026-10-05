"""Run the offline RAG indexer with an explicitly selected local provider."""

from __future__ import annotations

import argparse

from app.core.config import settings
from app.core.database import SessionLocal
from app.jcc_data.repository import get_current_snapshot
from app.rag.embedding import DeterministicEmbeddingClient, EmbeddingClient, OpenAICompatibleEmbeddingClient
from app.rag.indexer import index_snapshot


def build_embedding_client(provider: str | None = None) -> EmbeddingClient:
    selected = provider or settings.rag_embedding_provider
    if selected == "fake":
        return DeterministicEmbeddingClient(settings.rag_embedding_dimension)
    if selected == "openai_compatible":
        return OpenAICompatibleEmbeddingClient(
            model_name=settings.rag_embedding_model,
            base_url=settings.rag_embedding_base_url,
            api_key=settings.rag_embedding_api_key,
            dimension=settings.rag_embedding_dimension,
            timeout_seconds=settings.rag_embedding_timeout_seconds,
        )
    raise SystemExit(f"unsupported embedding provider: {selected}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the snapshot-scoped RAG index")
    parser.add_argument("--mode", default=settings.jcc_data_mode)
    parser.add_argument("--batch-size", type=int, default=settings.rag_embedding_batch_size)
    parser.add_argument("--provider", choices=("fake", "openai_compatible"), default=settings.rag_embedding_provider)
    args = parser.parse_args()
    embedding = build_embedding_client(args.provider)
    db = SessionLocal()
    try:
        snapshot = get_current_snapshot(db, args.mode)
        if snapshot is None:
            raise SystemExit("no current snapshot")
        result = index_snapshot(db, snapshot, embedding, batch_size=args.batch_size)
        print({key: result[key] for key in ("index_id", "document_count", "reused_count", "embedded_count", "status")})
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

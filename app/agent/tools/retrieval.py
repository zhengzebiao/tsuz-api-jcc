"""RAG retrieval tool using the run-pinned snapshot."""

from __future__ import annotations

from app.agent.tools.schemas import SearchKnowledgeInput, SourceRecord, ToolContext, ToolExecutionResult
from app.rag.retriever import search_documents


def search_knowledge(context: ToolContext, input: SearchKnowledgeInput) -> ToolExecutionResult:
    db = context.session_factory()
    try:
        results = search_documents(db, snapshot_id=context.snapshot.snapshot_id, mode=context.snapshot.mode, query=input.query, limit=input.limit)
    finally:
        db.close()
    sources = tuple(
        SourceRecord(
            source_type="rag_document", snapshot_id=item.snapshot_id, version=item.version,
            entity_type=item.entity_type, entity_id=item.entity_id, rank=item.rank,
            excerpt=item.content[:1000], metadata={**item.metadata, "score": item.score, "section": item.section},
        )
        for item in results
    )
    return ToolExecutionResult(
        {"items": [{"document_id": item.document_id, "entity_type": item.entity_type, "entity_id": item.entity_id,
                    "section": item.section, "content": item.content[:4000], "version": item.version,
                    "rank": item.rank, "score": item.score} for item in results],
         "snapshot_id": context.snapshot.snapshot_id, "version": context.snapshot.version,
         "source_type": "rag_document", "has_more": False},
        sources,
    )

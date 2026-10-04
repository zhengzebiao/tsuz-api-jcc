"""add generation-based RAG documents and indexes

Revision ID: 0006_rag_documents_and_indexes
Revises: 0005_agent_tools_and_sources
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.rag.models import Vector1024

revision: str = "0006_rag_documents_and_indexes"
down_revision: str | None = "0005_agent_tools_and_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "rag_index_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("embedded_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('building', 'active', 'failed', 'retired')", name="ck_rag_index_runs_status"),
    )
    op.create_index("ix_rag_index_runs_mode_status", "rag_index_runs", ["mode", "status"])
    op.create_table(
        "rag_current_indexes",
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("index_id", sa.String(36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["index_id"], ["rag_index_runs.id"]),
        sa.PrimaryKeyConstraint("mode"),
    )
    op.create_table(
        "rag_documents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("index_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(255), nullable=False),
        sa.Column("parent_id", sa.String(255)),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("section", sa.String(64), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("season", sa.String(32), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR()),
        sa.Column("embedding", Vector1024()),
        sa.Column("embedding_model", sa.String(128)),
        sa.Column("embedding_dimension", sa.Integer()),
        sa.Column("source_file", sa.String(255)),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("metadata", sa.JSON()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["index_id"], ["rag_index_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("index_id", "document_id", name="uq_rag_documents_index_document"),
        sa.CheckConstraint("entity_type IN ('hero', 'trait', 'equipment', 'augment', 'adventure', 'galaxy')", name="ck_rag_documents_entity_type"),
        sa.CheckConstraint("status IN ('pending', 'embedded', 'failed', 'retired')", name="ck_rag_documents_status"),
    )
    op.create_index("ix_rag_documents_index_snapshot", "rag_documents", ["index_id", "snapshot_id"])
    op.create_index("ix_rag_documents_index_entity", "rag_documents", ["index_id", "entity_type", "entity_id"])
    op.execute("ALTER TABLE agent_message_sources DROP CONSTRAINT IF EXISTS ck_agent_message_sources_type")
    op.create_check_constraint(
        "ck_agent_message_sources_type", "agent_message_sources",
        "source_type IN ('official_structured_data', 'system_derived', 'model_explanation', 'rag_document')",
    )
    op.execute("CREATE INDEX ix_rag_documents_search_vector ON rag_documents USING gin (to_tsvector('simple', content))")
    op.execute("CREATE INDEX ix_rag_documents_embedding ON rag_documents USING hnsw ((embedding::vector(1024)) vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_documents_embedding")
    op.execute("DROP INDEX IF EXISTS ix_rag_documents_search_vector")
    op.drop_index("ix_rag_documents_index_entity", table_name="rag_documents")
    op.drop_index("ix_rag_documents_index_snapshot", table_name="rag_documents")
    op.drop_table("rag_documents")
    op.drop_table("rag_current_indexes")
    op.drop_index("ix_rag_index_runs_mode_status", table_name="rag_index_runs")
    op.drop_table("rag_index_runs")
    op.drop_constraint("ck_agent_message_sources_type", "agent_message_sources", type_="check")
    op.create_check_constraint(
        "ck_agent_message_sources_type", "agent_message_sources",
        "source_type IN ('official_structured_data', 'system_derived', 'model_explanation')",
    )

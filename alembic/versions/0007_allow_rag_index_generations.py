"""allow repeated RAG index generations

Revision ID: 0007_allow_rag_index_generations
Revises: 0006_rag_documents_and_indexes
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_allow_rag_index_generations"
down_revision: str | None = "0006_rag_documents_and_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE rag_index_runs
        DROP CONSTRAINT IF EXISTS uq_rag_index_runs_key
        """
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_rag_index_runs_key",
        "rag_index_runs",
        ["mode", "snapshot_id", "embedding_model", "embedding_dimension"],
    )

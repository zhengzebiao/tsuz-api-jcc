"""add phase five run cost fields

Revision ID: 0008_phase5_quality_runtime
Revises: 0007_allow_rag_index_generations
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_phase5_quality_runtime"
down_revision: str | None = "0007_allow_rag_index_generations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("estimated_cost", sa.Numeric(20, 10), nullable=True))
    op.add_column("agent_runs", sa.Column("pricing_key", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "pricing_key")
    op.drop_column("agent_runs", "estimated_cost")

"""initial app schema

Revision ID: 0001_initial_app_schema
Revises:
Create Date: 2026-07-02
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_app_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index("ix_app_settings_id", "app_settings", ["id"])
    op.create_index("ix_app_settings_key", "app_settings", ["key"], unique=True)

    op.create_table(
        "sample_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_sample_profiles_id", "sample_profiles", ["id"])
    op.create_index("ix_sample_profiles_slug", "sample_profiles", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sample_profiles_slug", table_name="sample_profiles")
    op.drop_index("ix_sample_profiles_id", table_name="sample_profiles")
    op.drop_table("sample_profiles")
    op.drop_index("ix_app_settings_key", table_name="app_settings")
    op.drop_index("ix_app_settings_id", table_name="app_settings")
    op.drop_table("app_settings")

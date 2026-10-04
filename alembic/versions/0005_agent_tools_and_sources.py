"""add agent tool calls and structured sources

Revision ID: 0005_agent_tools_and_sources
Revises: 0004_agent_runs
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_agent_tools_and_sources"
down_revision: str | None = "0004_agent_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_mode", sa.String(length=32), nullable=True),
        sa.Column("snapshot_season", sa.String(length=32), nullable=True),
        sa.Column("snapshot_version", sa.String(length=64), nullable=True),
        sa.Column("snapshot_revision", sa.Integer(), nullable=True),
        sa.Column("snapshot_content_hash", sa.String(length=64), nullable=True),
    ):
        op.add_column("agent_runs", column)

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("tool_use_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="requested"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_version", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('requested', 'running', 'succeeded', 'failed', 'timeout', 'rejected', 'cancelled')",
            name="ck_agent_tool_calls_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["agent_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tool_use_id", name="uq_agent_tool_calls_run_tool_use"),
    )
    op.create_index("ix_agent_tool_calls_run_created", "agent_tool_calls", ["run_id", "created_at"])
    op.create_index("ix_agent_tool_calls_status_created", "agent_tool_calls", ["status", "created_at"])

    op.create_table(
        "agent_message_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("tool_call_id", sa.String(length=36), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("excerpt", sa.String(length=1000), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('official_structured_data', 'system_derived', 'model_explanation')",
            name="ck_agent_message_sources_type",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["agent_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_call_id"], ["agent_tool_calls.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_message_sources_message_rank", "agent_message_sources", ["message_id", "rank"])
    op.create_index("ix_agent_message_sources_run", "agent_message_sources", ["run_id"])
    op.create_index("ix_agent_message_sources_snapshot", "agent_message_sources", ["snapshot_id"])


def downgrade() -> None:
    for column_name in (
        "snapshot_content_hash",
        "snapshot_revision",
        "snapshot_version",
        "snapshot_season",
        "snapshot_mode",
        "snapshot_id",
    ):
        op.drop_column("agent_runs", column_name)

    op.drop_index("ix_agent_message_sources_snapshot", table_name="agent_message_sources")
    op.drop_index("ix_agent_message_sources_run", table_name="agent_message_sources")
    op.drop_index("ix_agent_message_sources_message_rank", table_name="agent_message_sources")
    op.drop_table("agent_message_sources")
    op.drop_index("ix_agent_tool_calls_status_created", table_name="agent_tool_calls")
    op.drop_index("ix_agent_tool_calls_run_created", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")

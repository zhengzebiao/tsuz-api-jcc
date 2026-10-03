"""add agent conversations and messages

Revision ID: 0003_agent_conversations
Revises: 0002_jcc_structured_data
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_agent_conversations"
down_revision: str | None = "0002_jcc_structured_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("strategy_mode", sa.String(length=16), nullable=False, server_default="gamble"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "strategy_mode IN ('gamble', 'operation')",
            name="ck_agent_conversations_strategy_mode",
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_agent_conversations_status"),
        sa.CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR (status = 'archived' AND archived_at IS NOT NULL)",
            name="ck_agent_conversations_archive_consistency",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_conversations_user_id", "agent_conversations", ["user_id"])
    op.create_index(
        "ix_agent_conversations_user_updated",
        "agent_conversations",
        ["user_id", "updated_at", "id"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("strategy_mode", sa.String(length=16), nullable=False),
        sa.Column("client_request_id", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system')",
            name="ck_agent_messages_role",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'streaming', 'completed', 'failed', 'cancelling', 'cancelled')",
            name="ck_agent_messages_status",
        ),
        sa.CheckConstraint(
            "strategy_mode IN ('gamble', 'operation')",
            name="ck_agent_messages_strategy_mode",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_messages_sequence_positive"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 8000", name="ck_agent_messages_content_length"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name="fk_agent_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_agent_messages_conversation_sequence",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "client_request_id",
            name="uq_agent_messages_conversation_request",
        ),
    )
    op.create_index("ix_agent_messages_conversation_id", "agent_messages", ["conversation_id"])
    op.create_index("ix_agent_messages_user_id", "agent_messages", ["user_id"])
    op.create_index(
        "ix_agent_messages_conversation_created",
        "agent_messages",
        ["conversation_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_messages_conversation_created", table_name="agent_messages")
    op.drop_index("ix_agent_messages_user_id", table_name="agent_messages")
    op.drop_index("ix_agent_messages_conversation_id", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_conversations_user_updated", table_name="agent_conversations")
    op.drop_index("ix_agent_conversations_user_id", table_name="agent_conversations")
    op.drop_table("agent_conversations")

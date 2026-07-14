"""add persistent ai setup chat

Revision ID: 7a8b9c0d1e2f
Revises: 6f7a8b9c0d1e
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7a8b9c0d1e2f"
down_revision: str | None = "6f7a8b9c0d1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_setup_chat_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("original_idea", sa.Text(), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("draft_schema_json", sa.JSON(), nullable=True),
        sa.Column("translation_sheet", sa.JSON(), nullable=False),
        sa.Column("lint_warnings", sa.JSON(), nullable=False),
        sa.Column("rule_confidence", sa.JSON(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("ambiguities", sa.JSON(), nullable=False),
        sa.Column("unsupported_conditions", sa.JSON(), nullable=False),
        sa.Column("approved_strategy_id", sa.Uuid(), nullable=True),
        sa.Column("approved_strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["approved_strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["approved_strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_setup_chat_sessions_user_id",
        "ai_setup_chat_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_setup_chat_user_status_updated",
        "ai_setup_chat_sessions",
        ["user_id", "status", "updated_at"],
        unique=False,
    )
    op.create_table(
        "ai_setup_chat_messages",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("message_type", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ai_setup_chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_ai_setup_chat_message_sequence"),
    )
    op.create_index(
        "ix_ai_setup_chat_message_session_created",
        "ai_setup_chat_messages",
        ["session_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_setup_chat_message_session_created", table_name="ai_setup_chat_messages"
    )
    op.drop_table("ai_setup_chat_messages")
    op.drop_index(
        "ix_ai_setup_chat_user_status_updated", table_name="ai_setup_chat_sessions"
    )
    op.drop_index("ix_ai_setup_chat_sessions_user_id", table_name="ai_setup_chat_sessions")
    op.drop_table("ai_setup_chat_sessions")

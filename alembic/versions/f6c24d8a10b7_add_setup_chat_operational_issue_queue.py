"""Add the deduplicated Setup Chat operational issue queue.

Revision ID: f6c24d8a10b7
Revises: e5b13c7a29d4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6c24d8a10b7"
down_revision: str | None = "e5b13c7a29d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setup_chat_operational_issues",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("chat_session_id", sa.Uuid(), nullable=True),
        sa.Column("setup_chat_turn_id", sa.Uuid(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("issue_kind", sa.String(length=48), nullable=False),
        sa.Column("failure_class", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("semantic_paths", sa.JSON(), nullable=False),
        sa.Column("safe_source_excerpt", sa.Text(), nullable=False),
        sa.Column("support_reference", sa.String(length=64), nullable=False),
        sa.Column("failure_proof", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["ai_setup_chat_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["setup_chat_turn_id"], ["setup_chat_turns.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_setup_chat_operational_issue_fingerprint"),
    )
    op.create_index(
        "ix_setup_chat_operational_issue_status_seen",
        "setup_chat_operational_issues",
        ["status", "last_seen_at"],
    )
    op.create_index(
        "ix_setup_chat_operational_issue_chat",
        "setup_chat_operational_issues",
        ["chat_session_id", "last_seen_at"],
    )
    op.create_index(
        "ix_setup_chat_operational_issues_user_id",
        "setup_chat_operational_issues",
        ["user_id"],
    )
    op.create_index(
        "ix_setup_chat_operational_issues_chat_session_id",
        "setup_chat_operational_issues",
        ["chat_session_id"],
    )
    op.create_index(
        "ix_setup_chat_operational_issues_setup_chat_turn_id",
        "setup_chat_operational_issues",
        ["setup_chat_turn_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_setup_chat_operational_issues_setup_chat_turn_id",
        table_name="setup_chat_operational_issues",
    )
    op.drop_index(
        "ix_setup_chat_operational_issues_chat_session_id",
        table_name="setup_chat_operational_issues",
    )
    op.drop_index(
        "ix_setup_chat_operational_issues_user_id",
        table_name="setup_chat_operational_issues",
    )
    op.drop_index(
        "ix_setup_chat_operational_issue_chat",
        table_name="setup_chat_operational_issues",
    )
    op.drop_index(
        "ix_setup_chat_operational_issue_status_seen",
        table_name="setup_chat_operational_issues",
    )
    op.drop_table("setup_chat_operational_issues")

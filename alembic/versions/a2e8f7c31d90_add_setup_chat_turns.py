"""Add durable authenticated Setup Chat turn state.

Revision ID: a2e8f7c31d90
Revises: 91d7c4a2b6e8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a2e8f7c31d90"
down_revision: str | None = "91d7c4a2b6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setup_chat_turns",
        sa.Column("chat_session_id", sa.Uuid(), nullable=False),
        sa.Column("client_message_id", sa.String(length=80), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("assistant_message_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("planner_model", sa.String(length=120), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("execution_result_json", sa.JSON(), nullable=True),
        sa.Column("reply_json", sa.JSON(), nullable=True),
        sa.Column(
            "mutation_committed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("executable_version_before", sa.Integer(), nullable=True),
        sa.Column("executable_version_after", sa.Integer(), nullable=True),
        sa.Column("workflow_revision_before", sa.Integer(), nullable=True),
        sa.Column("workflow_revision_after", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("failure_stage", sa.String(length=80), nullable=True),
        sa.Column("failure_retryable", sa.Boolean(), nullable=True),
        sa.Column("failure_details_json", sa.JSON(), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["ai_setup_chat_messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["ai_setup_chat_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["ai_setup_chat_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "client_message_id",
            name="uq_setup_chat_turn_session_client_message",
        ),
    )
    op.create_index(
        "ix_setup_chat_turn_session_status",
        "setup_chat_turns",
        ["chat_session_id", "status"],
        unique=False,
    )
    op.create_table(
        "setup_chat_draft_snapshots",
        sa.Column("chat_session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_turn_id", sa.Uuid(), nullable=True),
        sa.Column("executable_version", sa.Integer(), nullable=False),
        sa.Column("executable_hash", sa.String(length=64), nullable=False),
        sa.Column("draft_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["ai_setup_chat_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_turn_id"],
            ["ai_setup_chat_messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_session_id",
            "executable_version",
            "executable_hash",
            name="uq_setup_chat_snapshot_identity",
        ),
    )
    op.create_index(
        "ix_setup_chat_snapshot_owner_version",
        "setup_chat_draft_snapshots",
        ["user_id", "chat_session_id", "executable_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_setup_chat_snapshot_owner_version",
        table_name="setup_chat_draft_snapshots",
    )
    op.drop_table("setup_chat_draft_snapshots")
    op.drop_index("ix_setup_chat_turn_session_status", table_name="setup_chat_turns")
    op.drop_table("setup_chat_turns")

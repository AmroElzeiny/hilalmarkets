"""Storage for Hilal, the assistant inside the dashboard.

Four tables: one conversation per person that never ends, the messages in it, reports
about a particular answer, and the star rating asked for when the window is closed.

The conversation is keyed by user id and unique on it, because "history from all the
sessions" only means anything if there is exactly one place it accumulates.

Revision ID: a7d2e94c1b60
Revises: 9d21c4e75f80
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7d2e94c1b60"
down_revision: str | None = "9d21c4e75f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hilal_chat_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("message_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_sequence", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_hilal_chat_conversation_user"),
    )
    op.create_index(
        "ix_hilal_chat_conversation_updated",
        "hilal_chat_conversations",
        ["updated_at"],
    )

    op.create_table(
        "hilal_chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), server_default="ANSWER", nullable=False),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("page", sa.String(length=120), nullable=True),
        sa.Column("client_message_id", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(12, 8),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["hilal_chat_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_hilal_chat_message_sequence"
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_hilal_chat_message_client_id",
        ),
    )
    op.create_index(
        "ix_hilal_chat_message_conversation_created",
        "hilal_chat_messages",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_hilal_chat_message_retain_until",
        "hilal_chat_messages",
        ["retain_until"],
    )

    op.create_table(
        "hilal_chat_message_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reported_content", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["hilal_chat_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["hilal_chat_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id", "user_id", name="uq_hilal_chat_report_message_user"
        ),
    )
    op.create_index(
        "ix_hilal_chat_message_reports_message_id",
        "hilal_chat_message_reports",
        ["message_id"],
    )
    op.create_index(
        "ix_hilal_chat_report_created",
        "hilal_chat_message_reports",
        ["created_at"],
    )

    op.create_table(
        "hilal_chat_ratings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("message_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["hilal_chat_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hilal_chat_ratings_conversation_id",
        "hilal_chat_ratings",
        ["conversation_id"],
    )
    op.create_index(
        "ix_hilal_chat_rating_created",
        "hilal_chat_ratings",
        ["created_at", "stars"],
    )


def downgrade() -> None:
    op.drop_index("ix_hilal_chat_rating_created", table_name="hilal_chat_ratings")
    op.drop_index("ix_hilal_chat_ratings_conversation_id", table_name="hilal_chat_ratings")
    op.drop_table("hilal_chat_ratings")
    op.drop_index(
        "ix_hilal_chat_report_created", table_name="hilal_chat_message_reports"
    )
    op.drop_index(
        "ix_hilal_chat_message_reports_message_id",
        table_name="hilal_chat_message_reports",
    )
    op.drop_table("hilal_chat_message_reports")
    op.drop_index("ix_hilal_chat_message_retain_until", table_name="hilal_chat_messages")
    op.drop_index(
        "ix_hilal_chat_message_conversation_created", table_name="hilal_chat_messages"
    )
    op.drop_table("hilal_chat_messages")
    op.drop_index(
        "ix_hilal_chat_conversation_updated", table_name="hilal_chat_conversations"
    )
    op.drop_table("hilal_chat_conversations")

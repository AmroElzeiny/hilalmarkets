"""add public chat modes and answer feedback

Revision ID: 3cedf4051627
Revises: 2bdce3f40516
Create Date: 2026-07-18 09:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3cedf4051627"
down_revision: str | None = "2bdce3f40516"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("public_chat_answer_events") as batch_op:
        batch_op.add_column(
            sa.Column(
                "mode",
                sa.String(length=48),
                server_default="PRODUCT_FACT",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("knowledge_gap_reason", sa.String(length=120)))
        batch_op.add_column(
            sa.Column("is_greeting", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "support_handoff_available",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("support_handoff_reason", sa.String(length=500)))

    with op.batch_alter_table("public_inquiries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "support_metadata",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )

    op.create_table(
        "public_chat_answer_feedback",
        sa.Column("answer_event_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("session_key_hash", sa.String(length=64), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column(
            "support_form_requested",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("mode", sa.String(length=48), nullable=False),
        sa.Column("intent", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("validation_failure", sa.String(length=120), nullable=True),
        sa.Column("knowledge_gap_reason", sa.String(length=120), nullable=True),
        sa.Column("inquiry_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["answer_event_id"],
            ["public_chat_answer_events.id"],
            name=op.f(
                "fk_public_chat_answer_feedback_answer_event_id_public_chat_answer_events"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["public_chat_conversations.id"],
            name=op.f(
                "fk_public_chat_answer_feedback_conversation_id_public_chat_conversations"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_public_chat_answer_feedback_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["inquiry_id"],
            ["public_inquiries.id"],
            name=op.f("fk_public_chat_answer_feedback_inquiry_id_public_inquiries"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_chat_answer_feedback")),
        sa.UniqueConstraint(
            "answer_event_id", name="uq_public_chat_feedback_answer_event"
        ),
        sa.UniqueConstraint("inquiry_id", name=op.f("uq_public_chat_answer_feedback_inquiry_id")),
    )
    op.create_index(
        "ix_public_chat_feedback_created",
        "public_chat_answer_feedback",
        ["created_at"],
    )
    op.create_index(
        "ix_public_chat_feedback_helpful_created",
        "public_chat_answer_feedback",
        ["helpful", "created_at"],
    )
    op.create_index(
        op.f("ix_public_chat_answer_feedback_answer_event_id"),
        "public_chat_answer_feedback",
        ["answer_event_id"],
    )
    op.create_index(
        op.f("ix_public_chat_answer_feedback_conversation_id"),
        "public_chat_answer_feedback",
        ["conversation_id"],
    )
    op.create_index(
        op.f("ix_public_chat_answer_feedback_user_id"),
        "public_chat_answer_feedback",
        ["user_id"],
    )
    op.create_index(
        op.f("ix_public_chat_answer_feedback_inquiry_id"),
        "public_chat_answer_feedback",
        ["inquiry_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_public_chat_answer_feedback_inquiry_id"),
        table_name="public_chat_answer_feedback",
    )
    op.drop_index(
        op.f("ix_public_chat_answer_feedback_user_id"),
        table_name="public_chat_answer_feedback",
    )
    op.drop_index(
        op.f("ix_public_chat_answer_feedback_conversation_id"),
        table_name="public_chat_answer_feedback",
    )
    op.drop_index(
        op.f("ix_public_chat_answer_feedback_answer_event_id"),
        table_name="public_chat_answer_feedback",
    )
    op.drop_index(
        "ix_public_chat_feedback_helpful_created",
        table_name="public_chat_answer_feedback",
    )
    op.drop_index(
        "ix_public_chat_feedback_created",
        table_name="public_chat_answer_feedback",
    )
    op.drop_table("public_chat_answer_feedback")

    with op.batch_alter_table("public_inquiries") as batch_op:
        batch_op.drop_column("support_metadata")

    with op.batch_alter_table("public_chat_answer_events") as batch_op:
        for column in (
            "support_handoff_reason",
            "support_handoff_available",
            "is_greeting",
            "knowledge_gap_reason",
            "mode",
        ):
            batch_op.drop_column(column)

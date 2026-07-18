"""add grounded public chat conversation state and AI audit fields

Revision ID: 2bdce3f40516
Revises: 1acbd2e3f405
Create Date: 2026-07-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2bdce3f40516"
down_revision: str | None = "1acbd2e3f405"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_chat_conversations",
        sa.Column("session_key_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=20), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_public_chat_conversations_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_chat_conversations")),
        sa.UniqueConstraint(
            "session_key_hash", name="uq_public_chat_conversation_session"
        ),
    )
    op.create_index(
        "ix_public_chat_conversation_expires",
        "public_chat_conversations",
        ["expires_at"],
    )
    op.create_index(
        "ix_public_chat_conversation_user_updated",
        "public_chat_conversations",
        ["user_id", "updated_at"],
    )
    op.create_table(
        "public_chat_turns",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("client_message_id", sa.String(length=120), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["public_chat_conversations.id"],
            name=op.f("fk_public_chat_turns_conversation_id_public_chat_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_chat_turns")),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_public_chat_turn_client_message",
        ),
    )
    op.create_index(
        "ix_public_chat_turn_conversation_created",
        "public_chat_turns",
        ["conversation_id", "created_at"],
    )

    with op.batch_alter_table("public_chat_answer_events") as batch_op:
        batch_op.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "stage",
                sa.String(length=48),
                server_default="ANSWER",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "intent",
                sa.String(length=100),
                server_default="product_help",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("model", sa.String(length=100)))
        batch_op.add_column(
            sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("reasoning_tokens", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "estimated_cost_usd",
                sa.Numeric(precision=12, scale=8),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("validation_failure", sa.String(length=120))
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_public_chat_answer_events_conversation_id_public_chat_conversations"
            ),
            "public_chat_conversations",
            ["conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            op.f("fk_public_chat_answer_events_user_id_users"),
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            op.f("ix_public_chat_answer_events_conversation_id"),
            ["conversation_id"],
        )
        batch_op.create_index(
            op.f("ix_public_chat_answer_events_user_id"),
            ["user_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_public_chat_turn_conversation_created",
        table_name="public_chat_turns",
    )
    op.drop_table("public_chat_turns")
    with op.batch_alter_table("public_chat_answer_events") as batch_op:
        batch_op.drop_index(op.f("ix_public_chat_answer_events_user_id"))
        batch_op.drop_index(op.f("ix_public_chat_answer_events_conversation_id"))
        batch_op.drop_constraint(
            op.f("fk_public_chat_answer_events_user_id_users"),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            op.f(
                "fk_public_chat_answer_events_conversation_id_public_chat_conversations"
            ),
            type_="foreignkey",
        )
        for column in (
            "validation_failure",
            "estimated_cost_usd",
            "latency_ms",
            "reasoning_tokens",
            "output_tokens",
            "input_tokens",
            "model",
            "intent",
            "stage",
            "user_id",
            "conversation_id",
        ):
            batch_op.drop_column(column)
    op.drop_index(
        "ix_public_chat_conversation_user_updated",
        table_name="public_chat_conversations",
    )
    op.drop_index(
        "ix_public_chat_conversation_expires",
        table_name="public_chat_conversations",
    )
    op.drop_table("public_chat_conversations")

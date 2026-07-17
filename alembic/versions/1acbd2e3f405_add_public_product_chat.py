"""add public product chat inquiry records

Revision ID: 1acbd2e3f405
Revises: 09bac1d2e3f4
Create Date: 2026-07-17 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1acbd2e3f405"
down_revision: str | None = "09bac1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_chat_answer_events",
        sa.Column("session_key_hash", sa.String(length=64), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("coverage_score", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("related_route_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_chat_answer_events")),
    )
    op.create_index(
        "ix_public_chat_answer_session_created",
        "public_chat_answer_events",
        ["session_key_hash", "created_at"],
    )
    op.create_index(
        "ix_public_chat_answer_outcome_created",
        "public_chat_answer_events",
        ["outcome", "created_at"],
    )

    op.create_table(
        "public_inquiries",
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("source_page", sa.String(length=240), nullable=False),
        sa.Column("referrer", sa.String(length=500)),
        sa.Column("attribution", sa.JSON(), nullable=False),
        sa.Column("knowledge_gap_category", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True)),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_inquiries")),
        sa.UniqueConstraint("idempotency_key", name="uq_public_inquiry_idempotency"),
        sa.UniqueConstraint("reference", name="uq_public_inquiry_reference"),
    )
    op.create_index(
        "ix_public_inquiry_status_created",
        "public_inquiries",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_public_inquiry_retain_until",
        "public_inquiries",
        ["retain_until"],
    )

    op.create_table(
        "public_inquiry_email_deliveries",
        sa.Column("inquiry_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("recipient_kind", sa.String(length=20), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255)),
        sa.Column("last_error", sa.String(length=500)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["inquiry_id"],
            ["public_inquiries.id"],
            name=op.f("fk_public_inquiry_email_deliveries_inquiry_id_public_inquiries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_inquiry_email_deliveries")),
        sa.UniqueConstraint("event_key", name="uq_public_inquiry_email_event_key"),
    )
    op.create_index(
        op.f("ix_public_inquiry_email_deliveries_inquiry_id"),
        "public_inquiry_email_deliveries",
        ["inquiry_id"],
    )
    op.create_index(
        "ix_public_inquiry_email_due",
        "public_inquiry_email_deliveries",
        ["status", "next_retry_at"],
    )

    op.create_table(
        "public_inquiry_ratings",
        sa.Column("inquiry_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer()),
        sa.Column("helpful", sa.Boolean()),
        sa.Column("feedback", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["inquiry_id"],
            ["public_inquiries.id"],
            name=op.f("fk_public_inquiry_ratings_inquiry_id_public_inquiries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_inquiry_ratings")),
        sa.UniqueConstraint("inquiry_id", name="uq_public_inquiry_rating_inquiry"),
    )
    op.create_index(
        op.f("ix_public_inquiry_ratings_inquiry_id"),
        "public_inquiry_ratings",
        ["inquiry_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_public_inquiry_ratings_inquiry_id"),
        table_name="public_inquiry_ratings",
    )
    op.drop_table("public_inquiry_ratings")
    op.drop_index(
        "ix_public_inquiry_email_due",
        table_name="public_inquiry_email_deliveries",
    )
    op.drop_index(
        op.f("ix_public_inquiry_email_deliveries_inquiry_id"),
        table_name="public_inquiry_email_deliveries",
    )
    op.drop_table("public_inquiry_email_deliveries")
    op.drop_index("ix_public_inquiry_retain_until", table_name="public_inquiries")
    op.drop_index("ix_public_inquiry_status_created", table_name="public_inquiries")
    op.drop_table("public_inquiries")
    op.drop_index(
        "ix_public_chat_answer_outcome_created",
        table_name="public_chat_answer_events",
    )
    op.drop_index(
        "ix_public_chat_answer_session_created",
        table_name="public_chat_answer_events",
    )
    op.drop_table("public_chat_answer_events")

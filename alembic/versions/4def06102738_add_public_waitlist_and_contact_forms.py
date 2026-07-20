"""add public waitlist and contact forms

Revision ID: 4def06102738
Revises: 3cedf4051627
Create Date: 2026-07-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4def06102738"
down_revision: str | None = "3cedf4051627"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "waitlist_signups",
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("source_page", sa.String(length=240), nullable=False),
        sa.Column("attribution", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_waitlist_signups")),
        sa.UniqueConstraint("idempotency_key", name="uq_waitlist_signup_idempotency"),
        sa.UniqueConstraint("normalized_email", name="uq_waitlist_signup_email"),
    )
    op.create_index(
        "ix_waitlist_signup_submitted",
        "waitlist_signups",
        ["submitted_at"],
    )
    op.create_index(
        "ix_waitlist_signup_country_submitted",
        "waitlist_signups",
        ["country_code", "submitted_at"],
    )
    op.create_table(
        "waitlist_sheet_deliveries",
        sa.Column("signup_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_type", sa.String(length=120), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["signup_id"],
            ["waitlist_signups.id"],
            name=op.f("fk_waitlist_sheet_deliveries_signup_id_waitlist_signups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_waitlist_sheet_deliveries")),
        sa.UniqueConstraint("event_key", name="uq_waitlist_sheet_delivery_event"),
        sa.UniqueConstraint("signup_id", name="uq_waitlist_sheet_delivery_signup"),
    )
    op.create_index(
        "ix_waitlist_sheet_delivery_due",
        "waitlist_sheet_deliveries",
        ["status", "next_retry_at"],
    )
    op.create_index(
        op.f("ix_waitlist_sheet_deliveries_signup_id"),
        "waitlist_sheet_deliveries",
        ["signup_id"],
    )

    op.create_table(
        "contact_submissions",
        sa.Column("normalized_email", sa.String(length=320), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_page", sa.String(length=240), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_submissions")),
        sa.UniqueConstraint("idempotency_key", name="uq_contact_submission_idempotency"),
    )
    op.create_index(
        "ix_contact_submission_status_submitted",
        "contact_submissions",
        ["status", "submitted_at"],
    )
    op.create_table(
        "contact_email_deliveries",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_error_type", sa.String(length=120), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["contact_submissions.id"],
            name=op.f("fk_contact_email_deliveries_submission_id_contact_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_email_deliveries")),
        sa.UniqueConstraint("event_key", name="uq_contact_email_delivery_event"),
        sa.UniqueConstraint("submission_id", name="uq_contact_email_delivery_submission"),
    )
    op.create_index(
        "ix_contact_email_delivery_due",
        "contact_email_deliveries",
        ["status", "next_retry_at"],
    )
    op.create_index(
        op.f("ix_contact_email_deliveries_submission_id"),
        "contact_email_deliveries",
        ["submission_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_contact_email_deliveries_submission_id"),
        table_name="contact_email_deliveries",
    )
    op.drop_index(
        "ix_contact_email_delivery_due",
        table_name="contact_email_deliveries",
    )
    op.drop_table("contact_email_deliveries")
    op.drop_index(
        "ix_contact_submission_status_submitted",
        table_name="contact_submissions",
    )
    op.drop_table("contact_submissions")
    op.drop_index(
        op.f("ix_waitlist_sheet_deliveries_signup_id"),
        table_name="waitlist_sheet_deliveries",
    )
    op.drop_index(
        "ix_waitlist_sheet_delivery_due",
        table_name="waitlist_sheet_deliveries",
    )
    op.drop_table("waitlist_sheet_deliveries")
    op.drop_index(
        "ix_waitlist_signup_country_submitted",
        table_name="waitlist_signups",
    )
    op.drop_index("ix_waitlist_signup_submitted", table_name="waitlist_signups")
    op.drop_table("waitlist_signups")

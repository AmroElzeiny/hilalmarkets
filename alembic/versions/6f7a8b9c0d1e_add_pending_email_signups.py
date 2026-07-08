"""Add pending email signups.

Revision ID: 6f7a8b9c0d1e
Revises: 4d5e6f7a8b9c
Create Date: 2026-07-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f7a8b9c0d1e"
down_revision: str | None = "4d5e6f7a8b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_email_signups",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_identifier", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("telegram_link", sa.String(length=1000), nullable=True),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("requested_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pending_email_signup_email_created",
        "pending_email_signups",
        ["email", "created_at"],
    )
    op.create_index(
        "ix_pending_email_signup_expires",
        "pending_email_signups",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_email_signup_expires", table_name="pending_email_signups")
    op.drop_index("ix_pending_email_signup_email_created", table_name="pending_email_signups")
    op.drop_table("pending_email_signups")

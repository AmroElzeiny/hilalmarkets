"""add email auth challenges

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2026-06-25 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d5e6f7a8b9c"
down_revision: str | None = "3c4d5e6f7a8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_auth_challenges",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("code_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("requested_ip_hash", sa.String(64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_auth_challenge_email_purpose",
        "email_auth_challenges",
        ["email", "purpose", "created_at"],
    )
    op.create_index(
        "ix_email_auth_challenge_user_expires",
        "email_auth_challenges",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_email_auth_challenges_user_id",
        "email_auth_challenges",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_auth_challenges_user_id", table_name="email_auth_challenges")
    op.drop_index(
        "ix_email_auth_challenge_user_expires",
        table_name="email_auth_challenges",
    )
    op.drop_index(
        "ix_email_auth_challenge_email_purpose",
        table_name="email_auth_challenges",
    )
    op.drop_table("email_auth_challenges")

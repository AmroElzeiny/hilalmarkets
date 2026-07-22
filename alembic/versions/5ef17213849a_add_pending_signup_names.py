"""add pending signup names

Revision ID: 5ef17213849a
Revises: 4def06102738
Create Date: 2026-07-22 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5ef17213849a"
down_revision: str | None = "4def06102738"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pending_email_signups", sa.Column("first_name", sa.String(60)))
    op.add_column("pending_email_signups", sa.Column("last_name", sa.String(60)))


def downgrade() -> None:
    op.drop_column("pending_email_signups", "last_name")
    op.drop_column("pending_email_signups", "first_name")

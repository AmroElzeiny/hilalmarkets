"""count support messages once, for both doors

Two forms take support messages: the public ``/contact`` page and the dashboard's own
support form. Neither counted anything, so nothing stopped one script opening a
thousand tickets, and "two messages per email" could only ever have meant two per form.

This table is the one place a message is counted. Both doors write one row through
``services/support_intake.py``; the quota reads it. It holds no personal data: the
address and the browser session are salted hashes, so the ledger can count a person
without storing who they are.

Revision ID: c8e5a2f14b70
Revises: b3f81c07d5a4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e5a2f14b70"
down_revision: str | None = "b3f81c07d5a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_intake_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("door", sa.String(length=32), nullable=False),
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column("client_hash", sa.String(length=64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # One index per question the quota asks: this address, this session, and everybody.
    op.create_index(
        "ix_support_intake_email",
        "support_intake_records",
        ["email_hash", "accepted_at"],
    )
    op.create_index(
        "ix_support_intake_client",
        "support_intake_records",
        ["client_hash", "accepted_at"],
    )
    op.create_index(
        "ix_support_intake_accepted",
        "support_intake_records",
        ["accepted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_intake_accepted", table_name="support_intake_records")
    op.drop_index("ix_support_intake_client", table_name="support_intake_records")
    op.drop_index("ix_support_intake_email", table_name="support_intake_records")
    op.drop_table("support_intake_records")

"""Add the customer-entered billing profile to checkout attempts.

Revision ID: 91d7c4a2b6e8
Revises: 81b24a6c37de
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "91d7c4a2b6e8"
down_revision: str | None = "81b24a6c37de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "billing_checkout_attempts",
        sa.Column(
            "billing_profile",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("billing_checkout_attempts", "billing_profile")

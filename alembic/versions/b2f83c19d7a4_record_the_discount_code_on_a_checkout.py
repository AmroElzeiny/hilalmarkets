"""Record the discount code a checkout was opened with.

The amount on a checkout attempt is already the discounted figure, because that is what
the payment company is asked for and what the webhook holds the payment against. These
two columns keep the reason beside it, so a finished payment can still say why it was
cheaper after the code itself has been withdrawn.

Both are nullable: every checkout made before this migration was made without a code, and
almost every checkout after it will be too.

Revision ID: b2f83c19d7a4
Revises: a1d5e9c73b42
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2f83c19d7a4"
down_revision: str | None = "a1d5e9c73b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "billing_checkout_attempts",
        sa.Column("discount_code", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "billing_checkout_attempts",
        sa.Column("discount_percent", sa.Numeric(precision=5, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("billing_checkout_attempts", "discount_percent")
    op.drop_column("billing_checkout_attempts", "discount_code")

"""Record the refunded amount on a checkout attempt.

A refund event is currently read as a full refund whatever it carries: the refunded
amount is never stored, so a small refund erases the whole payment. This column holds
the running total the provider's refund events reported, in the payment's own currency,
so "how much of this payment still holds money" has one stored answer — read through
``ai_market_monitor.core.money.money_kept``, never by hand.

Revision ID: a6d1f4c80b27
Revises: e5a72b10c94d
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a6d1f4c80b27"
down_revision = "e5a72b10c94d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("billing_checkout_attempts") as batch:
        batch.add_column(
            sa.Column(
                "refunded_amount",
                sa.Numeric(12, 2),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("billing_checkout_attempts") as batch:
        batch.drop_column("refunded_amount")

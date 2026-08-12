"""Join the AI usage ledger to the money that was reserved for it.

``ai_usage_events`` recorded what an AI call cost. ``ai_budget_reservations`` recorded what
it was allowed to cost. Nothing joined them, so the two records of the same money could
not be checked against each other: a reviewer seeing £4 spent and £4 reserved had no way
to tell whether that was one turn written down twice or two turns written down once.

Four of the five columns close that gap — the reservation, the provider's own request id,
the processing tier that answered, and how the call ended. The fifth records which rollout
configuration was in force, so a spending spike can be replayed against the rollout that
caused it instead of against whatever the configuration says by the time somebody looks.

Every column is nullable. Rows written before this migration genuinely do not know these
things, and inventing a value for them would make the ledger say something that was never
true.

Revision ID: b7c41d9e2a06
Revises: f3b90c21d7e5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c41d9e2a06"
down_revision: str | None = "f3b90c21d7e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_usage_events", sa.Column("reservation_id", sa.Uuid(), nullable=True))
    op.add_column(
        "ai_usage_events", sa.Column("provider_request_id", sa.String(length=160), nullable=True)
    )
    op.add_column(
        "ai_usage_events", sa.Column("service_tier", sa.String(length=40), nullable=True)
    )
    op.add_column("ai_usage_events", sa.Column("outcome", sa.String(length=40), nullable=True))
    op.add_column(
        "ai_usage_events", sa.Column("rollout_version", sa.String(length=40), nullable=True)
    )
    op.create_index(
        "ix_ai_usage_reservation", "ai_usage_events", ["reservation_id"]
    )
    with op.batch_alter_table("ai_usage_events") as batch:
        # Named so a later migration can drop it. SQLite rewrites the table to add a
        # foreign key, which is what the batch context is for.
        batch.create_foreign_key(
            "fk_ai_usage_reservation",
            "ai_budget_reservations",
            ["reservation_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.drop_constraint("fk_ai_usage_reservation", type_="foreignkey")
    op.drop_index("ix_ai_usage_reservation", table_name="ai_usage_events")
    op.drop_column("ai_usage_events", "rollout_version")
    op.drop_column("ai_usage_events", "outcome")
    op.drop_column("ai_usage_events", "service_tier")
    op.drop_column("ai_usage_events", "provider_request_id")
    op.drop_column("ai_usage_events", "reservation_id")

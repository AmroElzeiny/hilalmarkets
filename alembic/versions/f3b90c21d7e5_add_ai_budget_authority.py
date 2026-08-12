"""One atomic authority for AI spending: lockable counters and reservations.

Budgets used to be checked, where they were checked at all, by summing past usage and
then acting on the answer. That is a read followed by an independent write: two workers
both read "£9 of £10 spent", both decide there is room, and both spend.

``ai_budget_counters`` is a row per budget window, taken with a row lock inside the same
transaction that increments it, so the second worker waits and then reads what the first
one wrote. ``ai_budget_reservations`` holds money promised to a turn before anybody knows
what it will really cost, keyed by an idempotency key so a replay finds its own
reservation instead of taking a second one.

Revision ID: f3b90c21d7e5
Revises: e8f2c60b7a14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3b90c21d7e5"
down_revision: str | None = "e8f2c60b7a14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_budget_counters",
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("scope_key", sa.String(length=120), nullable=False),
        sa.Column("window_key", sa.String(length=20), nullable=False),
        sa.Column(
            "spent_usd", sa.Numeric(18, 8), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "reserved_usd", sa.Numeric(18, 8), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "reserved_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope", "scope_key", "window_key"),
        sa.UniqueConstraint("scope", "scope_key", "window_key", name="uq_ai_budget_window"),
    )
    op.create_index(
        "ix_ai_budget_counter_lookup",
        "ai_budget_counters",
        ["scope", "scope_key", "window_key"],
    )

    op.create_table(
        "ai_budget_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("feature", sa.String(length=60), nullable=False),
        sa.Column(
            "provider", sa.String(length=30), server_default="openai", nullable=False
        ),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("service_tier", sa.String(length=40), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
        sa.Column("state", sa.String(length=20), server_default="reserved", nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=True),
        sa.Column("windows", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_budget_reservation_key"),
    )
    op.create_index(
        "ix_ai_budget_reservation_state",
        "ai_budget_reservations",
        ["state", "expires_at"],
    )
    op.create_index(
        "ix_ai_budget_reservation_user",
        "ai_budget_reservations",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_budget_reservation_user", table_name="ai_budget_reservations")
    op.drop_index("ix_ai_budget_reservation_state", table_name="ai_budget_reservations")
    op.drop_table("ai_budget_reservations")
    op.drop_index("ix_ai_budget_counter_lookup", table_name="ai_budget_counters")
    op.drop_table("ai_budget_counters")

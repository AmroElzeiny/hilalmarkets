"""add condition runtime states

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2026-06-25 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c4d5e6f7a8b"
down_revision: str | None = "2b3c4d5e6f7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "condition_runtime_states",
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("condition_key", sa.String(100), nullable=False),
        sa.Column(
            "last_outcome",
            sa.Enum(
                "passed",
                "failed",
                "pending",
                "unavailable",
                "error",
                name="condition_runtime_outcome",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("first_true_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_true_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consecutive_true_count", sa.Integer(), nullable=False),
        sa.Column("actual_value", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "strategy_version_id",
            "exchange",
            "symbol",
            "timeframe",
            "direction",
            "condition_key",
            name="uq_condition_runtime_market_key",
        ),
    )
    op.create_index(
        "ix_condition_runtime_version_market",
        "condition_runtime_states",
        ["strategy_version_id", "exchange", "symbol", "timeframe"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_condition_runtime_version_market",
        table_name="condition_runtime_states",
    )
    op.drop_table("condition_runtime_states")

"""expand strategy logical operator storage

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LOGICAL_OPERATORS = (
    "and",
    "or",
    "not",
    "sequence",
    "within_last",
    "persisted_for",
    "count_of",
    "cooldown_condition",
    "first_time_true",
    "changed_state",
    "cross_with_confirmation",
    "conditional_branch",
)


def upgrade() -> None:
    expanded = sa.Enum(
        *LOGICAL_OPERATORS,
        name="logical_operator",
        native_enum=False,
    )
    with op.batch_alter_table("strategy_conditions") as batch_op:
        batch_op.alter_column(
            "logical_operator",
            existing_type=sa.String(length=3),
            type_=expanded,
            existing_nullable=True,
        )


def downgrade() -> None:
    original = sa.Enum(
        "and",
        "or",
        name="logical_operator",
        native_enum=False,
    )
    with op.batch_alter_table("strategy_conditions") as batch_op:
        batch_op.alter_column(
            "logical_operator",
            existing_type=sa.String(length=23),
            type_=original,
            existing_nullable=True,
        )

"""add pending mechanic strategy revision

Revision ID: cf3a4b5c6d7e
Revises: be2f3a4b5c6d
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cf3a4b5c6d7e"
down_revision: str | None = "be2f3a4b5c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("capability_extensions") as batch_op:
        batch_op.add_column(
            sa.Column("pending_strategy_version_id", sa.Uuid(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_capability_extension_pending_strategy_version",
            "strategy_versions",
            ["pending_strategy_version_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("capability_extensions") as batch_op:
        batch_op.drop_constraint(
            "fk_capability_extension_pending_strategy_version",
            type_="foreignkey",
        )
        batch_op.drop_column("pending_strategy_version_id")

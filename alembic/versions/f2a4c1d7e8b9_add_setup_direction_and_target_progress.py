"""add setup direction and target progress

Revision ID: f2a4c1d7e8b9
Revises: dedffe529345
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a4c1d7e8b9"
down_revision: str | None = "dedffe529345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "setup_instances",
        sa.Column("direction", sa.String(length=10), nullable=False, server_default="long"),
    )
    op.add_column(
        "setup_instances",
        sa.Column("target_levels", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "setup_instances",
        sa.Column("targets_reached", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "setup_instances",
        sa.Column("lifecycle_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("setup_instances", "lifecycle_version")
    op.drop_column("setup_instances", "targets_reached")
    op.drop_column("setup_instances", "target_levels")
    op.drop_column("setup_instances", "direction")

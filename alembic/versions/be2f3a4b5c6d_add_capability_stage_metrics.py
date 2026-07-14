"""add capability resolution stage metrics

Revision ID: be2f3a4b5c6d
Revises: ad1e2f3a4b5c
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "be2f3a4b5c6d"
down_revision: str | None = "ad1e2f3a4b5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "capability_resolution_events",
        sa.Column("selection_source", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "capability_resolution_events",
        sa.Column("selected_parameters", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "capability_resolution_events",
        sa.Column("parameters_validated", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("capability_resolution_events", "parameters_validated")
    op.drop_column("capability_resolution_events", "selected_parameters")
    op.drop_column("capability_resolution_events", "selection_source")

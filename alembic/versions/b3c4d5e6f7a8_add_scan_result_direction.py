"""add scan result direction

Revision ID: b3c4d5e6f7a8
Revises: a7b8c9d0e1f2
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scan_results") as batch_op:
        batch_op.drop_constraint("uq_scan_result_market", type_="unique")
        batch_op.add_column(
            sa.Column(
                "direction",
                sa.String(length=10),
                nullable=False,
                server_default="long",
            )
        )
        batch_op.create_unique_constraint(
            "uq_scan_result_market",
            [
                "scan_job_id",
                "exchange",
                "symbol",
                "timeframe",
                "direction",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("scan_results") as batch_op:
        batch_op.drop_constraint("uq_scan_result_market", type_="unique")
        batch_op.drop_column("direction")
        batch_op.create_unique_constraint(
            "uq_scan_result_market",
            ["scan_job_id", "exchange", "symbol", "timeframe"],
        )

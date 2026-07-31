"""Store the reviewed evidence and market-data contract on an approved version.

The worker had no record of what was promised about market data before approval, so it
could not tell whether a market still needed checking. It now reads the contract from the
approved version and fails closed per symbol.

Revision ID: d4a02f6b18cc
Revises: c3d91e2f70aa
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4a02f6b18cc"
down_revision: str | None = "c3d91e2f70aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_versions",
        sa.Column(
            "approval_evidence",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("strategy_versions", "approval_evidence")

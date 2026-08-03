"""Persist exact measured telemetry on each idempotent Setup Chat turn.

Revision ID: e5b13c7a29d4
Revises: d4a02f6b18cc
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b13c7a29d4"
down_revision: str | None = "d4a02f6b18cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("setup_chat_turns", sa.Column("telemetry_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("setup_chat_turns", "telemetry_json")

"""Keep the board a monitor was drawn from, so it can be opened again.

A monitor stores what it *runs*: a compiled definition. Nothing turns that back into the
cards a person drew, so "Change it" had nowhere to send them except the older assistant
page. That page is gone, and the canvas is the only place a monitor is authored now.

This adds one nullable column beside the compiled definition, holding the board exactly
as the canvas sent it. It is written in the same transaction that compiles the monitor,
so the drawing and the running rules can never describe two different things.

``NULL`` is a real answer and stays one: every monitor made before this, and every
monitor made in Telegram, has no board. The canvas says so plainly instead of guessing a
drawing that would watch something else.

Revision ID: f2c60b83a915
Revises: e1a7d4c92b03
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f2c60b83a915"
down_revision: str | None = "e1a7d4c92b03"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_versions",
        sa.Column("canvas_plan_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategy_versions", "canvas_plan_json")

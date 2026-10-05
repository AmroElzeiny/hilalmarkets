"""Keep the reasons a reader sees on a Passport, as the reviewer confirmed them.

A reviewer's typed reason stays the record. ``public_reasons`` is the plain version shown
on the coin's Passport — drafted by the AI from that reason, then edited or kept by the
reviewer. Decisions recorded before this column existed have none, which is what the
empty list says.

Revision ID: a1d7c3e95f20
Revises: c4e8a1f05b93
Create Date: 2026-10-05

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1d7c3e95f20"
down_revision = "c4e8a1f05b93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sharia_review_decisions") as batch:
        batch.add_column(
            sa.Column(
                "public_reasons",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("sharia_review_decisions") as batch:
        batch.drop_column("public_reasons")

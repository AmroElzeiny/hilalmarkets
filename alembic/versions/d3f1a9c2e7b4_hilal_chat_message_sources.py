"""Keep the source cards under each Hilal answer.

An answer from Hilal ends with a preview card for each page it was built from — a coin's
Passport, the Halal Assets list, the published methodology. The cards are stored with
the answer, so a person who reopens the chat sees the same cards under the same answer
rather than a transcript that lost them on reload.

A plain JSON list, empty for every answer written before this. No constraint or index is
added, so no name has to be shortened.

Revision ID: d3f1a9c2e7b4
Revises: b7c2e94d1a36
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d3f1a9c2e7b4"
down_revision = "b7c2e94d1a36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("hilal_chat_messages") as batch:
        batch.add_column(
            sa.Column("sources", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("hilal_chat_messages") as batch:
        batch.drop_column("sources")

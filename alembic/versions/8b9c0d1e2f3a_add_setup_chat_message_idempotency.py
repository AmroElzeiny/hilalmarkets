"""add setup chat message idempotency

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b9c0d1e2f3a"
down_revision: str | None = "7a8b9c0d1e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_setup_chat_messages") as batch_op:
        batch_op.add_column(sa.Column("client_message_id", sa.String(length=80), nullable=True))
        batch_op.create_unique_constraint(
            "uq_ai_setup_chat_client_message", ["session_id", "client_message_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_setup_chat_messages") as batch_op:
        batch_op.drop_constraint("uq_ai_setup_chat_client_message", type_="unique")
        batch_op.drop_column("client_message_id")

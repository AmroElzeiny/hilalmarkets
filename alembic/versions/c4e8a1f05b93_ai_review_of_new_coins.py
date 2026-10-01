"""Keep the AI reviewer's report on each automated screen run, and link its task.

Every new coin the automated screen reads is now also read by an AI reviewer, and the
two readings together become one report in the reviewers' task list. The report lives
on the run it describes; the run points at the task that carries it.

``hold_state`` records what the machine did with the coin while it waits for a person —
``held_back`` when a term against the methodology was found. It is not a Shariah status.

Revision ID: c4e8a1f05b93
Revises: d3f1a9c2e7b4
Create Date: 2026-09-30

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4e8a1f05b93"
down_revision = "d3f1a9c2e7b4"
branch_labels = None
depends_on = None

_FK = "fk_automated_screen_runs_review_case_id_sharia_review_cases"


def upgrade() -> None:
    with op.batch_alter_table("automated_screen_runs") as batch:
        batch.add_column(
            sa.Column(
                "review_report",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "ai_review_state",
                sa.String(length=32),
                nullable=False,
                # Runs written before the AI reviewer existed have never been read by
                # it, which is exactly what "pending" means: the next sweep reads them.
                server_default="pending",
            )
        )
        batch.add_column(
            sa.Column(
                "ai_review_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("hold_state", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("review_case_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f(_FK),
            "sharia_review_cases",
            ["review_case_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("automated_screen_runs") as batch:
        batch.drop_constraint(op.f(_FK), type_="foreignkey")
        batch.drop_column("review_case_id")
        batch.drop_column("hold_state")
        batch.drop_column("ai_review_attempts")
        batch.drop_column("ai_review_state")
        batch.drop_column("review_report")

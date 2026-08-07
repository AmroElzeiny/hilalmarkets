"""Record what each waitlist signup chose about private-beta contact.

The public waitlist form now asks one question: may we contact you about private beta
testing? The answer belongs on the signup row, not in a spreadsheet note, so the team
can see it for every person who joined.

Rows created before the question existed get ``false``. An answer nobody was asked for
is not agreement, and defaulting them to ``true`` would invent one.

Revision ID: c4a71f28d90e
Revises: b1c93d7e5a26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4a71f28d90e"
down_revision: str | None = "b1c93d7e5a26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("waitlist_signups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "beta_contact_consent",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("waitlist_signups") as batch_op:
        batch_op.drop_column("beta_contact_consent")

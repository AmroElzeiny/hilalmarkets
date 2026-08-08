"""Stop recording a private-beta contact answer on waitlist signups.

The waitlist form briefly offered a "contact me about private beta testing" box, already
ticked. A box that is ticked before the person touches it is not a choice they made, so
the column was storing an answer nobody gave. The question is withdrawn rather than kept
in a form that cannot produce a real answer.

The column is dropped rather than left in place and ignored. A column that always holds
the same value is the kind of thing somebody later reads as consent.

The migration that added it is already in the history, so this reverses it forward. The
history stays valid whether or not an environment ever ran the earlier one.

Revision ID: e8f2c60b7a14
Revises: c4a71f28d90e
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8f2c60b7a14"
down_revision: str | None = "c4a71f28d90e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("waitlist_signups") as batch_op:
        batch_op.drop_column("beta_contact_consent")


def downgrade() -> None:
    # Restoring the column cannot restore the answers, and it must not invent them:
    # every row comes back as False, which is what "was never asked" means here.
    with op.batch_alter_table("waitlist_signups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "beta_contact_consent",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )

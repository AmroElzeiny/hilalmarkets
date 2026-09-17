"""Keep a risk-note acceptance when the sign-in that gave it is removed.

The acceptance pointed at the sign-in row with ``ON DELETE RESTRICT``. Unlinking Telegram
deletes the Telegram sign-in, so a person who had accepted the note inside the bot could
never unlink: PostgreSQL refused the delete and the page said "We could not unlink it".
SQLite does not enforce the rule, so no offline test ever saw it.

The acceptance is a legal record and must stay. It now keeps its own copy of which sign-in
gave it (``identity_provider`` and ``identity_subject``), and the pointer to the live row is
cleared when that row goes, instead of blocking the removal.

Revision ID: b7c2e94d1a36
Revises: a6d1f4c80b27
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b7c2e94d1a36"
down_revision = "a6d1f4c80b27"
branch_labels = None
depends_on = None

FOREIGN_KEY = "fk_disclaimer_acceptances_identity_id_user_identities"


def upgrade() -> None:
    with op.batch_alter_table("disclaimer_acceptances") as batch:
        batch.add_column(sa.Column("identity_provider", sa.String(32), nullable=True))
        batch.add_column(sa.Column("identity_subject", sa.String(255), nullable=True))

    op.execute(
        """
        UPDATE disclaimer_acceptances
        SET identity_provider = (
                SELECT CAST(user_identities.provider AS VARCHAR(32))
                FROM user_identities
                WHERE user_identities.id = disclaimer_acceptances.identity_id
            ),
            identity_subject = (
                SELECT user_identities.provider_subject
                FROM user_identities
                WHERE user_identities.id = disclaimer_acceptances.identity_id
            )
        """
    )

    with op.batch_alter_table("disclaimer_acceptances") as batch:
        batch.drop_constraint(op.f(FOREIGN_KEY), type_="foreignkey")
        batch.alter_column("identity_id", existing_type=sa.Uuid(), nullable=True)
        batch.create_foreign_key(
            op.f(FOREIGN_KEY),
            "user_identities",
            ["identity_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Fails on purpose while any acceptance has outlived its sign-in: going back would
    # have to delete a legal record to satisfy NOT NULL, and that is not a migration's
    # decision to make.
    with op.batch_alter_table("disclaimer_acceptances") as batch:
        batch.drop_constraint(op.f(FOREIGN_KEY), type_="foreignkey")
        batch.alter_column("identity_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            op.f(FOREIGN_KEY),
            "user_identities",
            ["identity_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.drop_column("identity_subject")
        batch.drop_column("identity_provider")

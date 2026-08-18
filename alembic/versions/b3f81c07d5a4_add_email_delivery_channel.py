"""add email as a delivery channel, and let an account email have no admin behind it

Two changes, both about email.

**Email becomes a real way to be told about an alert**, equal to Telegram: the same
alerts, recorded in the same `alert_deliveries` table, retried by the same machinery.
`delivery_channel` is a non-native enum — a VARCHAR with a CHECK constraint — so adding
a value means rewriting the constraint. `discord` stays in the list even though nothing
delivers to it: historical rows still point at it, and dropping the value would make
those rows unreadable. `docs/RETIRED_DISCORD_COMPATIBILITY.md` explains why, and
`services/notification_preferences.offered_channels` is what decides which channels are
actually offered to a person.

**`account_email_deliveries.admin_action_id` becomes nullable.** That table is the
retrying outbox for emails about somebody's account, but it required an administrator's
action to point at — so it could only ever hold administrator notices. The welcome a
person receives when they finish signing up is raised by the person themselves and has
no such action, so it had nowhere to queue.

Revision ID: b3f81c07d5a4
Revises: a7d2e94c1b60
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3f81c07d5a4"
down_revision: str | None = "a7d2e94c1b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_DELIVERY_CHANNELS = ("telegram", "whatsapp", "discord", "web")
DELIVERY_CHANNELS = ("telegram", "whatsapp", "email", "discord", "web")


def _channel_enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name="delivery_channel", native_enum=False)


def upgrade() -> None:
    with op.batch_alter_table("alert_deliveries") as batch_op:
        batch_op.alter_column(
            "channel",
            existing_type=_channel_enum(OLD_DELIVERY_CHANNELS),
            type_=_channel_enum(DELIVERY_CHANNELS),
            existing_nullable=False,
        )
    with op.batch_alter_table("account_email_deliveries") as batch_op:
        batch_op.alter_column(
            "admin_action_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )


def downgrade() -> None:
    # Any row that has no administrator behind it has to go first, or the column cannot
    # be made required again. These are welcome emails; the accounts they belong to are
    # untouched.
    op.execute(sa.text("DELETE FROM account_email_deliveries WHERE admin_action_id IS NULL"))
    with op.batch_alter_table("account_email_deliveries") as batch_op:
        batch_op.alter_column(
            "admin_action_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
    # Any email delivery already recorded has to go first, or the narrower constraint
    # cannot be applied. Deleting the delivery row leaves the alert itself untouched:
    # the alert is the record of what happened, the delivery row only of how it was sent.
    op.execute(sa.text("DELETE FROM alert_deliveries WHERE channel = 'email'"))
    with op.batch_alter_table("alert_deliveries") as batch_op:
        batch_op.alter_column(
            "channel",
            existing_type=_channel_enum(DELIVERY_CHANNELS),
            type_=_channel_enum(OLD_DELIVERY_CHANNELS),
            existing_nullable=False,
        )

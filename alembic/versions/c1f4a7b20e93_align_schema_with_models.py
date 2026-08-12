"""Close three model/migration disagreements, one of which loses compliance alerts.

``alembic check`` has been failing since before this phase, reporting the same three
differences between the models and the schema the migrations build. Two are cosmetic.
The first is not.

**alerts.alert_type could not hold the word "compliance".**

``5aa448aa1447`` created the column from an enum listing six values — forming,
near_miss, confirmed, lifecycle, failure, trial. With ``native_enum=False`` SQLAlchemy
renders that as ``VARCHAR(9)``, the length of its longest value. ``AlertType`` later
gained ``COMPLIANCE = "compliance"``, which is ten characters, and no migration ever
widened the column.

SQLite does not enforce ``VARCHAR`` length, so every local test and every CI run
passes. PostgreSQL does enforce it, so in staging and production every attempt to
write a compliance alert fails with "value too long for type character varying(9)".
That is the Shariah compliance-drift notification — the alert that tells a customer
the religious status of something they hold has changed — failing to persist, in
production only, on a path no offline test can reach.

The remaining two are name and shape drift with no behavioural difference:

* ``ai_usage_events.reservation_id`` is declared ``index=True``, which SQLAlchemy names
  ``ix_ai_usage_events_reservation_id``; ``b7c41d9e2a06`` created it as
  ``ix_ai_usage_reservation``.
* ``public_chat_answer_feedback.inquiry_id`` is declared ``unique=True, index=True``,
  which is one unique index; the migration built a unique *constraint* plus a separate
  non-unique index. Uniqueness was enforced either way.

Both are corrected here so ``alembic check`` can pass, which is what makes the next
real drift visible instead of hidden behind three known failures.

Revision ID: c1f4a7b20e93
Revises: 84e25ab68ade
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1f4a7b20e93"
down_revision: str | None = "84e25ab68ade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every value ``AlertType`` currently declares. Longest is "compliance", so the
#: column becomes VARCHAR(10).
_ALERT_TYPES = (
    "forming",
    "near_miss",
    "confirmed",
    "lifecycle",
    "failure",
    "trial",
    "compliance",
)

#: What the column was created as, for the downgrade.
_ALERT_TYPES_BEFORE = (
    "forming",
    "near_miss",
    "confirmed",
    "lifecycle",
    "failure",
    "trial",
)


def upgrade() -> None:
    # Widening only. No existing value can fail to fit a longer column, so this needs
    # no data migration and cannot lose a row.
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.alter_column(
            "alert_type",
            existing_type=sa.VARCHAR(length=9),
            type_=sa.Enum(*_ALERT_TYPES, name="alert_type", native_enum=False),
            existing_nullable=False,
        )

    with op.batch_alter_table("ai_usage_events", schema=None) as batch_op:
        batch_op.drop_index("ix_ai_usage_reservation")
        batch_op.create_index(
            "ix_ai_usage_events_reservation_id", ["reservation_id"], unique=False
        )

    with op.batch_alter_table("public_chat_answer_feedback", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_public_chat_answer_feedback_inquiry_id", type_="unique"
        )
        batch_op.drop_index("ix_public_chat_answer_feedback_inquiry_id")
        batch_op.create_index(
            "ix_public_chat_answer_feedback_inquiry_id", ["inquiry_id"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("public_chat_answer_feedback", schema=None) as batch_op:
        batch_op.drop_index("ix_public_chat_answer_feedback_inquiry_id")
        batch_op.create_index(
            "ix_public_chat_answer_feedback_inquiry_id", ["inquiry_id"], unique=False
        )
        batch_op.create_unique_constraint(
            "uq_public_chat_answer_feedback_inquiry_id", ["inquiry_id"]
        )

    with op.batch_alter_table("ai_usage_events", schema=None) as batch_op:
        batch_op.drop_index("ix_ai_usage_events_reservation_id")
        batch_op.create_index("ix_ai_usage_reservation", ["reservation_id"], unique=False)

    # Narrowing back to VARCHAR(9) would truncate or reject any compliance alert
    # written while this revision was applied, so those rows are removed first. They
    # are notifications, not governance records: the Passport, the review case and the
    # status history all live elsewhere and are untouched.
    op.execute(sa.text("DELETE FROM alerts WHERE alert_type = 'compliance'"))
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.alter_column(
            "alert_type",
            existing_type=sa.Enum(*_ALERT_TYPES, name="alert_type", native_enum=False),
            type_=sa.Enum(*_ALERT_TYPES_BEFORE, name="alert_type", native_enum=False),
            existing_nullable=False,
        )

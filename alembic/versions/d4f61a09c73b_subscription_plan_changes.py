"""Every cancellation, upgrade and downgrade a customer asks for, written down.

One table. A person presses "Cancel plan", "Upgrade" or "Downgrade" in their own
dashboard, and a row lands here **before** the payment company is asked for anything.

That order is the whole point. Asking Creem first and recording afterwards means a slow
or unreachable payment company loses the request entirely — including the reason the
person gave and the sentence they agreed to. This way the request always exists, and
``status`` says how far it got: ``requested``, ``scheduled``, ``applied`` or ``failed``.

``consent_text`` keeps the exact sentence that was ticked rather than a yes/no flag. A
flag records that somebody agreed; only the sentence records what they agreed to, and
these boxes carry the two promises that matter — that the card is not charged again, and
that the plan runs to the end of the period already paid for.

**Every name here goes through ``op.f()``.** That marks a string as already produced by
the naming convention, which is what decides whether a name too long for PostgreSQL's
63-character limit is shortened deterministically or refused outright. The model generates
its names through the same convention, so both sides shorten the same string the same way.

Revision ID: d4f61a09c73b
Revises: c9a13b7e4f28
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4f61a09c73b"
down_revision = "c9a13b7e4f28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_plan_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("from_plan_code", sa.String(length=50), nullable=False),
        sa.Column("to_plan_code", sa.String(length=50), nullable=True),
        sa.Column("timing", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="requested",
        ),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("reason_text", sa.String(length=500), nullable=True),
        sa.Column("consent_text", sa.String(length=500), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("provider_error", sa.String(length=500), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_subscription_plan_changes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name=op.f("fk_subscription_plan_changes_subscription_id_subscriptions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_plan_changes")),
    )
    op.create_index(
        op.f("ix_plan_change_user_created"),
        "subscription_plan_changes",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plan_change_due"),
        "subscription_plan_changes",
        ["status", "effective_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_plan_change_due"), table_name="subscription_plan_changes")
    op.drop_index(
        op.f("ix_plan_change_user_created"), table_name="subscription_plan_changes"
    )
    op.drop_table("subscription_plan_changes")

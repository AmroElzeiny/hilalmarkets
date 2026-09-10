"""Record a paid plan replacement and the money a person must return.

Revision ID: e5a72b10c94d
Revises: d4f61a09c73b
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e5a72b10c94d"
down_revision = "d4f61a09c73b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("billing_checkout_attempts") as batch:
        batch.add_column(sa.Column("replaces_subscription_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("replaces_checkout_attempt_id", sa.Uuid(), nullable=True)
        )
        batch.create_foreign_key(
            op.f("fk_billing_checkout_attempts_replaces_subscription_id_subscriptions"),
            "subscriptions",
            ["replaces_subscription_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            op.f(
                "fk_billing_checkout_attempts_replaces_checkout_attempt_id_billing_checkout_attempts"
            ),
            "billing_checkout_attempts",
            ["replaces_checkout_attempt_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "plan_move_money_owed",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("ended_subscription_id", sa.Uuid(), nullable=False),
        sa.Column("replacement_subscription_id", sa.Uuid(), nullable=False),
        sa.Column("source_checkout_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("billing_event_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("from_plan_code", sa.String(length=50), nullable=False),
        sa.Column("to_plan_code", sa.String(length=50), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount_owed", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "paid_amount >= 0", name=op.f("ck_plan_move_paid_nonnegative")
        ),
        sa.CheckConstraint(
            "amount_owed > 0", name=op.f("ck_plan_move_owed_positive")
        ),
        sa.CheckConstraint(
            "amount_owed <= paid_amount",
            name=op.f("ck_plan_move_owed_not_more_paid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_plan_move_money_owed_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ended_subscription_id"],
            ["subscriptions.id"],
            name=op.f(
                "fk_plan_move_money_owed_ended_subscription_id_subscriptions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_subscription_id"],
            ["subscriptions.id"],
            name=op.f(
                "fk_plan_move_money_owed_replacement_subscription_id_subscriptions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_checkout_attempt_id"],
            ["billing_checkout_attempts.id"],
            name=op.f(
                "fk_plan_move_money_owed_source_checkout_attempt_id_billing_checkout_attempts"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["billing_event_id"],
            ["billing_events.id"],
            name=op.f("fk_plan_move_money_owed_billing_event_id_billing_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_move_money_owed")),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_plan_move_money_owed_key")
        ),
    )
    op.create_index(
        op.f("ix_plan_move_money_owed_due"),
        "plan_move_money_owed",
        ["status", "due_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plan_move_money_owed_user"),
        "plan_move_money_owed",
        ["user_id", "created_at"],
        unique=False,
    )

    with op.batch_alter_table("payment_email_deliveries") as batch:
        batch.add_column(
            sa.Column(
                "purpose",
                sa.String(length=40),
                nullable=False,
                server_default="payment_success",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("payment_email_deliveries") as batch:
        batch.drop_column("purpose")
    op.drop_index(
        op.f("ix_plan_move_money_owed_user"), table_name="plan_move_money_owed"
    )
    op.drop_index(
        op.f("ix_plan_move_money_owed_due"), table_name="plan_move_money_owed"
    )
    op.drop_table("plan_move_money_owed")
    with op.batch_alter_table("billing_checkout_attempts") as batch:
        batch.drop_constraint(
            op.f(
                "fk_billing_checkout_attempts_replaces_checkout_attempt_id_billing_checkout_attempts"
            ),
            type_="foreignkey",
        )
        batch.drop_constraint(
            op.f("fk_billing_checkout_attempts_replaces_subscription_id_subscriptions"),
            type_="foreignkey",
        )
        batch.drop_column("replaces_checkout_attempt_id")
        batch.drop_column("replaces_subscription_id")

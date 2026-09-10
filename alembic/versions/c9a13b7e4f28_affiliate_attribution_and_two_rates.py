"""Two commission rates, a permanent assignment, and the two ledgers behind them.

Four changes, and none of them rewrites anything already stored.

``affiliate_applications.subsequent_commission_percent`` is the share on every payment
after a customer's first. It is nullable and left empty on every existing row, and the
service reads an empty one as *the same as the first-payment share* — never as zero.
Filling it in with a number here would have invented a decision nobody made; reading it
as zero would silently stop paying affiliates who were approved before this existed.

``referral_relationships.assignment_source`` says which door a customer came through:
``link`` if they arrived on the affiliate's link, ``code`` if they typed the code with no
link behind them. Rows written before this exist keep ``NULL``, which is honest — nothing
recorded it at the time, and guessing would put people in a count they never belonged to.

``affiliate_code_uses`` is every time somebody used a code, and ``affiliate_commissions``
is every payment that earned money. Both are ledgers rather than counters because the
affiliate's page has to open each total and show when, who and how much;
``referral_codes.use_count`` is a single number and can only ever be shown as one. Each
has a unique ``event_key`` so a retried request or a replayed payment webhook records the
same thing once, rather than relying on a check somebody has to remember to write.

Both keep their own copy of the customer's name, and both let ``user_id`` become ``NULL``
rather than taking the row with the account. An earning is a receipt: it survives the
customer closing their account, because the affiliate really did earn it.

**Every name here goes through ``op.f()``.** That marks a string as already produced by
the naming convention, which is what decides whether a name too long for PostgreSQL's
63-character limit is shortened deterministically or refused outright. The models generate
their names through the same convention, so both sides shorten the same string the same
way and can never disagree about what a constraint is called.

Revision ID: c9a13b7e4f28
Revises: b2f83c19d7a4
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c9a13b7e4f28"
down_revision: str | None = "b2f83c19d7a4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "affiliate_applications",
        sa.Column("subsequent_commission_percent", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "referral_relationships",
        sa.Column("assignment_source", sa.String(length=16), nullable=True),
    )

    op.create_table(
        "affiliate_code_uses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("affiliate_user_id", sa.Uuid(), nullable=False),
        sa.Column("referral_code_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("customer_name", sa.String(length=120), nullable=False),
        sa.Column("context", sa.String(length=16), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["affiliate_user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_code_uses_affiliate_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["referral_code_id"],
            ["referral_codes.id"],
            name=op.f("fk_affiliate_code_uses_referral_code_id_referral_codes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_code_uses_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affiliate_code_uses")),
        sa.UniqueConstraint("event_key", name=op.f("uq_affiliate_code_use_event")),
    )
    op.create_index(
        op.f("ix_affiliate_code_use_owner_used"),
        "affiliate_code_uses",
        ["affiliate_user_id", "used_at"],
    )

    op.create_table(
        "affiliate_commissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("affiliate_user_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_id", sa.Uuid(), nullable=True),
        sa.Column("customer_user_id", sa.Uuid(), nullable=True),
        sa.Column("customer_name", sa.String(length=120), nullable=False),
        sa.Column("sequence_kind", sa.String(length=12), nullable=False),
        sa.Column("paid_amount_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("commission_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("commission_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["affiliate_user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_commissions_affiliate_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["referral_relationships.id"],
            # 62 characters, one under the limit — and marked anyway, because whether a
            # name fits is not something a person should have to count.
            name=op.f("fk_affiliate_commissions_relationship_id_referral_relationships"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_commissions_customer_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affiliate_commissions")),
        sa.UniqueConstraint("event_key", name=op.f("uq_affiliate_commission_event")),
    )
    op.create_index(
        op.f("ix_affiliate_commission_owner_earned"),
        "affiliate_commissions",
        ["affiliate_user_id", "earned_at"],
    )
    op.create_index(
        op.f("ix_affiliate_commission_owner_kind"),
        "affiliate_commissions",
        ["affiliate_user_id", "sequence_kind"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_affiliate_commission_owner_kind"), table_name="affiliate_commissions"
    )
    op.drop_index(
        op.f("ix_affiliate_commission_owner_earned"), table_name="affiliate_commissions"
    )
    op.drop_table("affiliate_commissions")
    op.drop_index(
        op.f("ix_affiliate_code_use_owner_used"), table_name="affiliate_code_uses"
    )
    op.drop_table("affiliate_code_uses")
    op.drop_column("referral_relationships", "assignment_source")
    op.drop_column("affiliate_applications", "subsequent_commission_percent")

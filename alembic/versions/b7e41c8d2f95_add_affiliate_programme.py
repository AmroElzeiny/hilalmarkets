"""Add the affiliate programme: applications and payout requests.

Two tables, and nothing changed on the ones already here.

``affiliate_applications`` keeps what a person asked for separately from what an
administrator granted. The columns are in two groups on purpose: ``requested_*`` is the
form, and ``discount_code`` / ``discount_percent`` / ``commission_percent`` are the
decision. Reusing one column for both would mean an applicant's typed number and an
approved rate were the same field, and there would be no way afterwards to tell a granted
25 percent from a hopeful one.

One row per person, enforced by a unique key on ``user_id``. A refused application is
rewritten in place when they apply again, so a person has one application and one
history — not a pile of refusals with the newest one silently winning.

``affiliate_payout_requests`` is a ledger. ``amount_usd`` is frozen at the moment of
asking rather than recomputed, because a paid row is a receipt: if the amount were
derived from the current balance, last month's receipt would change every time a new
referral converted.

**Every name here goes through ``op.f()``**, the same as the other migrations in this
directory. ``op.f()`` marks a string as *already produced by the naming convention*, and
that changes what SQLAlchemy does with one that is too long for PostgreSQL's 63-character
identifier limit: a marked name is shortened deterministically with a hash suffix, an
unmarked one is refused outright with ``IdentifierError``. The models generate their names
through the same convention, so both sides shorten the same string the same way and the
migration and the model always agree on what the constraint is called.

Revision ID: b7e41c8d2f95
Revises: a4d17e6b93c8
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b7e41c8d2f95"
down_revision: str | None = "a4d17e6b93c8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "affiliate_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("social_links", sa.JSON(), nullable=False),
        sa.Column("requested_discount_code", sa.String(length=40), nullable=False),
        sa.Column("requested_commission_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("applicant_note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("discount_code", sa.String(length=40), nullable=True),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("commission_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("referral_code_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_applications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_applications_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["referral_code_id"],
            ["referral_codes.id"],
            name=op.f("fk_affiliate_applications_referral_code_id_referral_codes"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affiliate_applications")),
        sa.UniqueConstraint("user_id", name=op.f("uq_affiliate_application_user")),
    )
    op.create_index(
        op.f("ix_affiliate_application_status_submitted"),
        "affiliate_applications",
        ["status", "submitted_at"],
    )

    op.create_table(
        "affiliate_payout_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("network", sa.String(length=24), nullable=False),
        sa.Column("destination_address", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("transaction_reference", sa.String(length=160), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["affiliate_applications.id"],
            # 66 characters. PostgreSQL stops at 63, so this is the name that has to be
            # marked: `op.f()` shortens it, a bare string would be refused.
            name=op.f("fk_affiliate_payout_requests_application_id_affiliate_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_payout_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_affiliate_payout_requests_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affiliate_payout_requests")),
    )
    op.create_index(
        op.f("ix_affiliate_payout_requests_application_id"),
        "affiliate_payout_requests",
        ["application_id"],
    )
    op.create_index(
        op.f("ix_affiliate_payout_status_requested"),
        "affiliate_payout_requests",
        ["status", "requested_at"],
    )
    op.create_index(
        op.f("ix_affiliate_payout_user_requested"),
        "affiliate_payout_requests",
        ["user_id", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_affiliate_payout_user_requested"),
        table_name="affiliate_payout_requests",
    )
    op.drop_index(
        op.f("ix_affiliate_payout_status_requested"),
        table_name="affiliate_payout_requests",
    )
    op.drop_index(
        op.f("ix_affiliate_payout_requests_application_id"),
        table_name="affiliate_payout_requests",
    )
    op.drop_table("affiliate_payout_requests")
    op.drop_index(
        op.f("ix_affiliate_application_status_submitted"),
        table_name="affiliate_applications",
    )
    op.drop_table("affiliate_applications")

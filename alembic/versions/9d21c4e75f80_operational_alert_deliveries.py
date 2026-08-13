"""Operational alert delivery records

One row per page that has been claimed for sending. ``idempotency_key`` is unique,
built from the alert rule, the issue's dedupe key and the current repeat window.
That uniqueness is the whole mechanism: rules are re-evaluated every minute, and
without it a one-hour outage would send sixty identical messages until somebody
muted the channel.

Nothing customer-owned is stored. The body is assembled from the alert rule's own
fixed sentences, and the service refuses any payload that looks like a secret.

Revision ID: 9d21c4e75f80
Revises: 3ba17c6d40f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d21c4e75f80"
down_revision: str | None = "3ba17c6d40f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_alert_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("rule_name", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("route", sa.String(length=24), nullable=False),
        sa.Column("primary_route", sa.String(length=24), nullable=False),
        sa.Column("fallback_route", sa.String(length=24), nullable=True),
        sa.Column("used_fallback", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("last_error", sa.String(length=240), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id"], ["operational_issues.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_operational_alert_delivery_idempotency"
        ),
    )
    op.create_index(
        "ix_operational_alert_delivery_status",
        "operational_alert_deliveries",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "ix_operational_alert_delivery_rule",
        "operational_alert_deliveries",
        ["rule_name", "created_at"],
    )


def downgrade() -> None:
    # Dropping this loses the record of which pages were sent. Nothing else points at
    # it, and no customer-owned data lives here.
    op.drop_index(
        "ix_operational_alert_delivery_rule", table_name="operational_alert_deliveries"
    )
    op.drop_index(
        "ix_operational_alert_delivery_status", table_name="operational_alert_deliveries"
    )
    op.drop_table("operational_alert_deliveries")

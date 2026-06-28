"""add v2 scan claims and trial cycles

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scan_jobs") as batch_op:
        batch_op.add_column(sa.Column("worker_id", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_scan_job_status_retry", ["status", "next_retry_at"])
        batch_op.create_index("ix_scan_job_worker_heartbeat", ["worker_id", "heartbeat_at"])

    op.create_table(
        "trial_cycles",
        sa.Column("trial_id", sa.Uuid(), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("qualifying_alerts_generated", sa.Integer(), nullable=False),
        sa.Column("qualifying_alerts_delivered", sa.Integer(), nullable=False),
        sa.Column("suppressed_alert_count", sa.Integer(), nullable=False),
        sa.Column("active_monitor_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("notification_ready_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("successful_scan_coverage", sa.Numeric(8, 5), nullable=True),
        sa.Column("renewal_decision", sa.String(length=64), nullable=True),
        sa.Column("renewal_reason", sa.String(length=80), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["trial_id"],
            ["trials.id"],
            name=op.f("fk_trial_cycles_trial_id_trials"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_cycles")),
        sa.UniqueConstraint("trial_id", "cycle_number", name="uq_trial_cycle_number"),
    )
    with op.batch_alter_table("trial_cycles") as batch_op:
        batch_op.create_index("ix_trial_cycle_status_ends", ["status", "ends_at"])
        batch_op.create_index("ix_trial_cycle_trial_status", ["trial_id", "status"])

    op.create_table(
        "trial_alert_attributions",
        sa.Column("trial_cycle_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("first_successful_delivery_id", sa.Uuid(), nullable=True),
        sa.Column("qualification_status", sa.String(length=32), nullable=False),
        sa.Column("qualification_reason", sa.String(length=120), nullable=False),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_trial_alert_attributions_alert_id_alerts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["first_successful_delivery_id"],
            ["alert_deliveries.id"],
            name=op.f(
                "fk_trial_alert_attributions_first_successful_delivery_id_alert_deliveries"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["trial_cycle_id"],
            ["trial_cycles.id"],
            name=op.f("fk_trial_alert_attributions_trial_cycle_id_trial_cycles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_alert_attributions")),
        sa.UniqueConstraint("trial_cycle_id", "alert_id", name="uq_trial_cycle_alert"),
    )
    with op.batch_alter_table("trial_alert_attributions") as batch_op:
        batch_op.create_index("ix_trial_alert_status", ["qualification_status", "attributed_at"])


def downgrade() -> None:
    op.drop_table("trial_alert_attributions")
    op.drop_table("trial_cycles")
    with op.batch_alter_table("scan_jobs") as batch_op:
        batch_op.drop_index("ix_scan_job_worker_heartbeat")
        batch_op.drop_index("ix_scan_job_status_retry")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("worker_id")

"""Add immutable on-demand Scanner run records.

Revision ID: b7f42a8d9c11
Revises: a2e8f7c31d90
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7f42a8d9c11"
down_revision: str | None = "a2e8f7c31d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "on_demand_scan_runs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("usage_record_id", sa.Uuid(), nullable=True),
        sa.Column("sharia_universe_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("draft_hash", sa.String(length=64), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quota_metric", sa.String(length=80), nullable=False),
        sa.Column(
            "quota_reserved",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("sharia_universe_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "candle_snapshot_manifest",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("candle_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("safe_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sharia_universe_snapshot_id"],
            ["sharia_universe_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["usage_record_id"],
            ["usage_records.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_on_demand_scan_run_user_key",
        ),
    )
    op.create_index(
        "ix_on_demand_scan_run_user_created",
        "on_demand_scan_runs",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_on_demand_scan_run_status_started",
        "on_demand_scan_runs",
        ["status", "started_at"],
        unique=False,
    )
    op.create_table(
        "on_demand_scan_market_records",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("exchange", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("completion_score", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "result_payload",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["on_demand_scan_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_on_demand_scan_market_sequence",
        ),
    )
    op.create_index(
        "ix_on_demand_scan_market_category",
        "on_demand_scan_market_records",
        ["run_id", "category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_on_demand_scan_market_category",
        table_name="on_demand_scan_market_records",
    )
    op.drop_table("on_demand_scan_market_records")
    op.drop_index(
        "ix_on_demand_scan_run_status_started",
        table_name="on_demand_scan_runs",
    )
    op.drop_index(
        "ix_on_demand_scan_run_user_created",
        table_name="on_demand_scan_runs",
    )
    op.drop_table("on_demand_scan_runs")

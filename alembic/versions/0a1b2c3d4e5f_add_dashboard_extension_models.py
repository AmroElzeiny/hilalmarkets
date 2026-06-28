"""add dashboard extension models

Revision ID: 0a1b2c3d4e5f
Revises: e6f7a8b9c0d1
Create Date: 2026-06-23 10:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0a1b2c3d4e5f"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_templates",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_strategy_id", sa.Uuid(), nullable=True),
        sa.Column("source_strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("is_private", sa.Boolean(), nullable=False),
        sa.Column("shared_scope", sa.String(length=40), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_templates_user_id", "strategy_templates", ["user_id"])
    op.create_index(
        "ix_strategy_template_user_category", "strategy_templates", ["user_id", "category"]
    )
    op.create_index(
        "ix_strategy_template_user_archived", "strategy_templates", ["user_id", "archived_at"]
    )

    op.create_table(
        "setup_replay_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("exchange", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("approximate_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_before_minutes", sa.Integer(), nullable=False),
        sa.Column("window_after_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_setup_replay_jobs_user_id", "setup_replay_jobs", ["user_id"])
    op.create_index(
        "ix_replay_user_status_created", "setup_replay_jobs", ["user_id", "status", "created_at"]
    )
    op.create_index(
        "ix_replay_strategy_requested", "setup_replay_jobs", ["strategy_id", "requested_at"]
    )

    op.create_table(
        "backtest_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("exchange", sa.String(length=40), nullable=False),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("started_at_range", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at_range", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_jobs_user_id", "backtest_jobs", ["user_id"])
    op.create_index(
        "ix_backtest_user_status_created", "backtest_jobs", ["user_id", "status", "created_at"]
    )
    op.create_index("ix_backtest_strategy_created", "backtest_jobs", ["strategy_id", "created_at"])

    op.create_table(
        "chart_snapshots",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.String(length=80), nullable=False),
        sa.Column("exchange", sa.String(length=40), nullable=False),
        sa.Column("symbol", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("image_url", sa.String(length=1000), nullable=True),
        sa.Column("chart_config", sa.JSON(), nullable=False),
        sa.Column("proof_reference", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chart_snapshots_user_id", "chart_snapshots", ["user_id"])
    op.create_index(
        "ix_chart_snapshot_user_created", "chart_snapshots", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_chart_snapshot_subject", "chart_snapshots", ["subject_type", "subject_id"]
    )

    op.create_table(
        "user_export_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("export_type", sa.String(length=40), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("file_url", sa.String(length=1000), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_export_jobs_user_id", "user_export_jobs", ["user_id"])
    op.create_index(
        "ix_user_export_status_created", "user_export_jobs", ["user_id", "status", "created_at"]
    )

    op.create_table(
        "integration_test_results",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("integration", sa.String(length=40), nullable=False),
        sa.Column("connection_id", sa.String(length=80), nullable=True),
        sa.Column("destination", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integration_test_results_user_id", "integration_test_results", ["user_id"]
    )
    op.create_index(
        "ix_integration_test_user_created",
        "integration_test_results",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_integration_test_connection",
        "integration_test_results",
        ["integration", "connection_id"],
    )

    op.create_table(
        "setup_replay_results",
        sa.Column("replay_job_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("timeline_points", sa.JSON(), nullable=False),
        sa.Column("candle_proofs", sa.JSON(), nullable=False),
        sa.Column("suggested_adjustments", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["replay_job_id"], ["setup_replay_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_replay_result_job", "setup_replay_results", ["replay_job_id"])

    op.create_table(
        "backtest_results",
        sa.Column("backtest_job_id", sa.Uuid(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("equity_curve", sa.JSON(), nullable=False),
        sa.Column("setup_results", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["backtest_job_id"], ["backtest_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_result_job", "backtest_results", ["backtest_job_id"])

    op.create_table(
        "support_ticket_messages",
        sa.Column("support_request_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("author_type", sa.String(length=30), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("internal", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["support_request_id"], ["support_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_support_message_ticket_created",
        "support_ticket_messages",
        ["support_request_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_message_ticket_created", table_name="support_ticket_messages")
    op.drop_table("support_ticket_messages")
    op.drop_index("ix_backtest_result_job", table_name="backtest_results")
    op.drop_table("backtest_results")
    op.drop_index("ix_replay_result_job", table_name="setup_replay_results")
    op.drop_table("setup_replay_results")
    op.drop_index("ix_integration_test_connection", table_name="integration_test_results")
    op.drop_index("ix_integration_test_user_created", table_name="integration_test_results")
    op.drop_index("ix_integration_test_results_user_id", table_name="integration_test_results")
    op.drop_table("integration_test_results")
    op.drop_index("ix_user_export_status_created", table_name="user_export_jobs")
    op.drop_index("ix_user_export_jobs_user_id", table_name="user_export_jobs")
    op.drop_table("user_export_jobs")
    op.drop_index("ix_chart_snapshot_subject", table_name="chart_snapshots")
    op.drop_index("ix_chart_snapshot_user_created", table_name="chart_snapshots")
    op.drop_index("ix_chart_snapshots_user_id", table_name="chart_snapshots")
    op.drop_table("chart_snapshots")
    op.drop_index("ix_backtest_strategy_created", table_name="backtest_jobs")
    op.drop_index("ix_backtest_user_status_created", table_name="backtest_jobs")
    op.drop_index("ix_backtest_jobs_user_id", table_name="backtest_jobs")
    op.drop_table("backtest_jobs")
    op.drop_index("ix_replay_strategy_requested", table_name="setup_replay_jobs")
    op.drop_index("ix_replay_user_status_created", table_name="setup_replay_jobs")
    op.drop_index("ix_setup_replay_jobs_user_id", table_name="setup_replay_jobs")
    op.drop_table("setup_replay_jobs")
    op.drop_index("ix_strategy_template_user_archived", table_name="strategy_templates")
    op.drop_index("ix_strategy_template_user_category", table_name="strategy_templates")
    op.drop_index("ix_strategy_templates_user_id", table_name="strategy_templates")
    op.drop_table("strategy_templates")

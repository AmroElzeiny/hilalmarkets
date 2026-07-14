"""add setup observability read models

Revision ID: d0e1f2a3b4c5
Revises: cf3a4b5c6d7e
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "cf3a4b5c6d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_evaluation_cycles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("scan_job_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(120)),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("next_expected_at", sa.DateTime(timezone=True)),
        sa.Column("symbols_expected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("symbols_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_candles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_candles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_limit_incidents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delayed_evaluations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scanner_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_job_id"], ["scan_jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("scan_job_id", name="uq_monitor_cycle_scan_job"),
    )
    op.create_index("ix_monitor_cycle_user_started", "monitor_evaluation_cycles", ["user_id", "started_at"])
    op.create_index("ix_monitor_cycle_strategy_started", "monitor_evaluation_cycles", ["strategy_id", "started_at"])
    op.create_index("ix_monitor_cycle_status_heartbeat", "monitor_evaluation_cycles", ["status", "heartbeat_at"])

    op.create_table(
        "candidate_readiness_snapshots",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("setup_instance_id", sa.Uuid()),
        sa.Column("scan_result_id", sa.Uuid(), nullable=False),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("lifecycle_state", sa.String(40), nullable=False),
        sa.Column("stage_rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("required_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("optional_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("optional_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocker_key", sa.String(100)),
        sa.Column("blocker_label", sa.String(240)),
        sa.Column("blocker_outcome", sa.String(32)),
        sa.Column("blocker_actual", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("blocker_required", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("blocker_distance", sa.Numeric(18, 8)),
        sa.Column("blocker_unit", sa.String(32)),
        sa.Column("most_recent_change", sa.String(300), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_freshness_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_health", sa.String(32), nullable=False, server_default="healthy"),
        sa.Column("next_candle_close_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("notification_status", sa.String(32), nullable=False, server_default="not_attempted"),
        sa.Column("condition_tree", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("latest_values", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["setup_instance_id"], ["setup_instances.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scan_result_id"], ["scan_results.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("strategy_version_id", "exchange", "symbol", "timeframe", "direction", name="uq_candidate_readiness_market"),
    )
    op.create_index("ix_candidate_readiness_user_state", "candidate_readiness_snapshots", ["user_id", "lifecycle_state"])
    op.create_index("ix_candidate_readiness_strategy_updated", "candidate_readiness_snapshots", ["strategy_id", "updated_at"])
    op.create_index("ix_candidate_readiness_rank_updated", "candidate_readiness_snapshots", ["stage_rank", "updated_at"])
    op.create_index("ix_candidate_readiness_data_health", "candidate_readiness_snapshots", ["data_health", "last_evaluated_at"])

    op.create_table(
        "monitor_health_summaries",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("technical_status", sa.String(32), nullable=False),
        sa.Column("strategy_status", sa.String(32), nullable=False),
        sa.Column("technical_causes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("strategy_causes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("strategy_version_id", name="uq_monitor_health_version"),
    )
    op.create_index("ix_monitor_health_user_calculated", "monitor_health_summaries", ["user_id", "calculated_at"])
    op.create_index("ix_monitor_health_strategy_calculated", "monitor_health_summaries", ["strategy_id", "calculated_at"])

    op.create_table(
        "condition_observability_aggregates",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_condition_id", sa.Uuid()),
        sa.Column("condition_key", sa.String(100), nullable=False),
        sa.Column("condition_label", sa.String(240), nullable=False),
        sa.Column("rule_role", sa.String(40), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("timeframe", sa.String(16)),
        sa.Column("evaluation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pass_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unavailable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_blocker_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("near_miss_blocker_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalidation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("average_actual", sa.Numeric(30, 10)),
        sa.Column("median_actual_when_blocked", sa.Numeric(30, 10)),
        sa.Column("average_required", sa.Numeric(30, 10)),
        sa.Column("average_distance", sa.Numeric(30, 10)),
        sa.Column("co_occurrence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("previous_version_delta", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sample_status", sa.String(24), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_condition_id"], ["strategy_conditions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("strategy_version_id", "condition_key", "window_started_at", "window_ended_at", name="uq_condition_observability_window"),
    )
    op.create_index("ix_condition_observability_user_window", "condition_observability_aggregates", ["user_id", "window_ended_at"])
    op.create_index("ix_condition_observability_version_blocker", "condition_observability_aggregates", ["strategy_version_id", "final_blocker_count"])

    op.create_table(
        "observability_explanations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("setup_instance_id", sa.Uuid()),
        sa.Column("explanation_type", sa.String(40), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("grounded_payload", sa.JSON(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["setup_instance_id"], ["setup_instances.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_observability_explanation_user_created", "observability_explanations", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("observability_explanations")
    op.drop_table("condition_observability_aggregates")
    op.drop_table("monitor_health_summaries")
    op.drop_table("candidate_readiness_snapshots")
    op.drop_table("monitor_evaluation_cycles")

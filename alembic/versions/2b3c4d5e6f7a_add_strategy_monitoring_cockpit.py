"""add strategy monitoring cockpit

Revision ID: 2b3c4d5e6f7a
Revises: 1a2b3c4d5e6f
Create Date: 2026-06-25 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b3c4d5e6f7a"
down_revision: str | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "edge_health_snapshots",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("score", sa.Numeric(6, 3), nullable=False),
        sa.Column("grade", sa.String(12), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("main_issue", sa.String(500), nullable=True),
        sa.Column("suggested_action", sa.String(500), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_edge_health_strategy_calculated",
        "edge_health_snapshots",
        ["strategy_id", "calculated_at"],
    )
    op.create_index(
        "ix_edge_health_user_calculated",
        "edge_health_snapshots",
        ["user_id", "calculated_at"],
    )
    op.create_index("ix_edge_health_snapshots_user_id", "edge_health_snapshots", ["user_id"])

    op.create_table(
        "condition_bottleneck_aggregates",
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("condition_key", sa.String(100), nullable=False),
        sa.Column("condition_label", sa.String(240), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("unavailable_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("blocking_count", sa.Integer(), nullable=False),
        sa.Column("pass_rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("blocking_rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bottleneck_version_calculated",
        "condition_bottleneck_aggregates",
        ["strategy_version_id", "calculated_at"],
    )
    op.create_index(
        "ix_bottleneck_strategy_impact",
        "condition_bottleneck_aggregates",
        ["strategy_id", "blocking_rate"],
    )

    op.create_table(
        "missed_move_analyses",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("replay_job_id", sa.Uuid(), nullable=True),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("approximate_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_move_threshold", sa.Numeric(10, 4), nullable=True),
        sa.Column("user_question", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["replay_job_id"], ["setup_replay_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_missed_move_user_status",
        "missed_move_analyses",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "ix_missed_move_strategy_time",
        "missed_move_analyses",
        ["strategy_id", "approximate_time"],
    )
    op.create_index("ix_missed_move_analyses_user_id", "missed_move_analyses", ["user_id"])

    op.create_table(
        "strategy_experiments",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("version_ids", sa.JSON(), nullable=False),
        sa.Column("comparison", sa.JSON(), nullable=False),
        sa.Column("promoted_version_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["promoted_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiment_strategy_status",
        "strategy_experiments",
        ["strategy_id", "status"],
    )
    op.create_index(
        "ix_experiment_user_created",
        "strategy_experiments",
        ["user_id", "created_at"],
    )
    op.create_index("ix_strategy_experiments_user_id", "strategy_experiments", ["user_id"])

    op.create_table(
        "alert_frequency_forecasts",
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("estimated_min_per_week", sa.Numeric(10, 3), nullable=False),
        sa.Column("estimated_max_per_week", sa.Numeric(10, 3), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_frequency_forecast_strategy_calculated",
        "alert_frequency_forecasts",
        ["strategy_id", "calculated_at"],
    )

    op.create_table(
        "universe_optimization_snapshots",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("included_symbols", sa.JSON(), nullable=False),
        sa.Column("excluded_symbols", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_universe_snapshot_strategy_created",
        "universe_optimization_snapshots",
        ["strategy_id", "created_at"],
    )
    op.create_index(
        "ix_universe_optimization_snapshots_user_id",
        "universe_optimization_snapshots",
        ["user_id"],
    )

    op.create_table(
        "strategy_validation_records",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("critical_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("info_count", sa.Integer(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_validation_strategy_created",
        "strategy_validation_records",
        ["strategy_id", "created_at"],
    )
    op.create_index(
        "ix_validation_schema_hash",
        "strategy_validation_records",
        ["schema_hash"],
    )
    op.create_index(
        "ix_strategy_validation_records_user_id",
        "strategy_validation_records",
        ["user_id"],
    )

    op.create_table(
        "strategy_suggestions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("before_schema", sa.JSON(), nullable=False),
        sa.Column("proposed_schema", sa.JSON(), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=False),
        sa.Column("applied_version_id", sa.Uuid(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["applied_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_suggestion_strategy_status",
        "strategy_suggestions",
        ["strategy_id", "status"],
    )
    op.create_index(
        "ix_suggestion_user_created",
        "strategy_suggestions",
        ["user_id", "created_at"],
    )
    op.create_index("ix_strategy_suggestions_user_id", "strategy_suggestions", ["user_id"])

    op.create_table(
        "user_strategy_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("last_derived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_strategy_preference"),
    )
    op.create_index(
        "ix_user_strategy_preferences_user_id",
        "user_strategy_preferences",
        ["user_id"],
    )

    op.create_table(
        "alert_inbox_items",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.String(40), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("setup_instance_id", sa.Uuid(), nullable=True),
        sa.Column("alert_id", sa.Uuid(), nullable=True),
        sa.Column("symbol", sa.String(40), nullable=True),
        sa.Column("timeframe", sa.String(16), nullable=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("health_score", sa.Numeric(6, 3), nullable=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("proof_reference", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["setup_instance_id"], ["setup_instances.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "item_type",
            "source_type",
            "source_id",
            name="uq_inbox_source",
        ),
    )
    op.create_index(
        "ix_inbox_user_created",
        "alert_inbox_items",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_inbox_user_state",
        "alert_inbox_items",
        ["user_id", "state", "archived_at"],
    )
    op.create_index(
        "ix_inbox_strategy_created",
        "alert_inbox_items",
        ["strategy_id", "created_at"],
    )
    op.create_index("ix_alert_inbox_items_user_id", "alert_inbox_items", ["user_id"])

    op.create_table(
        "strategy_decay_events",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("current", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("suggested_actions", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_decay_strategy_status",
        "strategy_decay_events",
        ["strategy_id", "status", "detected_at"],
    )
    op.create_index(
        "ix_decay_user_detected",
        "strategy_decay_events",
        ["user_id", "detected_at"],
    )
    op.create_index("ix_strategy_decay_events_user_id", "strategy_decay_events", ["user_id"])


def downgrade() -> None:
    op.drop_table("strategy_decay_events")
    op.drop_table("alert_inbox_items")
    op.drop_table("user_strategy_preferences")
    op.drop_table("strategy_suggestions")
    op.drop_table("strategy_validation_records")
    op.drop_table("universe_optimization_snapshots")
    op.drop_table("alert_frequency_forecasts")
    op.drop_table("strategy_experiments")
    op.drop_table("missed_move_analyses")
    op.drop_table("condition_bottleneck_aggregates")
    op.drop_table("edge_health_snapshots")

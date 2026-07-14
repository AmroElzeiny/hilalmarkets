"""add verified strategy monitoring workflow

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    # Batch mode supports the local SQLite database and PostgreSQL deployments.
    with op.batch_alter_table("strategy_versions") as batch_op:
        batch_op.add_column(sa.Column("parent_version_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("restored_from_version_id", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(sa.Column("created_by_user_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("semantic_diff", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("change_summary", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_strategy_version_parent",
            "strategy_versions",
            ["parent_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_strategy_version_restored",
            "strategy_versions",
            ["restored_from_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_strategy_version_creator",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.add_column("alerts", sa.Column("proof_hash", sa.String(64), nullable=True))
    op.add_column(
        "alerts",
        sa.Column("proof_schema_version", sa.String(20), nullable=False, server_default="1.0"),
    )
    op.add_column("alerts", sa.Column("proof_sealed_at", sa.DateTime(timezone=True)))

    op.add_column(
        "strategy_suggestions",
        sa.Column("outcome_evidence", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "strategy_suggestions",
        sa.Column("historical_effect", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "strategy_suggestions",
        sa.Column("confidence", sa.String(20), nullable=False, server_default="low"),
    )
    op.add_column(
        "strategy_suggestions",
        sa.Column("limitations", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "strategy_interpretation_statements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("original_phrase", sa.Text(), nullable=False),
        sa.Column("structured_interpretation", sa.Text(), nullable=False),
        sa.Column("rule_keys", sa.JSON(), nullable=False),
        sa.Column("mechanics", sa.JSON(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("resolution_status", sa.String(24), nullable=False),
        sa.Column("resolution_text", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "strategy_version_id", "position", name="uq_interpretation_statement_position"
        ),
    )
    op.create_index(
        "ix_interpretation_statement_user_version",
        "strategy_interpretation_statements",
        ["user_id", "strategy_version_id"],
    )
    op.create_index(
        "ix_interpretation_statement_status",
        "strategy_interpretation_statements",
        ["strategy_version_id", "status"],
    )

    op.create_table(
        "strategy_test_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("case_type", sa.String(24), nullable=False),
        sa.Column("expected_result", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("evaluation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_strategy_test_case_user_strategy", "strategy_test_cases", ["user_id", "strategy_id"]
    )
    op.create_index(
        "ix_strategy_test_case_active", "strategy_test_cases", ["strategy_id", "active"]
    )

    op.create_table(
        "strategy_test_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("test_case_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expected_result", sa.String(32), nullable=False),
        sa.Column("actual_result", sa.String(32), nullable=False),
        sa.Column("condition_results", sa.JSON(), nullable=False),
        sa.Column("mismatch_reason", sa.Text()),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("data_source", sa.String(80)),
        sa.Column("candle_timestamp", sa.DateTime(timezone=True)),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["test_case_id"], ["strategy_test_cases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "test_case_id", "strategy_version_id", "schema_hash", name="uq_test_case_version_hash"
        ),
    )
    op.create_index(
        "ix_strategy_test_run_version_status",
        "strategy_test_runs",
        ["strategy_version_id", "status"],
    )
    op.create_index(
        "ix_strategy_test_run_case_run", "strategy_test_runs", ["test_case_id", "run_at"]
    )

    op.create_table(
        "strategy_version_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("interpretation_status", sa.String(24), nullable=False),
        sa.Column("tests_status", sa.String(24), nullable=False),
        sa.Column("historical_status", sa.String(24), nullable=False),
        sa.Column("historical_job_id", sa.Uuid()),
        sa.Column("historical_summary", sa.JSON(), nullable=False),
        sa.Column("semantic_diff", sa.JSON(), nullable=False),
        sa.Column("test_effects", sa.JSON(), nullable=False),
        sa.Column("historical_effects", sa.JSON(), nullable=False),
        sa.Column("quality_report", sa.JSON(), nullable=False),
        sa.Column("contract_hash", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["historical_job_id"], ["backtest_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_version_id", name="uq_strategy_version_verification"),
    )
    op.create_index(
        "ix_version_verification_user_updated",
        "strategy_version_verifications",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "forensic_investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid()),
        sa.Column("setup_instance_id", sa.Uuid()),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("timeframe", sa.String(16), nullable=False),
        sa.Column("requested_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("evidence_availability", sa.String(24), nullable=False),
        sa.Column("primary_category", sa.String(40), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("rule_results", sa.JSON(), nullable=False),
        sa.Column("timeline", sa.JSON(), nullable=False),
        sa.Column("system_diagnostics", sa.JSON(), nullable=False),
        sa.Column("delivery_diagnostics", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["setup_instance_id"], ["setup_instances.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_forensic_user_created", "forensic_investigations", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_forensic_strategy_time",
        "forensic_investigations",
        ["strategy_id", "requested_time"],
    )

    op.create_table(
        "outcome_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("setup_instance_id", sa.Uuid()),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("evaluation_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("classification", sa.String(24)),
        sa.Column("classification_rules", sa.JSON(), nullable=False),
        sa.Column("outcome_metrics", sa.JSON(), nullable=False),
        sa.Column("price_path", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["setup_instance_id"], ["setup_instances.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id", "horizon_minutes", name="uq_outcome_alert_horizon"),
    )
    op.create_index("ix_outcome_user_created", "outcome_reviews", ["user_id", "created_at"])
    op.create_index(
        "ix_outcome_strategy_classification",
        "outcome_reviews",
        ["strategy_id", "classification"],
    )


def downgrade() -> None:
    op.drop_table("outcome_reviews")
    op.drop_table("forensic_investigations")
    op.drop_table("strategy_version_verifications")
    op.drop_table("strategy_test_runs")
    op.drop_table("strategy_test_cases")
    op.drop_table("strategy_interpretation_statements")
    for name in ("limitations", "confidence", "historical_effect", "outcome_evidence"):
        op.drop_column("strategy_suggestions", name)
    for name in ("proof_sealed_at", "proof_schema_version", "proof_hash"):
        op.drop_column("alerts", name)
    with op.batch_alter_table("strategy_versions") as batch_op:
        batch_op.drop_constraint("fk_strategy_version_creator", type_="foreignkey")
        batch_op.drop_constraint("fk_strategy_version_restored", type_="foreignkey")
        batch_op.drop_constraint("fk_strategy_version_parent", type_="foreignkey")
        for name in (
            "change_summary",
            "semantic_diff",
            "created_by_user_id",
            "restored_from_version_id",
            "parent_version_id",
        ):
            batch_op.drop_column(name)

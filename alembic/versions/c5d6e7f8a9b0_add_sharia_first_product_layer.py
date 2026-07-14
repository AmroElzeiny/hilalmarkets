"""add Sharia-first screening, governance, and universe evidence

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-14
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _uuid(value: object) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


def upgrade() -> None:
    op.create_table(
        "sharia_methodologies",
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum("sharia_methodology_status", "draft", "active", "archived"),
            nullable=False,
        ),
        sa.Column("governing_body", sa.String(240)),
        sa.Column("reviewer_group", sa.String(240)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("rules_json", sa.JSON(), nullable=False),
        sa.Column("evidence_requirements_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", "version", name="uq_sharia_methodology_code_version"),
    )
    op.create_index(
        "ix_sharia_methodology_status_effective",
        "sharia_methodologies",
        ["status", "effective_from"],
    )

    op.create_table(
        "asset_sharia_assessments",
        sa.Column("canonical_asset", sa.String(32), nullable=False),
        sa.Column("asset_name", sa.String(160)),
        sa.Column("methodology_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "sharia_asset_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("qualifications", sa.JSON(), nullable=False),
        sa.Column("exclusion_reasons", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("reviewed_by", sa.String(240), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("supersedes_assessment_id", sa.Uuid()),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["methodology_id"], ["sharia_methodologies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["supersedes_assessment_id"],
            ["asset_sharia_assessments.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_sharia_assessment_asset_methodology_valid",
        "asset_sharia_assessments",
        ["canonical_asset", "methodology_id", "valid_from"],
    )
    op.create_index(
        "ix_sharia_assessment_status_reviewed",
        "asset_sharia_assessments",
        ["status", "reviewed_at"],
    )

    op.create_table(
        "sharia_evidence_sources",
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(60), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("publisher", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_category", sa.String(80), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["asset_sharia_assessments.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("assessment_id", "source_hash", name="uq_sharia_evidence_hash"),
    )
    op.create_index(
        "ix_sharia_evidence_assessment_category",
        "sharia_evidence_sources",
        ["assessment_id", "evidence_category"],
    )

    op.create_table(
        "approved_watchlists",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "name", name="uq_approved_watchlist_user_name"),
    )
    op.create_index(
        "ix_approved_watchlist_user_default",
        "approved_watchlists",
        ["user_id", "is_default"],
    )
    op.create_table(
        "approved_watchlist_assets",
        sa.Column("watchlist_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_asset", sa.String(32), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["watchlist_id"], ["approved_watchlists.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("watchlist_id", "canonical_asset", name="uq_watchlist_asset"),
    )
    op.create_index(
        "ix_watchlist_asset_asset", "approved_watchlist_assets", ["canonical_asset"]
    )

    op.create_table(
        "compliance_changes",
        sa.Column("canonical_asset", sa.String(32), nullable=False),
        sa.Column("change_type", sa.String(100), nullable=False),
        sa.Column(
            "severity",
            _enum(
                "compliance_change_severity",
                "informational",
                "review_required",
                "critical",
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("source_reference", sa.String(1000)),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("structured_change", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column(
            "status",
            _enum(
                "compliance_change_status",
                "detected",
                "triaged",
                "awaiting_review",
                "approved",
                "dismissed",
            ),
            nullable=False,
        ),
        sa.Column("detection_method", sa.String(80), nullable=False),
        sa.Column("confidence_label", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sharia_evidence_sources.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_compliance_change_idempotency"),
    )
    op.create_index(
        "ix_compliance_change_status_detected",
        "compliance_changes",
        ["status", "detected_at"],
    )
    op.create_index(
        "ix_compliance_change_asset_detected",
        "compliance_changes",
        ["canonical_asset", "detected_at"],
    )

    op.create_table(
        "asset_sharia_status_history",
        sa.Column("canonical_asset", sa.String(32), nullable=False),
        sa.Column("methodology_id", sa.Uuid(), nullable=False),
        sa.Column(
            "previous_status",
            _enum(
                "sharia_previous_asset_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
        ),
        sa.Column(
            "new_status",
            _enum(
                "sharia_new_asset_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=False),
        sa.Column("triggering_change_id", sa.Uuid()),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid()),
        sa.Column("approved_by", sa.String(240), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["methodology_id"], ["sharia_methodologies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["triggering_change_id"], ["compliance_changes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["asset_sharia_assessments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_sharia_status_history_asset_changed",
        "asset_sharia_status_history",
        ["canonical_asset", "methodology_id", "changed_at"],
    )

    op.create_table(
        "sharia_universe_snapshots",
        sa.Column("user_id", sa.Uuid()),
        sa.Column("strategy_version_id", sa.Uuid()),
        sa.Column("methodology_id", sa.Uuid(), nullable=False),
        sa.Column("methodology_code", sa.String(80), nullable=False),
        sa.Column("methodology_version", sa.String(32), nullable=False),
        sa.Column(
            "universe_mode",
            _enum(
                "sharia_universe_snapshot_mode",
                "eligible_market",
                "approved_watchlist",
                "explicit_assets",
            ),
            nullable=False,
        ),
        sa.Column("exchange", sa.String(40), nullable=False),
        sa.Column("quote_currencies", sa.JSON(), nullable=False),
        sa.Column("allowed_statuses", sa.JSON(), nullable=False),
        sa.Column("qualification_policy", sa.String(40), nullable=False),
        sa.Column("disputed_asset_policy", sa.String(40), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("considered_symbols", sa.JSON(), nullable=False),
        sa.Column("included_symbols", sa.JSON(), nullable=False),
        sa.Column("excluded_assets", sa.JSON(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("invalidation_reason", sa.String(160)),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["methodology_id"], ["sharia_methodologies.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_sharia_universe_methodology_resolved",
        "sharia_universe_snapshots",
        ["methodology_id", "resolved_at"],
    )
    op.create_index(
        "ix_sharia_universe_user_resolved",
        "sharia_universe_snapshots",
        ["user_id", "resolved_at"],
    )
    op.create_index(
        "ix_sharia_universe_hash", "sharia_universe_snapshots", ["snapshot_hash"]
    )

    op.create_table(
        "monitor_sharia_asset_states",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("methodology_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_asset", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("last_assessment_id", sa.Uuid()),
        sa.Column(
            "sharia_status",
            _enum(
                "monitor_sharia_asset_screening_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            _enum("monitor_sharia_asset_state", "active", "paused", "removed"),
            nullable=False,
        ),
        sa.Column(
            "policy_decision",
            _enum(
                "monitor_sharia_policy_decision",
                "included",
                "excluded_status",
                "missing_assessment",
                "methodology_unavailable",
                "not_in_watchlist",
                "not_explicitly_selected",
                "exchange_filtered",
                "paused_for_compliance",
            ),
            nullable=False,
        ),
        sa.Column("policy_reason", sa.String(300), nullable=False),
        sa.Column("universe_snapshot_id", sa.Uuid()),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["methodology_id"], ["sharia_methodologies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["last_assessment_id"], ["asset_sharia_assessments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["universe_snapshot_id"], ["sharia_universe_snapshots.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "strategy_version_id", "symbol", name="uq_monitor_sharia_asset"
        ),
    )
    op.create_index(
        "ix_monitor_sharia_state_strategy_status",
        "monitor_sharia_asset_states",
        ["strategy_id", "state"],
    )
    op.create_index(
        "ix_monitor_sharia_state_asset",
        "monitor_sharia_asset_states",
        ["canonical_asset", "methodology_id"],
    )

    op.create_table(
        "compliance_reviews",
        sa.Column("compliance_change_id", sa.Uuid(), nullable=False),
        sa.Column("methodology_id", sa.Uuid(), nullable=False),
        sa.Column(
            "previous_status",
            _enum(
                "compliance_review_previous_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
        ),
        sa.Column(
            "proposed_status",
            _enum(
                "compliance_review_proposed_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
        ),
        sa.Column(
            "final_status",
            _enum(
                "compliance_review_final_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
        ),
        sa.Column(
            "decision",
            _enum(
                "compliance_review_decision",
                "approved",
                "more_evidence_required",
                "dismissed",
            ),
            nullable=False,
        ),
        sa.Column("reviewer_id", sa.Uuid()),
        sa.Column("reviewer_identity", sa.String(240), nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["compliance_change_id"], ["compliance_changes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["methodology_id"], ["sharia_methodologies.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_compliance_review_change_reviewed",
        "compliance_reviews",
        ["compliance_change_id", "reviewed_at"],
    )

    op.create_table(
        "compliance_drift_notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("compliance_change_id", sa.Uuid()),
        sa.Column("strategy_id", sa.Uuid()),
        sa.Column("alert_id", sa.Uuid()),
        sa.Column("canonical_asset", sa.String(32), nullable=False),
        sa.Column(
            "previous_status",
            _enum(
                "compliance_drift_previous_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
        ),
        sa.Column(
            "new_status",
            _enum(
                "compliance_drift_new_status",
                "eligible",
                "eligible_with_qualifications",
                "disputed",
                "under_review",
                "excluded",
                "insufficient_information",
            ),
            nullable=False,
        ),
        sa.Column(
            "behavior",
            _enum(
                "compliance_drift_behavior",
                "pause_asset",
                "remove_asset",
                "pause_monitor_if_any_asset_changes",
                "notify_only",
            ),
            nullable=False,
        ),
        sa.Column("impact", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("digest_processed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["compliance_change_id"], ["compliance_changes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_compliance_drift_idempotency"),
    )
    op.create_index(
        "ix_compliance_drift_user_created",
        "compliance_drift_notifications",
        ["user_id", "created_at"],
    )

    op.create_table(
        "sharia_monitor_migration_records",
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid()),
        sa.Column("prior_status", sa.String(32), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("strategy_id", name="uq_sharia_monitor_migration_strategy"),
    )
    op.create_index(
        "ix_sharia_monitor_migration_action",
        "sharia_monitor_migration_records",
        ["action", "created_at"],
    )

    with op.batch_alter_table("strategy_universes") as batch_op:
        batch_op.add_column(
            sa.Column(
                "universe_mode",
                _enum(
                    "strategy_sharia_universe_mode",
                    "eligible_market",
                    "approved_watchlist",
                    "explicit_assets",
                ),
            )
        )
        batch_op.add_column(sa.Column("methodology_id", sa.Uuid()))
        batch_op.add_column(
            sa.Column("allowed_sharia_statuses", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("qualification_policy", sa.String(40)))
        batch_op.add_column(sa.Column("disputed_asset_policy", sa.String(40)))
        batch_op.add_column(
            sa.Column(
                "compliance_change_behavior",
                _enum(
                    "strategy_compliance_change_behavior",
                    "pause_asset",
                    "remove_asset",
                    "pause_monitor_if_any_asset_changes",
                    "notify_only",
                ),
            )
        )
        batch_op.add_column(sa.Column("approved_watchlist_id", sa.Uuid()))
        batch_op.add_column(sa.Column("universe_snapshot_version", sa.Integer()))
        batch_op.add_column(sa.Column("universe_snapshot_hash", sa.String(64)))
        batch_op.add_column(sa.Column("universe_last_resolved_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column(
                "sharia_policy_ready",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_foreign_key(
            "fk_strategy_universes_methodology_id_sharia_methodologies",
            "sharia_methodologies",
            ["methodology_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_strategy_universes_approved_watchlist_id_approved_watchlists",
            "approved_watchlists",
            ["approved_watchlist_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("scan_results") as batch_op:
        batch_op.add_column(sa.Column("sharia_methodology_id", sa.Uuid()))
        batch_op.add_column(sa.Column("sharia_methodology_version", sa.String(32)))
        batch_op.add_column(sa.Column("sharia_status_at_scan", sa.String(40)))
        batch_op.add_column(sa.Column("sharia_assessment_id", sa.Uuid()))
        batch_op.add_column(sa.Column("sharia_universe_snapshot_id", sa.Uuid()))
        batch_op.add_column(sa.Column("sharia_policy_decision", sa.String(60)))
        batch_op.add_column(sa.Column("sharia_policy_reason", sa.String(300)))
        batch_op.create_foreign_key(
            "fk_scan_results_sharia_methodology_id_sharia_methodologies",
            "sharia_methodologies",
            ["sharia_methodology_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_scan_results_sharia_assessment_id_asset_sharia_assessments",
            "asset_sharia_assessments",
            ["sharia_assessment_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_scan_results_sharia_universe_snapshot",
            "sharia_universe_snapshots",
            ["sharia_universe_snapshot_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("setup_instances") as batch_op:
        batch_op.add_column(sa.Column("sharia_methodology_id", sa.Uuid()))
        batch_op.add_column(sa.Column("sharia_methodology_version", sa.String(32)))
        batch_op.add_column(sa.Column("sharia_status_at_detection", sa.String(40)))
        batch_op.add_column(sa.Column("sharia_assessment_id", sa.Uuid()))
        batch_op.create_foreign_key(
            "fk_setup_instances_sharia_methodology_id_sharia_methodologies",
            "sharia_methodologies",
            ["sharia_methodology_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_setup_instances_sharia_assessment",
            "asset_sharia_assessments",
            ["sharia_assessment_id"],
            ["id"],
            ondelete="SET NULL",
        )

    now = datetime.now(UTC)
    methodology_id = uuid4()
    methodology_table = sa.table(
        "sharia_methodologies",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("version", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("governing_body", sa.String()),
        sa.column("reviewer_group", sa.String()),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("rules_json", sa.JSON()),
        sa.column("evidence_requirements_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        methodology_table,
        [
            {
                "id": methodology_id,
                "code": "TRACEDGE_DEV_TEST_V1",
                "name": "Development/Test Methodology - Not a religious ruling",
                "version": "0.1-test",
                "description": (
                    "Schema and workflow seed only. It contains no approved asset conclusions, "
                    "cannot power production scans, and requires qualified human governance."
                ),
                "status": "draft",
                "governing_body": None,
                "reviewer_group": None,
                "published_at": None,
                "effective_from": None,
                "rules_json": {"development_only": True, "executable": False},
                "evidence_requirements_json": {
                    "development_only": True,
                    "qualified_review_required": True,
                },
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    connection = op.get_bind()
    strategy_rows = connection.execute(
        sa.text(
            "SELECT id, active_version_id, status FROM strategies WHERE status = 'active'"
        )
    ).mappings()
    migration_rows = []
    for row in strategy_rows:
        migration_rows.append(
            {
                "id": uuid4(),
                "strategy_id": _uuid(row["id"]),
                "strategy_version_id": _uuid(row["active_version_id"]),
                "prior_status": row["status"],
                "action": "paused_pending_approved_methodology",
                "reason": (
                    "Existing active monitor paused fail-closed. Assign an approved active "
                    "methodology, resolve its screened universe, preview it, and explicitly "
                    "resume it after deployment."
                ),
                "created_at": now,
            }
        )
    connection.execute(
        sa.text(
            "UPDATE strategy_universes SET universe_mode = 'eligible_market', "
            "allowed_sharia_statuses = :statuses, qualification_policy = "
            "'include_with_warning', disputed_asset_policy = 'exclude', "
            "compliance_change_behavior = 'pause_asset', sharia_policy_ready = false"
        ),
        {"statuses": '["eligible", "eligible_with_qualifications"]'},
    )
    connection.execute(
        sa.text(
            "UPDATE strategies SET status = 'paused', paused_at = :paused_at "
            "WHERE status = 'active'"
        ),
        {"paused_at": now},
    )
    if migration_rows:
        migration_table = sa.table(
            "sharia_monitor_migration_records",
            sa.column("id", sa.Uuid()),
            sa.column("strategy_id", sa.Uuid()),
            sa.column("strategy_version_id", sa.Uuid()),
            sa.column("prior_status", sa.String()),
            sa.column("action", sa.String()),
            sa.column("reason", sa.Text()),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(migration_table, migration_rows)


def downgrade() -> None:
    with op.batch_alter_table("setup_instances") as batch_op:
        batch_op.drop_constraint(
            "fk_setup_instances_sharia_assessment",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_setup_instances_sharia_methodology_id_sharia_methodologies",
            type_="foreignkey",
        )
        batch_op.drop_column("sharia_assessment_id")
        batch_op.drop_column("sharia_status_at_detection")
        batch_op.drop_column("sharia_methodology_version")
        batch_op.drop_column("sharia_methodology_id")
    with op.batch_alter_table("scan_results") as batch_op:
        batch_op.drop_constraint(
            "fk_scan_results_sharia_universe_snapshot",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_scan_results_sharia_assessment_id_asset_sharia_assessments",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_scan_results_sharia_methodology_id_sharia_methodologies",
            type_="foreignkey",
        )
        batch_op.drop_column("sharia_policy_reason")
        batch_op.drop_column("sharia_policy_decision")
        batch_op.drop_column("sharia_universe_snapshot_id")
        batch_op.drop_column("sharia_assessment_id")
        batch_op.drop_column("sharia_status_at_scan")
        batch_op.drop_column("sharia_methodology_version")
        batch_op.drop_column("sharia_methodology_id")
    with op.batch_alter_table("strategy_universes") as batch_op:
        batch_op.drop_constraint(
            "fk_strategy_universes_approved_watchlist_id_approved_watchlists",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_strategy_universes_methodology_id_sharia_methodologies",
            type_="foreignkey",
        )
        batch_op.drop_column("sharia_policy_ready")
        batch_op.drop_column("universe_last_resolved_at")
        batch_op.drop_column("universe_snapshot_hash")
        batch_op.drop_column("universe_snapshot_version")
        batch_op.drop_column("approved_watchlist_id")
        batch_op.drop_column("compliance_change_behavior")
        batch_op.drop_column("disputed_asset_policy")
        batch_op.drop_column("qualification_policy")
        batch_op.drop_column("allowed_sharia_statuses")
        batch_op.drop_column("methodology_id")
        batch_op.drop_column("universe_mode")

    op.drop_table("sharia_monitor_migration_records")
    op.drop_table("compliance_drift_notifications")
    op.drop_table("compliance_reviews")
    op.drop_table("monitor_sharia_asset_states")
    op.drop_table("sharia_universe_snapshots")
    op.drop_table("asset_sharia_status_history")
    op.drop_table("compliance_changes")
    op.drop_table("approved_watchlist_assets")
    op.drop_table("approved_watchlists")
    op.drop_table("sharia_evidence_sources")
    op.drop_table("asset_sharia_assessments")
    op.drop_table("sharia_methodologies")

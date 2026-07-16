"""add passport governance checkout and payment email records

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-16 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sharia_review_cases") as batch_op:
        batch_op.add_column(sa.Column("assigned_reviewer_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("source_freshness_deadline", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_sharia_review_cases_assigned_reviewer_id_users",
            "users",
            ["assigned_reviewer_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_sharia_review_cases_due_at", ["due_at"], unique=False)

    with op.batch_alter_table("sharia_review_decisions") as batch_op:
        batch_op.add_column(
            sa.Column("criterion_decisions", sa.JSON(), server_default="[]", nullable=False)
        )
        batch_op.add_column(
            sa.Column("qualifications", sa.JSON(), server_default="[]", nullable=False)
        )
        batch_op.add_column(
            sa.Column("acknowledged_gaps", sa.JSON(), server_default="[]", nullable=False)
        )
        batch_op.add_column(sa.Column("ai_analysis_snapshot_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "actor_role", sa.String(length=40), server_default="REVIEWER", nullable=False
            )
        )
        batch_op.add_column(sa.Column("application_version", sa.String(length=80)))
        batch_op.add_column(
            sa.Column("security_metadata", sa.JSON(), server_default="{}", nullable=False)
        )
        batch_op.add_column(sa.Column("integrity_hash", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_review_decision_ai_analysis_snapshot",
            "sharia_ai_analysis_snapshots",
            ["ai_analysis_snapshot_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_sharia_review_decision_integrity_hash", ["integrity_hash"]
        )

    with op.batch_alter_table("published_asset_assessments") as batch_op:
        batch_op.add_column(sa.Column("supersedes_publication_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_published_asset_supersedes",
            "published_asset_assessments",
            ["supersedes_publication_id"],
            ["id"],
            ondelete="SET NULL",
        )

    for table_name in ("scan_results", "setup_instances"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("sharia_passport_version_id", sa.Uuid(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table_name}_sharia_passport_version",
                "published_asset_assessments",
                ["sharia_passport_version_id"],
                ["id"],
                ondelete="SET NULL",
            )
    with op.batch_alter_table("setup_instances") as batch_op:
        batch_op.add_column(sa.Column("sharia_universe_snapshot_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("sharia_policy_decision", sa.String(length=60)))
        batch_op.create_foreign_key(
            "fk_setup_instances_sharia_universe_snapshot",
            "sharia_universe_snapshots",
            ["sharia_universe_snapshot_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("alerts") as batch_op:
        batch_op.add_column(sa.Column("sharia_assessment_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("sharia_passport_version_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("sharia_methodology_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("sharia_universe_snapshot_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("sharia_policy_decision", sa.String(length=60)))
        batch_op.create_foreign_key(
            "fk_alerts_sharia_assessment",
            "asset_sharia_assessments",
            ["sharia_assessment_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_alerts_sharia_passport_version",
            "published_asset_assessments",
            ["sharia_passport_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_alerts_sharia_methodology",
            "sharia_methodologies",
            ["sharia_methodology_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_alerts_sharia_universe_snapshot",
            "sharia_universe_snapshots",
            ["sharia_universe_snapshot_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "sharia_governance_role_grants",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role", name="uq_sharia_governance_user_role"),
    )
    op.create_index(
        "ix_sharia_governance_role_active",
        "sharia_governance_role_grants",
        ["role", "revoked_at"],
    )
    op.create_table(
        "sharia_reviewer_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("organization", sa.String(length=240)),
        sa.Column("authorization_role", sa.String(length=160), nullable=False),
        sa.Column("qualifications", sa.JSON(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "sharia_review_assignment_events",
        sa.Column("review_case_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("previous_assignee_id", sa.Uuid()),
        sa.Column("assigned_reviewer_id", sa.Uuid()),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.String(length=20)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_case_id"], ["sharia_review_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_reviewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sharia_assignment_case_created",
        "sharia_review_assignment_events",
        ["review_case_id", "created_at"],
    )
    op.create_table(
        "sharia_passport_problem_reports",
        sa.Column("reporter_user_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_asset_id", sa.Uuid(), nullable=False),
        sa.Column("asset_assessment_id", sa.Uuid()),
        sa.Column("passport_version_id", sa.Uuid()),
        sa.Column("review_case_id", sa.Uuid()),
        sa.Column("report_type", sa.String(length=60), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("resolution", sa.Text()),
        sa.Column("resolved_by_user_id", sa.Uuid()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canonical_asset_id"], ["canonical_assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["asset_assessment_id"],
            ["asset_sharia_assessments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["passport_version_id"],
            ["published_asset_assessments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["review_case_id"],
            ["sharia_review_cases.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_case_id", name="uq_sharia_passport_problem_report_case"
        ),
    )
    op.create_index(
        "ix_sharia_passport_report_state_created",
        "sharia_passport_problem_reports",
        ["state", "created_at"],
    )
    op.create_index(
        "ix_sharia_passport_report_asset",
        "sharia_passport_problem_reports",
        ["canonical_asset_id", "created_at"],
    )
    op.create_table(
        "billing_checkout_attempts",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("billing_cycle", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255)),
        sa.Column("provider_event_id", sa.String(length=255)),
        sa.Column("checkout_url", sa.String(length=2000)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("terms_version", sa.String(length=80), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(length=500)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_billing_checkout_idempotency"),
    )
    op.create_index(
        "ix_billing_checkout_user_status",
        "billing_checkout_attempts",
        ["user_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_billing_checkout_attempts_provider_session_id",
        "billing_checkout_attempts",
        ["provider_session_id"],
    )
    op.create_index(
        "ix_billing_checkout_attempts_provider_event_id",
        "billing_checkout_attempts",
        ["provider_event_id"],
    )
    op.create_table(
        "payment_email_deliveries",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("billing_event_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("plan_code", sa.String(length=50), nullable=False),
        sa.Column("billing_frequency", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("payment_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewal_date", sa.DateTime(timezone=True)),
        sa.Column("receipt_url", sa.String(length=2000)),
        sa.Column("plan_limits", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255)),
        sa.Column("last_error", sa.String(length=500)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["billing_event_id"], ["billing_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_payment_email_event_key"),
    )
    op.create_index(
        "ix_payment_email_due",
        "payment_email_deliveries",
        ["status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_table("payment_email_deliveries")
    op.drop_table("billing_checkout_attempts")
    op.drop_table("sharia_passport_problem_reports")
    op.drop_table("sharia_review_assignment_events")
    op.drop_table("sharia_reviewer_profiles")
    op.drop_table("sharia_governance_role_grants")
    with op.batch_alter_table("alerts") as batch_op:
        batch_op.drop_column("sharia_policy_decision")
        batch_op.drop_column("sharia_universe_snapshot_id")
        batch_op.drop_column("sharia_methodology_id")
        batch_op.drop_column("sharia_passport_version_id")
        batch_op.drop_column("sharia_assessment_id")
    with op.batch_alter_table("setup_instances") as batch_op:
        batch_op.drop_column("sharia_policy_decision")
        batch_op.drop_column("sharia_universe_snapshot_id")
        batch_op.drop_column("sharia_passport_version_id")
    with op.batch_alter_table("scan_results") as batch_op:
        batch_op.drop_column("sharia_passport_version_id")
    with op.batch_alter_table("published_asset_assessments") as batch_op:
        batch_op.drop_column("supersedes_publication_id")
    with op.batch_alter_table("sharia_review_decisions") as batch_op:
        batch_op.drop_column("integrity_hash")
        batch_op.drop_column("security_metadata")
        batch_op.drop_column("application_version")
        batch_op.drop_column("actor_role")
        batch_op.drop_column("ai_analysis_snapshot_id")
        batch_op.drop_column("acknowledged_gaps")
        batch_op.drop_column("qualifications")
        batch_op.drop_column("criterion_decisions")
    with op.batch_alter_table("sharia_review_cases") as batch_op:
        batch_op.drop_column("source_freshness_deadline")
        batch_op.drop_column("due_at")
        batch_op.drop_column("assigned_at")
        batch_op.drop_column("assigned_reviewer_id")

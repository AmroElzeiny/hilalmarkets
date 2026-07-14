"""add certified capability extension pipeline

Revision ID: ad1e2f3a4b5c
Revises: 9c0d1e2f3a4b
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ad1e2f3a4b5c"
down_revision: str | None = "9c0d1e2f3a4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capability_extensions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_session_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("capability_key", sa.String(length=120), nullable=False),
        sa.Column("capability_version", sa.String(length=32), nullable=False),
        sa.Column("registry_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=True),
        sa.Column("source_prompt", sa.Text(), nullable=False),
        sa.Column("conversation_history", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("expression", sa.JSON(), nullable=False),
        sa.Column("generated_code", sa.Text(), nullable=True),
        sa.Column("build_log", sa.JSON(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("validation_score", sa.Float(), nullable=False),
        sa.Column("ai_review", sa.JSON(), nullable=False),
        sa.Column("failure_classification", sa.String(length=40), nullable=True),
        sa.Column("scan_count", sa.Integer(), nullable=False),
        sa.Column("empty_scan_streak", sa.Integer(), nullable=False),
        sa.Column("no_notification_streak", sa.Integer(), nullable=False),
        sa.Column("symbols_scanned_total", sa.Integer(), nullable=False),
        sa.Column("candidates_total", sa.Integer(), nullable=False),
        sa.Column("notifications_total", sa.Integer(), nullable=False),
        sa.Column("repair_generation", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["ai_setup_chat_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["strategy_versions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "request_fingerprint", name="uq_capability_extension_user_request"
        ),
        sa.UniqueConstraint(
            "capability_key",
            "capability_version",
            name="uq_capability_extension_key_version",
        ),
    )
    op.create_index(
        "ix_capability_extension_status_updated",
        "capability_extensions",
        ["status", "updated_at"],
    )
    op.create_index(
        "ix_capability_extension_user_created",
        "capability_extensions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_capability_extensions_artifact_hash",
        "capability_extensions",
        ["artifact_hash"],
    )

    op.create_table(
        "capability_extension_attempts",
        sa.Column("extension_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=60), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=20), nullable=False),
        sa.Column("service_tier", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["extension_id"], ["capability_extensions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extension_id",
            "attempt_number",
            name="uq_capability_extension_attempt_number",
        ),
    )
    op.create_index(
        "ix_capability_extension_attempt_stage",
        "capability_extension_attempts",
        ["extension_id", "operation"],
    )

    op.create_table(
        "capability_extension_scans",
        sa.Column("extension_id", sa.Uuid(), nullable=False),
        sa.Column("scan_job_id", sa.Uuid(), nullable=True),
        sa.Column("phase", sa.String(length=24), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("exchange", sa.String(length=40), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("symbols_planned", sa.Integer(), nullable=False),
        sa.Column("symbols_scanned", sa.Integer(), nullable=False),
        sa.Column("candidates_found", sa.Integer(), nullable=False),
        sa.Column("notifications_created", sa.Integer(), nullable=False),
        sa.Column("candidate_rate", sa.Float(), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["extension_id"], ["capability_extensions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_job_id"], ["scan_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_capability_extension_scan_cycle",
        "capability_extension_scans",
        ["extension_id", "phase", "cycle_number"],
    )

    op.create_table(
        "capability_clarification_evidence",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("chat_session_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_fragment", sa.Text(), nullable=False),
        sa.Column("clarification_question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("capability_key", sa.String(length=120), nullable=True),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["ai_setup_chat_sessions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_fingerprint", name="uq_capability_clarification_evidence"),
    )
    op.create_index(
        "ix_capability_clarification_key_created",
        "capability_clarification_evidence",
        ["capability_key", "created_at"],
    )

    op.create_table(
        "capability_registry_artifacts",
        sa.Column("registry_hash", sa.String(length=64), nullable=False),
        sa.Column("registry_version", sa.String(length=80), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embeddings", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registry_hash", name="uq_capability_registry_artifact_hash"),
    )
    op.create_index(
        "ix_capability_registry_active_created",
        "capability_registry_artifacts",
        ["active", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("capability_registry_artifacts")
    op.drop_table("capability_clarification_evidence")
    op.drop_table("capability_extension_scans")
    op.drop_table("capability_extension_attempts")
    op.drop_table("capability_extensions")

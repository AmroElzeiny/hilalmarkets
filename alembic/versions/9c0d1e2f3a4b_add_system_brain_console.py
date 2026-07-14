"""add system brain coverage console

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c0d1e2f3a4b"
down_revision: str | None = "8b9c0d1e2f3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_brain_auth_challenges",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("requested_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_brain_challenge_email_created",
        "system_brain_auth_challenges",
        ["email", "created_at"],
    )
    op.create_table(
        "system_brain_sessions",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("session_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_digest", name="uq_system_brain_session_digest"),
    )
    op.create_index("ix_system_brain_session_expires", "system_brain_sessions", ["expires_at"])
    op.create_table(
        "system_brain_login_attempts",
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("username_hash", sa.String(length=64), nullable=False),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_brain_attempt_ip_created",
        "system_brain_login_attempts",
        ["ip_hash", "created_at"],
    )
    op.create_table(
        "capability_resolution_events",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("chat_session_id", sa.Uuid(), nullable=True),
        sa.Column("event_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_fragment", sa.Text(), nullable=False),
        sa.Column("normalized_fragment", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("selected_capability_key", sa.String(length=120), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("unknown_terms", sa.JSON(), nullable=False),
        sa.Column("top_confidence", sa.Float(), nullable=True),
        sa.Column("provider_requirement", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["chat_session_id"], ["ai_setup_chat_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_fingerprint", name="uq_capability_resolution_fingerprint"),
    )
    op.create_index(
        "ix_capability_resolution_status_created",
        "capability_resolution_events",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_capability_resolution_chat_created",
        "capability_resolution_events",
        ["chat_session_id", "created_at"],
    )
    op.create_index(
        "ix_capability_resolution_events_user_id",
        "capability_resolution_events",
        ["user_id"],
    )
    op.create_index(
        "ix_capability_resolution_events_chat_session_id",
        "capability_resolution_events",
        ["chat_session_id"],
    )
    op.create_index(
        "ix_capability_resolution_events_selected_capability_key",
        "capability_resolution_events",
        ["selected_capability_key"],
    )
    op.create_table(
        "capability_alias_proposals",
        sa.Column("alias", sa.String(length=240), nullable=False),
        sa.Column("normalized_alias", sa.String(length=240), nullable=False),
        sa.Column("capability_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("source_event_ids", sa.JSON(), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_alias",
            "capability_key",
            name="uq_capability_alias_proposal_target",
        ),
    )
    op.create_index(
        "ix_capability_alias_status_created",
        "capability_alias_proposals",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_capability_alias_proposals_capability_key",
        "capability_alias_proposals",
        ["capability_key"],
    )
    op.create_table(
        "ai_usage_events",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("chat_session_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(length=60), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=20), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("pricing_source", sa.String(length=200), nullable=False),
        sa.Column("raw_usage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["chat_session_id"], ["ai_setup_chat_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_model_created", "ai_usage_events", ["model", "created_at"])
    op.create_index("ix_ai_usage_user_created", "ai_usage_events", ["user_id", "created_at"])
    op.create_index(
        "ix_ai_usage_events_chat_session_id",
        "ai_usage_events",
        ["chat_session_id"],
    )


def downgrade() -> None:
    op.drop_table("ai_usage_events")
    op.drop_table("capability_alias_proposals")
    op.drop_table("capability_resolution_events")
    op.drop_table("system_brain_login_attempts")
    op.drop_table("system_brain_sessions")
    op.drop_table("system_brain_auth_challenges")

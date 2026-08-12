"""Operational issue queue: one deduplicated row per problem, with its audit trail.

Adds ``operational_issues`` and ``operational_issue_events`` and nothing else.

Autogenerate also offered four unrelated edits — a renamed index on
``ai_usage_events``, an ``alerts.alert_type`` VARCHAR-to-Enum change and two
constraint changes on ``public_chat_answer_feedback``. All four are SQLite
reflection differences that exist at ``b7c41d9e2a06`` before this change, and none
of them belongs to the issue queue. They were removed by hand: a migration that
quietly carries someone else's drift is impossible to roll back cleanly, and the
drift itself needs its own decision rather than a ride on this one.

Revision ID: 84e25ab68ade
Revises: b7c41d9e2a06
Create Date: 2026-08-12 22:35:40.647337
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "84e25ab68ade"
down_revision: str | None = "b7c41d9e2a06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_issues",
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.String(length=240), nullable=False),
        sa.Column("affected_scope", sa.String(length=120), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assignee", sa.String(length=60), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("runbook_anchor", sa.String(length=80), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("suppressed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("definition_version", sa.String(length=40), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_issues")),
        sa.UniqueConstraint("dedupe_key", name="uq_operational_issue_dedupe_key"),
    )
    with op.batch_alter_table("operational_issues", schema=None) as batch_op:
        batch_op.create_index(
            "ix_operational_issue_category", ["category", "last_seen_at"], unique=False
        )
        batch_op.create_index(
            "ix_operational_issue_severity_state", ["severity", "state"], unique=False
        )
        batch_op.create_index(
            "ix_operational_issue_state_last_seen", ["state", "last_seen_at"], unique=False
        )

    op.create_table(
        "operational_issue_events",
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.String(length=24), nullable=True),
        sa.Column("to_state", sa.String(length=24), nullable=False),
        sa.Column("actor", sa.String(length=60), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["operational_issues.id"],
            name=op.f("fk_operational_issue_events_issue_id_operational_issues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operational_issue_events")),
    )
    with op.batch_alter_table("operational_issue_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_operational_issue_event_issue", ["issue_id", "created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("operational_issue_events", schema=None) as batch_op:
        batch_op.drop_index("ix_operational_issue_event_issue")
    op.drop_table("operational_issue_events")

    with op.batch_alter_table("operational_issues", schema=None) as batch_op:
        batch_op.drop_index("ix_operational_issue_state_last_seen")
        batch_op.drop_index("ix_operational_issue_severity_state")
        batch_op.drop_index("ix_operational_issue_category")
    op.drop_table("operational_issues")

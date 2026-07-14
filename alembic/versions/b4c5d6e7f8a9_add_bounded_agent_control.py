"""add bounded agent control traces

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("chat_session_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("timeout_outcome", sa.String(length=40), nullable=True),
        sa.Column("budget_outcome", sa.String(length=40), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("shadow_mode", sa.Boolean(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("final_intent", sa.String(length=40), nullable=True),
        sa.Column("final_response_status", sa.String(length=40), nullable=True),
        sa.Column("comparison", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"],
            ["ai_setup_chat_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_run_user_started", "agent_runs", ["user_id", "started_at"])
    op.create_index("ix_agent_run_status_started", "agent_runs", ["status", "started_at"])
    op.create_index("ix_agent_run_correlation", "agent_runs", ["correlation_id"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_chat_session_id", "agent_runs", ["chat_session_id"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("openai_call_id", sa.String(length=160), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("argument_hash", sa.String(length=64), nullable=False),
        sa.Column("redacted_arguments", sa.JSON(), nullable=False),
        sa.Column("policy_decision", sa.String(length=60), nullable=False),
        sa.Column("result_status", sa.String(length=40), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "openai_call_id", name="uq_agent_tool_call_openai"),
    )
    op.create_index("ix_agent_tool_run_created", "agent_tool_calls", ["agent_run_id", "created_at"])
    op.create_index("ix_agent_tool_name_status", "agent_tool_calls", ["tool_name", "result_status"])
    op.create_index("ix_agent_tool_calls_agent_run_id", "agent_tool_calls", ["agent_run_id"])


def downgrade() -> None:
    op.drop_table("agent_tool_calls")
    op.drop_table("agent_runs")

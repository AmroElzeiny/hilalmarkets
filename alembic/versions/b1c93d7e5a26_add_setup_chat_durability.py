"""Durable Setup Chat turns: one active claim, request fingerprint, recovery leases.

Three things this adds, none of which existed:

* ``session_claim`` — the column that stops two mutating turns from owning one chat
  session at the same time. It is NULL once a turn settles, so the unique constraint
  only ever binds the single turn that is in flight.
* ``request_fingerprint`` — what a client message id was used for, so reusing it with
  different content is refused rather than answered from the wrong record.
* lease and recovery columns — so a crashed turn can be found, claimed by exactly one
  recovery owner, and finished without re-running anything that already committed.

Also adds ``setup_chat_pending_changes``: destructive proposals live in their own table
because they are not draft state and must never be mistaken for it.

Revision ID: b1c93d7e5a26
Revises: a7d35e9c41b2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c93d7e5a26"
down_revision: str | None = "a7d35e9c41b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TURN_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    sa.Column("session_claim", sa.Uuid(), nullable=True),
    sa.Column("is_mutating", sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column("executable_hash_before", sa.String(length=64), nullable=True),
    sa.Column("workflow_state_hash_before", sa.String(length=64), nullable=True),
    sa.Column("prompt_version", sa.String(length=40), nullable=True),
    sa.Column("schema_version", sa.String(length=40), nullable=True),
    sa.Column("planner_usage_json", sa.JSON(), nullable=True),
    sa.Column("provider_request_id", sa.String(length=120), nullable=True),
    sa.Column("stage_timestamps_json", sa.JSON(), nullable=True),
    sa.Column("lease_owner", sa.String(length=80), nullable=True),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("recovery_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("recovery_disposition", sa.String(length=48), nullable=True),
    sa.Column("recovery_usage_json", sa.JSON(), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("setup_chat_turns") as batch_op:
        for column in _TURN_COLUMNS:
            batch_op.add_column(column)
        batch_op.create_foreign_key(
            "fk_setup_chat_turn_session_claim",
            "ai_setup_chat_sessions",
            ["session_claim"],
            ["id"],
            ondelete="CASCADE",
        )
        # Existing rows carry no claim. Any turn already in flight when this deploys is
        # left unclaimed on purpose: inventing a claim for it would block its own
        # session, and the recovery worker settles it on the next cycle anyway.
        batch_op.create_unique_constraint("uq_setup_chat_turn_active_claim", ["session_claim"])
    op.create_index(
        "ix_setup_chat_turn_lease",
        "setup_chat_turns",
        ["status", "lease_expires_at"],
        unique=False,
    )

    op.create_table(
        "setup_chat_pending_changes",
        sa.Column("chat_session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("source_turn_id", sa.Uuid(), nullable=True),
        sa.Column("client_message_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("executable_hash", sa.String(length=64), nullable=False),
        sa.Column("workflow_state_hash", sa.String(length=64), nullable=False),
        sa.Column("executable_version", sa.Integer(), nullable=False),
        sa.Column("operations_json", sa.JSON(), nullable=False),
        sa.Column("operation_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("diff_json", sa.JSON(), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("invalidates_approval", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("governance_notes_json", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["ai_setup_chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_turn_id"], ["setup_chat_turns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", name="uq_setup_chat_pending_change_proposal"),
    )
    op.create_index(
        "ix_setup_chat_pending_change_owner",
        "setup_chat_pending_changes",
        ["chat_session_id", "status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_setup_chat_pending_change_owner", table_name="setup_chat_pending_changes"
    )
    op.drop_table("setup_chat_pending_changes")
    op.drop_index("ix_setup_chat_turn_lease", table_name="setup_chat_turns")
    with op.batch_alter_table("setup_chat_turns") as batch_op:
        batch_op.drop_constraint("uq_setup_chat_turn_active_claim", type_="unique")
        batch_op.drop_constraint("fk_setup_chat_turn_session_claim", type_="foreignkey")
        for column in reversed(_TURN_COLUMNS):
            batch_op.drop_column(column.name)

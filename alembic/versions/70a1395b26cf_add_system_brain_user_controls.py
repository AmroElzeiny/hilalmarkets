"""add System Brain user controls

Revision ID: 70a1395b26cf
Revises: 6f02832495ab
Create Date: 2026-07-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "70a1395b26cf"
down_revision: str | None = "6f02832495ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_bans",
        sa.Column("identifier_hash", sa.String(length=64), nullable=False),
        sa.Column("banned_user_id", sa.Uuid(), nullable=True),
        sa.Column("banned_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["banned_by_user_id"],
            ["users.id"],
            name=op.f("fk_account_bans_banned_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["banned_user_id"],
            ["users.id"],
            name=op.f("fk_account_bans_banned_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_bans")),
        sa.UniqueConstraint(
            "identifier_hash",
            name="uq_account_ban_identifier_hash",
        ),
    )
    op.create_index(
        "ix_account_ban_active_created",
        "account_bans",
        ["is_active", "created_at"],
    )
    op.create_index(
        op.f("ix_account_bans_banned_user_id"),
        "account_bans",
        ["banned_user_id"],
    )

    op.create_table(
        "account_admin_actions",
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_redacted", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_account_admin_actions_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            name=op.f("fk_account_admin_actions_target_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_admin_actions")),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_account_admin_action_idempotency",
        ),
    )
    op.create_index(
        "ix_account_admin_action_actor_created",
        "account_admin_actions",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_account_admin_action_target_created",
        "account_admin_actions",
        ["target_user_id", "created_at"],
    )

    op.create_table(
        "account_email_deliveries",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("admin_action_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("template_kind", sa.String(length=48), nullable=False),
        sa.Column("payload_redacted", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_action_id"],
            ["account_admin_actions.id"],
            name="fk_account_email_admin_action",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_account_email_deliveries_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_email_deliveries")),
        sa.UniqueConstraint(
            "event_key",
            name="uq_account_email_delivery_event",
        ),
    )
    op.create_index(
        "ix_account_email_delivery_due",
        "account_email_deliveries",
        ["status", "next_retry_at"],
    )
    op.create_index(
        op.f("ix_account_email_deliveries_user_id"),
        "account_email_deliveries",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_account_email_deliveries_user_id"),
        table_name="account_email_deliveries",
    )
    op.drop_index(
        "ix_account_email_delivery_due",
        table_name="account_email_deliveries",
    )
    op.drop_table("account_email_deliveries")
    op.drop_index(
        "ix_account_admin_action_target_created",
        table_name="account_admin_actions",
    )
    op.drop_index(
        "ix_account_admin_action_actor_created",
        table_name="account_admin_actions",
    )
    op.drop_table("account_admin_actions")
    op.drop_index(
        op.f("ix_account_bans_banned_user_id"),
        table_name="account_bans",
    )
    op.drop_index(
        "ix_account_ban_active_created",
        table_name="account_bans",
    )
    op.drop_table("account_bans")

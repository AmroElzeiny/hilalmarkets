"""add web dashboard sessions

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_web_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_sessions")),
        sa.UniqueConstraint("session_digest", name="uq_web_session_digest"),
    )
    with op.batch_alter_table("web_sessions") as batch_op:
        batch_op.create_index("ix_web_session_user_expires", ["user_id", "expires_at"])

    op.create_table(
        "dashboard_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("theme", sa.String(length=24), nullable=False),
        sa.Column("default_timezone", sa.String(length=64), nullable=False),
        sa.Column("default_dashboard_path", sa.String(length=120), nullable=False),
        sa.Column("notification_preferences", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_dashboard_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_preferences")),
        sa.UniqueConstraint("user_id", name="uq_dashboard_preference_user"),
    )

    op.create_table(
        "dashboard_notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("action_label", sa.String(length=80), nullable=True),
        sa.Column("action_url", sa.String(length=500), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_dashboard_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_notifications")),
    )
    with op.batch_alter_table("dashboard_notifications") as batch_op:
        batch_op.create_index("ix_dashboard_notification_user_created", ["user_id", "created_at"])
        batch_op.create_index("ix_dashboard_notification_user_read", ["user_id", "read_at"])

    op.create_table(
        "telegram_dashboard_links",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=64), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("target_path", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_telegram_dashboard_links_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_dashboard_links")),
        sa.UniqueConstraint("token_digest", name="uq_telegram_dashboard_token"),
    )
    with op.batch_alter_table("telegram_dashboard_links") as batch_op:
        batch_op.create_index("ix_telegram_dashboard_links_telegram_user_id", ["telegram_user_id"])
        batch_op.create_index("ix_telegram_dashboard_user_expires", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_table("telegram_dashboard_links")
    op.drop_table("dashboard_notifications")
    op.drop_table("dashboard_preferences")
    op.drop_table("web_sessions")

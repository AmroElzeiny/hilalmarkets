from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DiscordOAuthState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "discord_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_digest", name="uq_discord_oauth_state_digest"),
        Index("ix_discord_oauth_user_expiry", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    redirect_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiscordGuildInstallation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "discord_guild_installations"
    __table_args__ = (UniqueConstraint("guild_id", name="uq_discord_guild_installation"),)

    guild_id: Mapped[str] = mapped_column(String(64), nullable=False)
    guild_name: Mapped[str | None] = mapped_column(String(160))
    installed_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    bot_permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_permission_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class DiscordDeliveryDestination(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "discord_delivery_destinations"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "mode", "guild_id", "channel_id", name="uq_discord_destination_scope"
        ),
        Index("ix_discord_destination_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    guild_installation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("discord_guild_installations.id", ondelete="SET NULL")
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    discord_user_id: Mapped[str | None] = mapped_column(String(64))
    guild_id: Mapped[str | None] = mapped_column(String(64))
    channel_id: Mapped[str | None] = mapped_column(String(64))
    thread_policy: Mapped[str] = mapped_column(String(32), default="per_setup", nullable=False)
    permissions_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    test_status: Mapped[str] = mapped_column(String(32), default="not_sent", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class DiscordSetupThread(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "discord_setup_threads"
    __table_args__ = (
        UniqueConstraint("destination_id", "setup_key", name="uq_discord_setup_thread"),
        Index("ix_discord_thread_strategy", "strategy_version_id", "setup_key"),
    )

    destination_id: Mapped[UUID] = mapped_column(
        ForeignKey("discord_delivery_destinations.id", ondelete="CASCADE"), nullable=False
    )
    setup_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("setup_instances.id", ondelete="SET NULL")
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False
    )
    setup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    first_message_id: Mapped[str | None] = mapped_column(String(64))
    last_message_id: Mapped[str | None] = mapped_column(String(64))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscordRoleMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "discord_role_mappings"
    __table_args__ = (
        UniqueConstraint("guild_id", "role_id", "entitlement_key", name="uq_discord_role_map"),
    )

    guild_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_name: Mapped[str] = mapped_column(String(80), nullable=False)
    entitlement_key: Mapped[str] = mapped_column(String(80), nullable=False)
    plan_code: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class DiscordRoleSyncJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "discord_role_sync_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_discord_role_sync_idempotency"),
        Index("ix_discord_role_sync_status_retry", "status", "next_retry_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    guild_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

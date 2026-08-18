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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from ai_market_monitor.db.models.enums import (
    ConnectionStatus,
    IdentityProvider,
    OnboardingStatus,
    OnboardingStep,
    UserRole,
    UserStatus,
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    status: Mapped[UserStatus] = mapped_column(
        enum_type(UserStatus, name="user_status"),
        default=UserStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    role: Mapped[UserRole] = mapped_column(
        enum_type(UserRole, name="user_role"), default=UserRole.USER, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(120))
    locale: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identities: Mapped[list["UserIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    onboarding_sessions: Mapped[list["OnboardingSession"]] = relationship(back_populates="user")


class UserIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_identity_provider_subject"),
        UniqueConstraint(
            "provider", "normalized_identifier", name="uq_identity_provider_identifier"
        ),
        Index("ix_user_identities_user_provider", "user_id", "provider"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[IdentityProvider] = mapped_column(
        enum_type(IdentityProvider, name="identity_provider"), nullable=False
    )
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_identifier: Mapped[str | None] = mapped_column(String(320))
    display_identifier: Mapped[str | None] = mapped_column(String(320))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped[User] = relationship(back_populates="identities")


class AccountBan(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "account_bans"
    __table_args__ = (
        UniqueConstraint("identifier_hash", name="uq_account_ban_identifier_hash"),
        Index("ix_account_ban_active_created", "is_active", "created_at"),
    )

    identifier_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    banned_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    banned_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountAdminAction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "account_admin_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_account_admin_action_idempotency"),
        Index("ix_account_admin_action_target_created", "target_user_id", "created_at"),
        Index("ix_account_admin_action_actor_created", "actor_user_id", "created_at"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    target_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountEmailDelivery(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "account_email_deliveries"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_account_email_delivery_event"),
        Index("ix_account_email_delivery_due", "status", "next_retry_at"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    #: The admin action that caused this email, when one did.
    #:
    #: Nullable, because not every account email comes from an administrator. The
    #: welcome a person gets when they finish signing up is raised by the person
    #: themselves, and there is no action to point at. Required, this table could only
    #: ever hold admin notices — which is what it held, and why the welcome had nowhere
    #: to queue.
    admin_action_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account_admin_actions.id", ondelete="CASCADE")
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    template_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telegram_connections"
    __table_args__ = (UniqueConstraint("telegram_user_id", name="uq_telegram_user_id"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    telegram_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ConnectionStatus] = mapped_column(
        enum_type(ConnectionStatus, name="connection_status"),
        default=ConnectionStatus.PENDING,
        nullable=False,
    )
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class DiscordConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "discord_connections"
    __table_args__ = (
        UniqueConstraint("discord_user_id", name="uq_discord_user_id"),
        UniqueConstraint("guild_id", "channel_id", name="uq_discord_guild_channel"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discord_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    guild_id: Mapped[str | None] = mapped_column(String(64))
    channel_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ConnectionStatus] = mapped_column(
        enum_type(ConnectionStatus, name="discord_connection_status"),
        default=ConnectionStatus.PENDING,
        nullable=False,
    )
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class AttributionTouch(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "attribution_touches"
    __table_args__ = (Index("ix_attribution_user_created", "user_id", "created_at"),)

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    onboarding_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("onboarding_sessions.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str | None] = mapped_column(String(100), index=True)
    medium: Mapped[str | None] = mapped_column(String(100))
    campaign: Mapped[str | None] = mapped_column(String(160), index=True)
    referrer: Mapped[str | None] = mapped_column(Text)
    referral_code: Mapped[str | None] = mapped_column(String(64), index=True)
    entry_channel: Mapped[str] = mapped_column(String(32), nullable=False)
    landing_path: Mapped[str | None] = mapped_column(String(500))
    consented: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DisclaimerAcceptance(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "disclaimer_acceptances"
    __table_args__ = (
        UniqueConstraint("user_id", "disclaimer_version", name="uq_disclaimer_user_version"),
        Index("ix_disclaimer_user_accepted", "user_id", "accepted_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_identities.id", ondelete="RESTRICT"), nullable=False
    )
    disclaimer_version: Mapped[str] = mapped_column(String(40), nullable=False)
    acceptance_source: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))


class OnboardingSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        Index("ix_onboarding_user_status", "user_id", "status"),
        Index("ix_onboarding_updated", "updated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[OnboardingStatus] = mapped_column(
        enum_type(OnboardingStatus, name="onboarding_status"),
        default=OnboardingStatus.IN_PROGRESS,
        nullable=False,
    )
    current_step: Mapped[OnboardingStep] = mapped_column(
        enum_type(OnboardingStep, name="onboarding_step"),
        default=OnboardingStep.INTRODUCTION,
        nullable=False,
    )
    entry_channel: Mapped[str] = mapped_column(String(32), nullable=False)
    state_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(500))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="onboarding_sessions")


class IdentityLinkToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "identity_link_tokens"
    __table_args__ = (Index("ix_link_token_user_expiry", "user_id", "expires_at"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    onboarding_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE")
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    target_channel: Mapped[str] = mapped_column(String(32), default="web", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferralCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "referral_codes"

    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    campaign: Mapped[str | None] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "web_sessions"
    __table_args__ = (
        UniqueConstraint("session_digest", name="uq_web_session_digest"),
        Index("ix_web_session_user_expires", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))


class EmailAuthChallenge(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "email_auth_challenges"
    __table_args__ = (
        Index("ix_email_auth_challenge_email_purpose", "email", "purpose", "created_at"),
        Index("ix_email_auth_challenge_user_expires", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    requested_ip_hash: Mapped[str | None] = mapped_column(String(64))


class PendingEmailSignup(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "pending_email_signups"
    __table_args__ = (
        Index("ix_pending_email_signup_email_created", "email", "created_at"),
        Index("ix_pending_email_signup_expires", "expires_at"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_identifier: Mapped[str] = mapped_column(String(320), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(60))
    last_name: Mapped[str | None] = mapped_column(String(60))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_link: Mapped[str | None] = mapped_column(String(1000))
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    requested_ip_hash: Mapped[str | None] = mapped_column(String(64))


class DashboardPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "dashboard_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_dashboard_preference_user"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    theme: Mapped[str] = mapped_column(String(24), default="dark", nullable=False)
    default_timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    default_dashboard_path: Mapped[str] = mapped_column(
        String(120), default="/dashboard", nullable=False
    )
    notification_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class DashboardNotification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "dashboard_notifications"
    __table_args__ = (
        Index("ix_dashboard_notification_user_created", "user_id", "created_at"),
        Index("ix_dashboard_notification_user_read", "user_id", "read_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(24), default="info", nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    action_label: Mapped[str | None] = mapped_column(String(80))
    action_url: Mapped[str | None] = mapped_column(String(500))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramDashboardLink(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "telegram_dashboard_links"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_telegram_dashboard_token"),
        Index("ix_telegram_dashboard_user_expires", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_path: Mapped[str] = mapped_column(String(500), default="/dashboard", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

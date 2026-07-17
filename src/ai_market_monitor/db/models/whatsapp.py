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
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from ai_market_monitor.db.models.enums import ConnectionStatus


class WhatsAppConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "whatsapp_connections"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_whatsapp_connection_user"),
        UniqueConstraint("wa_id", name="uq_whatsapp_connection_wa_id"),
        UniqueConstraint("phone_e164", name="uq_whatsapp_connection_phone"),
        Index("ix_whatsapp_connection_status", "status", "alerts_enabled"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wa_id: Mapped[str] = mapped_column(String(32), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    profile_name: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[ConnectionStatus] = mapped_column(
        enum_type(ConnectionStatus, name="whatsapp_connection_status"),
        default=ConnectionStatus.PENDING,
        nullable=False,
    )
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preferred_locale: Mapped[str] = mapped_column(String(16), default="en_US", nullable=False)
    opt_in_categories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_window_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opt_in_source: Mapped[str | None] = mapped_column(String(80))
    opt_in_version: Mapped[str | None] = mapped_column(String(40))
    opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opt_out_reason: Mapped[str | None] = mapped_column(String(160))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WhatsAppConversationState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "whatsapp_conversation_states"
    __table_args__ = (
        UniqueConstraint("wa_id", name="uq_whatsapp_conversation_wa_id"),
        Index("ix_whatsapp_conversation_user_flow", "user_id", "flow", "step"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    wa_id: Mapped[str] = mapped_column(String(32), nullable=False)
    flow: Mapped[str] = mapped_column(String(64), default="main_menu", nullable=False)
    step: Mapped[str] = mapped_column(String(64), default="idle", nullable=False)
    state_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_inbound_message_id: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WhatsAppWebhookReceipt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "whatsapp_webhook_receipts"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_whatsapp_webhook_event_key"),
        Index("ix_whatsapp_webhook_processing", "processing_status", "received_at"),
        Index("ix_whatsapp_webhook_retention", "retain_until"),
        Index("ix_whatsapp_webhook_provider_message", "provider_message_id"),
    )

    event_key: Mapped[str] = mapped_column(String(320), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    provider_status: Mapped[str | None] = mapped_column(String(40))
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_redacted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_provider_message_id: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

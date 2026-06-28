from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TelegramConversationState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telegram_conversation_states"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_telegram_conversation_user"),
        Index("ix_telegram_conversation_user_flow", "user_id", "flow", "step"),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    onboarding_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("onboarding_sessions.id", ondelete="SET NULL"), index=True
    )
    telegram_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    flow: Mapped[str] = mapped_column(String(64), default="main_menu", nullable=False)
    step: Mapped[str] = mapped_column(String(64), default="idle", nullable=False)
    state_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_message_id: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramCallbackReceipt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "telegram_callback_receipts"
    __table_args__ = (
        UniqueConstraint("callback_query_id", name="uq_telegram_callback_query"),
        Index("ix_telegram_callback_user_created", "telegram_user_id", "created_at"),
    )

    callback_query_id: Mapped[str] = mapped_column(String(128), nullable=False)
    telegram_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramUpdateReceipt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "telegram_update_receipts"
    __table_args__ = (
        UniqueConstraint("update_id", name="uq_telegram_update_id"),
        Index("ix_telegram_update_status_created", "status", "created_at"),
    )

    update_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    provider_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

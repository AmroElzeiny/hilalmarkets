from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PublicChatAnswerEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "public_chat_answer_events"
    __table_args__ = (
        Index("ix_public_chat_answer_session_created", "session_key_hash", "created_at"),
        Index("ix_public_chat_answer_outcome_created", "outcome", "created_at"),
    )

    session_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("public_chat_conversations.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(48), nullable=False, default="ANSWER")
    intent: Mapped[str] = mapped_column(String(100), nullable=False, default="product_help")
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), default=Decimal("0"), nullable=False
    )
    validation_failure: Mapped[str | None] = mapped_column(String(120))
    coverage_score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    related_route_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "public_chat_conversations"
    __table_args__ = (
        UniqueConstraint("session_key_hash", name="uq_public_chat_conversation_session"),
        Index("ix_public_chat_conversation_expires", "expires_at"),
        Index("ix_public_chat_conversation_user_updated", "user_id", "updated_at"),
    )

    session_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    stage: Mapped[str] = mapped_column(
        String(48), default="GREETING_AND_PROFILE", nullable=False
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    reasoning_effort: Mapped[str | None] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicChatTurn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "public_chat_turns"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_public_chat_turn_client_message",
        ),
        Index("ix_public_chat_turn_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("public_chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_message_id: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="processing", nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(120))


class PublicInquiry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "public_inquiries"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_public_inquiry_reference"),
        UniqueConstraint("idempotency_key", name="uq_public_inquiry_idempotency"),
        Index("ix_public_inquiry_status_created", "status", "created_at"),
        Index("ix_public_inquiry_retain_until", "retain_until"),
    )

    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[str] = mapped_column(String(240), nullable=False)
    referrer: Mapped[str | None] = mapped_column(String(500))
    attribution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    knowledge_gap_category: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="received", nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicInquiryEmailDelivery(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "public_inquiry_email_deliveries"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_public_inquiry_email_event_key"),
        Index("ix_public_inquiry_email_due", "status", "next_retry_at"),
    )

    inquiry_id: Mapped[UUID] = mapped_column(
        ForeignKey("public_inquiries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicInquiryRating(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "public_inquiry_ratings"
    __table_args__ = (
        UniqueConstraint("inquiry_id", name="uq_public_inquiry_rating_inquiry"),
    )

    inquiry_id: Mapped[UUID] = mapped_column(
        ForeignKey("public_inquiries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rating: Mapped[int | None] = mapped_column(Integer)
    helpful: Mapped[bool | None] = mapped_column(Boolean)
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

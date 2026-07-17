from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    JSON,
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
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    coverage_score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    related_route_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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

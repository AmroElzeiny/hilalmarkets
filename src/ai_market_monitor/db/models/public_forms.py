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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WaitlistSignup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "waitlist_signups"
    __table_args__ = (
        UniqueConstraint("normalized_email", name="uq_waitlist_signup_email"),
        UniqueConstraint("idempotency_key", name="uq_waitlist_signup_idempotency"),
        Index("ix_waitlist_signup_submitted", "submitted_at"),
        Index("ix_waitlist_signup_country_submitted", "country_code", "submitted_at"),
    )

    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    source_page: Mapped[str] = mapped_column(String(240), nullable=False)
    attribution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: What this person chose about being contacted for private beta testing.
    #: Older rows were created before the question existed, so they default to False:
    #: an absent answer must never be read as agreement.
    beta_contact_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WaitlistSheetDelivery(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "waitlist_sheet_deliveries"
    __table_args__ = (
        UniqueConstraint("signup_id", name="uq_waitlist_sheet_delivery_signup"),
        UniqueConstraint("event_key", name="uq_waitlist_sheet_delivery_event"),
        Index("ix_waitlist_sheet_delivery_due", "status", "next_retry_at"),
    )

    signup_id: Mapped[UUID] = mapped_column(
        ForeignKey("waitlist_signups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_type: Mapped[str | None] = mapped_column(String(120))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContactSubmission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "contact_submissions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_contact_submission_idempotency"),
        Index("ix_contact_submission_status_submitted", "status", "submitted_at"),
    )

    normalized_email: Mapped[str] = mapped_column(String(320), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[str] = mapped_column(String(240), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="received", nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContactEmailDelivery(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "contact_email_deliveries"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_contact_email_delivery_submission"),
        UniqueConstraint("event_key", name="uq_contact_email_delivery_event"),
        Index("ix_contact_email_delivery_due", "status", "next_retry_at"),
    )

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("contact_submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error_type: Mapped[str | None] = mapped_column(String(120))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

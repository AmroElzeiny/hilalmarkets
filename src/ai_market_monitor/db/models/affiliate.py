"""The affiliate programme: one application per person, and the payouts that follow.

Three things are deliberately separate here.

**The application** is what a person asks for. It keeps what they typed — their name,
where they will share, the discount code they would like — untouched by any later
decision, so a rejected application can be reopened and shown back to them exactly as
they wrote it.

**The decision** is the platform's, and it is the only place the real numbers live. The
code a customer types, the discount it gives, and the share the affiliate earns are all
set by an administrator at approval time. What the applicant asked for is a request, not
a setting: an applicant who types ``95`` into a commission box has asked for 95 percent
and been told no, they have not configured anything.

**The payout** is a separate record with its own state, because money leaving is not the
same event as money being earned. A request sits at ``pending`` until a person marks it
paid or refuses it, and neither the amount nor the address is ever rewritten afterwards —
a paid row is the receipt.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AffiliateApplication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "affiliate_applications"
    __table_args__ = (
        # One live application per person. A rejected one is reopened by being replaced,
        # never by leaving two rows and hoping the newest wins.
        UniqueConstraint("user_id", name="uq_affiliate_application_user"),
        Index("ix_affiliate_application_status_submitted", "status", "submitted_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # ── What the applicant wrote ────────────────────────────────────────────────
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Where they will share, as a list of addresses they typed. A list because most
    #: people have more than one, and asking for "your social media link" singular is how
    #: a form loses the account that actually has the audience.
    social_links: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    requested_discount_code: Mapped[str] = mapped_column(String(40), nullable=False)
    #: What share they asked for. A request. The granted share is `commission_percent`.
    requested_commission_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False
    )
    applicant_note: Mapped[str | None] = mapped_column(Text)

    # ── Where it stands ─────────────────────────────────────────────────────────
    #: ``pending`` · ``approved`` · ``rejected``. A rejected application stays, so the
    #: person can be shown why and the next one can be compared with it.
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # ── What the administrator decided ──────────────────────────────────────────
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Shown to the applicant when the answer is no. Written for them, not for the file.
    decision_note: Mapped[str | None] = mapped_column(Text)

    #: The code a customer actually types. Set at approval; ``None`` until then.
    discount_code: Mapped[str | None] = mapped_column(String(40))
    #: What that code takes off the customer's price.
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    #: What the affiliate earns of what the customer pays.
    commission_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    #: The referral code row this application was granted, once approved.
    referral_code_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("referral_codes.id", ondelete="SET NULL")
    )


class AffiliatePayoutRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "affiliate_payout_requests"
    __table_args__ = (
        Index("ix_affiliate_payout_status_requested", "status", "requested_at"),
        Index("ix_affiliate_payout_user_requested", "user_id", "requested_at"),
    )

    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("affiliate_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    #: What was owed at the moment of asking, frozen. Recomputing it when the row is read
    #: would let a receipt for a paid request change after the fact.
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    network: Mapped[str] = mapped_column(String(24), nullable=False)
    destination_address: Mapped[str] = mapped_column(String(160), nullable=False)

    #: ``pending`` · ``paid`` · ``rejected``.
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    #: Where the money went. A transaction hash, when there is one.
    transaction_reference: Mapped[str | None] = mapped_column(String(160))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

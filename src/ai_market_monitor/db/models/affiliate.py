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

Two ledgers sit beside them, and they are ledgers rather than counters on purpose.
``affiliate_code_uses`` is every time somebody used the code, and ``affiliate_commissions``
is every payment that earned money. A number with no rows behind it can only ever be shown
as a total; the affiliate's page has to open each one and say *when*, *who* and *how much*,
and a counter column cannot answer that. `ReferralCode.use_count` is exactly such a
counter, which is why it is not what the page reads.

Both keep their own copy of the customer's name and of the money, frozen at the moment
they were written. The row that earned it is a receipt: it survives the customer closing
their account, and it never changes because a rate was changed afterwards.
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
    #: What the affiliate earns on a customer's **first** payment.
    #:
    #: The column keeps its old name because every row already written means exactly this:
    #: before there was a second rate, this was the rate on everything, and the first
    #: payment is the one it always applied to. Renaming it would have changed nothing
    #: about the data and broken every reader.
    #:
    #: **Nothing reads this column directly.** `AffiliateService.rates_for` is the one
    #: place the pair of rates is resolved, so a caller cannot pick up the first-payment
    #: rate and quietly apply it to a renewal.
    commission_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    #: What the affiliate earns on every payment **after** the first one from the same
    #: customer — renewals, and a fresh subscription after a cancelled one.
    #:
    #: ``None`` means "the same as the first-payment rate", which is what every
    #: application approved before this rate existed is. It is not a zero: a missing
    #: answer and "this affiliate earns nothing on renewals" are different facts, and
    #: storing the second for the first would silently stop paying people.
    subsequent_commission_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
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


class AffiliateCodeUse(UUIDPrimaryKeyMixin, Base):
    """One time somebody used an affiliate's code, with the moment and the name.

    ``ReferralCode.use_count`` counts the same thing and cannot be opened: it is a single
    number, so a page built on it can say "14" and nothing else. The affiliate's page has
    to show *when* each one happened and *who* it was, so the uses are rows.

    ``event_key`` is what stops one use being counted twice. The same sign-up retried, or
    a payment page opened again with the same code, arrives here with the same key and is
    refused by the unique index rather than by a check somebody has to remember to write.

    The customer's name is copied in rather than joined at read time. An affiliate is
    shown who used their code; if that person later closes their account the row still has
    to read correctly, and ``user_id`` going to ``NULL`` must not take the name with it.
    """

    __tablename__ = "affiliate_code_uses"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_affiliate_code_use_event"),
        Index("ix_affiliate_code_use_owner_used", "affiliate_user_id", "used_at"),
    )

    #: Whose code it was. This is the affiliate, not the person who typed it.
    affiliate_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    referral_code_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("referral_codes.id", ondelete="SET NULL")
    )
    #: The code as it was typed, normalised. Kept as text so a withdrawn code still reads.
    code: Mapped[str] = mapped_column(String(40), nullable=False)

    #: Who used it. ``NULL`` once that account is gone; the name below stays.
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: ``signup`` — used on the way in — or ``checkout``, typed into the payment page.
    context: Mapped[str] = mapped_column(String(16), nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)


class AffiliateCommission(UUIDPrimaryKeyMixin, Base):
    """One payment that earned an affiliate money, and everything needed to explain it.

    Every figure on the affiliate's page is a sum of these rows, and every popup is the
    rows themselves. That is the reason they exist: three totals that could not be opened
    would leave an affiliate with no way to check a number they are being paid on.

    **The rate is frozen here.** The share is copied onto the row at the moment it is
    earned, so changing an affiliate's rate later cannot rewrite what past payments were
    worth — including money already shown to them and already paid out.

    ``sequence_kind`` is decided once, when the row is written, by asking whether this
    customer has ever earned this affiliate anything before. It is stored rather than
    worked out at read time because a row deleted or a customer removed would otherwise
    silently turn somebody's second payment into their first.
    """

    __tablename__ = "affiliate_commissions"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_affiliate_commission_event"),
        Index("ix_affiliate_commission_owner_earned", "affiliate_user_id", "earned_at"),
        Index("ix_affiliate_commission_owner_kind", "affiliate_user_id", "sequence_kind"),
    )

    affiliate_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The permanent assignment this payment was earned under. ``NULL`` if that row is
    #: ever released — the money was still earned, and a receipt does not disappear.
    relationship_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("referral_relationships.id", ondelete="SET NULL")
    )
    customer_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: ``first`` — the customer's first paid subscription — or ``subsequent``, every
    #: payment after it.
    sequence_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    #: What the customer actually paid, and what share of it this affiliate keeps.
    paid_amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    commission_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    commission_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

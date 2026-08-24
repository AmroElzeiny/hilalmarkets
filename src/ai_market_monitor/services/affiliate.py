"""The affiliate programme, from the form somebody fills to the money going out.

Every number a person is shown here comes from a stored row, never from a calculation
done at display time. That is not a style choice: a commission balance recomputed on each
page load changes after a payout is requested, so the amount on the request and the
amount on the page stop agreeing, and neither is wrong enough to notice.

The rules this service holds, in one place so no surface can disagree with another:

* **Only an administrator sets money.** The applicant asks for a code; the discount, the
  code and the commission share are all written at approval. The share is not even asked
  for — everybody applies on `DEFAULT_COMMISSION_PERCENT`, the form has no box for it, and
  `apply()` has no parameter for it. Somebody who wants more is pointed at a person, not
  at a field.
* **A payout can only be asked for out of what is actually eligible.** Eligible means the
  referred person converted to a paid plan; anything else is not yet money.
* **Already-requested money is not available twice.** Pending and paid requests are
  subtracted from the balance, so pressing the button twice cannot pay twice.
* **A refused payout returns the money to the balance.** It was never sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AccountEmailDelivery,
    AffiliateApplication,
    AffiliatePayoutRequest,
    AuditEvent,
    ReferralCode,
    ReferralRelationship,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider
from ai_market_monitor.services.affiliate_payout_options import (
    MINIMUM_PAYOUT_USD,
    network_for,
)

#: The share everybody applies on, and what approval uses when nobody changes it.
#:
#: One constant and not two: the page shows this number as a fact, `apply()` stores it
#: because the applicant is never asked, and `approve()` falls back to it when the
#: administrator leaves the box alone. Anyone who wants more than this is sent to a person
#: rather than to a form field — see `ALTERNATIVE_METHOD_EMAIL`.
DEFAULT_COMMISSION_PERCENT = Decimal("25")

#: How long an applicant is told to wait. Shown on the page and written into the email,
#: from here, so the two can never promise different things.
DECISION_TARGET_HOURS = 24

#: How many places to share. Enough for somebody with a real audience across platforms,
#: bounded so the form cannot be used to store a list of links.
MAXIMUM_SOCIAL_LINKS = 5

#: A discount code a customer has to be able to type without getting it wrong.
_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,39}$")

_STATUS_PENDING = "pending"
_STATUS_APPROVED = "approved"
_STATUS_REJECTED = "rejected"

_PAYOUT_PENDING = "pending"
_PAYOUT_PAID = "paid"
_PAYOUT_REJECTED = "rejected"

#: A referral that has become money. One name for it, because three surfaces ask.
ELIGIBLE_REWARD_STATUSES = frozenset({"eligible_after_first_paid_month", "granted"})


async def enqueue_affiliate_email(
    session: AsyncSession,
    *,
    user_id: UUID,
    template_kind: str,
    event_key: str,
    payload: dict[str, object],
) -> AccountEmailDelivery | None:
    """Queue one affiliate message on the account outbox.

    Returns ``None`` when the account has no email address to reach, and when the same
    ``event_key`` is already queued. Both are ordinary: an application approved twice by
    two administrators pressing the button together must send one email, not two, and the
    unique key on the outbox is what makes that true rather than a check-then-insert that
    two requests can both pass.

    A failure to queue never fails the decision. The approval is the thing that matters;
    an email that could not be raised is visible in the outbox, and blocking a decision
    on it would be the diagnostic becoming the failure.
    """

    identity = await session.scalar(
        select(UserIdentity)
        .where(
            UserIdentity.user_id == user_id,
            UserIdentity.provider == IdentityProvider.EMAIL,
            UserIdentity.is_verified.is_(True),
        )
        .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        .limit(1)
    )
    recipient = identity.normalized_identifier if identity else None
    if not recipient:
        return None
    existing = await session.scalar(
        select(AccountEmailDelivery).where(AccountEmailDelivery.event_key == event_key)
    )
    if existing is not None:
        return existing
    delivery = AccountEmailDelivery(
        user_id=user_id,
        event_key=event_key,
        recipient=recipient,
        template_kind=template_kind,
        payload_redacted=payload,
        status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def try_sending_now(
    session: AsyncSession,
    settings: object,
    delivery: AccountEmailDelivery | None,
) -> None:
    """Attempt the queued message straight away, and never let it break the action.

    The outbox is swept every minute either way, so this only decides whether somebody
    who has just pressed a button waits a moment or a minute. It is wrapped because a
    provider that is refusing connections must not turn an approval that already
    happened into an error page — the row is written, the sweep will carry it, and a
    failure here is a diagnostic rather than the failure itself.

    Called after the commit, deliberately: an unsent row is recoverable, a sent message
    about a decision that was then rolled back is not.
    """

    if delivery is None:
        return
    from ai_market_monitor.services.account_emails import AccountEmailOutboxService

    try:
        await AccountEmailOutboxService(session, settings).process_due(  # type: ignore[arg-type]
            delivery_id=delivery.id
        )
    except Exception:
        await session.rollback()


def first_name_of(user: User) -> str:
    """The name to greet somebody by, or nothing rather than a guess."""

    return ((str(user.display_name or "").strip().split() or [""])[0]) or ""


class AffiliateError(ValueError):
    """Something the programme will not do, with a sentence a beginner can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReferralEarning:
    """One person who joined through an affiliate, as the affiliate may see them.

    The name and the date, and no email. An affiliate is told who bought so they can
    recognise their own audience; handing them a customer's address would be giving away
    somebody else's contact details, which is not theirs to give.
    """

    customer_name: str
    joined_at: datetime
    converted_at: datetime | None
    commission_usd: Decimal
    is_paid_conversion: bool


@dataclass(frozen=True, slots=True)
class AdminApplicationRow:
    """One application as the System Brain reads it: the row, the person, the address."""

    application: AffiliateApplication
    user: User
    email: str

    @property
    def display_name(self) -> str:
        return self.application.display_name


@dataclass(frozen=True, slots=True)
class AdminPayoutRow:
    payout: AffiliatePayoutRequest
    user: User
    email: str

    @property
    def display_name(self) -> str:
        return str(self.user.display_name or "").strip() or self.email


@dataclass(frozen=True, slots=True)
class AffiliateStats:
    uses: int
    paid_conversions: int
    total_commission_usd: Decimal
    requested_or_paid_usd: Decimal
    available_usd: Decimal
    earnings: tuple[ReferralEarning, ...]

    @property
    def can_request_payout(self) -> bool:
        return self.available_usd >= MINIMUM_PAYOUT_USD


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AffiliateError("invalid_number", f"{field} must be a number.") from exc


def _percent(value: object, *, field: str) -> Decimal:
    number = _decimal(value, field=field)
    if number <= 0 or number > 100:
        raise AffiliateError(
            "percent_out_of_range",
            f"{field} must be more than 0 and no more than 100.",
        )
    return number.quantize(Decimal("0.01"))


def normalize_discount_code(raw: str) -> str:
    """One spelling of a code, so two people cannot claim the same one in two cases.

    Upper case, and only the characters somebody can read out over a call without being
    asked "was that a one or an l".
    """

    code = re.sub(r"\s+", "", str(raw or "")).upper().replace(" ", "")
    if not _CODE_PATTERN.match(code):
        raise AffiliateError(
            "invalid_code",
            "A code needs 3 to 40 letters or numbers. Dashes are fine; spaces are not.",
        )
    return code


def normalize_social_links(raw: object) -> list[str]:
    """The addresses somebody typed, cleaned but never invented.

    A link with no scheme gets ``https://`` in front of it, because a person typing
    ``instagram.com/name`` has given a usable address and refusing it teaches nothing. A
    link that is still not a link is refused rather than stored as text nobody can open.
    """

    values = raw if isinstance(raw, list) else [raw]
    links: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        if not text.startswith(("http://", "https://")):
            text = f"https://{text}"
        if not re.match(r"^https?://[^\s/]+\.[^\s/]{2,}(/.*)?$", text):
            raise AffiliateError(
                "invalid_link",
                f"“{item}” does not look like a web address. Paste the full link.",
            )
        if text not in links:
            links.append(text)
    if not links:
        raise AffiliateError(
            "links_missing",
            "Add at least one link to where you will share Hilal Markets.",
        )
    if len(links) > MAXIMUM_SOCIAL_LINKS:
        raise AffiliateError(
            "too_many_links",
            f"Up to {MAXIMUM_SOCIAL_LINKS} links, please.",
        )
    return links


class AffiliateService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── The applicant's side ────────────────────────────────────────────────────

    async def application_for(self, user_id: UUID) -> AffiliateApplication | None:
        return await self.session.scalar(
            select(AffiliateApplication).where(AffiliateApplication.user_id == user_id)
        )

    async def apply(
        self,
        *,
        user_id: UUID,
        display_name: str,
        social_links: object,
        requested_discount_code: str,
        applicant_note: str | None = None,
    ) -> AffiliateApplication:
        """Take an application, or replace a refused one with a new attempt.

        An approved application is never overwritten. A rejected one is, in place, so the
        person keeps one row and one history rather than collecting refusals.

        **The share is not an argument here, on purpose.** Everybody applies on the same
        standard share and an administrator is the only one who can change it, so there is
        nothing for an applicant to fill in and nothing for a crafted request to send. A
        box shown as read-only would have been worse than no box at all: the number would
        still arrive in the form body, and the server would still have to decide whether
        to believe it. Removing the parameter is what makes that impossible rather than
        merely discouraged.

        The column stays, and it stays a *requested* share rather than the granted one.
        It records the share the application was made under, so a row written today still
        reads correctly if the standard share is ever changed.
        """

        name = str(display_name or "").strip()
        if len(name) < 2:
            raise AffiliateError("name_missing", "Tell us the name to pay this out to.")
        if len(name) > 120:
            raise AffiliateError("name_too_long", "That name is too long for the form.")

        links = normalize_social_links(social_links)
        code = normalize_discount_code(requested_discount_code)
        share = _percent(DEFAULT_COMMISSION_PERCENT, field="Commission")
        note = (str(applicant_note or "").strip() or None) if applicant_note else None

        existing = await self.application_for(user_id)
        now = datetime.now(UTC)
        if existing is not None:
            if existing.status == _STATUS_APPROVED:
                raise AffiliateError(
                    "already_approved",
                    "You are already an affiliate. There is nothing to apply for.",
                )
            if existing.status == _STATUS_PENDING:
                raise AffiliateError(
                    "already_pending",
                    "Your application is already with us. We will answer within "
                    f"{DECISION_TARGET_HOURS} hours.",
                )
            # Rejected, and applying again. The old decision is cleared so the row reads
            # as a fresh request rather than a refusal with new text stapled to it.
            existing.display_name = name
            existing.social_links = links
            existing.requested_discount_code = code
            existing.requested_commission_percent = share
            existing.applicant_note = note
            existing.status = _STATUS_PENDING
            existing.submitted_at = now
            existing.decided_by_user_id = None
            existing.decided_at = None
            existing.decision_note = None
            application = existing
        else:
            application = AffiliateApplication(
                user_id=user_id,
                display_name=name,
                social_links=links,
                requested_discount_code=code,
                requested_commission_percent=share,
                applicant_note=note,
                status=_STATUS_PENDING,
                submitted_at=now,
            )
            self.session.add(application)

        self._audit(
            actor_user_id=user_id,
            actor_type="user",
            action="affiliate.application_submitted",
            target_id=None,
            metadata={"requested_code": code, "links": len(links)},
        )
        await self.session.flush()
        return application

    # ── The administrator's side ────────────────────────────────────────────────

    async def pending_applications(self, limit: int = 50) -> list[AdminApplicationRow]:
        return await self._application_rows(pending=True, limit=limit)

    async def decided_applications(self, limit: int = 50) -> list[AdminApplicationRow]:
        return await self._application_rows(pending=False, limit=limit)

    async def _application_rows(
        self, *, pending: bool, limit: int
    ) -> list[AdminApplicationRow]:
        """Applications with the applicant's address beside them.

        The address is joined here rather than read off ``User``, which does not carry
        one: an account's email lives on ``UserIdentity``, and a template reaching for
        ``user.email`` gets an empty cell rather than an error. An administrator deciding
        who represents the product has to be able to see who they are deciding about, so
        a blank there is not a cosmetic problem.
        """

        condition = (
            AffiliateApplication.status == _STATUS_PENDING
            if pending
            else AffiliateApplication.status != _STATUS_PENDING
        )
        order = (
            AffiliateApplication.submitted_at.asc()
            if pending
            else AffiliateApplication.decided_at.desc()
        )
        rows = await self.session.execute(
            select(AffiliateApplication, User)
            .join(User, User.id == AffiliateApplication.user_id)
            .where(condition)
            .order_by(order)
            .limit(limit)
        )
        pairs = list(rows)
        emails = await self._emails_for({user.id for _, user in pairs})
        return [
            AdminApplicationRow(
                application=application,
                user=user,
                email=emails.get(user.id, "No email on the account"),
            )
            for application, user in pairs
        ]

    async def _emails_for(self, user_ids: set[UUID]) -> dict[UUID, str]:
        """One query for every address, rather than one per row.

        A per-row lookup on a page listing fifty applications is fifty round trips, and
        this database has already been brought to a stop once by a list view that read a
        row at a time.
        """

        if not user_ids:
            return {}
        rows = await self.session.execute(
            select(UserIdentity.user_id, UserIdentity.normalized_identifier)
            .where(
                UserIdentity.user_id.in_(user_ids),
                UserIdentity.provider == IdentityProvider.EMAIL,
            )
            .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
        )
        found: dict[UUID, str] = {}
        for user_id, identifier in rows:
            if identifier:
                found.setdefault(user_id, identifier)
        return found

    async def approve(
        self,
        *,
        application_id: UUID,
        admin_user_id: UUID,
        discount_code: str | None = None,
        discount_percent: object = None,
        commission_percent: object = None,
        decision_note: str | None = None,
    ) -> AffiliateApplication:
        """Grant the application, and create the code a customer will type.

        Every money field left blank falls back to something stated rather than guessed:
        the code the applicant asked for, and :data:`DEFAULT_COMMISSION_PERCENT`. The
        discount has no such fallback — a discount nobody chose is a price change nobody
        approved — so it must be filled in.
        """

        application = await self._pending(application_id)
        code = normalize_discount_code(discount_code or application.requested_discount_code)
        if discount_percent is None or str(discount_percent).strip() == "":
            raise AffiliateError(
                "discount_missing",
                "Set the discount this code gives the customer before approving.",
            )
        discount = _percent(discount_percent, field="Discount")
        share = (
            DEFAULT_COMMISSION_PERCENT
            if commission_percent is None or str(commission_percent).strip() == ""
            else _percent(commission_percent, field="Commission")
        )

        clash = await self.session.scalar(
            select(ReferralCode).where(ReferralCode.code == code)
        )
        if clash is not None and clash.owner_user_id != application.user_id:
            raise AffiliateError(
                "code_taken",
                f"The code {code} already belongs to somebody else. Choose another.",
            )

        referral_code = clash
        if referral_code is None:
            referral_code = ReferralCode(
                owner_user_id=application.user_id,
                code=code,
                campaign="affiliate",
                is_active=True,
            )
            self.session.add(referral_code)
            await self.session.flush()
        else:
            referral_code.is_active = True
            referral_code.campaign = "affiliate"

        application.status = _STATUS_APPROVED
        application.discount_code = code
        application.discount_percent = discount
        application.commission_percent = share
        application.referral_code_id = referral_code.id
        application.decided_by_user_id = admin_user_id
        application.decided_at = datetime.now(UTC)
        application.decision_note = (str(decision_note or "").strip() or None)

        self._audit(
            actor_user_id=admin_user_id,
            actor_type="admin",
            action="affiliate.application_approved",
            target_id=application.id,
            metadata={
                "code": code,
                "discount_percent": str(discount),
                "commission_percent": str(share),
            },
        )
        await self.session.flush()
        return application

    async def reject(
        self,
        *,
        application_id: UUID,
        admin_user_id: UUID,
        decision_note: str | None = None,
    ) -> AffiliateApplication:
        """Refuse it, and let the person apply again.

        The reason is optional in the form and always sent in the email, because an
        answer of "no" with nothing after it is the one thing an applicant cannot act on.
        """

        application = await self._pending(application_id)
        application.status = _STATUS_REJECTED
        application.decided_by_user_id = admin_user_id
        application.decided_at = datetime.now(UTC)
        application.decision_note = str(decision_note or "").strip() or None
        self._audit(
            actor_user_id=admin_user_id,
            actor_type="admin",
            action="affiliate.application_rejected",
            target_id=application.id,
            metadata={"has_reason": bool(application.decision_note)},
        )
        await self.session.flush()
        return application

    async def _pending(self, application_id: UUID) -> AffiliateApplication:
        application = await self.session.get(AffiliateApplication, application_id)
        if application is None:
            raise AffiliateError("application_missing", "That application no longer exists.")
        if application.status != _STATUS_PENDING:
            raise AffiliateError(
                "already_decided",
                f"This application was already {application.status}.",
            )
        return application

    # ── What the affiliate has earned ───────────────────────────────────────────

    async def stats(self, application: AffiliateApplication) -> AffiliateStats:
        """Everything the affiliate's own page shows, from stored rows only."""

        share = application.commission_percent or DEFAULT_COMMISSION_PERCENT
        rows = await self.session.execute(
            select(ReferralRelationship, User)
            .join(User, User.id == ReferralRelationship.referred_user_id)
            .where(ReferralRelationship.referrer_user_id == application.user_id)
            .order_by(ReferralRelationship.created_at.desc())
        )
        earnings: list[ReferralEarning] = []
        total = Decimal("0")
        paid_conversions = 0
        for relationship, customer in rows:
            eligible = relationship.reward_status in ELIGIBLE_REWARD_STATUSES
            commission = self._commission_for(relationship, share) if eligible else Decimal("0")
            if eligible:
                paid_conversions += 1
                total += commission
            earnings.append(
                ReferralEarning(
                    customer_name=_display_name(customer),
                    joined_at=relationship.created_at,
                    converted_at=relationship.reward_granted_at,
                    commission_usd=commission,
                    is_paid_conversion=eligible,
                )
            )

        held = await self.session.scalar(
            select(func.coalesce(func.sum(AffiliatePayoutRequest.amount_usd), 0)).where(
                AffiliatePayoutRequest.user_id == application.user_id,
                AffiliatePayoutRequest.status.in_({_PAYOUT_PENDING, _PAYOUT_PAID}),
            )
        )
        held_amount = Decimal(str(held or 0))
        available = total - held_amount
        return AffiliateStats(
            uses=len(earnings),
            paid_conversions=paid_conversions,
            total_commission_usd=total.quantize(Decimal("0.01")),
            requested_or_paid_usd=held_amount.quantize(Decimal("0.01")),
            available_usd=max(Decimal("0"), available).quantize(Decimal("0.01")),
            earnings=tuple(earnings),
        )

    @staticmethod
    def _commission_for(relationship: ReferralRelationship, share: Decimal) -> Decimal:
        """What this referral earned, from what the customer actually paid.

        The paid amount is written onto the relationship when the conversion is recorded.
        Without one there is nothing to take a share of, and the honest answer is zero —
        never an assumed plan price, which would show an affiliate money that does not
        exist and let them request a payout of it.

        The share is the affiliate's current one, applied to every referral. That is safe
        only because there is no way to change an approved affiliate's share: approval
        happens once, and ``approve`` refuses an application that is not pending. **If a
        route to change it is ever added, the share has to be frozen onto the
        relationship at conversion the way the paid amount already is** — otherwise
        changing it would silently rewrite what past referrals earned, including money an
        affiliate has already been shown.
        """

        metadata = relationship.metadata_json or {}
        raw = metadata.get("paid_amount_usd")
        if raw is None:
            return Decimal("0")
        try:
            paid = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")
        if paid <= 0:
            return Decimal("0")
        return (paid * share / Decimal("100")).quantize(Decimal("0.01"))

    # ── Payouts ─────────────────────────────────────────────────────────────────

    async def payout_requests(
        self, user_id: UUID, limit: int = 50
    ) -> list[AffiliatePayoutRequest]:
        rows = await self.session.scalars(
            select(AffiliatePayoutRequest)
            .where(AffiliatePayoutRequest.user_id == user_id)
            .order_by(AffiliatePayoutRequest.requested_at.desc())
            .limit(limit)
        )
        return list(rows)

    async def request_payout(
        self,
        *,
        application: AffiliateApplication,
        currency: str,
        network: str,
        destination_address: str,
    ) -> AffiliatePayoutRequest:
        if application.status != _STATUS_APPROVED:
            raise AffiliateError(
                "not_an_affiliate",
                "Payouts are for approved affiliates.",
            )
        chosen = network_for(str(currency or "").upper(), str(network or "").lower())
        if chosen is None:
            raise AffiliateError(
                "unsupported_destination",
                "Pick one of the coins and networks on the list.",
            )
        address = str(destination_address or "").strip()
        if len(address) < 12 or len(address) > 160 or re.search(r"\s", address):
            raise AffiliateError(
                "invalid_address",
                "That does not look like a wallet address. Copy it from your wallet.",
            )

        stats = await self.stats(application)
        if stats.available_usd < MINIMUM_PAYOUT_USD:
            raise AffiliateError(
                "below_minimum",
                f"You can ask for a payout once you have ${MINIMUM_PAYOUT_USD:.2f}. "
                f"You have ${stats.available_usd:.2f} right now.",
            )

        request = AffiliatePayoutRequest(
            application_id=application.id,
            user_id=application.user_id,
            amount_usd=stats.available_usd,
            currency=chosen_currency(currency),
            network=chosen.key,
            destination_address=address,
            status=_PAYOUT_PENDING,
            requested_at=datetime.now(UTC),
            metadata_json={
                "network_label": chosen.label,
                "typical_fee_usd": str(chosen.typical_fee_usd),
            },
        )
        self.session.add(request)
        self._audit(
            actor_user_id=application.user_id,
            actor_type="user",
            action="affiliate.payout_requested",
            target_id=None,
            metadata={"amount_usd": str(stats.available_usd), "currency": request.currency},
        )
        await self.session.flush()
        return request

    async def pending_payouts(self, limit: int = 50) -> list[AdminPayoutRow]:
        return await self._payout_rows(pending=True, limit=limit)

    async def decided_payouts(self, limit: int = 50) -> list[AdminPayoutRow]:
        return await self._payout_rows(pending=False, limit=limit)

    async def _payout_rows(self, *, pending: bool, limit: int) -> list[AdminPayoutRow]:
        condition = (
            AffiliatePayoutRequest.status == _PAYOUT_PENDING
            if pending
            else AffiliatePayoutRequest.status != _PAYOUT_PENDING
        )
        order = (
            AffiliatePayoutRequest.requested_at.asc()
            if pending
            else AffiliatePayoutRequest.decided_at.desc()
        )
        rows = await self.session.execute(
            select(AffiliatePayoutRequest, User)
            .join(User, User.id == AffiliatePayoutRequest.user_id)
            .where(condition)
            .order_by(order)
            .limit(limit)
        )
        pairs = list(rows)
        emails = await self._emails_for({user.id for _, user in pairs})
        return [
            AdminPayoutRow(
                payout=payout,
                user=user,
                email=emails.get(user.id, "No email on the account"),
            )
            for payout, user in pairs
        ]

    async def settle_payout(
        self,
        *,
        payout_id: UUID,
        admin_user_id: UUID,
        status: str,
        transaction_reference: str | None = None,
        decision_note: str | None = None,
    ) -> AffiliatePayoutRequest:
        """Mark a payout paid or refused. Both are final; neither rewrites the amount."""

        if status not in {_PAYOUT_PAID, _PAYOUT_REJECTED}:
            raise AffiliateError("unknown_status", "A payout is either paid or refused.")
        request = await self.session.get(AffiliatePayoutRequest, payout_id)
        if request is None:
            raise AffiliateError("payout_missing", "That payout request no longer exists.")
        if request.status != _PAYOUT_PENDING:
            raise AffiliateError(
                "already_settled",
                f"This payout was already marked {request.status}.",
            )
        request.status = status
        request.decided_by_user_id = admin_user_id
        request.decided_at = datetime.now(UTC)
        request.decision_note = str(decision_note or "").strip() or None
        request.transaction_reference = str(transaction_reference or "").strip() or None
        self._audit(
            actor_user_id=admin_user_id,
            actor_type="admin",
            action=f"affiliate.payout_{status}",
            target_id=request.id,
            metadata={"amount_usd": str(request.amount_usd), "currency": request.currency},
        )
        await self.session.flush()
        return request

    def _audit(
        self,
        *,
        actor_user_id: UUID,
        actor_type: str,
        action: str,
        target_id: UUID | None,
        metadata: dict[str, object],
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type=actor_type,
                action=action,
                target_type="affiliate",
                target_id=str(target_id) if target_id else None,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )


def chosen_currency(value: object) -> str:
    return str(value or "").upper()


def _display_name(user: User) -> str:
    """A customer's name for the affiliate to read, and never their email address."""

    name = str(getattr(user, "display_name", "") or "").strip()
    if name:
        return name
    # No name on the account. Better a plain placeholder than the local part of an email
    # address, which is the address in all but punctuation.
    return "A Hilal Markets member"

"""Cancelling, moving up a plan, and moving down a plan. One owner for all three.

A person on a paid plan can do exactly three things to it, and every one of them is the
same shape: a form asking why, a sentence they must tick, a record written down, and then
a request to the payment company. Writing that three times would be three chances to
forget the record, or to ask the payment company for something the form did not promise.

**The record is written first, always.** The payment company can be slow, unreachable, or
can refuse. A request that only ever existed inside a failed API call is a request nobody
can see afterwards — not the customer, not support, not the System Brain. So the row lands
in ``subscription_plan_changes`` before Creem is asked anything, and its ``status`` says
how far it got.

**Nothing here creates a payment page.** Creem can change a subscription's product and
schedule a cancellation through its own API, so an upgrade and a downgrade are a form and
a tick box — no checkout, no second card entry, no second address form. The three
behaviours Creem offers map onto exactly what this product promises:

===========================  ====================================  ==========================
What the customer chose      What Creem is asked                   What they are charged
===========================  ====================================  ==========================
Pro now                      ``upgrade`` / charge immediately      the difference, now
Pro at the end of the month  ``upgrade`` / no proration            nothing now; Pro at renewal
Plus at the end of the month ``upgrade`` / no proration            nothing now; Plus at renewal
Cancel                       ``cancel`` / scheduled                nothing, ever again
===========================  ====================================  ==========================

The two "at the end of the month" rows are the ones that carry the money promise the
product makes: **the next charge happens on the renewal date, at the new plan's price.**
Not today, and not at the old price. Creem's "no proration" behaviour is precisely that.

**What the customer may use is decided here, not by Creem.** A change timed for the end of
the period leaves ``Subscription.plan_id`` alone, so the limits do not move until the
period they already paid for has finished. :meth:`PlanChangeService.apply_due_changes`
moves it at that moment, and it is the same moment Creem's renewal charges the new price.

Crypto is a different world and says so. A NOWPayments payment buys thirty days and there
is no card on file to charge again, so there is nothing to cancel and nothing to re-price:
moving up a plan means buying it. The dialogs read that answer from here rather than
guessing from the provider's name.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import (
    PLAN_CHANGE_DOWNGRADE,
    PLAN_CHANGE_NEW,
    PLAN_CHANGE_SAME,
    PLAN_CHANGE_UPGRADE,
    PLAN_DEFINITIONS,
    PURCHASABLE_PLAN_CODES,
    effective_monthly_price,
    plan_change_kind,
    plan_name,
)
from ai_market_monitor.db.models import (
    AuditEvent,
    Plan,
    Subscription,
    SubscriptionPlanChange,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services.billing import RECURRING_PROVIDERS
from ai_market_monitor.services.entitlements import (
    EntitlementService,
    PlanCatalogService,
)
from ai_market_monitor.services.plan_replacements import manual_return_window_words
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request

logger = logging.getLogger(__name__)

__all__ = [
    "CANCELLATION_REASONS",
    "CONSENT_CANCEL",
    "CONSENT_DOWNGRADE",
    "CONSENT_UPGRADE_NOW",
    "CONSENT_UPGRADE_PERIOD_END",
    "DOWNGRADE_REASONS",
    "OTHER_REASON_CODE",
    "PlanChangeError",
    "PlanChangeService",
    "PlanSwitchOffer",
    "TIMING_IMMEDIATE",
    "TIMING_PERIOD_END",
    "reason_words",
]


# ── The words on the forms ───────────────────────────────────────────────────────
#
# Written here, not in a template. Three things need the same list: the form draws it, the
# route checks that what came back is on it, and the System Brain turns a stored code back
# into words months later. A list that lives only in HTML cannot be checked and cannot be
# read back.

#: The reason picked when none of the offered ones fit. Choosing it opens a box to type in.
OTHER_REASON_CODE: Final[str] = "other"

CANCELLATION_REASONS: Final[tuple[tuple[str, str], ...]] = (
    ("too_many_bugs", "It has too many bugs"),
    ("do_not_need_it", "I do not need it any more"),
    ("found_better", "I found a better option"),
    ("too_expensive", "It is too expensive"),
    ("quitting_trading", "I am stopping trading"),
    ("not_enough_alerts", "I did not get enough useful alerts"),
    ("too_hard_to_use", "It was too hard to use"),
    ("missing_coins", "The coins I want are not on it"),
    (OTHER_REASON_CODE, "Another reason"),
)

DOWNGRADE_REASONS: Final[tuple[tuple[str, str], ...]] = (
    ("too_expensive", "Pro is too expensive for me"),
    ("do_not_use_pro", "I do not use the extra Pro limits"),
    ("fewer_monitors", "I need fewer market monitors now"),
    ("too_many_alerts", "I get more alerts than I want"),
    ("trading_less", "I am trading less at the moment"),
    (OTHER_REASON_CODE, "Another reason"),
)


def reason_words(kind: str, code: str | None) -> str:
    """A stored reason code turned back into the words the person read."""

    if not code:
        return "No reason given"
    offered = DOWNGRADE_REASONS if kind == PLAN_CHANGE_DOWNGRADE else CANCELLATION_REASONS
    for value, words in offered:
        if value == code:
            return words
    return code.replace("_", " ").capitalize()


#: Exactly what somebody agrees to. Stored word for word on the change, because a tick box
#: records that a person agreed and only the sentence records what they agreed to.
CONSENT_CANCEL: Final[str] = (
    "I want to cancel. My card will not be charged again, and my plan keeps working "
    "until the end of the period I have already paid for."
)
CONSENT_DOWNGRADE: Final[str] = (
    "I understand I pay the full price of the new plan today. It starts today and my "
    "old paid period ends today. A person will send me the value of its unused time "
    f"{manual_return_window_words()}."
)
CONSENT_UPGRADE_NOW: Final[str] = (
    "I understand I pay the full price of the new plan today. It starts today and my "
    "old paid period ends today. A person will send me the value of its unused time "
    f"{manual_return_window_words()}."
)
CONSENT_UPGRADE_PERIOD_END: Final[str] = (
    "I understand I pay the full price of the new plan today. It starts today and my "
    "old paid period ends today. A person will send me the value of its unused time "
    f"{manual_return_window_words()}."
)

#: When a change takes effect.
TIMING_IMMEDIATE: Final[str] = "immediate"
TIMING_PERIOD_END: Final[str] = "period_end"

# Creem's ``update_behavior`` table used to be here. All three of its values are prorated
# and none restarts the paid period, so none of them can express the owner's rule. The
# table is gone so that no future reader can reach for it as "the nearest thing".

#: Providers that hold a card and can be asked to charge it again. Anything else — a
#: crypto invoice, an administrator's grant, a free plan — has nothing to re-price.
#:
#: Imported, not written out again. This file used to keep its own copy of the list while
#: `services/billing.py` decided who may buy a plan without it, so the two modules
#: disagreed: this one said "buying is the only route" and that one refused the purchase.
_RECURRING_PROVIDERS: Final[frozenset[str]] = RECURRING_PROVIDERS

#: The words on a plan card's button. Named here because they are the only words a page
#: may print on that button, and because a test that checks the page has to read the same
#: words rather than keep its own copy. A copy is how "Choose Monitor monthly" outlived
#: both the plan name and the button that said it.
SWITCH_LABEL_CURRENT: Final[str] = "Current plan"
SWITCH_LABEL_UNAVAILABLE: Final[str] = "Not available"
SWITCH_LABEL_BUY: Final[str] = "Pay"
#: The same button after the page's monthly/yearly switch is moved to yearly. The page
#: swaps the word without asking the server again, so it needs both words up front — but
#: it is given them, it does not know them.
SWITCH_LABEL_BUY_ANNUAL: Final[str] = "Pay for a year"
SWITCH_LABEL_UPGRADE: Final[str] = "Upgrade"
SWITCH_LABEL_DOWNGRADE: Final[str] = "Downgrade"


def switch_label_booked(name: str) -> str:
    """A plan already chosen for the end of the paid period."""

    return f"{name} is booked"


def switch_label_soon(name: str) -> str:
    """A plan that exists but cannot be paid for yet."""

    return f"{name} is coming soon"


class PlanChangeError(ValueError):
    """A plan change cannot be made, and why — in words a beginner can act on."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PlanSwitchOffer:
    """What one plan card's button should do for this person, right now.

    Decided on the server so a page cannot draw a button the routes refuse. Every field
    is an answer, not an ingredient: the template prints ``label`` and reads ``action``,
    and works nothing out for itself.
    """

    plan_code: str
    #: ``current``, ``unavailable``, ``buy``, ``upgrade``, ``downgrade`` or ``soon``.
    action: str
    label: str
    #: Whether the button can be pressed at all.
    enabled: bool
    #: A sentence under the button when it cannot be pressed, or ``None``.
    note: str | None = None
    #: Whether moving to this plan needs a payment page rather than a form. True only for
    #: crypto access, which holds no card to charge.
    needs_checkout: bool = False


class PlanChangeService:
    """Everything a customer can do to the plan they are already paying for."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    # ── Reading the current state ────────────────────────────────────────────

    async def paid_subscription(self, user_id: UUID) -> tuple[Subscription, Plan] | None:
        """The live paid subscription and its plan, or ``None``.

        "Paid" excludes the free plan, an administrator's grant and a trial. Those three
        are recorded as subscriptions too, and treating one of them as something that can
        be cancelled would offer a Cancel button that stops nothing.
        """

        now = datetime.now(UTC)
        row = (
            await self.session.execute(
                select(Subscription, Plan)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.provider.notin_(("admin", "free", "trial")),
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
                    ),
                    Plan.code.in_(PURCHASABLE_PLAN_CODES),
                    (Subscription.current_period_end.is_(None))
                    | (Subscription.current_period_end > now),
                )
                .order_by(Subscription.updated_at.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        return (row[0], row[1])

    async def pending_change(self, user_id: UUID) -> SubscriptionPlanChange | None:
        """A change already asked for and not yet in force, or ``None``.

        One at a time. Letting somebody schedule a downgrade and then a cancellation would
        leave two rows both claiming to own what happens at the end of the period, and
        whichever ran second would silently win.
        """

        return await self.session.scalar(
            select(SubscriptionPlanChange)
            .where(
                SubscriptionPlanChange.user_id == user_id,
                SubscriptionPlanChange.status.in_(("requested", "scheduled")),
            )
            .order_by(SubscriptionPlanChange.created_at.desc())
            .limit(1)
        )

    def change_needs_checkout(self, subscription: Subscription) -> bool:
        """Does moving this person to another plan need a payment page?

        Yes when nothing holds a card. A crypto payment buys thirty days and leaves
        nothing behind to charge, so there is no subscription to re-price and the only way
        to a different plan is to buy it.
        """

        del subscription
        return True

    async def switch_offers(
        self,
        user_id: UUID,
        *,
        purchasable: Mapping[str, bool],
    ) -> dict[str, PlanSwitchOffer]:
        """What every public plan's button should say and do for this person.

        ``purchasable`` is whether each plan can be paid for at all right now — the
        billing service's answer, passed in rather than worked out again here.
        """

        held = await self.paid_subscription(user_id)
        current_code = held[1].code if held else None
        pending = await self.pending_change(user_id)
        offers: dict[str, PlanSwitchOffer] = {}
        for code in PLAN_DEFINITIONS:
            if code not in ("demo", *PURCHASABLE_PLAN_CODES):
                continue
            offers[code] = self._switch_offer(
                plan_code=code,
                current_code=current_code,
                subscription=held[0] if held else None,
                pending=pending,
                purchasable=bool(purchasable.get(code, False)),
            )
        return offers

    def _switch_offer(
        self,
        *,
        plan_code: str,
        current_code: str | None,
        subscription: Subscription | None,
        pending: SubscriptionPlanChange | None,
        purchasable: bool,
    ) -> PlanSwitchOffer:
        name = plan_name(plan_code)
        if pending is not None and pending.to_plan_code == plan_code:
            return PlanSwitchOffer(
                plan_code=plan_code,
                action="current",
                label=switch_label_booked(name),
                enabled=False,
                note="This change is already set for the end of your paid period.",
            )
        if plan_code == current_code:
            if pending is not None and pending.kind == "cancel":
                return PlanSwitchOffer(
                    plan_code=plan_code,
                    action="current",
                    label=SWITCH_LABEL_CURRENT,
                    enabled=False,
                    note="Cancelling at the end of your paid period.",
                )
            return PlanSwitchOffer(
                plan_code=plan_code,
                action="current",
                label=SWITCH_LABEL_CURRENT,
                enabled=False,
            )
        # The free plan while something is being paid for. It is not "current", and it
        # cannot be bought either — the way back to it is to cancel the paid plan, which
        # the card above already offers. Saying "Not available" here rather than drawing a
        # dead Pay button is the honest answer.
        if plan_code == "demo":
            if current_code is None:
                return PlanSwitchOffer(
                    plan_code=plan_code,
                    action="current",
                    label=SWITCH_LABEL_CURRENT,
                    enabled=False,
                )
            return PlanSwitchOffer(
                plan_code=plan_code,
                action="unavailable",
                label=SWITCH_LABEL_UNAVAILABLE,
                enabled=False,
                note=f"Cancel {plan_name(current_code)} to come back to this plan.",
            )
        if not purchasable:
            return PlanSwitchOffer(
                plan_code=plan_code,
                action="soon",
                label=switch_label_soon(name),
                enabled=False,
            )
        kind = plan_change_kind(current=current_code, target=plan_code)
        if kind in (PLAN_CHANGE_NEW, PLAN_CHANGE_SAME):
            return PlanSwitchOffer(
                plan_code=plan_code, action="buy", label=SWITCH_LABEL_BUY, enabled=True
            )
        needs_checkout = subscription is not None and self.change_needs_checkout(subscription)
        if kind == PLAN_CHANGE_UPGRADE:
            return PlanSwitchOffer(
                plan_code=plan_code,
                action="upgrade",
                label=SWITCH_LABEL_UPGRADE,
                enabled=True,
                needs_checkout=needs_checkout,
            )
        return PlanSwitchOffer(
            plan_code=plan_code,
            action="downgrade",
            label=SWITCH_LABEL_DOWNGRADE,
            enabled=True,
            needs_checkout=needs_checkout,
        )

    # ── Making a change ──────────────────────────────────────────────────────

    async def request_cancellation(
        self,
        *,
        user_id: UUID,
        reason_code: str,
        reason_text: str,
        consented: bool,
    ) -> SubscriptionPlanChange:
        """Stop the plan renewing. Access runs to the end of the period already paid for."""

        held = await self._require_paid(user_id)
        subscription, plan = held
        change = await self._record(
            user_id=user_id,
            subscription=subscription,
            kind="cancel",
            from_plan_code=plan.code,
            to_plan_code=None,
            timing=TIMING_PERIOD_END,
            reason_code=reason_code,
            reason_text=reason_text,
            consented=consented,
            consent_text=CONSENT_CANCEL,
            offered=CANCELLATION_REASONS,
        )
        await self._stop_the_card(subscription, change)
        # Written on our side whatever the payment company said, because this is the flag
        # `BillingService.expire_ended_access` reads when the period ends. A cancellation
        # that Creem accepted but we never recorded would leave access running for ever.
        subscription.cancel_at_period_end = True
        await self.session.flush()
        return change

    async def request_switch(
        self,
        *,
        user_id: UUID,
        to_plan_code: str,
        timing: str,
        reason_code: str | None,
        reason_text: str,
        consented: bool,
    ) -> SubscriptionPlanChange:
        """Refuse every move between paid plans, and say where it is really done.

        This used to ask the payment company to re-price the subscription the person
        already had. The owner ruled that out on 2026-09-10: a different paid plan costs
        its **full** price today, and the payment company has no behaviour that does
        that. The move is made by :mod:`ai_market_monitor.services.plan_replacements`
        after a normal checkout is paid for.

        Nothing is recorded here and nothing is sent to the payment company, so this can
        never take money. It still answers, because a page left open in an old tab can
        still post the old form, and that person deserves to be told where to go.
        """

        del timing, reason_code, reason_text, consented
        held = await self._require_paid(user_id)
        subscription, plan = held
        if to_plan_code not in PURCHASABLE_PLAN_CODES:
            raise PlanChangeError("plan_not_available", "That plan cannot be bought.")
        kind = plan_change_kind(current=plan.code, target=to_plan_code)
        if kind not in (PLAN_CHANGE_UPGRADE, PLAN_CHANGE_DOWNGRADE):
            raise PlanChangeError(
                "plan_change_not_needed", "You are already on that plan."
            )
        # Asked through the one owner rather than assumed, so a single answer decides
        # both what this refuses and what the plan cards draw.
        if not self.change_needs_checkout(subscription):  # pragma: no cover - always true
            raise PlanChangeError(
                "plan_change_not_available",
                "This plan change cannot be made here. Please write to us.",
            )
        raise PlanChangeError(
            "plan_change_needs_payment",
            "The new plan must be paid for on the normal payment page first. "
            "Return to billing, choose the new plan, and press Pay.",
        )

    async def apply_due_changes(self, *, now: datetime | None = None) -> int:
        """Move access for every change whose period has now ended.

        Run from the scheduler. The payment company was told at the time what to charge on
        the renewal date; this is the other half — moving what the person may actually use
        on the same day, so the limits and the price change together.
        """

        moment = now or datetime.now(UTC)
        due = list(
            (
                await self.session.scalars(
                    select(SubscriptionPlanChange).where(
                        SubscriptionPlanChange.status == "scheduled",
                        SubscriptionPlanChange.effective_at.is_not(None),
                        SubscriptionPlanChange.effective_at <= moment,
                        SubscriptionPlanChange.to_plan_code.is_not(None),
                    )
                )
            ).all()
        )
        for change in due:
            subscription = (
                await self.session.get(Subscription, change.subscription_id)
                if change.subscription_id
                else None
            )
            await self._move_access(change, subscription=subscription, now=moment)
        await self.session.flush()
        return len(due)

    # ── The parts ────────────────────────────────────────────────────────────

    async def _require_paid(self, user_id: UUID) -> tuple[Subscription, Plan]:
        held = await self.paid_subscription(user_id)
        if held is None:
            raise PlanChangeError(
                "no_paid_plan", "There is no paid plan on this account to change."
            )
        pending = await self.pending_change(user_id)
        if pending is not None:
            raise PlanChangeError(
                "change_already_requested",
                "A change is already booked for the end of your paid period. Write to us "
                "if you want a different one.",
            )
        return held

    async def _record(
        self,
        *,
        user_id: UUID,
        subscription: Subscription,
        kind: str,
        from_plan_code: str,
        to_plan_code: str | None,
        timing: str,
        reason_code: str | None,
        reason_text: str,
        consented: bool,
        consent_text: str,
        offered: tuple[tuple[str, str], ...] | None,
    ) -> SubscriptionPlanChange:
        if not consented:
            raise PlanChangeError(
                "consent_required", "Tick the box to say you agree before continuing."
            )
        cleaned_text = " ".join(str(reason_text or "").split())[:500]
        if offered is not None:
            allowed = {value for value, _ in offered}
            if reason_code not in allowed:
                raise PlanChangeError(
                    "reason_required", "Choose one reason from the list first."
                )
            if reason_code == OTHER_REASON_CODE and not cleaned_text:
                raise PlanChangeError(
                    "reason_text_required",
                    "You chose another reason, so please write it in the box.",
                )
        else:
            reason_code = None
        now = datetime.now(UTC)
        change = SubscriptionPlanChange(
            user_id=user_id,
            subscription_id=subscription.id,
            kind=kind,
            from_plan_code=from_plan_code,
            to_plan_code=to_plan_code,
            timing=timing,
            status="requested",
            reason_code=reason_code,
            reason_text=cleaned_text or None,
            consent_text=consent_text,
            consented_at=now,
            effective_at=(
                now if timing == TIMING_IMMEDIATE else subscription.current_period_end
            ),
            provider=subscription.provider,
            metadata_json={
                "from_price": str(effective_monthly_price(from_plan_code)),
                "to_price": (
                    str(effective_monthly_price(to_plan_code)) if to_plan_code else None
                ),
            },
        )
        self.session.add(change)
        await self.session.flush()
        self._audit(
            user_id,
            f"billing.plan_change_{kind}",
            change.id,
            {
                "from_plan_code": from_plan_code,
                "to_plan_code": to_plan_code or "",
                "timing": timing,
                "reason_code": reason_code or "",
            },
        )
        return change

    async def _stop_the_card(
        self, subscription: Subscription, change: SubscriptionPlanChange
    ) -> None:
        """Tell the payment company not to charge this card again."""

        if (subscription.provider or "") not in _RECURRING_PROVIDERS:
            # A crypto invoice or a local test payment holds no card, so there is nothing
            # to stop. The access already ends on its own at the end of the thirty days.
            change.status = "scheduled"
            change.metadata_json = {
                **dict(change.metadata_json or {}),
                "note": "No card was held, so nothing had to be stopped.",
            }
            await self.session.flush()
            return
        if subscription.provider != "creem":
            change.status = "scheduled"
            await self.session.flush()
            return
        try:
            await self._creem_post(
                f"/v1/subscriptions/{subscription.provider_subscription_id}/cancel",
                {"mode": "scheduled", "onExecute": "cancel"},
            )
        except PlanChangeError as exc:
            # The record stays, marked failed, and the customer is told plainly. Pretending
            # this worked is the one outcome that costs somebody money.
            change.status = "failed"
            change.provider_error = str(exc)[:500]
            await self.session.flush()
            raise
        change.status = "scheduled"
        change.provider_reference = subscription.provider_subscription_id
        await self.session.flush()

    # ``_retune_the_card`` used to live here. It asked Creem to re-price the subscription
    # somebody already had, with ``update_behavior`` set to one of Creem's three prorated
    # behaviours. Every one of them charges the *difference*, which is exactly what the
    # owner ruled out on 2026-09-10, and none of them restarts the paid period. It is
    # deleted rather than left unreachable: an unreachable prorated charge is one changed
    # boolean away from silently taking the wrong amount of money again.

    async def _move_access(
        self,
        change: SubscriptionPlanChange,
        *,
        subscription: Subscription | None,
        now: datetime | None = None,
    ) -> None:
        """Move what the person may actually use onto the new plan."""

        if subscription is None or not change.to_plan_code:
            change.status = "failed"
            change.provider_error = "The subscription behind this change is gone."
            return
        plan = await PlanCatalogService(self.session).get_or_sync(change.to_plan_code)
        subscription.plan_id = plan.id
        change.status = "applied"
        change.applied_at = now or datetime.now(UTC)
        await self.session.flush()
        await EntitlementService(self.session).snapshot(subscription.user_id)
        # Moving down a plan can leave more monitors running than the new plan allows.
        # Pausing the newest ones is the existing rule for that, and it is the same rule
        # here — a downgrade must not quietly keep running what it no longer includes.
        await EntitlementService(self.session).pause_excess_after_downgrade(
            subscription.user_id
        )
        self._audit(
            subscription.user_id,
            "billing.plan_change_applied",
            change.id,
            {
                "from_plan_code": change.from_plan_code,
                "to_plan_code": change.to_plan_code,
                "timing": change.timing,
            },
        )

    async def _creem_post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        secret = self.settings.creem_api_key
        if secret is None:
            raise PlanChangeError(
                "billing_not_configured",
                "Card billing is not finished being set up, so nothing was changed.",
            )
        try:
            response = await provider_request(
                self.settings,
                "POST",
                f"{str(self.settings.creem_api_base).rstrip('/')}{path}",
                provider="creem",
                operation=path.strip("/").replace("/", "_"),
                timeout=self.settings.creem_timeout_seconds,
                # Money. A change that was made and then timed out looks exactly like one
                # that was not, and repeating an immediate upgrade would charge twice.
                mutation_committed=True,
                headers={
                    "x-api-key": secret.get_secret_value(),
                    "Content-Type": "application/json",
                    "User-Agent": "HilalMarkets/1.0",
                },
                json=dict(payload),
            )
        except httpx.TimeoutException as exc:
            raise PlanChangeError(
                "billing_timeout",
                "The payment company did not answer in time. Nothing was changed. "
                "Please try again in a few minutes.",
            ) from exc
        except (httpx.RequestError, ProviderCallError) as exc:
            raise PlanChangeError(
                "billing_unavailable",
                "The payment company cannot be reached just now. Nothing was changed.",
            ) from exc
        if response.is_error:
            logger.warning(
                "creem plan change refused",
                extra={"path": path, "status": response.status_code},
            )
            raise PlanChangeError(
                "billing_refused",
                "The payment company refused the change, so nothing was changed. "
                "Please write to us and we will sort it out.",
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}

    def _audit(
        self,
        user_id: UUID,
        action: str,
        change_id: UUID | None,
        metadata: dict[str, Any],
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=user_id,
                actor_type="user",
                action=action,
                target_type="subscription_plan_change",
                target_id=str(change_id) if change_id else None,
                metadata_redacted=metadata,
                created_at=datetime.now(UTC),
            )
        )

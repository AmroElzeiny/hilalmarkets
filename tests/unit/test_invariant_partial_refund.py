"""A refund is only a full refund when the event says the whole payment came back.

A refund event is treated today as a full refund: the refunded amount is never read, the
attempt is moved to ``refunded``, the plan it bought is ended, and the standing manual
payout is voided. A $1 refund on a $25 payment erases the whole $25.

The rule these tests pin: **read the amount the event carries**. Unknown amount, or a
refunded total at or above the payment, means a full refund and keeps today's behaviour
byte-for-byte. A known, smaller amount means a partial refund: the payment stays
``completed``, the refunded total is stored on it, the plan is **not** ended, staff get
one critical alert, and the money-owed row (if any) is re-valued to the money kept —
voided only when nothing is kept.

``core.money.money_kept`` is the one owner of "what this payment still holds"; every
money reader (the plan-move valuation, the affiliate commission, the staff payments
view and totals) must read through it.

The matrix is the whole family, not the reported case: every refund name × every company
× every amount shape (unknown, partial, exact full, over-full) × every delivery shape
(single, same-event replay, two partials that sum to the payment). Known amounts are put
directly into ``data`` as ``refunded_amount`` (major units) because that is the
normalized contract the provider normaliser produces — the normaliser itself is pinned
separately below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.plans import effective_monthly_price
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    BillingEvent,
    OperationalIssue,
    PlanMoveMoneyOwed,
    Subscription,
)
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services import plan_replacements as replacement_module
from ai_market_monitor.services.billing import (
    REFUND_ENDS_PLAN_EVENT_TYPES,
    BillingService,
)
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.plan_replacements import (
    PaidPlanReplacementService,
    money_owed_for_unused_time,
)
from tests.support.billing_config import live_billing_overrides
from tests.unit.test_invariant_refund_after_plan_move import (
    PENDING_MANUAL,
    _move_a_paid_plan,
)
from tests.unit.test_invariant_refund_is_recorded import (
    REFUND_EVENT_TYPES,
    REFUND_PROVIDERS,
    _refund_payload,
    _seed_paid_plan,
)

#: The amount shapes one refund event can carry. ``unknown`` is today's only reading —
#: it must keep today's result exactly. ``over-full`` says more came back than was paid
#: (possible when a later refund crosses the line); it is a full refund, not a credit.
AMOUNT_KINDS: tuple[str, ...] = ("unknown", "partial", "full", "over-full")

#: How the money coming back is delivered. One refund; the same event id twice (a
#: provider re-delivery, which ``process_event`` short-circuits); and two *distinct*
#: partial refunds that together reach the payment — the second becomes a full refund.
SCENARIOS: tuple[str, ...] = ("single", "same-event-replayed", "two-partials-summing-to-full")

PARTIAL_ALERT_SCOPE = "billing.partial_refund"


def _event_value(kind: str, amount: Decimal) -> Decimal | None:
    """What one refund event of this shape reports as the refunded amount (major units)."""

    if kind == "unknown":
        return None
    if kind == "partial":
        return (amount / Decimal(2)).quantize(Decimal("0.01"))
    if kind == "full":
        return amount
    return amount + Decimal("1.00")


def _delivery_values(
    provider: str, kind: str, scenario: str, amount: Decimal
) -> list[Decimal | None]:
    """The sequence of refunded amounts the recorder gets to read, one per delivery.

    Stripe reports ``amount_refunded`` as the **cumulative** total on the charge, so a
    second partial refund on Stripe states the whole refunded total, not the increment —
    that is exactly why the recorder must not add it to a running total twice.
    """

    value = _event_value(kind, amount)
    if scenario == "single":
        return [value]
    if scenario == "same-event-replayed":
        # One id, delivered twice: only the first delivery ever reaches the recorder.
        return [value]
    if kind == "unknown":
        return [None, None]
    if kind == "partial" and provider == "stripe":
        return [value, amount]
    return [value, value]


def _final_total(provider: str, kind: str, scenario: str, amount: Decimal) -> Decimal:
    """The refunded total the state contract stores after these deliveries."""

    total = Decimal("0")
    for value in _delivery_values(provider, kind, scenario, amount):
        if value is None:
            # Unknown: "total stays as stored" — nothing a person must not lose is added.
            continue
        total = max(total, value) if provider == "stripe" else total + value
    return total


def _expected_status(provider: str, kind: str, scenario: str, amount: Decimal) -> str:
    if kind == "unknown":
        # The event says money came back and nothing says how little: full, today's rule.
        return "refunded"
    return "refunded" if _final_total(provider, kind, scenario, amount) >= amount else "completed"


def _expected_alerts(kind: str) -> int:
    """One partial delivery, one staff alert; a replay changes nothing a second time."""

    return 1 if kind == "partial" else 0


def _kept(paid: Decimal, refunded: Decimal) -> Decimal:
    """The money kept, computed from the contract here so these tests fail (rather than
    import-error) on unfixed code. ``test_money_kept_is_the_one_owner`` and the reader
    tests assert the real ``core.money.money_kept`` is the same figure."""

    if refunded <= 0:
        return paid
    return max(paid - refunded, Decimal("0"))


def _refund_event_payload(
    *,
    provider: str,
    event_type: str,
    attempt: BillingCheckoutAttempt,
    user_id: Any,
    subscription_reference: str,
    index: int,
    value: Decimal | None,
    replayed: bool,
) -> dict[str, Any]:
    payload = _refund_payload(
        event_type=event_type,
        attempt=attempt,
        user_id=user_id,
        subscription_reference=subscription_reference,
    )
    if not replayed:
        payload["id"] = f"{payload['id']}-{index}"
    if value is not None:
        payload["data"]["refunded_amount"] = f"{value:.2f}"
        if provider == "stripe":
            payload["data"]["refunded_total_is_cumulative"] = True
    return payload


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("amount_kind", AMOUNT_KINDS)
@pytest.mark.parametrize("provider", REFUND_PROVIDERS)
@pytest.mark.parametrize("event_type", REFUND_EVENT_TYPES)
async def test_the_refund_amount_decides_full_or_partial(
    test_context, event_type: str, provider: str, amount_kind: str, scenario: str
) -> None:
    """The whole matrix: every name, every company, every amount shape, every delivery.

    On unfixed code this fails on the first ``refunded_amount`` read and on the status
    of every partial case: today the amount is never read at all.
    """

    replayed = scenario == "same-event-replayed"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        amount = Decimal(str(attempt.amount))
        service = BillingService(session, test_context["settings"])
        values = _delivery_values(provider, amount_kind, scenario, amount)
        payloads = [
            _refund_event_payload(
                provider=provider,
                event_type=event_type,
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
                index=index,
                value=value,
                replayed=replayed,
            )
            for index, value in enumerate(values)
        ]
        if replayed:
            payloads = [payloads[0], payloads[0]]
        results = []
        for payload in payloads:
            results.append(await service.process_event(provider=provider, payload=payload))
            # Commit between deliveries: separate webhooks are separate transactions.
            await session.commit()

    if replayed:
        assert results[0].replayed is False
        assert results[1].replayed is True, (
            "the same event id must short-circuit in process_event, never reach the money"
        )

    total = _final_total(provider, amount_kind, scenario, amount)
    status = _expected_status(provider, amount_kind, scenario, amount)
    plan_ended = event_type in REFUND_ENDS_PLAN_EVENT_TYPES and status == "refunded"
    alerts = _expected_alerts(amount_kind)

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == status, (
            f"{provider}/{event_type} carrying {amount_kind} money moved the payment to "
            f"{settled.status!r}; the amount decides full vs partial"
        )
        # The refunded total is stored on the payment (and stays 0 when no amount was
        # reported — an unknown amount must reproduce today's behaviour, not invent one).
        assert settled.refunded_amount == total
        assert _kept(settled.amount, settled.refunded_amount) == _kept(amount, total)
        if status == "completed":
            # A partial refund is the money kept, in plain terms.
            assert settled.refunded_amount == _event_value(amount_kind, amount)
            assert _kept(settled.amount, settled.refunded_amount) == amount - total

        held = await session.get(Subscription, subscription.id)
        assert held is not None
        assert held.status == (
            SubscriptionStatus.CANCELED if plan_ended else SubscriptionStatus.ACTIVE
        ), (
            f"{event_type} with a {amount_kind} amount "
            f"{'ended' if held.status == SubscriptionStatus.CANCELED else 'left'} the plan; "
            f"expected plan_ended={plan_ended}"
        )

        partial_alerts = list(
            (
                await session.scalars(
                    select(OperationalIssue).where(
                        OperationalIssue.affected_scope == PARTIAL_ALERT_SCOPE
                    )
                )
            ).all()
        )
        assert len(partial_alerts) == alerts, (
            f"expected {alerts} partial-refund staff alert(s), got {len(partial_alerts)}"
        )
        if alerts:
            alert = partial_alerts[0]
            assert alert.severity == "critical"
            assert alert.category == "billing"
            assert alert.state == "open"
            assert alert.occurrence_count == 1, "one alert per payment, not per redelivery"
            assert f"{amount:.2f}" in alert.summary, alert.summary
            assert "8.50" in alert.summary, (
                "the alert must say the money in plain words: " + alert.summary
            )
            assert str(settled.id) in " ".join(alert.evidence_refs)
        # A placed refund never raises the unplaced-money alarm, full or partial.
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == "billing.refund_reconciliation"
            )
        ) == 0
        expected_events = 2 if scenario == "two-partials-summing-to-full" else 1
        assert await session.scalar(select(func.count(BillingEvent.id))) == expected_events


async def test_a_second_partial_that_reaches_the_payment_is_a_full_refund(
    test_context,
) -> None:
    """The step-over case the matrix covers again as one readable story.

    Two distinct partial events for one payment are two refunds: the first keeps the
    plan and the ``completed`` status; the second reaches the payment amount and must
    read as a full refund — status ``refunded``, the ended-plan name ends the plan, and
    the money kept drops to zero.
    """

    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        amount = Decimal(str(attempt.amount))
        half = (amount / 2).quantize(Decimal("0.01"))
        service = BillingService(session, test_context["settings"])
        first = await service.process_event(
            provider=provider,
            payload=_refund_event_payload(
                provider=provider,
                event_type="refund.created",
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
                index=0,
                value=half,
                replayed=False,
            ),
        )
        await session.commit()
        assert first.processing_status == "processed"

    async with test_context["session_factory"]() as session:
        after_first = await session.get(BillingCheckoutAttempt, attempt.id)
        assert after_first is not None
        assert after_first.status == "completed"
        assert after_first.refunded_amount == half

        user_row = await session.get(Subscription, subscription.id)
        assert user_row is not None and user_row.status == SubscriptionStatus.ACTIVE
        service = BillingService(session, test_context["settings"])
        await service.process_event(
            provider=provider,
            payload=_refund_event_payload(
                provider=provider,
                event_type="refund.created",
                attempt=after_first,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
                index=1,
                value=half,
                replayed=False,
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "refunded", "the second partial completed the refund"
        assert settled.refunded_amount == amount
        assert _kept(settled.amount, settled.refunded_amount) == Decimal("0")
        held = await session.get(Subscription, subscription.id)
        assert held is not None and held.status == SubscriptionStatus.CANCELED
        # The first delivery's alert stands; nothing else about the money is missing.
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == PARTIAL_ALERT_SCOPE
            )
        ) == 1


async def test_a_partial_refund_revalues_the_manual_payout_and_the_full_one_voids_it(
    test_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small refund does not silence a live payout promise — it re-values it.

    The move wrote ``pending_manual`` valued from the whole payment. After a partial
    refund the row must still say "a person sends money by hand", but sized to the
    money still kept, and its ticket must stay open and un-re-reported. The second
    refund that reaches the payment amount voids it, exactly as today's full refund
    does. (The full path on one refund is covered by
    ``test_invariant_refund_after_plan_move.py``.)
    """

    settings, held = await _move_a_paid_plan(test_context, monkeypatch)
    async with test_context["session_factory"]() as session:
        source = await session.get(BillingCheckoutAttempt, held["source_id"])
        assert source is not None
        amount = Decimal(str(source.amount))
        refunded = Decimal("4.00")
        kept = amount - refunded
        before = await session.get(PlanMoveMoneyOwed, held["owed_id"])
        assert before is not None
        original_paid = before.paid_amount
        original_owed = before.amount_owed
        assert before.status == PENDING_MANUAL

        await BillingService(session, settings).process_event(
            provider="creem",
            payload=_refund_event_payload(
                provider="creem",
                event_type="refund.created",
                attempt=source,
                user_id=held["user_id"],
                subscription_reference=held["old_subscription_reference"],
                index=0,
                value=refunded,
                replayed=False,
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, held["source_id"])
        assert settled is not None
        assert settled.status == "completed"
        assert settled.refunded_amount == refunded
        owed = await session.get(PlanMoveMoneyOwed, held["owed_id"])
        assert owed is not None
        assert owed.status == PENDING_MANUAL, (
            "a partial refund must not silence a payout the customer is still owed"
        )
        assert owed.paid_amount == kept
        assert owed.amount_owed == money_owed_for_unused_time(
            paid_amount=kept,
            period_start=owed.period_start,
            period_end=owed.original_period_end,
            ended_at=owed.ended_at,
        )
        assert owed.amount_owed < original_owed
        ticket = await session.scalar(
            select(OperationalIssue).where(
                OperationalIssue.dedupe_key == f"billing:money-owed:{held['owed_id']}"
            )
        )
        assert ticket is not None
        assert ticket.state == "open", "the payout still has to be sent; keep the ticket"
        assert ticket.occurrence_count == 1, "a re-valuation is not a second payout report"
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == PARTIAL_ALERT_SCOPE
            )
        ) == 1

        # The second refund brings the total to the payment amount: full now.
        await BillingService(session, settings).process_event(
            provider="creem",
            payload=_refund_event_payload(
                provider="creem",
                event_type="payment.refunded",
                attempt=settled,
                user_id=held["user_id"],
                subscription_reference=held["old_subscription_reference"],
                index=1,
                value=kept,
                replayed=False,
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, held["source_id"])
        assert settled is not None
        assert settled.status == "refunded"
        assert settled.refunded_amount == amount
        assert original_paid == amount  # the row moved off its own earlier value, not mine
        owed = await session.get(PlanMoveMoneyOwed, held["owed_id"])
        assert owed is not None and owed.status == "voided"
        ticket = await session.scalar(
            select(OperationalIssue).where(
                OperationalIssue.dedupe_key == f"billing:money-owed:{held['owed_id']}"
            )
        )
        assert ticket is not None and ticket.state == "resolved"


async def test_a_plan_move_paid_after_a_partial_refund_is_valued_from_the_money_kept(
    test_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2, the valuation owner: a frozen move completed after a partial refund.

    ``apply_after_payment`` reads the frozen source payment behind a lock. When that
    payment is still ``completed`` but partly refunded, the payout is written from the
    money kept — and ``paid_amount`` carries the same figure, so the row's own check
    (``amount_owed <= paid_amount``) still holds.
    """

    async def successful_cancel(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", str(args[2])))

    monkeypatch.setattr(replacement_module, "provider_request", successful_cancel)
    settings = test_context["settings"].model_copy(update=live_billing_overrides())
    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, old, source = await _seed_paid_plan(session, provider=provider)
        plan = await PlanCatalogService(session).get_or_sync("trader")
        now = datetime.now(UTC)
        new_attempt = BillingCheckoutAttempt(
            user_id=user.id,
            plan_id=plan.id,
            billing_cycle="monthly_auto_renewal",
            provider=provider,
            status="pending",
            idempotency_key=f"move-{user.id}",
            terms_version="test",
            amount=effective_monthly_price("trader"),
            currency="USD",
            terms_accepted_at=now,
            expires_at=now + timedelta(days=1),
            billing_profile={"first_name": "Amina"},
        )
        session.add(new_attempt)
        await session.flush()
        await PaidPlanReplacementService(session, settings).attach_source(
            attempt=new_attempt, target_plan_code="trader"
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        source_row = await session.get(BillingCheckoutAttempt, source.id)
        assert source_row is not None
        amount = Decimal(str(source_row.amount))
        refunded = Decimal("4.00")
        kept = amount - refunded
        await BillingService(session, settings).process_event(
            provider=provider,
            payload=_refund_event_payload(
                provider=provider,
                event_type="refund.created",
                attempt=source_row,
                user_id=user.id,
                subscription_reference=old.provider_subscription_id,
                index=0,
                value=refunded,
                replayed=False,
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        paid_at = datetime.now(UTC)
        await BillingService(session, settings).process_event(
            provider=provider,
            payload={
                "id": f"evt-paid-after-partial-{source.id}",
                "type": "subscription.paid",
                "data": {
                    "checkout_attempt_id": str(new_attempt.id),
                    "plan_code": "trader",
                    "user_id": str(user.id),
                    "provider_customer_id": f"cus_{user.id}",
                    "provider_subscription_id": f"sub_new_{user.id}",
                    "amount": str(new_attempt.amount),
                    "currency": "USD",
                    "status": "active",
                    "current_period_start": paid_at.isoformat(),
                    "current_period_end": (paid_at + timedelta(days=30)).isoformat(),
                },
            },
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        rows = list((await session.scalars(select(PlanMoveMoneyOwed))).all())
        assert len(rows) == 1, "the move is not blocked by a partial refund"
        owed = rows[0]
        assert owed.status == PENDING_MANUAL
        assert owed.paid_amount == kept, (
            "the payout was valued from the whole payment, not the money kept"
        )
        assert owed.amount_owed == money_owed_for_unused_time(
            paid_amount=kept,
            period_start=owed.period_start,
            period_end=owed.original_period_end,
            ended_at=owed.ended_at,
        )
        assert owed.amount_owed < money_owed_for_unused_time(
            paid_amount=amount,
            period_start=owed.period_start,
            period_end=owed.original_period_end,
            ended_at=owed.ended_at,
        )


async def test_every_money_reader_reads_the_money_kept_after_a_partial_refund(
    test_context,
) -> None:
    """R2, the three readers: plan-move source value, affiliate, and the staff view.

    One partial refund on a ``completed`` payment; every figure a reader shows must be
    the money kept, and the real ``core.money.money_kept`` owner must say the same.
    """

    from ai_market_monitor.core.money import money_kept
    from ai_market_monitor.services.affiliate_attribution import ReferralAttributionService
    from ai_market_monitor.services.system_brain_payments import SystemBrainPaymentsService

    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        amount = Decimal(str(attempt.amount))
        refunded = Decimal("4.00")
        kept = amount - refunded
        await BillingService(session, test_context["settings"]).process_event(
            provider=provider,
            payload=_refund_event_payload(
                provider=provider,
                event_type="refund.created",
                attempt=attempt,
                user_id=user.id,
                subscription_reference=subscription.provider_subscription_id,
                index=0,
                value=refunded,
                replayed=False,
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        row = await session.get(BillingCheckoutAttempt, attempt.id)
        assert row is not None and row.status == "completed"
        # The owner itself, against the contract on this row.
        assert money_kept(row.amount, row.refunded_amount) == kept

        # 1. The affiliate commission source.
        charged, source = await ReferralAttributionService(session)._amount_charged(
            customer_user_id=user.id, plan_id=row.plan_id, fallback=None
        )
        assert source == "checkout"
        assert charged == kept, "an affiliate was commissioned on money already returned"

        # 2. The staff payments view for this customer.
        view = await SystemBrainPaymentsService(session).customer(user.id)
        assert view is not None
        assert [payment.amount for payment in view.payments] == [kept]
        assert view.total_paid == kept

        # 3. The totals staff read at the top of the page and per customer.
        totals = await SystemBrainPaymentsService(session).overall_totals()
        assert totals["money_taken"] == kept
        assert totals["payments_made"] == 1
        page = await SystemBrainPaymentsService(session).list_customers()
        rows = [c for c in page["payment_customers"] if c.user_id == user.id]
        assert [c.total_paid for c in rows] == [kept]


def test_the_normaliser_maps_stripes_cumulative_refunded_amount_to_major_units() -> None:
    """Provider field knowledge lives in the normaliser, and only there.

    ``charge.refunded`` carries Stripe's cumulative ``amount_refunded`` (minor units);
    the normalized contract is ``refunded_amount`` in **major units** plus
    ``refunded_total_is_cumulative``. A dispute object carries no such field, and the
    other two companies' normalisers must not invent one.
    """

    normalized = BillingService._normalize_provider_payload(
        "stripe",
        {
            "id": "evt_ch_1",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_1",
                    "object": "charge",
                    "amount": 1700,
                    "amount_refunded": 850,
                    "currency": "usd",
                    "metadata": {"user_id": "u"},
                }
            },
        },
    )
    assert normalized["data"]["refunded_amount"] == "8.50"
    assert normalized["data"]["refunded_total_is_cumulative"] is True

    dispute = BillingService._normalize_provider_payload(
        "stripe",
        {
            "id": "evt_d_1",
            "type": "charge.dispute.created",
            "data": {
                "object": {
                    "id": "dp_1",
                    "object": "dispute",
                    "amount": 1700,
                    "charge": "ch_1",
                    "metadata": {},
                }
            },
        },
    )
    assert dispute["data"].get("refunded_amount") is None


@pytest.mark.parametrize("provider", ["creem", "nowpayments"])
def test_the_normaliser_invents_no_refunded_amount_for_the_other_companies(
    provider: str,
) -> None:
    """No field name is proven for Creem or NOWPayments, so the amount stays unknown.

    Unknown means "full refund" downstream — today's behaviour — rather than a guess
    read from a field nobody has seen.
    """

    if provider == "creem":
        payload: dict[str, Any] = {
            "id": "evt_cr_1",
            "eventType": "refund.created",
            "object": {
                "object": "refund",
                "amount": 1700,
                "checkout": {"metadata": {"checkout_attempt_id": "ca-1"}},
            },
        }
    else:
        payload = {
            "payment_status": "refunded",
            "order_id": "hm|ca-1|pro",
            "price_amount": "17.00",
            "purchase_id": "p-1",
        }
    normalized = BillingService._normalize_provider_payload(provider, payload)
    assert normalized["data"].get("refunded_amount") is None
    assert "refunded_total_is_cumulative" not in normalized["data"]


@pytest.mark.parametrize(
    ("paid", "refunded", "expected"),
    [
        pytest.param("17.00", None, "17.00", id="nothing-refunded"),
        pytest.param("17.00", "0.00", "17.00", id="zero-refunded"),
        pytest.param("17.00", "4.00", "13.00", id="partial"),
        pytest.param("17.00", "17.00", "0.00", id="exact-full"),
        pytest.param("17.00", "18.00", "0.00", id="over-full-clamps-to-zero"),
        pytest.param("0.00", "4.00", "0.00", id="nothing-paid"),
    ],
)
def test_money_kept_is_the_one_owner(
    paid: str, refunded: str | None, expected: str
) -> None:
    """``core.money.money_kept``: paid minus what came back, never below zero."""

    from ai_market_monitor.core.money import money_kept

    value = money_kept(
        Decimal(paid),
        None if refunded is None else Decimal(refunded),
    )
    # Numeric equality is the contract. The scale of the clamped zero is not: the
    # binding owner returns plain ``Decimal("0")`` when nothing is kept, and each
    # consumer of a zero keeps its own scale at the point of use (``money_owed_for_
    # unused_time`` quantises, the alert prints ``:.2f``, SQL binds as Numeric).
    assert value == Decimal(expected)
    assert value >= Decimal("0"), "money kept is never below zero"


async def test_a_full_refund_still_voids_the_payout_and_stores_no_amount(
    test_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-change promise: an unknown-amount refund behaves exactly as today.

    Same shape as ``test_invariant_refund_after_plan_move`` (which runs the whole
    family): one ``payment.refunded`` with no amount, on a payment whose move left a
    ``pending_manual`` payout. The payout is voided, its ticket resolved, the attempt
    moved to ``refunded`` — and, new in this change, the stored total stays 0 and no
    partial alert is written, because an unknown amount is decided as full without
    inventing a figure.
    """

    settings, held = await _move_a_paid_plan(test_context, monkeypatch)
    async with test_context["session_factory"]() as session:
        row = await session.get(BillingCheckoutAttempt, held["source_id"])
        assert row is not None
        await BillingService(session, settings).process_event(
            provider="creem",
            payload=_refund_payload(
                event_type="payment.refunded",
                attempt=row,
                user_id=held["user_id"],
                subscription_reference=held["old_subscription_reference"],
            ),
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, held["source_id"])
        assert settled is not None
        assert settled.status == "refunded"
        assert settled.refunded_amount == Decimal("0")
        owed = await session.get(PlanMoveMoneyOwed, held["owed_id"])
        assert owed is not None and owed.status == "voided"
        assert owed.amount_owed > 0, "voiding is not deleting"
        ticket = await session.scalar(
            select(OperationalIssue).where(
                OperationalIssue.dedupe_key == f"billing:money-owed:{held['owed_id']}"
            )
        )
        assert ticket is not None and ticket.state == "resolved"
        assert await session.scalar(
            select(func.count(OperationalIssue.id)).where(
                OperationalIssue.affected_scope == PARTIAL_ALERT_SCOPE
            )
        ) == 0


async def test_a_partial_refund_alert_survives_a_repeated_partial_and_counts_once(
    test_context,
) -> None:
    """Two distinct partial events on one payment are one alert, occurrence_count 2.

    The dedupe key is the payment, not the event: a staff queue that grows one row per
    provider retry buries the money signal this alert exists to carry.
    """

    provider = "creem"
    async with test_context["session_factory"]() as session:
        user, subscription, attempt = await _seed_paid_plan(session, provider=provider)
        amount = Decimal(str(attempt.amount))
        service = BillingService(session, test_context["settings"])
        for index in range(2):
            await service.process_event(
                provider=provider,
                payload=_refund_event_payload(
                    provider=provider,
                    event_type="refund.created",
                    attempt=attempt,
                    user_id=user.id,
                    subscription_reference=subscription.provider_subscription_id,
                    index=index,
                    value=Decimal("1.00"),
                    replayed=False,
                ),
            )
            await session.commit()

    async with test_context["session_factory"]() as session:
        settled = await session.get(BillingCheckoutAttempt, attempt.id)
        assert settled is not None
        assert settled.status == "completed"
        assert settled.refunded_amount == Decimal("2.00")
        alerts = list(
            (
                await session.scalars(
                    select(OperationalIssue).where(
                        OperationalIssue.affected_scope == PARTIAL_ALERT_SCOPE
                    )
                )
            ).all()
        )
        assert len(alerts) == 1
        assert alerts[0].occurrence_count == 2
        assert f"{(amount - Decimal('2.00')):.2f}" in alerts[0].summary

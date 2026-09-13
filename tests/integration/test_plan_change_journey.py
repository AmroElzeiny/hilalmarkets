"""End-to-end plan change journeys, under the owner's rule of 2026-09-10.

A customer on a paid plan moves to a different paid plan by buying it, at its full price,
on the normal payment page. The old plan ends only after that payment is confirmed; the
money side of that is proved in ``test_paid_plan_replacement.py``. The switch route that
used to re-price the card already held refuses every move now, records nothing, and never
reaches the payment company.

These tests drive the real routes with the real services and fake only the outbound
payment-company calls. For every pair of paid plans they hold three things together:

* the billing page offers a Pay button for the other plan, and no switch control;
* the switch route refuses, with the sentence that sends the person to that Pay button;
* the real checkout route accepts exactly the plans the page offers a Pay button for.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.plans import (
    PURCHASABLE_PLAN_CODES,
    effective_monthly_price,
    plan_name,
)
from ai_market_monitor.db.models import (
    BillingCheckoutAttempt,
    Subscription,
    SubscriptionPlanChange,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, SubscriptionStatus
from ai_market_monitor.services import plan_changes as plan_changes_module
from ai_market_monitor.services.entitlements import EntitlementService, PlanCatalogService
from ai_market_monitor.services.plan_changes import (
    TIMING_IMMEDIATE,
    TIMING_PERIOD_END,
    PlanChangeService,
    switch_label_booked,
)
from ai_market_monitor.services.plan_replacements import (
    CONSENT_PLAN_REPLACEMENT,
    REPLACEMENT_REFUSALS,
    manual_return_window_words,
)
from tests.support.billing_config import live_billing_overrides, stub_payment_companies

#: Every ordered pair of different paid plans: every move up and every move down.
PLAN_PAIRS = tuple(
    (held, target)
    for held in PURCHASABLE_PLAN_CODES
    for target in PURCHASABLE_PLAN_CODES
    if held != target
)

#: Words from the billing page's sentence for ``plan_change_needs_payment`` — the refusal
#: that sends the person to the Pay button. Checked on the page, because a refusal whose
#: sentence never reaches the screen tells nobody where to go.
NEEDS_PAYMENT_WORDS = "normal payment page"


async def _signup_and_verify(client, settings, email: str) -> None:
    """Create a verified email account and keep the session cookie in client."""

    requested = await client.post(
        "/signup/password",
        data={
            "email": email,
            "display_name": "Amina Trader",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert requested.status_code == 303
    code = settings.email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def _user_id_for_email(session_factory, email: str) -> UUID:
    """Find the user id behind a verified email identity."""

    async with session_factory() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.provider == IdentityProvider.EMAIL,
                UserIdentity.normalized_identifier == email,
            )
        )
    assert user_id is not None, f"No user found for {email}"
    return user_id


async def _grant_paid_plan(
    session_factory,
    user_id: UUID,
    plan_code: str,
    *,
    provider: str = "creem",
    period_days: int = 30,
    with_payment_record: bool = True,
) -> Subscription:
    """Give the account an active paid subscription, Creem by default.

    With the completed checkout that paid for it, as a real subscription has. A move to a
    different plan values the unused time from that payment, and refuses to open a
    payment page when it cannot find one — so an account seeded without it is an account
    no real customer has.
    """

    async with session_factory() as session:
        plan = await PlanCatalogService(session).get_or_sync(plan_code)
        now = datetime.now(UTC)
        subscription = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider=provider,
            provider_customer_id=f"cus_{user_id}",
            provider_subscription_id=f"sub_{user_id}_{plan_code}",
            current_period_start=now,
            current_period_end=now + timedelta(days=period_days),
        )
        session.add(subscription)
        if with_payment_record:
            session.add(
                BillingCheckoutAttempt(
                    user_id=user_id,
                    plan_id=plan.id,
                    billing_cycle="monthly_auto_renewal",
                    provider=provider,
                    status="completed",
                    idempotency_key=f"paid-{user_id}-{plan_code}",
                    terms_version="test",
                    amount=effective_monthly_price(plan_code),
                    currency="USD",
                    terms_accepted_at=now,
                    expires_at=now,
                    completed_at=now,
                    billing_profile={"first_name": "Amina"},
                )
            )
        await session.commit()
    return subscription


async def _book_change_made_before_the_rule(
    session_factory,
    *,
    user_id: UUID,
    subscription: Subscription,
    kind: str,
    from_plan_code: str,
    to_plan_code: str,
) -> SubscriptionPlanChange:
    """A change booked through the old switch form, before 2026-09-10.

    Nobody can book one now, but rows booked before the rule are still in the database
    waiting for the end of their paid period, and they must still behave.
    """

    async with session_factory() as session:
        change = SubscriptionPlanChange(
            user_id=user_id,
            subscription_id=subscription.id,
            kind=kind,
            from_plan_code=from_plan_code,
            to_plan_code=to_plan_code,
            timing=TIMING_PERIOD_END,
            status="scheduled",
            reason_code="too_expensive" if kind == "downgrade" else None,
            reason_text=None,
            consent_text="I agreed to this change before 2026-09-10.",
            consented_at=datetime.now(UTC) - timedelta(days=3),
            effective_at=subscription.current_period_end,
            provider=subscription.provider,
            metadata_json={},
        )
        session.add(change)
        await session.commit()
    return change


def _csrf(html: str) -> str:
    """CSRF token from the dashboard shell."""

    match = re.search(r'data-csrf-token="([a-f0-9]+)"', html)
    assert match is not None, "CSRF token not found on page"
    return match.group(1)


def _pay_buttons(html: str) -> set[str]:
    """The plans the billing page draws a live Pay button for, read from the page."""

    return set(
        re.findall(r'data-dashboard-purchase-button\s+data-plan-code="([a-z_]+)"', html)
    )


def _assert_no_switch_control(html: str) -> None:
    """No switch button, and no form that posts to the route that refuses every switch."""

    assert "data-plan-switch" not in html, "the page draws a plan-switch control"
    assert 'action="/dashboard/billing/switch"' not in html, (
        "the page carries a form that posts to the switch route, which refuses everything"
    )


async def _plan_change_rows(session_factory) -> int:
    async with session_factory() as session:
        return int(
            await session.scalar(select(func.count()).select_from(SubscriptionPlanChange))
            or 0
        )


@pytest.fixture
def fake_creem(monkeypatch):
    """Record any call the plan-change service sends to the payment company.

    Every test here expects it to stay empty: the switch route may refuse, but it may
    never ask the payment company for anything.
    """

    calls: list[dict] = []

    async def fake_provider_request(
        settings,
        method: str,
        url: str,
        *,
        provider: str,
        operation: str,
        **kwargs,
    ) -> httpx.Response:
        calls.append(
            {
                "method": method,
                "url": url,
                "provider": provider,
                "operation": operation,
                "payload": kwargs.get("json", {}),
            }
        )
        return httpx.Response(200, json={"id": f"creem_change_{uuid4().hex[:12]}"})

    monkeypatch.setattr(plan_changes_module, "provider_request", fake_provider_request)
    return calls


@pytest.mark.parametrize("timing", [TIMING_IMMEDIATE, TIMING_PERIOD_END])
@pytest.mark.parametrize("period_days", [30, 365], ids=["monthly", "annual"])
@pytest.mark.parametrize(("held_code", "target_code"), PLAN_PAIRS)
async def test_every_paid_plan_move_is_a_payment_and_the_switch_route_takes_nothing(
    test_context,
    fake_creem,
    held_code: str,
    target_code: str,
    timing: str,
    period_days: int,
):
    """Every move up and every move down, for a monthly and a yearly period.

    The page offers the other plan as a full-price payment, says what happens to the plan
    held now, and draws no switch control. An old form posted to the switch route anyway
    — from a tab left open since before the rule — is refused with the sentence that
    sends the person to the Pay button, records nothing, reaches no payment company, and
    leaves the plan they hold exactly as it was.
    """

    email = f"move-{held_code}-{target_code}-{timing}-{period_days}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    subscription = await _grant_paid_plan(
        session_factory, user_id, held_code, period_days=period_days
    )

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    offered = _pay_buttons(page.text)
    assert target_code in offered, f"no Pay button for {target_code}: {offered}"
    assert held_code not in offered, f"a Pay button for the plan already held: {offered}"
    _assert_no_switch_control(page.text)
    assert f"You pay the {plan_name(target_code)} price today" in page.text
    assert manual_return_window_words() in page.text
    assert CONSENT_PLAN_REPLACEMENT in page.text

    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": target_code,
            "timing": timing,
            "reason_code": "too_expensive",
            "switch_consent": "true",
            "csrf_token": _csrf(page.text),
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    location = post.headers["location"]
    assert "error=plan_change_needs_payment" in location
    assert f"selected_plan={target_code}" in location

    error_page = await client.get(location)
    assert NEEDS_PAYMENT_WORDS in error_page.text

    assert await _plan_change_rows(session_factory) == 0
    async with session_factory() as session:
        refreshed = await session.get(Subscription, subscription.id)
        held_plan = await PlanCatalogService(session).get_or_sync(held_code)
        assert refreshed is not None
        assert refreshed.plan_id == held_plan.id
        assert refreshed.status == SubscriptionStatus.ACTIVE
        entitlement = await EntitlementService(session).current(user_id)
        assert entitlement.plan.code == held_code

    assert fake_creem == []


async def test_a_change_booked_before_the_rule_still_applies_at_period_end(
    test_context,
    fake_creem,
):
    """A move down booked before 2026-09-10 happens on the day it was booked for.

    This test used to book the change itself through the switch form. That form is gone
    and the switch route refuses every move, but changes booked before the rule may still
    be waiting in the database. The page must say so, must not offer to buy the plan
    already booked, and the scheduler must move access on that day — never earlier, and
    without asking the payment company for anything.
    """

    email = "booked-downgrade-pro-to-trader@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    subscription = await _grant_paid_plan(session_factory, user_id, "pro", period_days=30)
    period_end = subscription.current_period_end
    change = await _book_change_made_before_the_rule(
        session_factory,
        user_id=user_id,
        subscription=subscription,
        kind="downgrade",
        from_plan_code="pro",
        to_plan_code="trader",
    )

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    assert f"You are moving to {plan_name('trader')}" in page.text
    assert switch_label_booked(plan_name("trader")) in page.text
    assert "trader" not in _pay_buttons(page.text)
    _assert_no_switch_control(page.text)

    async with session_factory() as session:
        assert (await EntitlementService(session).current(user_id)).plan.code == "pro"

        service = PlanChangeService(session, settings)
        assert await service.apply_due_changes(now=period_end - timedelta(seconds=1)) == 0
        assert (await EntitlementService(session).current(user_id)).plan.code == "pro"

        assert await service.apply_due_changes(now=period_end + timedelta(seconds=1)) == 1
        refreshed_sub = await session.get(Subscription, subscription.id)
        trader = await PlanCatalogService(session).get_or_sync("trader")
        assert refreshed_sub is not None
        assert refreshed_sub.plan_id == trader.id
        assert (await EntitlementService(session).current(user_id)).plan.code == "trader"

        refreshed_change = await session.get(SubscriptionPlanChange, change.id)
        assert refreshed_change is not None
        assert refreshed_change.status == "applied"
        assert refreshed_change.applied_at is not None

    assert fake_creem == []


async def test_downgrade_immediate_is_refused(test_context, fake_creem):
    """A downgrade sent with timing=immediate is refused, and nothing is recorded or sent.

    It is refused for the same reason as every other switch now: a different paid plan is
    bought on the payment page. The sentence on the page says where to go.
    """

    email = "downgrade-immediate-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "pro")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "trader",
            "timing": TIMING_IMMEDIATE,
            "reason_code": "too_expensive",
            "switch_consent": "true",
            "csrf_token": _csrf(page.text),
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=plan_change_needs_payment" in post.headers["location"]

    error_page = await client.get(post.headers["location"])
    assert NEEDS_PAYMENT_WORDS in error_page.text

    assert await _plan_change_rows(session_factory) == 0
    assert fake_creem == []


@pytest.mark.parametrize("held_code", PURCHASABLE_PLAN_CODES)
async def test_every_plan_card_button_matches_the_server(
    test_context,
    monkeypatch,
    held_code: str,
):
    """What a plan card offers is exactly what the server accepts, for every paid plan held.

    The codes are read from the page, not from the plan catalogue: the server decides the
    buttons, so an offered Pay button must open a real checkout and a plan with no button
    must be refused by the same route. The switch route is checked too — it refuses every
    plan, so the page must never offer it.
    """

    email = f"drift-check-{held_code}-{uuid4().hex[:8]}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, held_code)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    calls = stub_payment_companies(monkeypatch)

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    offered = _pay_buttons(page.text)
    assert offered, "the page drew no Pay buttons at all"
    _assert_no_switch_control(page.text)
    csrf = _csrf(page.text)

    for plan_code in PURCHASABLE_PLAN_CODES:
        response = await client.post(
            "/dashboard/billing/checkout",
            data={
                "plan_code": plan_code,
                "billing_cycle": "monthly",
                "payment_method": "card",
                "checkout_request_id": uuid4().hex,
                "terms_accepted": "true",
                "first_name": "Amina",
                "last_name": "Trader",
                "address_line1": "1 Market Street",
                "city": "Cairo",
                "country": "Egypt",
                "csrf_token": csrf,
            },
            headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
        )
        accepted = response.status_code == 200 and "checkout_url" in response.json()
        if plan_code in offered:
            assert accepted, (
                f"the page offers a Pay button for {plan_code}, but checkout refuses: "
                f"{response.status_code} {response.text[:300]}"
            )
        else:
            assert not accepted, (
                f"the page offers no Pay button for {plan_code}, but checkout accepted it"
            )

    for plan_code in (*PURCHASABLE_PLAN_CODES, "demo"):
        post = await client.post(
            "/dashboard/billing/switch",
            data={
                "plan_code": plan_code,
                "timing": TIMING_PERIOD_END,
                "switch_consent": "true",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert "error=" in post.headers["location"], (
            f"the switch route accepted {plan_code}: {post.headers['location']}"
        )

    # A checkout was opened, and nothing else: no subscription was re-priced or changed.
    assert calls, "no checkout reached the (stubbed) payment company"
    assert all("/subscriptions/" not in call["url"] for call in calls), calls
    assert await _plan_change_rows(session_factory) == 0


async def _let_the_card_period_lapse(
    session_factory,
    subscription: Subscription,
    *,
    days_ago: int = 3,
) -> None:
    """Move a recurring card plan's paid period into the grace window.

    A Creem subscription stays ``ACTIVE`` at the payment company for up to 30 days after
    its paid period ends, until the renewal decision arrives, and ``cancel_at_period_end``
    is still false — the card will charge again. In that window the checkout route still
    holds the plan: buying it again is refused (`already_subscribed`) and buying a
    different one replaces it. This is what makes a seeded subscription a *lapsed
    recurring card* rather than an ordinary one.
    """

    async with session_factory() as session:
        held = await session.get(Subscription, subscription.id)
        assert held is not None
        now = datetime.now(UTC)
        held.current_period_start = now - timedelta(days=30 + days_ago)
        held.current_period_end = now - timedelta(days=days_ago)
        held.cancel_at_period_end = False
        await session.commit()


def _billing_page_availability(html_text: str, plan_code: str) -> dict:
    """What the billing page tells the browser about "may this account buy this plan".

    The popup and the live state of every Pay button read the availability the server
    computed, carried in the page's `billing-plan-data` JSON. The offline suite runs no
    JavaScript, so the payload itself is the page's answer — a payload that says
    "purchasable" is a live Pay button in a real browser.
    """

    match = re.search(
        r'<script id="billing-plan-data" type="application/json">(.*?)</script>',
        html_text,
        re.S,
    )
    assert match is not None, "the billing page carried no plan payload"
    payload = json.loads(match.group(1))
    availability = payload["plans"][plan_code]["availability"]
    assert isinstance(availability, dict)
    return availability


async def _post_checkout(client, csrf: str, plan_code: str) -> httpx.Response:
    """Press Pay the way the popup does: the real checkout route, asking for JSON."""

    return await client.post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": plan_code,
            "billing_cycle": "monthly",
            "payment_method": "card",
            "checkout_request_id": uuid4().hex,
            "terms_accepted": "true",
            "first_name": "Amina",
            "last_name": "Trader",
            "address_line1": "1 Market Street",
            "city": "Cairo",
            "country": "Egypt",
            "csrf_token": csrf,
        },
        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
    )


@pytest.mark.parametrize("held_code", PURCHASABLE_PLAN_CODES)
async def test_a_lapsed_recurring_card_plan_is_held_by_every_page_exactly_like_the_route(
    test_context,
    monkeypatch,
    held_code: str,
):
    """In the 30-day grace window the pages hold the plan the route holds.

    The checkout route asks `paid_plan_codes_for_replacement_decisions` — the grace-aware
    owner: a card subscription whose period just ended is still held, because the card
    will charge again next to a new plan. The billing page, the review page and the
    subscription page used to answer the same question from `active_paid_plan_codes`,
    which drops that plan. The lapsed plan then drew a live Pay button on all three, and
    pressing it reached a route that refuses `already_subscribed` — the page offering
    what the server will not do, which is the exact defect class every other fix in this
    file exists to prevent.
    """

    other_code = next(code for code in PURCHASABLE_PLAN_CODES if code != held_code)
    email = f"lapsed-grace-{held_code}-{uuid4().hex[:8]}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    subscription = await _grant_paid_plan(session_factory, user_id, held_code)
    await _let_the_card_period_lapse(session_factory, subscription)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    calls = stub_payment_companies(monkeypatch)

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text

    # The seed really is what the review described: an ACTIVE recurring card plan whose
    # period ended inside the grace window, with no cancellation booked and the payment
    # that bought it on record.
    async with session_factory() as session:
        held = await session.get(Subscription, subscription.id)
        assert held is not None
        assert held.status == SubscriptionStatus.ACTIVE
        assert held.provider == "creem"
        assert held.cancel_at_period_end is False
        assert held.current_period_end is not None
        ended = held.current_period_end
        if ended.tzinfo is None:
            # SQLite stores the value and reads it back without the offset, like every
            # other date check in this file.
            ended = ended.replace(tzinfo=UTC)
        assert ended < datetime.now(UTC)

    # (a) The plan they hold: the page says NOT purchasable, with the held-plan flag and
    # the refusal the route's rejection is built from.
    held_availability = _billing_page_availability(page.text, held_code)
    assert held_availability["holds_this"] is True, (
        f"the page says this account does not hold {held_code}, whose card is still "
        "scheduled to charge — the checkout route holds it and refuses the purchase"
    )
    assert held_availability["purchasable"] is False, held_availability
    assert plan_name(held_code) in held_availability["refusal"], held_availability
    # The other plan stays a replacement purchase on the page — fixing the held plan
    # must not hide it.
    other_availability = _billing_page_availability(page.text, other_code)
    assert other_availability["holds_this"] is False, other_availability
    assert other_availability["holds_other"] is True, other_availability
    assert other_availability["purchasable"] is True, other_availability

    # The same answer on the other two surfaces that sell a plan.
    review_held = await client.get(f"/dashboard/billing/checkout?plan_code={held_code}")
    assert review_held.status_code == 200, review_held.text
    assert "checkout-confirm-form" not in review_held.text, (
        f"the review page asks for a name and address for {held_code}, which checkout "
        "refuses as already held"
    )
    subscription_page = await client.get("/dashboard/subscription")
    assert subscription_page.status_code == 200, subscription_page.text
    assert f'data-s-choose="{held_code}"' not in subscription_page.text, (
        f"the subscription page offers to choose {held_code}, which checkout refuses"
    )
    review_other = await client.get(f"/dashboard/billing/checkout?plan_code={other_code}")
    assert "checkout-confirm-form" in review_other.text, (
        "the fix must not hide the plan the route really does sell"
    )
    assert f'data-s-choose="{other_code}"' in subscription_page.text

    # (b) Pressing Pay for the held plan meets the route's refusal; the other plan is
    # accepted and frozen onto the lapsed subscription, so its old card is the one the
    # confirmed payment will end.
    csrf = _csrf(page.text)
    refused = await _post_checkout(client, csrf, held_code)
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "already_subscribed"

    accepted = await _post_checkout(client, csrf, other_code)
    assert accepted.status_code == 200, accepted.text
    assert "checkout_url" in accepted.json()
    async with session_factory() as session:
        attempt = await session.scalar(
            select(BillingCheckoutAttempt).where(
                BillingCheckoutAttempt.user_id == user_id,
                BillingCheckoutAttempt.status == "pending",
            )
        )
        assert attempt is not None
        assert attempt.replaces_subscription_id == subscription.id
    assert calls, "no checkout reached the (stubbed) payment company"
    assert all("/subscriptions/" not in call["url"] for call in calls), calls


@pytest.mark.parametrize("held_code", PURCHASABLE_PLAN_CODES)
async def test_a_lapsed_plan_with_no_payment_record_refuses_replacement_on_every_surface(
    test_context,
    monkeypatch,
    held_code: str,
):
    """A lapsed card plan also carries its refusal to the *other* plan's button.

    Replacing a paid plan values its unused days from the payment that bought it; with
    no payment on record the route refuses `paid_amount_missing`. The pages show that
    sentence only while they know a plan is held (`holds_other`). With the no-grace set
    a lapsed plan was not held at all, so every page offered a clean full-price purchase
    for the other plan and the refusal appeared only after pressing Pay. The grace-aware
    set makes the page say the route's sentence before anybody presses anything.
    """

    other_code = next(code for code in PURCHASABLE_PLAN_CODES if code != held_code)
    email = f"lapsed-nopay-{held_code}-{uuid4().hex[:8]}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    subscription = await _grant_paid_plan(
        session_factory, user_id, held_code, with_payment_record=False
    )
    await _let_the_card_period_lapse(session_factory, subscription)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    calls = stub_payment_companies(monkeypatch)
    refusal = REPLACEMENT_REFUSALS["paid_amount_missing"]

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    other_availability = _billing_page_availability(page.text, other_code)
    assert other_availability["holds_other"] is True, other_availability
    assert other_availability["purchasable"] is False, other_availability
    assert other_availability["refusal"] == refusal, other_availability
    held_availability = _billing_page_availability(page.text, held_code)
    assert held_availability["holds_this"] is True, held_availability
    assert held_availability["purchasable"] is False, held_availability

    review = await client.get(f"/dashboard/billing/checkout?plan_code={other_code}")
    assert review.status_code == 200, review.text
    assert "checkout-confirm-form" not in review.text, (
        "the review page asks for a name and address it will then refuse"
    )
    assert refusal in review.text

    subscription_page = await client.get("/dashboard/subscription")
    assert subscription_page.status_code == 200, subscription_page.text
    assert f'data-s-choose="{other_code}"' not in subscription_page.text
    assert refusal in subscription_page.text

    response = await _post_checkout(client, _csrf(page.text), other_code)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "paid_amount_missing"
    # The held plan itself is still refused by the route exactly as the page says.
    held_response = await _post_checkout(client, _csrf(page.text), held_code)
    assert held_response.status_code == 400, held_response.text
    assert held_response.json()["error"]["code"] == "already_subscribed"
    assert calls == [], f"a refused checkout reached the payment company: {calls}"


async def test_timeout_on_immediate_upgrade_never_invites_a_retry(
    test_context, monkeypatch
):
    """The switch route cannot lose a charge's answer, because it never asks for a charge.

    This was written when an immediate upgrade asked Creem for the price difference: a
    timeout left the charge's outcome unknown, so the refusal must not say "try again".
    Since 2026-09-10 the switch route sends nothing to the payment company, so a company
    that would time out is never reached, and the refusal still never says "try again" —
    it sends the person to the Pay button.
    """

    email = "upgrade-timeout@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    reached: list[str] = []

    async def timeout_provider_request(*args, **kwargs):
        reached.append(str(kwargs.get("operation") or ""))
        raise httpx.TimeoutException("connection read timed out")

    monkeypatch.setattr(
        plan_changes_module, "provider_request", timeout_provider_request
    )

    page = await client.get("/dashboard/billing")
    response = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_IMMEDIATE,
            "switch_consent": "true",
            "csrf_token": _csrf(page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=plan_change_needs_payment" in response.headers["location"]

    error_page = await client.get(response.headers["location"])
    assert NEEDS_PAYMENT_WORDS in error_page.text
    assert "try again" not in error_page.text.casefold(), (
        "the refusal asked the customer to retry"
    )

    assert reached == [], f"the switch route reached the payment company: {reached}"
    assert await _plan_change_rows(session_factory) == 0


async def test_crypto_paid_account_is_told_to_buy_not_switch(test_context, fake_creem):
    """A NOWPayments customer is offered the Pay button, like everybody else, and the
    switch route refuses with the sentence that sends them to it.
    """

    email = "crypto-switch-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(
        session_factory,
        user_id,
        "trader",
        provider="nowpayments",
        period_days=30,
    )

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    assert f"You pay the {plan_name('pro')} price today" in page.text
    assert "pro" in _pay_buttons(page.text)
    assert "data-plan-switch-trigger" not in page.text

    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_IMMEDIATE,
            "switch_consent": "true",
            "csrf_token": _csrf(page.text),
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=plan_change_needs_payment" in post.headers["location"]

    error_page = await client.get(post.headers["location"])
    assert NEEDS_PAYMENT_WORDS in error_page.text

    assert await _plan_change_rows(session_factory) == 0
    assert fake_creem == []


async def test_switch_to_free_plan_is_not_offered_and_is_refused(test_context):
    """The product does not sell a switch to the free plan. The page marks it
    unavailable and the route refuses it.
    """

    email = "switch-to-free-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text
    # The free plan card should say Not available, not show a switch form trigger.
    assert "Not available" in page.text

    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "demo",
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=plan_not_available" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None


@pytest.mark.parametrize(
    ("plan_code", "expected_error"),
    [
        ("trader", "plan_change_not_needed"),
        ("creator", "plan_not_available"),
        ("lifetime", "plan_not_available"),
    ],
)
async def test_invalid_or_denied_switches_are_refused(
    test_context,
    fake_creem,
    plan_code: str,
    expected_error: str,
):
    """Switching to the plan you already hold, or to a non-public/non-purchasable
    plan, must fail cleanly and write nothing.
    """

    email = f"denied-{expected_error}-{plan_code}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": plan_code,
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert f"error={expected_error}" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None

    assert fake_creem == []


async def test_switch_with_already_pending_change_is_refused(test_context, fake_creem):
    """Only one future change can be booked at a time.

    The booked change is one made before 2026-09-10, because nobody can book one through
    the switch route now. A second request is refused as already booked, the booking is
    left exactly as it was, and nothing is sent to the payment company.
    """

    email = "pending-change-refused@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    subscription = await _grant_paid_plan(session_factory, user_id, "trader")
    await _book_change_made_before_the_rule(
        session_factory,
        user_id=user_id,
        subscription=subscription,
        kind="upgrade",
        from_plan_code="trader",
        to_plan_code="pro",
    )

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    second = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": _csrf(page.text),
        },
        follow_redirects=False,
    )
    assert second.status_code == 303, second.text
    assert "error=change_already_requested" in second.headers["location"]

    async with session_factory() as session:
        changes = list(
            (await session.scalars(select(SubscriptionPlanChange))).all()
        )
        assert len(changes) == 1
        assert changes[0].to_plan_code == "pro"
        assert changes[0].status == "scheduled"

    assert fake_creem == []


async def test_switch_without_paid_plan_is_refused(test_context, fake_creem):
    """An account with no paid subscription cannot use the switch route."""

    email = "no-paid-plan-switch@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_PERIOD_END,
            "switch_consent": "true",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=no_paid_plan" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None

    assert fake_creem == []


async def test_upgrade_without_consent_is_refused(test_context, fake_creem):
    """Without the tick box nothing is recorded or sent — and with it neither, now.

    The switch route refuses before it reads the tick box: no switch is possible, so
    there is nothing to agree to there. Agreement is asked for where money is taken, in
    the tick box of the checkout.
    """

    email = "upgrade-no-consent@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, "trader")

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    csrf = _csrf(page.text)
    post = await client.post(
        "/dashboard/billing/switch",
        data={
            "plan_code": "pro",
            "timing": TIMING_IMMEDIATE,
            # switch_consent intentionally omitted
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert post.status_code == 303, post.text
    assert "error=plan_change_needs_payment" in post.headers["location"]

    async with session_factory() as session:
        assert await session.scalar(select(SubscriptionPlanChange)) is None

    assert fake_creem == []


@pytest.mark.parametrize(("held_code", "target_code"), PLAN_PAIRS)
async def test_every_checkout_tick_box_says_what_happens_to_the_plan_held(
    test_context,
    held_code: str,
    target_code: str,
):
    """The owner's sentence is in the tick box of all three checkouts, for every move.

    The billing popup, the review page and the subscription popup can each take the
    payment that replaces a paid plan. Each one's tick box must carry the same sentence —
    full price today, the old plan ends only when the payment is confirmed, the unused
    time is sent back by a person within the window — word for word from its one owner.
    """

    email = f"tick-box-{held_code}-{target_code}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, held_code)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    for path in (
        "/dashboard/billing",
        f"/dashboard/billing/checkout?plan_code={target_code}",
        "/dashboard/subscription",
    ):
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text[:300])
        assert CONSENT_PLAN_REPLACEMENT in response.text, (
            f"{path} takes a payment that replaces {held_code} without saying so"
        )

    review = await client.get(f"/dashboard/billing/checkout?plan_code={target_code}")
    assert "checkout-confirm-form" in review.text


@pytest.mark.parametrize("provider", [None, "admin", "trial"], ids=["none", "admin", "trial"])
async def test_nobody_without_a_paid_plan_is_promised_money_back(
    test_context,
    provider: str | None,
):
    """A free account, an administrator's grant and a trial owe nothing back.

    So none of the three checkouts may promise them the value of unused time: that
    sentence would be a promise the product does not keep.
    """

    email = f"owes-nothing-{provider or 'none'}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    if provider is not None:
        await _grant_paid_plan(
            session_factory, user_id, "trader", provider=provider, with_payment_record=False
        )

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    for path in (
        "/dashboard/billing",
        "/dashboard/billing/checkout?plan_code=pro",
        "/dashboard/subscription",
    ):
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text[:300])
        assert CONSENT_PLAN_REPLACEMENT not in response.text, (
            f"{path} promises money back to an account that paid nothing"
        )
        assert manual_return_window_words() not in response.text, path


@pytest.mark.parametrize(("held_code", "target_code"), PLAN_PAIRS)
async def test_a_paid_plan_with_no_payment_record_is_refused_alike_on_every_page(
    test_context,
    monkeypatch,
    held_code: str,
    target_code: str,
):
    """No page offers a payment the checkout route would refuse.

    Replacing a paid plan values its unused time from the payment that bought it. A
    subscription with no completed payment on record — an old row, or one written by hand
    — cannot be valued, so the checkout route refuses with `paid_amount_missing`. The
    billing page used to offer Pay anyway: the person typed their name and address and
    was refused only when they pressed it.

    Now the plan card, the review page and the subscription page give the route's own
    sentence, with no way to press Pay; the route refuses with the same code; and nothing
    reaches the payment company.
    """

    email = f"no-payment-record-{held_code}-{target_code}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    session_factory = test_context["session_factory"]

    await _signup_and_verify(client, settings, email)
    user_id = await _user_id_for_email(session_factory, email)
    await _grant_paid_plan(session_factory, user_id, held_code, with_payment_record=False)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    calls = stub_payment_companies(monkeypatch)
    refusal = REPLACEMENT_REFUSALS["paid_amount_missing"]

    billing = await client.get("/dashboard/billing")
    assert billing.status_code == 200, billing.text
    assert target_code not in _pay_buttons(billing.text), (
        f"the billing page offers Pay for {target_code}, which checkout refuses"
    )
    assert refusal in billing.text
    _assert_no_switch_control(billing.text)

    review = await client.get(f"/dashboard/billing/checkout?plan_code={target_code}")
    assert review.status_code == 200, review.text
    assert "checkout-confirm-form" not in review.text, (
        "the review page asks for a name and address it will then refuse"
    )
    assert refusal in review.text
    assert review.text.count("Nothing was charged") == 1

    subscription = await client.get("/dashboard/subscription")
    assert subscription.status_code == 200, subscription.text
    assert f'data-s-choose="{target_code}"' not in subscription.text
    assert refusal in subscription.text

    response = await client.post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": target_code,
            "billing_cycle": "monthly",
            "payment_method": "card",
            "checkout_request_id": uuid4().hex,
            "terms_accepted": "true",
            "first_name": "Amina",
            "last_name": "Trader",
            "address_line1": "1 Market Street",
            "city": "Cairo",
            "country": "Egypt",
            "csrf_token": _csrf(billing.text),
        },
        headers={"accept": "application/json", "x-requested-with": "XMLHttpRequest"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "paid_amount_missing"
    assert calls == [], f"a refused checkout reached the payment company: {calls}"

    # The notice after a redirect carrying the code says the sentence, not the code.
    notice = await client.get("/dashboard/billing?error=paid_amount_missing")
    assert refusal in notice.text
    assert "Paid Amount Missing" not in notice.text

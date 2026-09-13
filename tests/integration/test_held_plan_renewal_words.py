"""Both account pages say whether the plan held renews, read from that plan alone.

The subscription page and the billing page each say how the access somebody holds today
ends. Both used to answer a different question: which payment company *this server*
would use for a new sale. So on a server that sells by card, a customer who paid by
crypto read "renews by itself each month"; a plan Hilal Markets gave away read the same;
a card plan whose renewal had been stopped still read "renews"; and a yearly card plan
read "each month". On a server that sells nothing, a card plan that does renew read "does
not renew by itself".

Every shape of held access is checked on both pages, on each of the three shapes a server
can be in for a new sale. A page may promise a renewal only for a plan bought through a
company that renews, whose renewal has not been stopped, and it must name the period that
plan was bought for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import lxml.html
import pytest

from ai_market_monitor.api.template_env import day_only
from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES, effective_monthly_price
from ai_market_monitor.db.models import BillingCheckoutAttempt, Subscription, Trial
from ai_market_monitor.db.models.enums import SubscriptionStatus, TrialStatus
from ai_market_monitor.services.billing import BillingService, billing_provider_capabilities
from ai_market_monitor.services.entitlements import PlanCatalogService
from tests.integration.test_dashboard_web import _signup_and_verify
from tests.integration.test_paid_plan_replacement import _find_user
from tests.support.billing_config import live_billing_overrides

SUBSCRIPTION = "/dashboard/subscription"
BILLING = "/dashboard/billing"
EMAIL = "held-renewal@example.com"

#: The three shapes a server can be in for a new sale, and the company it would sell
#: through. The old pages read that company, so each shape is one the defect showed on.
SERVERS: dict[str, tuple[dict[str, object], str]] = {
    "sells_by_card": (live_billing_overrides(), "creem"),
    "sells_by_crypto_only": (
        {**live_billing_overrides(), "billing_card_provider": "disabled"},
        "nowpayments",
    ),
    "sells_nothing": ({}, "static"),
}

#: Every payment company `billing_provider_capabilities` knows. What the pages must say is
#: decided by that table's own `supports_recurring_billing`, so this list only names the
#: family and never decides the answer.
PAYMENT_COMPANIES = ("creem", "stripe", "nowpayments", "static")

#: Each company with every billing cycle a payment through it is written with.
HELD_PAYMENTS = [
    (company, cycle)
    for company in PAYMENT_COMPANIES
    for cycle in (
        ("monthly_auto_renewal", "annual_auto_renewal")
        if billing_provider_capabilities(company).supports_recurring_billing
        else ("one_time_30_day",)
    )
]

# What each page must say: (the subscription page's sentence, the billing page's
# "Renewal" tile). `{ends_on}` is the day the period ends, in the reader's timezone.
RENEWS = {
    "monthly_auto_renewal": (
        "This renews by itself each month until you stop it.",
        "Automatic renewal, every month",
    ),
    "annual_auto_renewal": (
        "This renews by itself each year until you stop it.",
        "Automatic renewal, every year",
    ),
}
STOPPED = ("This ends on {ends_on}. It will not renew.", "Ends after the current period")
FIXED_PERIOD = (
    "This lasts until {ends_on}. It does not renew by itself.",
    "Manual 30-day renewal",
)
FREE = ("Free, with no end date and nothing to cancel.", "Free forever")
TRIAL = (
    "Your trial access ends on {ends_on}. Nothing renews by itself.",
    "No automatic renewal",
)
GIVEN_UNTIL = (
    "Hilal Markets gave you this plan until {ends_on}. "
    "Nothing is charged, and nothing renews by itself.",
    "No automatic renewal",
)
GIVEN_FOREVER = (
    "Hilal Markets gave you this plan, with no end date. Nothing is charged.",
    "No automatic renewal",
)

#: Access nobody pays for. A plan given by Hilal Markets is written the two ways the admin
#: tools write it: with the stop flag (`account_admin`) and without it (`admin`).
UNPAID = [
    pytest.param("free", "", FREE, id="free"),
    pytest.param("trial", "", TRIAL, id="trial"),
    *(
        pytest.param(shape, code, words, id=f"{shape}-{code}")
        for shape, words in (
            ("given_until", GIVEN_UNTIL),
            ("given_until_with_stop_flag", GIVEN_UNTIL),
            ("given_forever", GIVEN_FOREVER),
        )
        for code in PURCHASABLE_PLAN_CODES
    ),
]


def _one(markup: str, xpath: str) -> str:
    nodes = lxml.html.fromstring(markup).xpath(xpath)
    assert len(nodes) == 1, f"{xpath} matched {len(nodes)} elements"
    return " ".join(nodes[0].text_content().split())


async def _read_both_pages(test_context) -> tuple[str, str]:
    """The renewal sentence on the subscription page and the tile on the billing page."""

    subscription = await test_context["client"].get(SUBSCRIPTION)
    assert subscription.status_code == 200, subscription.text[:800]
    billing = await test_context["client"].get(BILLING)
    assert billing.status_code == 200, billing.text[:800]
    return (
        _one(
            subscription.text,
            '//p[contains(concat(" ", normalize-space(@class), " "), " s-now-ending ")]',
        ),
        _one(
            billing.text,
            '//div[contains(concat(" ", normalize-space(@class), " "), " billing-current-meta ")]'
            '/span[small[normalize-space()="Renewal"]]/strong',
        ),
    )


async def _serve_as(test_context, server: str) -> None:
    """Put the server into this shape, and prove which company it would sell through."""

    overrides, sells_through = SERVERS[server]
    settings = test_context["settings"].model_copy(update=overrides)
    test_context["app"].dependency_overrides[get_settings] = lambda: settings
    async with test_context["session_factory"]() as session:
        assert BillingService(session, settings).provider.provider_name == sells_through


async def _ends_on(test_context, ends_at: datetime | None) -> str:
    if ends_at is None:
        return ""
    user = await _find_user(test_context, EMAIL)
    return day_only(ends_at, user.timezone or "UTC")


async def _hold_paid_plan(
    test_context, *, plan_code: str, company: str, cycle: str, stopped: bool
) -> datetime:
    """A paid plan and the completed payment that bought it. Returns when the period ends."""

    user = await _find_user(test_context, EMAIL)
    now = datetime.now(UTC)
    ends_at = now + timedelta(days=15)
    async with test_context["session_factory"]() as session:
        plan = await PlanCatalogService(session).get_or_sync(plan_code)
        session.add_all(
            [
                BillingCheckoutAttempt(
                    user_id=user.id,
                    plan_id=plan.id,
                    billing_cycle=cycle,
                    provider=company,
                    status="completed",
                    idempotency_key=f"held-{user.id}",
                    terms_version="test",
                    amount=effective_monthly_price(plan_code),
                    currency="USD",
                    terms_accepted_at=now - timedelta(days=15),
                    expires_at=now,
                    completed_at=now - timedelta(days=15),
                    billing_profile={"first_name": "Amina"},
                ),
                Subscription(
                    user_id=user.id,
                    plan_id=plan.id,
                    status=SubscriptionStatus.ACTIVE,
                    provider=company,
                    provider_subscription_id=f"held-{company}-{user.id}",
                    current_period_start=now - timedelta(days=15),
                    current_period_end=ends_at,
                    cancel_at_period_end=stopped,
                ),
            ]
        )
        await session.commit()
    return ends_at


async def _hold_unpaid_access(test_context, *, shape: str, plan_code: str) -> datetime | None:
    """Access nobody paid for. Returns when it ends, or None when it has no end."""

    if shape == "free":
        return None
    user = await _find_user(test_context, EMAIL)
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        if shape == "trial":
            ends_at: datetime | None = now + timedelta(days=6)
            trial_plan = await PlanCatalogService(session).get_or_sync("pro_trial")
            session.add(
                Trial(
                    user_id=user.id,
                    plan_id=trial_plan.id,
                    status=TrialStatus.ACTIVE,
                    starts_at=now - timedelta(days=1),
                    ends_at=ends_at,
                )
            )
        else:
            ends_at = None if shape == "given_forever" else now + timedelta(days=30)
            plan = await PlanCatalogService(session).get_or_sync(plan_code)
            session.add(
                Subscription(
                    user_id=user.id,
                    plan_id=plan.id,
                    status=SubscriptionStatus.ACTIVE,
                    provider="admin",
                    provider_subscription_id=f"admin:{user.id}",
                    current_period_start=now,
                    current_period_end=ends_at,
                    cancel_at_period_end=shape == "given_until_with_stop_flag",
                )
            )
        await session.commit()
    return ends_at


@pytest.mark.parametrize("server", SERVERS)
@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
@pytest.mark.parametrize(("company", "cycle"), HELD_PAYMENTS)
@pytest.mark.parametrize("stopped", [False, True], ids=["running", "stopped"])
async def test_a_paid_plan_promises_renewal_only_when_it_really_renews(
    test_context, server, plan_code, company, cycle, stopped
):
    await _signup_and_verify(test_context, email=EMAIL)
    ends_at = await _hold_paid_plan(
        test_context, plan_code=plan_code, company=company, cycle=cycle, stopped=stopped
    )
    await _serve_as(test_context, server)

    sentence, tile = await _read_both_pages(test_context)

    company_renews = billing_provider_capabilities(company).supports_recurring_billing
    will_renew = company_renews and not stopped
    if will_renew:
        words = RENEWS[cycle]
    elif company_renews:
        words = STOPPED
    else:
        words = FIXED_PERIOD
    ends_on = await _ends_on(test_context, ends_at)
    assert (sentence, tile) == (words[0].format(ends_on=ends_on), words[1])
    # The rule itself, whatever the wording: a charge is promised exactly when one will come.
    assert sentence.startswith("This renews by itself") is will_renew, sentence
    assert tile.startswith("Automatic renewal") is will_renew, tile


@pytest.mark.parametrize("server", SERVERS)
@pytest.mark.parametrize(("shape", "plan_code", "words"), UNPAID)
async def test_access_nobody_pays_for_never_promises_a_renewal(
    test_context, server, shape, plan_code, words
):
    await _signup_and_verify(test_context, email=EMAIL)
    ends_at = await _hold_unpaid_access(test_context, shape=shape, plan_code=plan_code)
    await _serve_as(test_context, server)

    sentence, tile = await _read_both_pages(test_context)

    ends_on = await _ends_on(test_context, ends_at)
    assert (sentence, tile) == (words[0].format(ends_on=ends_on), words[1])
    assert not sentence.startswith("This renews by itself"), sentence
    assert not tile.startswith("Automatic renewal"), tile

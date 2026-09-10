"""Invariant: a billing offer on the page is a checkout the server will accept.

The reported defect class is a silent dead control: the dashboard billing dialog
can show a payment method as available, but the checkout route refuses it with no
understandable explanation. These tests reproduce that disagreement for every
purchasable plan, payment method and billing cycle the product sells, and for an
account that already holds the same paid plan as well as one that holds a
different paid plan.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from ai_market_monitor.api.template_env import day_only
from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.plans import (
    PURCHASABLE_PLAN_CODES,
    plan_name,
    plan_offer,
)
from ai_market_monitor.db.models import Subscription, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, SubscriptionStatus
from ai_market_monitor.services.entitlements import PlanCatalogService
from tests.support.billing_config import (
    ReachedThePaymentCompany,
    live_billing_overrides,
    stub_payment_companies,
)

PAYMENT_METHODS = ("card", "crypto")
BILLING_CYCLES = ("monthly", "annual")
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "ai_market_monitor"
CHECKOUT_TEMPLATE = SRC / "templates" / "hilal" / "dashboard" / "checkout.html"
BILLING_TEMPLATE = SRC / "templates" / "hilal" / "dashboard" / "billing.html"
BILLING_PORTAL_TEMPLATE = SRC / "templates" / "hilal" / "dashboard" / "billing_portal.html"
BILLING_JS = SRC / "static" / "hilalmarkets-billing.js"
PLAN_CHANGE_JS = SRC / "static" / "hm-plan-change.js"
DASHBOARD_CSS = SRC / "static" / "hilalmarkets-dashboard-v2.css"


def _cycles_for_plan(plan_code: str) -> tuple[str, ...]:
    """Billing cycles the catalog says are open for this plan."""

    offer = plan_offer(plan_code)
    return tuple(
        cycle
        for cycle in BILLING_CYCLES
        if getattr(offer, f"{cycle}_available", False)
    )


def _other_paid_plan(plan_code: str) -> str:
    """The other plan in PURCHASABLE_PLAN_CODES."""

    return next(code for code in PURCHASABLE_PLAN_CODES if code != plan_code)


async def _signup(client, settings, email: str) -> None:
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


async def _grant_paid_plan(session_factory, user_id: UUID, plan_code: str) -> None:
    """Give the account an active paid subscription to plan_code."""

    async with session_factory() as session:
        plan = await PlanCatalogService(session).get_or_sync(plan_code)
        now = datetime.now(UTC)
        session.add(
            Subscription(
                user_id=user_id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="test",
                provider_subscription_id=f"test-{plan_code}-{user_id}",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        await session.commit()


def _billing_plan_data(html: str) -> dict:
    """Return the JSON the server placed in #billing-plan-data."""

    match = re.search(
        r'<script id="billing-plan-data"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None, "#billing-plan-data script not found"
    return json.loads(match.group(1))


def _extract_csrf_and_request_id(html: str) -> tuple[str, str]:
    """Pull the billing-page CSRF token and checkout request id."""

    csrf_match = re.search(r'name="csrf_token" value="([a-f0-9]+)"', html)
    request_match = re.search(
        r'name="checkout_request_id" value="([a-f0-9]+)"', html
    )
    assert csrf_match is not None, "CSRF token not found on billing page"
    assert request_match is not None, "checkout_request_id not found on billing page"
    return csrf_match.group(1), request_match.group(1)


def _page_says_method_available(
    plan_data: dict, plan_code: str, method: str, cycle: str
) -> bool:
    """The effective availability the dialog script draws from the JSON."""

    availability = plan_data["plans"][plan_code]["availability"]
    methods = plan_data["plans"][plan_code]["methods"]
    method_decision = methods.get(cycle, {}).get(method, {})
    return bool(
        availability.get("purchasable") and method_decision.get("available")
    )


def _checkout_dialog(html: str) -> str:
    """Return the markup of the checkout dialog, or fail if missing."""

    match = re.search(
        r'<dialog\s+id="billing-checkout-dialog".*?</dialog>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, "checkout dialog not found on billing page"
    return match.group(0)


_TEST_CASES = [
    pytest.param(
        plan,
        method,
        cycle,
        "same" if same else "different",
        id=f"{plan}-{method}-{cycle}-held-{('same' if same else 'different')}",
    )
    for plan in PURCHASABLE_PLAN_CODES
    for method in PAYMENT_METHODS
    for cycle in _cycles_for_plan(plan)
    for same in (True, False)
]


@pytest.mark.parametrize(("plan", "method", "cycle", "held_kind"), _TEST_CASES)
async def test_page_offer_matches_server_acceptance(
    test_context, monkeypatch, plan, method, cycle, held_kind
):
    """What the billing page marks available must match what checkout accepts.

    The page's JSON carries a ``purchasable`` flag and per-method flags. The
    dialog enables a method only when both are true. The server must reach the
    same answer, so that no control on the page is dead and no control refuses
    without a reason. Holding a *different* paid plan on an account that holds
    no card is a purchase, not a switch, and must be accepted.

    The payment companies are answered in process. Without that the POST would
    carry an obviously fake key to the real Creem and the real NOWPayments, and
    their "wrong key" would arrive here disguised as our own server refusing the
    customer — which is what this test measures.
    """

    email = f"billing-offer-{plan}-{method}-{cycle}-{held_kind}@example.com"
    held_plan = plan if held_kind == "same" else _other_paid_plan(plan)

    settings = test_context["settings"]
    client = test_context["client"]
    await _signup(client, settings, email)
    user_id = await _user_id_for_email(test_context["session_factory"], email)
    await _grant_paid_plan(test_context["session_factory"], user_id, held_plan)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled
    stub_payment_companies(monkeypatch)

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text

    plan_data = _billing_plan_data(page.text)
    page_available = _page_says_method_available(plan_data, plan, method, cycle)

    csrf, request_id = _extract_csrf_and_request_id(page.text)
    post = await client.post(
        "/dashboard/billing/checkout",
        data={
            "plan_code": plan,
            "billing_cycle": cycle,
            "payment_method": method,
            "checkout_request_id": request_id,
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
    server_accepts = post.status_code in (200, 302, 303)

    assert page_available == server_accepts, (
        f"Plan={plan}, method={method}, cycle={cycle}, held={held_plan}: "
        f"page says available={page_available}, but POST returned {post.status_code}"
    )


@pytest.mark.parametrize("held_plan", PURCHASABLE_PLAN_CODES)
async def test_dialog_explains_when_no_method_works(test_context, held_plan):
    """When no payment method is available, the dialog must say why in plain words.

    The separate checkout.html page already shows a notice beginning
    "There is no way to pay for this plan yet". The billing dialog currently has
    no equivalent visible reason block, so a dead control is silent.
    """

    email = f"billing-dialog-{held_plan}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    await _signup(client, settings, email)
    user_id = await _user_id_for_email(test_context["session_factory"], email)
    await _grant_paid_plan(test_context["session_factory"], user_id, held_plan)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get(f"/dashboard/billing?selected_plan={held_plan}&checkout=1")
    assert page.status_code == 200, page.text

    dialog = _checkout_dialog(page.text)
    assert "There is no way to pay for this plan yet" in dialog, (
        f"Plan={held_plan}: the billing dialog does not show a plain reason "
        f"when no method is available"
    )


@pytest.mark.parametrize("held_plan", PURCHASABLE_PLAN_CODES)
async def test_open_plan_held_by_account_tells_user(test_context, held_plan):
    """A refused plan carries the server's reason; an open plan carries none.

    Two halves of one rule, and both matter. A plan that cannot be bought must
    arrive with a sentence the server wrote, so the dialog never invents its own
    in the browser. A plan that *can* be bought must arrive with no sentence at
    all — a refusal printed beside a live Pay button tells somebody they cannot
    buy the thing they are about to buy.

    The account here pays by an outside invoice, so it holds no card. Only the
    plan it already has is refused; the other paid plan is a purchase it is
    allowed to make, which is the defect this whole file was written for.
    """

    email = f"billing-reason-{held_plan}@example.com"
    settings = test_context["settings"]
    client = test_context["client"]
    await _signup(client, settings, email)
    user_id = await _user_id_for_email(test_context["session_factory"], email)
    await _grant_paid_plan(test_context["session_factory"], user_id, held_plan)

    enabled = settings.model_copy(update=live_billing_overrides())
    test_context["app"].dependency_overrides[get_settings] = lambda: enabled

    page = await client.get("/dashboard/billing")
    assert page.status_code == 200, page.text

    plan_data = _billing_plan_data(page.text)
    for code in PURCHASABLE_PLAN_CODES:
        plan_entry = plan_data["plans"][code]
        assert "refusal" in plan_entry, (
            f"Plan={code}: billing_plan_data carries no server-owned refusal field"
        )
        refusal = plan_entry["refusal"]
        assert isinstance(refusal, str), f"Plan={code}: refusal is not a sentence"
        purchasable = bool(plan_entry["availability"]["purchasable"])

        if purchasable:
            assert not refusal.strip(), (
                f"Plan={code}: the page offers this plan for sale and also carries "
                f"a refusal for it: {refusal!r}"
            )
        else:
            assert refusal.strip(), (
                f"Plan={code}: the page refuses this plan with no server-owned reason"
            )

        if code == held_plan:
            assert not purchasable, (
                f"Plan={code}: offered for sale to an account that already holds it"
            )
            explains = "already" in refusal.casefold() or plan_name(held_plan) in refusal
            assert explains, (
                f"Plan={code}: refusal sentence does not explain the held plan"
            )
        else:
            # No card is held, so nothing can be re-priced and nothing can be
            # charged twice. Buying is the only route to this plan, and it must
            # be open. This is the reported defect, stated as a rule.
            assert purchasable, (
                f"Plan={code}: an account holding {held_plan} by outside invoice "
                f"cannot buy it, so the only route to it is closed"
            )

    js_source = BILLING_JS.read_text(encoding="utf-8")
    fallback = "This plan and billing period are not configured for checkout yet"
    assert fallback not in js_source, (
        "hilalmarkets-billing.js still contains its own fallback availability message"
    )


async def test_no_test_can_reach_a_payment_company_by_accident():
    """The guard that keeps the test run away from real money must be armed.

    Four cases in this file used to fail because the checkout code sent an obviously fake
    key to the real Creem and the real NOWPayments. They answered "wrong key", the route
    turned that into a 400, and the test read the 400 as *our* server refusing the
    customer. Both the page and the rule were right the whole time.

    The guard is wired autouse in ``tests/conftest.py``, so it protects every test that
    exists and every test written later. This proves it is still switched on, for both
    modules that can call out — a guard nobody checks is a guard that quietly stops
    working.
    """

    from ai_market_monitor.services import billing as billing_module
    from ai_market_monitor.services import plan_changes as plan_changes_module

    for module in (billing_module, plan_changes_module):
        with pytest.raises(ReachedThePaymentCompany):
            await module.provider_request(
                None,
                "POST",
                "https://api.creem.io/v1/checkouts",
                provider="creem",
                operation="checkouts",
            )


def test_the_plan_switch_form_names_no_plan_of_its_own():
    """Every sentence in the switch form is rebuilt from the button that opened it.

    The form is one dialog reused for every move up and every move down. Two of its
    labels had a plan name written straight into the page — "I want Pro now" — which is
    correct only for as long as the single upgrade anybody can make happens to be the one
    to Pro. Open it for any other plan and somebody agrees to a timing for a plan they
    are not buying.

    The rule, not the instance: no plan's name appears anywhere inside this form.
    """

    html = BILLING_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(
        r'<dialog\s+id="plan-switch-dialog".*?</dialog>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, "the plan switch dialog is not on the billing page"
    dialog = match.group(0)
    # A Jinja comment reaches nobody, and the one above these labels has to name the plan
    # it is explaining. Only what is really sent to a browser is checked.
    shown = re.sub(r"\{#.*?#\}", "", dialog, flags=re.DOTALL)
    for code in PURCHASABLE_PLAN_CODES:
        assert plan_name(code) not in shown, (
            f"the switch form has {plan_name(code)!r} written into the page; it must "
            f"come from the button that opened the form, like every other sentence here"
        )

    # And the script really does write both of them, so removing the name from the page
    # cannot leave the label empty.
    script = PLAN_CHANGE_JS.read_text(encoding="utf-8")
    for handle in ("data-plan-switch-timing-now", "data-plan-switch-timing-later"):
        assert handle in dialog, f"{handle} is not on the form"
        assert handle in script, f"{handle} is never written by the script"


def test_a_money_day_is_shown_as_a_day():
    """A date about money is a day, never a machine's timestamp.

    "It starts 2026-10-10 07:07:46 UTC" asks a beginner to read four numbers to find the
    one they need, in a timezone that is not theirs. Nothing is charged at a second.

    Moments keep the full stamp — when an alert fired, when a payment record was written
    — so this checks the billing pages only, which is where every date is about money.
    """

    for template in (BILLING_TEMPLATE, BILLING_PORTAL_TEMPLATE):
        text = template.read_text(encoding="utf-8")
        assert "|short_dt(" not in text, (
            f"{template.name} still prints a machine timestamp for a money date; "
            f"use the day_dt filter"
        )

    day = datetime(2026, 10, 10, 7, 7, 46, tzinfo=UTC)
    assert day_only(day, "UTC") == "10 October 2026"
    # The reader's own timezone decides which day it is, the same as every other date on
    # these pages: 07:07 UTC on the 10th is still the 10th in Cairo and already the 10th
    # in Sydney, but 23:30 UTC is not.
    assert day_only(datetime(2026, 10, 10, 23, 30, tzinfo=UTC), "Australia/Sydney") == (
        "11 October 2026"
    )
    assert day_only(None) == "-"


def test_no_hidden_required_control_owns_submission():
    """A visually hidden input must not be marked required, because it blocks submission.

    The checkout form hides its radio inputs with a CSS rule that makes them
    1x1 and transparent. If one of those inputs is also ``required``, a user who
    cannot see it can still be blocked by it, with no visible error.
    """

    css = DASHBOARD_CSS.read_text(encoding="utf-8")
    hidden_selectors = (
        ".billing-period-options input",
        ".billing-method-options input",
    )
    block = re.search(
        r"(?:\.billing-period-options input|\.billing-method-options input)[^}]*\{([^}]+)\}",
        css,
        flags=re.DOTALL,
    )
    assert block is not None, "Hidden CSS rule for billing option inputs not found"
    declarations = block.group(1)
    assert "position: absolute" in declarations and "opacity: 0" in declarations, (
        "CSS rule for billing option inputs does not visually hide them"
    )

    html = CHECKOUT_TEMPLATE.read_text(encoding="utf-8")
    hidden_required: list[str] = []

    # Parse the template enough to know which <input> tags sit inside a hidden
    # fieldset. We track open tags on a small stack and check required inputs.
    void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    tag_re = re.compile(r"</?([a-zA-Z][^\s/>]*)[^>]*>")
    stack: list[tuple[str, dict[str, str]]] = []
    for match in tag_re.finditer(html):
        tag = match.group(0)
        name = match.group(1).lower()
        if tag.startswith("</"):
            if stack and stack[-1][0] == name:
                stack.pop()
            continue
        attrs: dict[str, str] = {}
        for attr_match in re.finditer(r'\s([a-zA-Z\-]+)(?:="([^"]*)")?', tag):
            attrs[attr_match.group(1).lower()] = attr_match.group(2) or ""
        if name == "input" and "required" in attrs:
            for parent_name, parent_attrs in reversed(stack):
                if parent_name == "fieldset":
                    klass = parent_attrs.get("class", "")
                    if any(sel.split()[0].lstrip(".") in klass for sel in hidden_selectors):
                        input_type = attrs.get("type", "")
                        input_name = attrs.get("name", "")
                        hidden_required.append(
                            f"<input type={input_type!r} name={input_name!r}>"
                        )
                    break
        if not tag.endswith("/>") and name not in void_tags:
            stack.append((name, attrs))

    assert not hidden_required, (
        "Required inputs are visually hidden by the CSS; users cannot see what "
        "is blocking submission: " + ", ".join(hidden_required)
    )

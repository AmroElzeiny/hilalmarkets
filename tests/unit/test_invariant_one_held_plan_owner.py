"""One question, one owner: "which paid plans does this account hold?"

There are two readers of that question in `services/billing.py`, and they are
supposed to answer *different* questions:

* :func:`active_paid_plan_codes` — the no-grace reader. It answers the **access**
  question: is the person entitled right now? A recurring card whose paid period
  has ended no longer gives access. This is the semantics the entitlement service
  keeps, and the semantics this function must keep.
* :func:`paid_plan_codes_for_replacement_decisions` — the grace-aware owner of the
  **purchase/replacement** answer. A recurring card subscription stays ``ACTIVE``
  at the payment company for up to ``RECURRING_LAPSED_GRACE_WINDOW`` (30 days)
  after its paid period ends, until the renewal decision arrives, and the card
  will charge again. During that window the account still *holds* the plan as far
  as buying anything else is concerned.

The defect this file exists to make impossible: one path that decides who may buy
— ``BillingService.open_checkout_attempt``, the last gate before a payment page is
created — built its answer from the no-grace reader while its sibling
``prepare_checkout`` built the same answer from the grace-aware owner. In the grace
window the opener computed ``holds_this=False``, said the held plan was purchasable,
and opened a second payment page for a plan the account already holds and is still
scheduled to be charged for.

The rule, asserted for the whole family — every purchasable plan, both recurring
card companies, a monthly and an annual card period, and a checkout reopened for
either period: **a reopened checkout must be refused exactly as ``prepare_checkout``
refuses it, and the held plan must be visible to the opener's availability answer.**

Two source scans keep the fix from rotting: one forbids the *shape* of the mistake
(the no-grace reader wired into a purchase answer) anywhere in ``src``, and one
pins the opener to the one owner. Access code is deliberately not in scope: the
no-grace reader stays the owner of "entitled right now".
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES, effective_monthly_price
from ai_market_monitor.db.models import BillingCheckoutAttempt, Subscription, User
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.services import billing as billing_module
from ai_market_monitor.services.billing import (
    RECURRING_LAPSED_GRACE_WINDOW,
    BillingError,
    BillingService,
    active_paid_plan_codes,
    paid_plan_codes_for_replacement_decisions,
)
from ai_market_monitor.services.entitlements import PlanCatalogService

#: The two recurring card companies. Both keep a subscription ``ACTIVE`` after the
#: paid period ends, so the grace rule is a rule about each of them, not about Creem.
RECURRING_CARD_PROVIDERS = ("creem", "stripe")

#: How long a monthly and an annual card period cover. The lapse below is written
#: against each, so the family covers both sale cycles, not just the one reported.
CARD_PERIOD_DAYS = {"monthly": 30, "annual": 365}

#: Days past the end of the paid period at which the card is checked: well inside
#: the 30-day window, before the renewal decision arrives, exactly the state the
#: grace owner exists to hold. (Same shape as `_let_the_card_period_lapse` in
#: ``tests/integration/test_plan_change_journey.py``.)
DAYS_SINCE_PERIOD_ENDED = 3

#: A checkout reopened for either period the plan can be bought on. While annual is
#: withdrawn both cycles must still be refused; when annual opens, this test already
#: covers it.
ATTEMPT_CYCLES = ("monthly_auto_renewal", "annual_auto_renewal")


async def _hold_a_lapsed_recurring_card(
    session,
    *,
    plan_code: str,
    card_provider: str,
    card_cycle: str,
    attempt_cycle: str,
) -> tuple[User, BillingCheckoutAttempt]:
    """Seed an account that holds `plan_code` on a lapsed recurring card, plus a
    checkout for that same plan that was opened *before* the card lapsed and never
    reached a payment page (``creating``, no url — exactly what
    ``open_checkout_attempt`` is called to finish).
    """

    user = User(display_name="Grace Window Holder")
    session.add(user)
    await session.flush()
    plan = await PlanCatalogService(session).get_or_sync(plan_code)
    now = datetime.now(UTC)
    period = CARD_PERIOD_DAYS[card_cycle]
    session.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            provider=card_provider,
            provider_customer_id=f"cus_{user.id}",
            provider_subscription_id=f"sub_{user.id}_{plan_code}",
            current_period_start=now
            - timedelta(days=period + DAYS_SINCE_PERIOD_ENDED),
            current_period_end=now - timedelta(days=DAYS_SINCE_PERIOD_ENDED),
            cancel_at_period_end=False,
        )
    )
    # The completed payment that bought the card — a real lapsed card has one (the
    # seed in ``test_plan_change_journey._grant_paid_plan`` records it for the same
    # reason).
    session.add(
        BillingCheckoutAttempt(
            user_id=user.id,
            plan_id=plan.id,
            billing_cycle=f"{card_cycle}_auto_renewal",
            provider=card_provider,
            status="completed",
            idempotency_key=f"bought-{user.id}-{plan_code}",
            terms_version="test",
            amount=effective_monthly_price(plan_code),
            currency="USD",
            terms_accepted_at=now - timedelta(days=period + DAYS_SINCE_PERIOD_ENDED),
            expires_at=now,
            completed_at=now - timedelta(days=period + DAYS_SINCE_PERIOD_ENDED),
            billing_profile={"first_name": "Grace"},
        )
    )
    reopened = BillingCheckoutAttempt(
        id=uuid4(),
        user_id=user.id,
        plan_id=plan.id,
        billing_cycle=attempt_cycle,
        provider="static",
        provider_session_id=None,
        checkout_url=None,
        status="creating",
        idempotency_key=f"reopened-{uuid4().hex}",
        terms_version="test",
        amount=effective_monthly_price(plan_code),
        currency="USD",
        terms_accepted_at=now - timedelta(minutes=20),
        expires_at=now + timedelta(minutes=10),
        billing_profile={"first_name": "Grace"},
    )
    session.add(reopened)
    await session.commit()
    return user, reopened


@pytest.mark.parametrize("plan_code", PURCHASABLE_PLAN_CODES)
@pytest.mark.parametrize("card_provider", RECURRING_CARD_PROVIDERS)
@pytest.mark.parametrize("card_cycle", tuple(CARD_PERIOD_DAYS), ids=("card-monthly", "card-annual"))
@pytest.mark.parametrize("attempt_cycle", ATTEMPT_CYCLES, ids=("attempt-monthly", "attempt-annual"))
async def test_a_lapsed_card_is_held_by_the_opener_exactly_like_the_checkout_route(
    test_context,
    monkeypatch: pytest.MonkeyPatch,
    plan_code: str,
    card_provider: str,
    card_cycle: str,
    attempt_cycle: str,
) -> None:
    """Inside the grace window, `open_checkout_attempt` holds the plan the route holds.

    Before the fix the monthly-attempt cases failed with ``DID NOT RAISE``: the
    opener read the no-grace set, saw no held plan, and returned a freshly created
    payment page for the very plan the account is still scheduled to be charged
    for. The annual-attempt cases refused for the wrong reason — a withdrawn
    annual cycle — and failed assert (ca): the held plan never reached the
    availability answer.
    """

    # The seed itself is what the rule is about: an ACTIVE recurring card whose
    # period ended inside the window, with no cancellation booked.
    assert timedelta(days=DAYS_SINCE_PERIOD_ENDED) < RECURRING_LAPSED_GRACE_WINDOW

    settings = test_context["settings"].model_copy(update={"billing_enabled": True})
    session_factory = test_context["session_factory"]

    # A spy on the one owner of the availability answer, recording the held set the
    # opener hands it. This is how "the held plan must be visible to the opener's
    # availability answer" is asserted: not by trusting the refusal's code, which an
    # unrelated withdrawal could also produce, but by reading the answer's own input.
    seen_sets: list[object] = []
    real_availability = billing_module.plan_checkout_availability

    def spy_availability(*args: object, **kwargs: object) -> object:
        seen_sets.append(kwargs.get("active_paid_plan_codes"))
        return real_availability(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(billing_module, "plan_checkout_availability", spy_availability)

    async with session_factory() as session:
        user, reopened = await _hold_a_lapsed_recurring_card(
            session,
            plan_code=plan_code,
            card_provider=card_provider,
            card_cycle=card_cycle,
            attempt_cycle=attempt_cycle,
        )
        user_id = user.id
        attempt_id = reopened.id

        # (a) The mechanism, stated once so a future reader of a red assert knows
        # which reader drifted: the grace-aware owner holds the lapsed card; the
        # no-grace reader, correctly, does not — it answers a different question.
        held_for_purchase = await paid_plan_codes_for_replacement_decisions(
            session, user_id=user_id
        )
        assert plan_code in held_for_purchase, (
            f"the purchase/replacement owner dropped a {card_provider} {card_cycle} "
            f"card that ended {DAYS_SINCE_PERIOD_ENDED} days ago; the seed is no "
            "longer inside the grace window it claims to be"
        )
        assert plan_code not in await active_paid_plan_codes(session, user_id=user_id), (
            "access must NOT be granted to a card whose paid period has ended — if "
            "this fails, the no-grace reader's access semantics changed"
        )

        billing = BillingService(session, settings)

        # (b) The checkout route's refusal, unchanged by this fix, kept in the same
        # test so "refused exactly as prepare_checkout refuses it" is a comparison,
        # not two separate claims.
        with pytest.raises(BillingError) as refused:
            await billing.prepare_checkout(
                user_id=user_id,
                plan_code=plan_code,
                billing_cycle="monthly",
                request_key=uuid4().hex,
                terms_accepted=True,
            )
        assert refused.value.code == "already_subscribed"

        # (c) The opener must refuse too. It sells through the static local
        # adapter, so an unfixed opener really does create a payment page here
        # instead of touching the network — that is the defect being refused.
        with pytest.raises(BillingError) as reopened_refusal:
            await billing.open_checkout_attempt(
                attempt_id=attempt_id,
                user_id=user_id,
                success_url="http://testserver/billing/success",
                cancel_url="http://testserver/billing/cancel",
            )
        # The opener's own refusal code, the same one the resume route redirects
        # with: nothing was charged, go back to billing.
        assert reopened_refusal.value.code == "plan_not_available"

        # (ca) And it refused because the answer it built *saw* the held plan. The
        # no-grace set would have arrived here empty, and the refusal would have
        # been luck (a withdrawn cycle) rather than the rule.
        assert seen_sets, "the opener never asked the availability owner at all"
        assert all(plan_code in recorded for recorded in seen_sets), (
            f"the opener asked 'may this account buy {plan_code}' while handing the "
            f"owner a held set without {plan_code}: {seen_sets!r}"
        )

        # (d) And no payment page was created: the attempt is exactly as it was.
        after = await session.get(BillingCheckoutAttempt, attempt_id)
        assert after is not None
        assert after.checkout_url is None, (
            "the opener created a payment page for a plan the account already holds "
            "on a card that is still scheduled to charge"
        )
        assert after.provider_session_id is None
        assert after.status == "creating"
        await session.commit()


# ── The rule may not be copied back ──────────────────────────────────────────

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: The exact shape of this defect: the no-grace reader wired straight into the
#: purchase answer. Loading the set with this function for an *access* decision is
#: fine and is not what this forbids.
_NO_GRACE_INTO_PURCHASE_ANSWER = re.compile(
    r"active_paid_plan_codes\s*=\s*await\s+active_paid_plan_codes\s*\("
)


def _python_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_no_purchase_answer_is_built_from_the_no_grace_reader() -> None:
    """Nowhere in ``src`` may the availability answer be fed by the access reader.

    This scans the whole tree, not just billing, because the defect class is a call
    site further on from any one page: the resume router had the same shape before
    it was fixed, and ``open_checkout_attempt`` still did.
    """

    offenders = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if _NO_GRACE_INTO_PURCHASE_ANSWER.search(line):
                offenders.append(f"{path.relative_to(_SRC_ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        "These build a purchase answer from the no-grace access reader. A lapsed "
        "recurring card would vanish from it, and the path would sell a plan the "
        "account holds. Use paid_plan_codes_for_replacement_decisions:\n"
        + "\n".join(offenders)
    )


def test_the_scan_would_notice_the_defect_it_exists_for() -> None:
    """The scan above passes trivially if its pattern matches nothing. So the pattern
    is run against the shape it is meant to catch, and against shapes it must leave
    alone: a plain access read and the grace-aware owner passed under the
    availability function's keyword name.
    """

    assert _NO_GRACE_INTO_PURCHASE_ANSWER.search(
        "            active_paid_plan_codes=await active_paid_plan_codes("
    )
    assert not _NO_GRACE_INTO_PURCHASE_ANSWER.search(
        "            active_paid_plan_codes=await paid_plan_codes_for_replacement_decisions("
    )
    assert not _NO_GRACE_INTO_PURCHASE_ANSWER.search(
        "codes = await active_paid_plan_codes(session, user_id=user.id)"
    )


def test_the_checkout_opener_reads_the_one_held_plan_owner() -> None:
    """``open_checkout_attempt`` answers "who holds what" from the same owner
    ``prepare_checkout`` reads — the grace-aware one — and from no other.

    The scans cannot follow a value through a renamed local. This one reads the
    function itself.
    """

    source = inspect.getsource(BillingService.open_checkout_attempt)
    assert re.search(r"paid_plan_codes_for_replacement_decisions\s*\(", source), (
        "the checkout opener no longer reads the one owner of the held-plan "
        "question for purchase decisions"
    )
    assert not re.search(r"\bactive_paid_plan_codes\s*\(", source), (
        "the checkout opener calls the no-grace access reader again — a lapsed "
        "recurring card would disappear from its answer and it would open a second "
        "payment page for the plan already held"
    )

"""Who may buy a plan is one answer, and no page works it out for itself.

The reported symptom was a Pay button that did nothing. One cause was fixed on the
popup and the same rule was left hand-written on three other surfaces:

* the checkout review page decided "already subscribed" from *this very plan* alone, so
  somebody holding Trader who opened the Pro review page was handed the whole payment
  form, typed their name and address, and was refused only when they pressed pay;
* the plan card on the subscription page counted *any* held plan code as "a different
  paid plan", including codes nobody can buy;
* the billing page template decided "this is your plan" from a raw list of codes.

Three copies of one rule, each understanding a different subset. This file holds the
general rule instead of those three cases:

    for every plan, every set of plans the account already holds, and every shape a
    payment configuration can take, :func:`plan_checkout_availability` is the only
    thing that answers "may this account buy this plan today", and every answer it
    gives is either "yes" or "no with a sentence a beginner can act on".

The last test is the one that stops the fix from rotting: it reads the router and
template source and fails if any page starts deciding this for itself again.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES, plan_name
from ai_market_monitor.services.billing import plan_checkout_availability

#: A plan code the account can hold that nobody can buy. The subscription card used to
#: count this as "you are on a different paid plan" and refuse every purchase.
UNSELLABLE_HELD_CODE = "demo"

#: A code that is not a plan at all - a legacy row, or one retired from the price list.
#: It must be treated exactly like holding nothing.
RETIRED_HELD_CODE = "legacy_gold"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "billing_enabled": True,
        "billing_provider": "static",
        "billing_card_provider": "disabled",
        "billing_crypto_provider": "disabled",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


#: The payment shapes worth crossing with every account state: nothing switched on, the
#: live crypto-only shape, card only, and both together.
SETTINGS_CASES: dict[str, Settings] = {
    "no_way_to_pay": _settings(),
    "crypto_only": _settings(
        billing_provider="nowpayments",
        billing_crypto_provider="nowpayments",
        nowpayments_api_key=SecretStr("nowpayments-key"),
        nowpayments_ipn_secret=SecretStr("nowpayments-ipn"),
    ),
    "card_only": _settings(
        billing_card_provider="creem",
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={
            f"{code}_{cycle}": f"prod_{code}_{cycle}"
            for code in PURCHASABLE_PLAN_CODES
            for cycle in ("monthly", "annual")
        },
    ),
    "card_and_crypto": _settings(
        billing_card_provider="creem",
        billing_crypto_provider="nowpayments",
        creem_api_key=SecretStr("creem-key"),
        creem_webhook_secret=SecretStr("creem-webhook"),
        creem_product_ids={
            f"{code}_{cycle}": f"prod_{code}_{cycle}"
            for code in PURCHASABLE_PLAN_CODES
            for cycle in ("monthly", "annual")
        },
        nowpayments_api_key=SecretStr("nowpayments-key"),
        nowpayments_ipn_secret=SecretStr("nowpayments-ipn"),
    ),
}

SETTINGS_IDS = tuple(SETTINGS_CASES)

#: Every ordered pair of two different paid plans. The rule is about the surface, not
#: about Trader and Pro, so it is asserted for each pair rather than for one example.
PLAN_PAIRS = tuple(
    (held, target)
    for held in PURCHASABLE_PLAN_CODES
    for target in PURCHASABLE_PLAN_CODES
    if held != target
)


@pytest.mark.parametrize("shape", SETTINGS_IDS)
@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
def test_holding_a_plan_is_reported_as_holding_that_plan(shape: str, code: str) -> None:
    """Holding plan X, asking about plan X: "you have it", never "you have another"."""

    result = plan_checkout_availability(
        SETTINGS_CASES[shape], plan_code=code, active_paid_plan_codes={code}
    )
    assert result["holds_this"] is True
    assert result["holds_other"] is False
    assert result["purchasable"] is False
    # The sentence names the plan, so the reader knows which one is meant, and names
    # the page where they can change it. "Not available" tells nobody what to do.
    assert plan_name(code) in result["refusal"]
    assert "billing page" in result["refusal"].lower()


@pytest.mark.parametrize("shape", SETTINGS_IDS)
@pytest.mark.parametrize(("held", "target"), PLAN_PAIRS)
def test_holding_another_paid_plan_keeps_every_other_paid_plan_available(
    shape: str, held: str, target: str
) -> None:
    """Holding plan X does not change which payment options can sell plan Y.

    The new plan is bought at its full price first. The old plan is ended only after
    payment is confirmed, so the page and the server must allow every available method
    for every ordered pair of different paid plans.
    """

    result = plan_checkout_availability(
        SETTINGS_CASES[shape], plan_code=target, active_paid_plan_codes={held}
    )
    assert result["holds_this"] is False
    assert result["holds_other"] is True
    without_old_plan = plan_checkout_availability(
        SETTINGS_CASES[shape], plan_code=target, active_paid_plan_codes=set()
    )
    for key in ("purchasable", "card_monthly", "card_annual", "crypto_monthly"):
        assert result[key] is without_old_plan[key]
    assert result["refusal"] == without_old_plan["refusal"]


@pytest.mark.parametrize("shape", ("card_only", "card_and_crypto"))
@pytest.mark.parametrize(("held", "target"), PLAN_PAIRS)
def test_a_different_plan_uses_checkout_whatever_kind_of_payment_is_held(
    shape: str, held: str, target: str
) -> None:
    """The kind of old payment never changes the checkout answer for a new plan."""

    result = plan_checkout_availability(
        SETTINGS_CASES[shape],
        plan_code=target,
        active_paid_plan_codes={held},
        held_access_can_be_repriced=False,
    )
    assert result["holds_other"] is True, "they do still hold another plan"
    assert result["must_switch_instead"] is False, "but switching is not the route"
    assert result["purchasable"] is True
    assert result["refusal"] == ""


@pytest.mark.parametrize("shape", SETTINGS_IDS)
@pytest.mark.parametrize(("held", "target"), PLAN_PAIRS)
def test_the_same_plan_is_refused_even_when_no_card_is_held(
    shape: str, held: str, target: str
) -> None:
    """Holding crypto access changes which *other* plans may be bought. It never lets
    somebody buy the plan they are already using - that would charge them twice for the
    same thing, with no card involved either way."""

    del target
    result = plan_checkout_availability(
        SETTINGS_CASES[shape],
        plan_code=held,
        active_paid_plan_codes={held},
        held_access_can_be_repriced=False,
    )
    assert result["holds_this"] is True
    assert result["purchasable"] is False
    assert result["refusal"].strip()


def test_the_old_card_repricing_input_no_longer_changes_the_answer() -> None:
    """Old callers cannot restore the retired in-place card change rule."""

    for held, target in PLAN_PAIRS:
        without = plan_checkout_availability(
            SETTINGS_CASES["card_and_crypto"],
            plan_code=target,
            active_paid_plan_codes={held},
        )
        asked = plan_checkout_availability(
            SETTINGS_CASES["card_and_crypto"],
            plan_code=target,
            active_paid_plan_codes={held},
            held_access_can_be_repriced=True,
        )
        assert without == asked
        assert without["purchasable"] is True


@pytest.mark.parametrize("shape", SETTINGS_IDS)
@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
@pytest.mark.parametrize("held", (UNSELLABLE_HELD_CODE, RETIRED_HELD_CODE))
def test_a_plan_nobody_sells_is_not_a_paid_plan_in_the_way(
    shape: str, code: str, held: str
) -> None:
    """Holding the free plan, or a retired code, blocks nothing.

    The subscription card used to count any held code as "a different paid plan", so an
    account on the free plan could be told to go and switch plans instead of buying one.
    Holding one of these must give exactly the same answer as holding nothing at all.
    """

    settings = SETTINGS_CASES[shape]
    with_unsellable = plan_checkout_availability(
        settings, plan_code=code, active_paid_plan_codes={held}
    )
    holding_nothing = plan_checkout_availability(
        settings, plan_code=code, active_paid_plan_codes=set()
    )
    assert with_unsellable == holding_nothing
    assert with_unsellable["holds_other"] is False


@pytest.mark.parametrize("shape", SETTINGS_IDS)
@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
def test_holding_nothing_leaves_the_payment_configuration_to_decide(
    shape: str, code: str
) -> None:
    """With no plan held, the answer is only about the payment companies."""

    result = plan_checkout_availability(
        SETTINGS_CASES[shape], plan_code=code, active_paid_plan_codes=set()
    )
    assert result["holds_this"] is False
    assert result["holds_other"] is False
    assert result["purchasable"] is (
        result["card_monthly"] or result["card_annual"] or result["crypto_monthly"]
    )


def test_a_ready_payment_configuration_really_can_sell_every_paid_plan() -> None:
    """The tests above would all pass if nothing were ever purchasable.

    This one refuses that: with card and crypto both ready and every product created,
    every paid plan must be buyable by an account holding nothing. Without it, a bug
    that refused every purchase would keep this whole file green.
    """

    settings = SETTINGS_CASES["card_and_crypto"]
    for code in PURCHASABLE_PLAN_CODES:
        result = plan_checkout_availability(
            settings, plan_code=code, active_paid_plan_codes=set()
        )
        assert result["purchasable"] is True, code
        assert result["refusal"] == "", code


@pytest.mark.parametrize("shape", SETTINGS_IDS)
@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
@pytest.mark.parametrize(
    "held",
    (frozenset(), *(frozenset({c}) for c in PURCHASABLE_PLAN_CODES)),
)
def test_a_refusal_and_a_yes_are_never_both_true_or_both_missing(
    shape: str, code: str, held: frozenset[str]
) -> None:
    """Every "no" carries a sentence, and every "yes" carries none.

    A refused purchase with no words is the state the whole fix exists to remove: the
    reader is left looking at a page that will not move and is told nothing.
    """

    result = plan_checkout_availability(
        SETTINGS_CASES[shape], plan_code=code, active_paid_plan_codes=held
    )
    if result["purchasable"]:
        assert result["refusal"] == ""
    else:
        assert result["refusal"].strip(), result
        assert result["refusal"].rstrip().endswith("."), result["refusal"]


# ── The rule may not be copied ───────────────────────────────────────────────

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: Deciding "does this account hold a plan" by testing membership of the raw code set.
#: Every hand-written copy of the rule this file exists for looked exactly like this.
_MEMBERSHIP_COPY = re.compile(r"\bin\s+\w*active_paid_plan_codes\b")

#: The owner of the rule, and the only place allowed to read the raw set that way.
_OWNER = _SOURCE_ROOT / "services" / "billing.py"


def _pages_that_decide() -> list[Path]:
    routers = sorted((_SOURCE_ROOT / "api" / "routers").glob("*.py"))
    templates = sorted((_SOURCE_ROOT / "templates").rglob("*.html"))
    return [path for path in (*routers, *templates) if path != _OWNER]


def test_no_page_decides_who_may_buy_a_plan_for_itself() -> None:
    """No router and no template may work the rule out from the raw plan-code set.

    Calling `active_paid_plan_codes(...)` to load the set is fine - that is how the
    answer is fetched. Testing membership of it is the copy: it is the part that
    forgot about plans nobody can buy, and about holding a *different* plan.
    """

    offenders = []
    for path in _pages_that_decide():
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if _MEMBERSHIP_COPY.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "These decide for themselves whether an account may buy a plan. Read "
        "plan_checkout_availability() and use holds_this / holds_other / purchasable:\n"
        + "\n".join(offenders)
    )


def test_the_scan_would_notice_a_copy() -> None:
    """The scan above passes trivially if its pattern matches nothing.

    So the pattern is run against the shape it is meant to catch, and against the
    shape it must leave alone.
    """

    assert _MEMBERSHIP_COPY.search("already_subscribed=plan.code in active_paid_plan_codes")
    assert _MEMBERSHIP_COPY.search("current = code in user_active_paid_plan_codes")
    assert not _MEMBERSHIP_COPY.search(
        "codes = await active_paid_plan_codes(session, user_id=user.id)"
    )


def test_every_page_that_asks_the_question_reads_the_one_answer() -> None:
    """The three surfaces the customer can reach all call the one owner.

    A page that stopped calling it would go quiet rather than go wrong, so the call is
    asserted rather than assumed.
    """

    expected = {
        # the popup and the plan cards on the billing page
        _SOURCE_ROOT / "api" / "routers" / "dashboard.py",
        # the plan cards on the subscription page
        _SOURCE_ROOT / "api" / "routers" / "dashboard_test.py",
    }
    for path in expected:
        text = path.read_text(encoding="utf-8")
        assert "plan_checkout_availability(" in text, path.name

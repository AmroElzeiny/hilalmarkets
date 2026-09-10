"""Invariant: no module can reach a payment company without a test patching it.

``tests/support/billing_config.py`` patches the outbound door in every module that can
send a request to Creem, NOWPayments or Stripe. That list used to be typed out by hand
and named two modules while four could really make the call. The two it missed were
``plan_replacements`` - the cancellation of the old plan after a plan move - and
``discount_codes``. A test touching either path sent a real request to a real payment
company over the real internet, and read the company's refusal as if our own server had
refused the customer.

The list is now read from the source tree. These tests assert the rule for the whole
family rather than for the two modules that were once missed:

* every module that names a payment company in a ``provider_request`` call is patched;
* the door name each of those modules calls is really replaced by the patch;
* an unpatched payment call fails loudly instead of going out.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest

from tests.support.billing_config import (
    PAYMENT_COMPANIES,
    ReachedThePaymentCompany,
    payment_call_site_names,
    refuse_payment_network,
    stub_payment_companies,
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"


def _modules_naming_a_payment_company() -> list[str]:
    """Read the source tree directly, without reusing the helper being tested."""

    names: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "provider_request" not in text:
            continue
        if not any(f'provider="{company}"' in text for company in PAYMENT_COMPANIES):
            continue
        parts = path.relative_to(_PACKAGE_ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(("ai_market_monitor", *parts)))
    return names


def test_the_guard_covers_every_module_that_can_reach_a_payment_company() -> None:
    assert sorted(payment_call_site_names()) == sorted(
        _modules_naming_a_payment_company()
    )


def test_the_known_money_modules_are_all_covered() -> None:
    """The four modules that exist today, named so a deletion is visible."""

    covered = set(payment_call_site_names())
    for name in (
        "ai_market_monitor.services.billing",
        "ai_market_monitor.services.discount_codes",
        "ai_market_monitor.services.plan_changes",
        "ai_market_monitor.services.plan_replacements",
    ):
        assert name in covered, name


@pytest.mark.parametrize("module_name", payment_call_site_names())
def test_every_covered_module_really_holds_the_door_name(module_name: str) -> None:
    assert hasattr(import_module(module_name), "provider_request")


@pytest.mark.parametrize("module_name", payment_call_site_names())
@pytest.mark.parametrize("company", sorted(PAYMENT_COMPANIES))
def test_the_stub_answers_for_every_module_and_company(
    monkeypatch: pytest.MonkeyPatch, module_name: str, company: str
) -> None:
    calls = stub_payment_companies(monkeypatch)
    module = import_module(module_name)
    response = _call(module, company)
    assert response.status_code == 200
    assert [call["provider"] for call in calls] == [company]


@pytest.mark.parametrize("module_name", payment_call_site_names())
@pytest.mark.parametrize("company", sorted(PAYMENT_COMPANIES))
def test_an_unstubbed_payment_call_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, module_name: str, company: str
) -> None:
    refuse_payment_network(monkeypatch)
    module = import_module(module_name)
    with pytest.raises(ReachedThePaymentCompany):
        _call(module, company)


def _call(module: object, company: str):
    import asyncio

    door = module.provider_request  # type: ignore[attr-defined]
    return asyncio.run(
        door(
            None,
            "POST",
            f"https://{company}.example.com/v1/anything",
            provider=company,
            operation="invariant_probe",
            json={},
        )
    )

"""The billing settings of the server that is really running, for tests.

The offline test server starts with no payment company configured at all. That is the
right default — most tests are not about money, and a test that quietly holds live-looking
keys is worse than one that holds none.

It is the wrong default for the handful of tests that are about the pricing cards. "Can
this plan be bought?" is answered from the payment settings, so on a server with none the
answer is no, every paid card reads "coming soon", and a test written against that proves
nothing about the shipped product.

`configure_live_billing` puts the test server into the same shape as `.env.production`:
card payments through Creem, crypto through NOWPayments, and a product id for every plan
on sale. The values are obvious fakes; only their *presence* decides what a card says.

One helper rather than a copy in each test file, because the shape of "configured" is a
fact about the product, and six copies of it drift.

The second half of this file is about the *other* side of that switch. Settings that say
"a payment company is configured" are exactly the settings that let the checkout code
open a socket to one, and the fake keys above are then sent to the real Creem and the
real NOWPayments over the real internet. That happened: four cases of
``test_invariant_billing_offers`` were failing on a 401 from Creem and a 403 from
NOWPayments, and the test read those refusals as "our server refused the purchase". No
offline test may talk to a payment company, so `stub_payment_companies` answers for them
in process and `refuse_payment_network` — wired autouse in ``tests/conftest.py`` — makes
any call that was not stubbed fail loudly instead of quietly going out.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES

#: Never a real key. Long enough to look like one where a length is checked.
_FAKE_SECRET = "test-only-not-a-real-key-000000"


def live_billing_overrides() -> dict[str, object]:
    """The settings a server needs before it can take money for a plan.

    A product id per plan on sale, read from `PURCHASABLE_PLAN_CODES` rather than typed
    out: opening a new plan for sale must not need this file edited before its card can
    be tested.
    """

    return {
        "billing_enabled": True,
        "billing_card_provider": "creem",
        "billing_crypto_provider": "nowpayments",
        "creem_api_key": SecretStr(_FAKE_SECRET),
        "creem_webhook_secret": SecretStr(_FAKE_SECRET),
        "creem_product_ids": {
            f"{code}_monthly": f"prod_test_{code}_monthly"
            for code in PURCHASABLE_PLAN_CODES
        },
        "nowpayments_api_key": SecretStr(_FAKE_SECRET),
        "nowpayments_ipn_secret": SecretStr(_FAKE_SECRET),
    }


def configure_live_billing(settings: Settings) -> Settings:
    """Put a test server's settings into the shape the live server is in.

    Changed on the settings object the app already holds, because the app was built with
    that object and a copy would be ignored by everything already wired to it.
    """

    for field, value in live_billing_overrides().items():
        setattr(settings, field, value)
    return settings


class ReachedThePaymentCompany(AssertionError):
    """A test let an outbound call escape to a real payment company."""


#: Every company name the money code passes to ``provider_request`` as ``provider``.
PAYMENT_COMPANIES: Final[frozenset[str]] = frozenset({"creem", "nowpayments", "stripe"})

#: The package whose source is searched for modules that call a payment company.
_PACKAGE_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"
)

#: A module that both holds its own imported name for the outbound door *and* names a
#: payment company in a call is a module a test must patch. Each such module does
#: ``from ...provider_runtime import provider_request``, so the name it calls is its
#: *own* module attribute. Patching only the door's home module would replace a name
#: none of them read, and the call would still go out.
_DOOR_NAME: Final[str] = "provider_request"


@lru_cache(maxsize=1)
def payment_call_site_names() -> tuple[str, ...]:
    """Every module that can send a request to a payment company, found in the source.

    Read from the tree rather than typed out. A hand-written list is the exact shape of
    defect this file exists to prevent: it named two modules while four could reach a
    payment company, so ``plan_replacements`` (the plan-move cancellation) and
    ``discount_codes`` were never patched and their calls went out over the real
    internet. A list cannot drift if nothing writes it by hand.
    """

    found: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if _DOOR_NAME not in text:
            continue
        if not any(f'provider="{company}"' in text for company in PAYMENT_COMPANIES):
            continue
        parts = path.relative_to(_PACKAGE_ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found.append(".".join(("ai_market_monitor", *parts)))
    return tuple(found)


def _payment_call_site_modules() -> tuple[Any, ...]:
    """Import and return the modules that call a payment company."""

    from importlib import import_module

    return tuple(import_module(name) for name in payment_call_site_names())


def _real_provider_request() -> Any:
    """The unpatched outbound door, so a non-payment call still travels normally."""

    from ai_market_monitor.services.provider_runtime import provider_request

    return provider_request


def _creem_answer(url: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """A Creem-shaped success body for whichever Creem call this is."""

    if url.endswith("/v1/checkouts"):
        request_id = str(payload.get("request_id") or uuid4())
        return {
            "id": f"ch_{request_id}",
            "checkout_url": f"https://checkout.creem.io/{request_id}",
        }
    if url.endswith("/v1/customers/billing"):
        return {"customer_portal_link": "https://creem.io/customer/test-session"}
    # Everything else Creem is asked for is a change to an existing subscription
    # (an upgrade, a re-price, a cancellation). All of them answer with the record.
    return {"id": f"creem_change_{uuid4().hex[:12]}"}


def _nowpayments_answer(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A NOWPayments-shaped invoice body."""

    order_id = str(payload.get("order_id") or uuid4().hex)
    return {
        "id": f"inv_{order_id}",
        "invoice_url": f"https://nowpayments.io/payment/{order_id}",
    }


def payment_company_answer(
    url: str, provider: str, payload: Mapping[str, Any]
) -> httpx.Response:
    """The reply a working payment company would send for this call.

    One place decides these shapes. Each test file used to write its own fake and each
    knew a different subset of the calls, so a test could pass while the call it did not
    know about went out over the network.
    """

    if provider == "creem":
        return httpx.Response(200, json=_creem_answer(url, payload))
    if provider == "nowpayments":
        return httpx.Response(200, json=_nowpayments_answer(payload))
    if provider == "stripe":
        return httpx.Response(
            200,
            json={
                "id": f"stripe_{uuid4().hex[:12]}",
                "url": "https://billing.stripe.com/session/test",
            },
        )
    raise ReachedThePaymentCompany(
        f"No stubbed answer for provider {provider!r} at {url!r}."
    )


def stub_payment_companies(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Answer every payment-company call in process, and record what was sent.

    Returns the list of calls, so a test can assert what the company was asked for. A
    call to any other provider still goes through the real door, because the AI and
    market-data providers have their own fakes and are not this helper's business.
    """

    calls: list[dict[str, Any]] = []
    modules = _payment_call_site_modules()
    real = _real_provider_request()

    async def answer(
        settings: Any,
        method: str,
        url: str,
        *,
        provider: str,
        operation: str = "",
        **kwargs: Any,
    ) -> httpx.Response:
        if provider not in PAYMENT_COMPANIES:
            return await real(
                settings, method, url, provider=provider, operation=operation, **kwargs
            )
        payload = kwargs.get("json") or kwargs.get("data") or {}
        calls.append(
            {
                "method": method,
                "url": url,
                "provider": provider,
                "operation": operation,
                "payload": payload,
            }
        )
        return payment_company_answer(url, provider, payload)

    for module in modules:
        monkeypatch.setattr(module, "provider_request", answer)
    return calls


def refuse_payment_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make an unstubbed call to a payment company fail loudly instead of going out.

    Fail closed, the way the compiler does. A test that silently reaches Creem is not a
    test of our rules — it is a test of our internet connection, and it reports the
    company's "401 wrong key" as if our own server had refused the customer.

    A test that means to exercise the payment path calls `stub_payment_companies`, or
    patches the call itself; either replaces this refusal.
    """

    async def refuse(
        settings: Any,
        method: str,
        url: str,
        *,
        provider: str,
        operation: str = "",
        **kwargs: Any,
    ) -> httpx.Response:
        if provider in PAYMENT_COMPANIES:
            raise ReachedThePaymentCompany(
                f"This test tried to send a real {provider} request to {url}. "
                "Offline tests must never contact a payment company. Call "
                "tests.support.billing_config.stub_payment_companies(monkeypatch) "
                "in the test instead."
            )
        return await real(
            settings, method, url, provider=provider, operation=operation, **kwargs
        )

    real = _real_provider_request()
    for module in _payment_call_site_modules():
        monkeypatch.setattr(module, "provider_request", refuse)

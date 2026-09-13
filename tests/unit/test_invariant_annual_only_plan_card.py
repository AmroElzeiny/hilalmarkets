"""Plan cards and pre-selection must not lag the server's checkout availability.

The owner for "can this plan be bought" is :func:`plan_checkout_availability`, which
sells `card_annual` as well as monthly. The dashboard cards used to gate on monthly only,
so an annual-only configuration showed "coming soon" while the checkout review page would
have sold.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_market_monitor.core import plans
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import PURCHASABLE_PLAN_CODES
from ai_market_monitor.services.billing import (
    plan_checkout_availability,
    plan_is_on_sale,
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "billing_enabled": True,
        "billing_provider": "creem",
        "billing_card_provider": "creem",
        "billing_crypto_provider": "disabled",
        "creem_api_key": SecretStr("creem-key"),
        "creem_webhook_secret": SecretStr("creem-webhook"),
        # Only annual product ids: monthly is listed in the catalog but not sold.
        "creem_product_ids": {
            f"{code}_annual": f"prod_{code}_annual"
            for code in PURCHASABLE_PLAN_CODES
        },
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def annual_only_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the purchasable plans annual-only for the duration of one test."""

    for code in PURCHASABLE_PLAN_CODES:
        offer = plans.PLAN_OFFERS[code]
        monkeypatch.setitem(
            plans.PLAN_OFFERS,
            code,
            plans.PlanOffer(
                monthly_available=False,
                annual_available=True,
                promotional_monthly_price=offer.promotional_monthly_price,
            ),
        )


@pytest.mark.usefixtures("annual_only_catalog")
@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
def test_annual_only_plan_is_on_sale_for_annual_not_monthly(code: str) -> None:
    settings = _settings()
    assert plan_is_on_sale(settings, code, billing_cycle="annual") is True
    assert plan_is_on_sale(settings, code, billing_cycle="monthly") is False


@pytest.mark.usefixtures("annual_only_catalog")
@pytest.mark.parametrize("code", PURCHASABLE_PLAN_CODES)
def test_annual_only_checkout_sells_annual_not_monthly(code: str) -> None:
    settings = _settings()
    monthly = plan_checkout_availability(
        settings,
        plan_code=code,
        active_paid_plan_codes=frozenset(),
        billing_cycle="monthly",
        payment_method="card",
    )
    annual = plan_checkout_availability(
        settings,
        plan_code=code,
        active_paid_plan_codes=frozenset(),
        billing_cycle="annual",
        payment_method="card",
    )
    assert monthly["requested_purchasable"] is False
    assert annual["requested_purchasable"] is True
    # The high-level flag that pages use for the whole card.
    assert annual["card_annual"] is True
    assert annual["card_monthly"] is False


def test_dashboard_gates_plan_cards_on_any_cycle() -> None:
    """The billing page must ask about monthly OR annual, not monthly alone."""

    dashboard_path = Path(
        "src/ai_market_monitor/api/routers/dashboard.py"
    ).resolve()
    source = dashboard_path.read_text(encoding="utf-8")
    # The plan_switch_offers call builds purchasable from both cycles.
    assert re.search(
        r"plan_is_on_sale\([^\n]*billing_cycle=\"monthly\"\)[^\n]*\n"
        r"[^\n]*or plan_is_on_sale\([^\n]*billing_cycle=\"annual\"\)",
        source,
    ) or re.search(
        r"plan_is_on_sale\([^\n]*billing_cycle=\"monthly\"\)[^\n]*or[^\n]*plan_is_on_sale\([^\n]*billing_cycle=\"annual\"\)",
        source,
    ), "plan card gate must check any on-sale cycle"
    # The pre-selected plan query parameter uses the same any-cycle gate.
    assert re.search(
        r"if selected_checkout_plan not in PURCHASABLE_PLAN_CODES or not \(",
        source,
    ), "pre-selected plan gate must check any on-sale cycle"

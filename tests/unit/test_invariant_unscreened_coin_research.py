"""The unscreened-coin researcher gathers facts and never a Shariah status.

Two things are asserted, and the second one caught a real defect.

1. **No status leaks in.** A provider profile has no column that could hold halal or
   haram, and the model must not grow one. A coin with a gathered whitepaper is still
   an unscreened coin.
2. **The exchange-plumbing filter does not eat real coins.** Matching a leveraged
   suffix on its own dropped ``JUP`` — Jupiter, a top-100 asset — because it ends with
   ``UP``, and it would have been silently unresearchable for ever.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.db.models import ProviderCoinProfile
from ai_market_monitor.services.unscreened_coin_research import (
    ResearchPlan,
    _is_researchable,
)


@pytest.mark.parametrize(
    "symbol",
    [
        # Real coins whose tickers collide with a leveraged suffix or a stablecoin.
        "JUP",   # ends with UP
        "SUP",   # ends with UP
        "DOWN",  # is the suffix, but nothing is left of it
        "UP",
        "DAI",   # a stablecoin is a project with a whitepaper
        "USDC",
        "USDT",
        "BTC",   # also a quote asset, still a coin
        "ETH",
        "SEI",
        "TAO",
        "QUBIC",
    ],
)
def test_real_coins_are_researchable(symbol: str):
    assert _is_researchable(symbol), f"{symbol} must not be filtered out"


@pytest.mark.parametrize(
    "symbol",
    ["BTCUP", "BTCDOWN", "ETHBULL", "ETHBEAR", "BTC3L", "ETH3S", "SOL5L", "ADA5S"],
)
def test_leveraged_tickers_are_not_researched(symbol: str):
    assert not _is_researchable(symbol)


@pytest.mark.parametrize("code", ["USD", "EUR", "GBP", "TRY", "JPY"])
def test_national_currencies_are_not_coins(code: str):
    assert not _is_researchable(code)


def test_a_provider_profile_cannot_carry_a_shariah_status():
    """Fact storage only. A status column here would become a ruling nobody made."""

    columns = set(ProviderCoinProfile.__table__.columns.keys())
    forbidden = {
        "status",
        "sharia_status",
        "shariah_status",
        "halal",
        "is_halal",
        "haram",
        "is_haram",
        "eligible",
        "is_eligible",
        "compliant",
        "is_compliant",
        "verdict",
        "methodology_id",
        "assessment_id",
    }
    assert columns & forbidden == set(), (
        "provider_coin_profiles must hold facts only; "
        f"found status-shaped columns: {sorted(columns & forbidden)}"
    )


def test_the_plan_costs_nothing_until_it_finds_work():
    assert ResearchPlan().estimated_provider_calls == 0


@pytest.mark.parametrize(
    ("coins", "expected_calls"),
    [(1, 1), (99, 1), (100, 1), (101, 2), (250, 3)],
)
def test_one_provider_call_carries_a_hundred_coins(coins: int, expected_calls: int):
    """The property that makes a whole-universe sweep affordable."""

    plan = ResearchPlan(to_research=[f"C{index}" for index in range(coins)])
    assert plan.estimated_provider_calls == expected_calls

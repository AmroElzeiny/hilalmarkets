"""The free and paid Hilal allowances must be choosable independently, and coherent.

The paid allowance used to be derived as ``free x multiplier`` with a whole-number
multiplier, so the two figures shared one dial. A free allowance of 0.15 could only buy a
paid one of 0.15, 0.30, 0.45 and so on; asking for 0.15 free and 0.25 paid was not an
unusual request, it was simply not expressible, and the only way to reach it was to
distort the free figure until the arithmetic happened to land.

Stating the paid figure outright fixes that, and removes a guarantee the multiplier gave
away for free: with ``ge=1`` it could never express "less than the free tier". A standalone
number can, so the relationship is checked instead.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.hilal_chat import daily_allowance

BASE = {
    "_env_file": None,
    "app_env": "test",
    "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
}


def settings(**overrides: object) -> Settings:
    return Settings(**{**BASE, **overrides})


@pytest.mark.parametrize(
    ("free", "paid"),
    [
        ("0.15", "0.25"),  # the pair the multiplier could not express
        ("0.10", "0.35"),
        ("0.01", "0.02"),
        ("1.00", "1.00"),  # equal is allowed: no upgrade, but not a downgrade
        ("0.05", "50.00"),
    ],
)
def test_any_paid_figure_at_or_above_the_free_one_is_expressible(free: str, paid: str) -> None:
    """The point of the change: the two numbers are independent."""

    config = settings(hilal_chat_free_daily_usd=float(free), hilal_chat_paid_daily_usd=float(paid))

    assert daily_allowance(config, paying=False) == Decimal(free)
    assert daily_allowance(config, paying=True) == Decimal(paid)


@pytest.mark.parametrize(
    ("free", "paid"),
    [("0.15", "0.10"), ("1.00", "0.99"), ("10.0", "0.01")],
)
def test_a_paid_figure_below_the_free_one_is_refused(free: str, paid: str) -> None:
    """A subscription must never buy a smaller allowance than paying nothing."""

    with pytest.raises(ValidationError, match="HILAL_CHAT_PAID_DAILY_USD"):
        settings(
            hilal_chat_free_daily_usd=float(free),
            hilal_chat_paid_daily_usd=float(paid),
        )


@pytest.mark.parametrize("multiplier", [1, 2, 5, 10, 100])
def test_the_multiplier_still_decides_when_no_paid_figure_is_stated(multiplier: int) -> None:
    """Existing deployments set no paid figure, and must behave exactly as before."""

    config = settings(
        hilal_chat_free_daily_usd=0.10,
        hilal_chat_paid_daily_multiplier=multiplier,
    )

    assert config.hilal_chat_paid_daily_usd is None
    assert daily_allowance(config, paying=True) == Decimal("0.10") * multiplier


def test_a_stated_figure_wins_over_the_multiplier() -> None:
    """Two dials for one number, so which one rules has to be settled and asserted."""

    config = settings(
        hilal_chat_free_daily_usd=0.10,
        hilal_chat_paid_daily_multiplier=5,
        hilal_chat_paid_daily_usd=0.25,
    )

    assert daily_allowance(config, paying=True) == Decimal("0.25")


def test_the_free_allowance_never_depends_on_the_paid_one() -> None:
    """Whatever a subscriber is given, a visitor's allowance is the free figure alone."""

    for paid in (None, 0.25, 99.0):
        config = settings(hilal_chat_free_daily_usd=0.15, hilal_chat_paid_daily_usd=paid)
        assert daily_allowance(config, paying=False) == Decimal("0.15")


def test_the_default_deployment_is_unchanged() -> None:
    """No paid figure ships set, so the shipped behaviour is the old arithmetic."""

    config = settings()

    assert config.hilal_chat_paid_daily_usd is None
    free = Decimal(str(config.hilal_chat_free_daily_usd))
    expected = free * config.hilal_chat_paid_daily_multiplier
    assert daily_allowance(config, paying=True) == expected


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_an_empty_line_means_unset_rather_than_a_crash(blank: str) -> None:
    """An env file writes "absent" as an empty value, for a number as much as for text.

    Every optional text setting already reads a bare ``KEY=`` that way. This one did not,
    and startup died on a template that was only saying "no opinion, use the multiplier" --
    the same shape as the two list settings that made both example files unloadable.
    """

    config = settings(
        hilal_chat_free_daily_usd=0.10,
        hilal_chat_paid_daily_multiplier=5,
        hilal_chat_paid_daily_usd=blank,
    )

    assert config.hilal_chat_paid_daily_usd is None
    assert daily_allowance(config, paying=True) == Decimal("0.50")

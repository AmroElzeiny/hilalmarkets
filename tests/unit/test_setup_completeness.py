"""Deterministic completeness gate for a described setup.

Evaluator run 20260725T122105Z captured a structured strategy object in 1 of 42
cases. ``ready_to_compile`` is owned by the interviewer model, so a session holding
every field the compiler needs could still loop on questions and never produce a
draft. These cases pin down when a draft must exist.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.setup_completeness import (
    REQUIRED_FIELDS,
    assess_setup_requirements,
    detect_universe_scope,
)
from ai_market_monitor.schemas.strategy import StrategyDirection


@pytest.mark.parametrize(
    "text",
    [
        "watchlist for SOLUSDT only, explicitly exclude BTCUSDT. We'll use 4h context "
        "and a 1h trigger with a short bias: bearish move of at least 7.5%",
        "alert me when BTCUSDT RSI drops below 30 on the 15m",
        "scan all USDT pairs for coins up 5% today on the 1h",
        "watch my favorites for a volume spike above 3x average on the 5m",
        "halal compliant coins only, breakout above the previous day high on the 4h",
        "top 50 coins, price crosses above the 20 EMA on the 1h",
    ],
)
def test_a_fully_described_setup_is_complete(text: str) -> None:
    assert assess_setup_requirements(text).is_complete is True


@pytest.mark.parametrize(
    ("text", "missing"),
    [
        ("watch BTCUSDT", ("timeframe", "trigger_condition")),
        ("RSI below 30 on the 15m", ("universe",)),
        ("all coins, RSI below 30", ("timeframe",)),
        ("all coins on the 15m", ("trigger_condition",)),
        ("hi there", ("universe", "timeframe", "trigger_condition")),
        ("", ("universe", "timeframe", "trigger_condition")),
    ],
)
def test_an_underspecified_setup_names_exactly_what_is_missing(
    text: str, missing: tuple[str, ...]
) -> None:
    requirements = assess_setup_requirements(text)
    assert requirements.is_complete is False
    assert requirements.missing == missing


def test_every_missing_field_has_a_user_facing_label() -> None:
    requirements = assess_setup_requirements("")
    assert len(requirements.missing_labels) == len(REQUIRED_FIELDS)
    assert all(label and not label.endswith("_") for label in requirements.missing_labels)


def test_a_field_settled_outside_the_text_counts_as_known() -> None:
    """A timeframe chosen in guided setup is genuinely held by the compiler."""
    text = "all coins, RSI below 30"
    assert assess_setup_requirements(text).is_complete is False
    assert (
        assess_setup_requirements(text, known_fields=frozenset({"timeframe"})).is_complete is True
    )


@pytest.mark.parametrize(
    ("text", "scope"),
    [
        ("all coins", "market_wide"),
        ("every pair", "market_wide"),
        ("the whole market", "market_wide"),
        ("market-wide", "market_wide"),
        ("top 100", "market_wide"),
        ("all USDT pairs", "market_wide"),
        ("my favourites", "curated_list"),
        ("my watchlist", "curated_list"),
        ("halal compliant coins", "screened"),
        ("screened universe", "screened"),
    ],
)
def test_universe_scope_wording_is_recognised(text: str, scope: str) -> None:
    assert detect_universe_scope(text) == scope


@pytest.mark.parametrize("text", ["all of it", "any time", "every candle", "top"])
def test_bare_quantifiers_do_not_scope_a_universe(text: str) -> None:
    """`all` alone is not a universe. Only a scoping noun makes it one."""
    assert detect_universe_scope(text) is None


def test_named_symbols_take_precedence_over_a_market_wide_phrase() -> None:
    requirements = assess_setup_requirements("watch all coins but only BTCUSDT on the 1h RSI < 30")
    assert requirements.universe_scope == "explicit_symbols"
    assert "BTCUSDT" in requirements.symbols


def test_the_report_carries_the_parsed_values_it_used() -> None:
    requirements = assess_setup_requirements(
        "short SOLUSDT, exclude BTCUSDT, 1h trigger, bearish move of at least 7.5%"
    )
    assert requirements.symbols == ("SOLUSDT",)
    assert requirements.excluded_symbols == ("BTCUSDT",)
    assert requirements.timeframes == ("1h",)
    assert requirements.direction is StrategyDirection.SHORT
    assert requirements.trigger_fragments


def test_completeness_never_claims_a_field_it_did_not_find() -> None:
    """satisfied and missing must partition the required fields exactly."""
    for text in ("", "watch BTCUSDT", "BTCUSDT on the 1h with RSI below 30"):
        requirements = assess_setup_requirements(text)
        assert set(requirements.satisfied).isdisjoint(requirements.missing)
        assert set(requirements.satisfied) | set(requirements.missing) == set(REQUIRED_FIELDS)

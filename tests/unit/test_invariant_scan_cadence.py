"""A monitor is checked once per candle it watches.

Measured on the live server on 24 August 2026: every monitor was built on the ``1m``
timeframe, and each was being checked about **once an hour**. A person had asked to be told
about one-minute candles and was being shown prices up to sixty candles old.

The cause was two owners of one fact. ``strategy_universes.scan_interval_seconds`` is a
stored number, written once when a monitor is made; the timeframe is what the monitor
actually watches. Nothing kept them in step, and the scheduler read the stored one.

The rule is asserted here over **every** timeframe the product supports, not over the
one that was reported. A cadence that happens to be right for ``1m`` and wrong for ``4h``
is the same defect wearing a different number.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.data_freshness import timeframe_ms
from ai_market_monitor.engine.scan_cadence import (
    FALLBACK_INTERVAL_SECONDS,
    base_timeframe_of,
    scan_interval_seconds,
)
from ai_market_monitor.schemas.timeframes import ORDERED_TIMEFRAMES, TIMEFRAME_MINUTES


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_the_interval_is_exactly_one_candle(timeframe: str) -> None:
    """Every supported candle, not the one that was reported broken."""

    assert scan_interval_seconds({"base_timeframe": timeframe}) == TIMEFRAME_MINUTES[timeframe] * 60


@pytest.mark.parametrize("timeframe", ORDERED_TIMEFRAMES)
def test_the_interval_agrees_with_the_freshness_owner(timeframe: str) -> None:
    """One candle means the same thing here as it does where lateness is measured.

    ``measure_freshness`` decides whether a check read the newest candle, and it sizes a
    candle with ``timeframe_ms``. If the cadence sized a candle differently, a monitor
    could be scanned exactly on time and still be told its data was late.
    """

    assert scan_interval_seconds({"base_timeframe": timeframe}) == timeframe_ms(timeframe) // 1000


def test_a_one_minute_monitor_is_checked_every_minute() -> None:
    """The reported case, kept as a case — the rule above is what actually guards it."""

    assert scan_interval_seconds({"base_timeframe": "1m"}) == 60


def test_a_longer_candle_is_not_checked_every_minute() -> None:
    """The other half of the rule. Checking a daily monitor every minute is 1439 wasted
    checks a day on a server whose limit is how much work one process can do."""

    assert scan_interval_seconds({"base_timeframe": "1d"}) == 86_400


@pytest.mark.parametrize(
    "schema",
    [
        None,
        {},
        {"base_timeframe": None},
        {"base_timeframe": ""},
        {"base_timeframe": "not-a-timeframe"},
        {"base_timeframe": 15},
        "a string, not a schema",
        [],
    ],
)
def test_an_unreadable_schema_falls_back_to_the_shortest_candle(schema: object) -> None:
    """Refuse to guess a long interval from a broken schema.

    The two ways to be wrong are not equal. Checking too often costs a skipped job — the
    scheduler will not queue a second check while the first is still running. Checking too
    rarely costs a late alert, and that one reaches a customer.
    """

    assert scan_interval_seconds(schema) == FALLBACK_INTERVAL_SECONDS


def test_the_timeframe_is_read_from_the_key_the_definition_stores_it_under() -> None:
    """The scheduler reads one key rather than validating a whole definition, so the key
    it reads has to be the one the definition writes."""

    from ai_market_monitor.schemas.strategy import StrategyDefinition

    assert "base_timeframe" in StrategyDefinition.model_fields
    assert base_timeframe_of({"base_timeframe": "5m"}) == "5m"


def test_no_scheduler_reads_the_stored_cadence_column() -> None:
    """The stored ``scan_interval_seconds`` column must have no reader left anywhere.

    Leaving one behind is how two owners come back: the column keeps its old value for
    ever, so anything still reading it disagrees with the candle silently, and only about
    monitors nobody has touched recently.

    Read as code rather than as text. The comment in ``scanner.py`` that explains what this
    used to do names the column on purpose, and a rule that cannot tell an explanation from
    a reader would push people to delete the explanation.
    """

    import ast
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"
    offenders: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "scan_interval_seconds":
                offenders.append(f"{path.relative_to(source_root)}:{node.lineno}")
    assert offenders == [], (
        "the stored cadence column is being read again: "
        + ", ".join(offenders)
        + ". The candle a monitor watches is the only thing that decides how often it is "
        "checked — see engine/scan_cadence.py."
    )

"""A scan reads the exchange's extra details once, not once per symbol.

``fetch_universe_metadata`` has always taken a list of symbols. The scanner called it with
one symbol at a time from inside the per-symbol loop, so a 22-symbol universe paid for it
22 times — and it is the most expensive call the product makes. When any named symbol is
missing from the answer the provider falls back to reading *every* ticker on the exchange:
1,886,437 bytes and 4,000 ms of ccxt rate-limit sleep, measured against Binance on
24 August 2026.

That is most of why one scan took 198 seconds while the monitors it served were watching
one-minute candles.

The rule is asserted over universe sizes rather than over the size that was reported,
because "once per scan" that happens to hold at 22 symbols and not at 300 is not a rule.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from ai_market_monitor.db.models.enums import ConditionType
from ai_market_monitor.schemas.strategy import (
    AlertPolicy,
    Comparator,
    ConditionGroup,
    ConditionRule,
    LogicalOperator,
    Operand,
    OperandKind,
    StrategyDefinition,
    TriggerMode,
    UniverseDefinition,
)
from ai_market_monitor.services.scanner import ScanOrchestrator

UNIVERSE_SIZES = [1, 2, 8, 9, 22, 50, 119, 300]

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"


def a_definition(*, min_listing_age_days: int | None = None) -> StrategyDefinition:
    return StrategyDefinition(
        name="A monitor",
        base_timeframe="1m",
        trigger_mode=TriggerMode.CANDLE_CLOSE,
        universe=UniverseDefinition(
            exchange="binance",
            quote_currencies=["USDT"],
            min_listing_age_days=min_listing_age_days,
        ),
        conditions=ConditionGroup(
            key="all_required_conditions",
            operator=LogicalOperator.AND,
            children=[
                ConditionRule(
                    key="close_above_a_level",
                    label="Price closes above 100",
                    condition_type=ConditionType.PRICE_ACTION,
                    timeframe="1m",
                    left=Operand(kind=OperandKind.PRICE, field="close"),
                    comparator=Comparator.GREATER_THAN,
                    right=Operand(kind=OperandKind.CONSTANT, value=100.0),
                )
            ],
        ),
        alerts=AlertPolicy(channels=["telegram"]),
    )


def universe(size: int) -> list[str]:
    return [f"A{index:04d}/USDT" for index in range(size)]


class CountingProvider:
    def __init__(self, *, raises: bool = False) -> None:
        self.metadata_calls: list[list[str]] = []
        self.raises = raises

    async def fetch_universe_metadata(
        self, exchange: str, symbols: list[str], *, include_listing_dates: bool = False
    ) -> dict[str, dict[str, Any]]:
        self.metadata_calls.append(list(symbols))
        if self.raises:
            raise RuntimeError("the exchange would not answer")
        return {symbol: {"bid": 10.0, "data_quality_ok": True} for symbol in symbols}


def orchestrator(provider: CountingProvider) -> ScanOrchestrator:
    # The session is never touched by the metadata read: reading the exchange writes
    # nothing, which is the whole reason it can be done for many symbols at once.
    return ScanOrchestrator(None, provider, settings=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("size", UNIVERSE_SIZES)
async def test_a_scan_asks_for_the_extra_details_exactly_once(size):
    """One request, whatever the universe size, and it names every symbol."""

    provider = CountingProvider()
    symbols = universe(size)

    answer = await orchestrator(provider)._fetch_universe_metadata(a_definition(), symbols)

    assert len(provider.metadata_calls) == 1, (
        f"{len(provider.metadata_calls)} requests for {size} symbols — "
        "this is the call that costs 1.9 MB and 4 seconds when it misses"
    )
    assert provider.metadata_calls[0] == symbols
    assert set(answer) == set(symbols)


@pytest.mark.parametrize("size", UNIVERSE_SIZES)
async def test_the_batched_details_are_used_and_never_looked_up_again(size):
    """The batch is only worth reading if the per-symbol path stops running.

    A record that came back empty is a real answer — the exchange had nothing extra to
    say — and re-reading it per symbol would restore the whole cost the batch removed.
    """

    provider = CountingProvider()
    symbols = universe(size)
    scanner = orchestrator(provider)
    metadata = await scanner._fetch_universe_metadata(a_definition(), symbols)
    provider.metadata_calls.clear()

    for symbol in symbols:
        assert metadata[symbol] == {"bid": 10.0, "data_quality_ok": True}
    assert provider.metadata_calls == []


@pytest.mark.parametrize("size", UNIVERSE_SIZES)
async def test_a_refused_request_marks_every_symbol_rather_than_inventing_details(size):
    """The failure shape the per-symbol version had, kept for the whole batch.

    Nothing may be substituted for details the exchange would not give: a symbol whose
    data could not be read is marked unreadable so the scan skips it visibly.
    """

    provider = CountingProvider(raises=True)
    symbols = universe(size)

    answer = await orchestrator(provider)._fetch_universe_metadata(a_definition(), symbols)

    assert set(answer) == set(symbols)
    for symbol in symbols:
        assert answer[symbol]["data_quality_ok"] is False
        assert answer[symbol]["exchange_available"] is False
        assert answer[symbol]["metadata_source"] == "provider_metadata_unavailable"


async def test_one_symbols_record_is_never_shared_with_another():
    """Separate dictionaries, so a later reader's edit cannot appear against every symbol
    in the scan. ``dict.fromkeys`` would have given all of them one object."""

    provider = CountingProvider(raises=True)
    symbols = universe(5)

    answer = await orchestrator(provider)._fetch_universe_metadata(a_definition(), symbols)
    answer[symbols[0]]["marked"] = True

    assert "marked" not in answer[symbols[1]]


@pytest.mark.parametrize("min_listing_age_days", [None, 1, 30, 365])
async def test_the_listing_date_question_is_passed_through_unchanged(min_listing_age_days):
    """Asking for listing dates costs more, so it is asked for only when a monitor's
    universe actually filters on them — the same condition the per-symbol version used."""

    recorded: list[bool] = []

    class Recorder(CountingProvider):
        async def fetch_universe_metadata(self, exchange, symbols, *, include_listing_dates=False):
            recorded.append(include_listing_dates)
            return await super().fetch_universe_metadata(
                exchange, symbols, include_listing_dates=include_listing_dates
            )

    provider = Recorder()
    definition = a_definition(min_listing_age_days=min_listing_age_days)

    await orchestrator(provider)._fetch_universe_metadata(definition, universe(4))

    assert recorded == [min_listing_age_days is not None]


async def test_a_provider_without_the_capability_costs_nothing():
    """Not every provider offers extra details. Asking one that does not must be free
    rather than an error — the fixture provider is one of them."""

    class Bare:
        pass

    answer = await orchestrator(Bare())._fetch_universe_metadata(  # type: ignore[arg-type]
        a_definition(), universe(10)
    )

    assert answer == {}


# ------------------------------------------------- one owner for building a provider


def test_only_one_module_decides_which_market_provider_to_build():
    """``CcxtMarketDataProvider(...)`` may be constructed in exactly one place.

    There were eight. ``api/dependencies.py`` chose between the real provider and the
    fixture one; ``worker.py`` wrote the real one by hand seven times and so could not be
    put into fixture mode at all. Anything that must be true of *every* provider — the
    fixture switch, the shared cache — has to be decided once, or the next thing added
    will reach seven of the eight callers again.
    """

    builders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CcxtMarketDataProvider"
            ):
                builders.append(f"{path.relative_to(SOURCE_ROOT).as_posix()}:{node.lineno}")

    outside = [entry for entry in builders if not entry.startswith("services/market_provider.py")]
    assert outside == [], (
        "a market provider is being built outside services/market_provider.py: "
        + ", ".join(outside)
        + ". Build it through market_data_provider(settings) so the fixture switch and "
        "the shared cache reach every caller."
    )
    assert builders, "the one place that builds a provider has disappeared"

"""A card the Builder offers must have something in the product that answers it.

``test_invariant_every_card_evaluates`` asks whether the **engine** can read a card. It
hands the engine a value for every operand name first, so it can only ever prove that
the reading code exists. It cannot see the failure this file is about: a card that is
offered, compiles, is read by code that knows exactly where to look — and nothing in
the product ever puts a value there.

Six cards were in that state, and each reported "unavailable" on every candle of every
coin, for ever:

* ``atr_stop`` — the only producer of the risk context wrote every neighbouring key and
  not this one, with the risk model on **and** off;
* ``listing_age_filter``, ``spread_filter``, ``stablecoin_exclusion`` and
  ``leveraged_token_exclusion`` — the only producer of ``market_context`` wrote two
  keys, though the market snapshot beside it already carried all four readings;
* ``fibonacci_extension_targets`` — genuinely needs a configured target, but was the one
  card in that family that never said so, so the Builder offered it anyway.

So this file builds the condition context the way a scan does — with the real
:class:`ProviderContextService` and nothing injected — and asserts every offered card
produces an answer.
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ai_market_monitor.engine.builder_operations import (
    _build,
    _probe_values,
    mechanic_catalog,
)
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import EvaluationState
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
from ai_market_monitor.provider_context import ProviderContextService
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    DraftMode,
    MarketScopeV2,
    ShariaPolicyV2,
    ShariaUniverseMode,
    StrategyDraftV2,
    StrategyUniverseV2,
)
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.market_preview import market_snapshot_from_candles

SYMBOL = "SOL/USDT"

METADATA: dict[str, Any] = {
    "asset_name": "SOL",
    "quote_volume_24h": 5_000_000_000.0,
    "base_volume_24h": 50_000.0,
    "bid": 99.9,
    "ask": 100.1,
    "last": 100.0,
    "spread_bps": 20.0,
    "listed_at": datetime(2017, 1, 1, tzinfo=UTC),
    "market_cap": 1_000_000_000_000.0,
    "data_quality_ok": True,
    "exchange_available": True,
    "metadata_source": "test",
}


def _candles(count: int = 400, seed: int = 7) -> list[Candle]:
    """A long, wavy, closed history — enough warm-up for every card's lookback.

    Every step is a fraction of the current price so the series never reaches a floor
    and flattens, which would make unrelated cards look broken.
    """

    generator = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    rows: list[Candle] = []
    for index in range(count):
        rate = (math.sin(index / 9) * 1.5 + generator.uniform(-0.8, 0.8) + 0.9) / 100
        open_price = price
        close = price * (1 + rate)
        rows.append(
            Candle(
                timestamp=start + timedelta(minutes=15 * index),
                open=open_price,
                high=max(open_price, close) * 1.004,
                low=min(open_price, close) * 0.996,
                close=close,
                volume=1000 + generator.uniform(0, 500),
                is_closed=True,
            )
        )
        price = close
    return rows


HISTORY = _candles()
NOW = HISTORY[-1].timestamp + timedelta(minutes=15)


class _Provider:
    """Real-shaped candles for whatever symbol a producer asks for. Nothing else."""

    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return [SYMBOL, "BTC/USDT", "ETH/USDT"]

    async def fetch_ohlcv(
        self, exchange: str, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]:
        return _candles(max(limit, 400), seed={"BTC/USDT": 11, "ETH/USDT": 13}.get(symbol, 7))[
            -limit:
        ]

    async def fetch_universe_metadata(
        self, exchange: str, symbols: list[str], *, include_listing_dates: bool = False
    ) -> dict[str, dict[str, Any]]:
        return {symbol: dict(METADATA) for symbol in symbols}

    async def fetch_order_book_context(
        self, exchange: str, symbol: str, *, depth: int = 50
    ) -> dict[str, Any]:
        return {}

    async def fetch_derivatives_context(self, exchange: str, symbol: str) -> dict[str, Any]:
        return {}


def _scan_context(node: ConditionNodeV2) -> dict[str, Any]:
    """What a scan really knows before a rule is read.

    Deliberately **not** a value per operand name. Injecting one is what let six cards
    with no producer at all pass the neighbouring invariant.
    """

    return {
        "last_triggered_at": NOW - timedelta(hours=3),
        "last_symbol_triggered_at": NOW - timedelta(hours=3),
        "last_strategy_triggered_at": NOW - timedelta(hours=3),
        "setup_first_detected_at": NOW - timedelta(hours=2),
        "condition_first_true_at_by_key": {node.node_id: NOW - timedelta(hours=1)},
        "alerts_last_hour": 0,
        "alerts_last_day": 0,
    }


OFFERED = [mechanic for mechanic in mechanic_catalog() if mechanic.available]
OFFERED_KEYS = [mechanic.key for mechanic in OFFERED]
BY_KEY = {mechanic.key: mechanic for mechanic in OFFERED}


def test_the_builder_offers_cards_at_all() -> None:
    """Guards this file: an empty catalogue would make every case below vacuous."""

    assert len(OFFERED) > 300


#: One scan per card, reused by every test that asks about it. Building a strategy and
#: running a scan is the expensive part, and it gives the same answer every time.
_EVALUATED: dict[str, Any] = {}


async def _evaluate(key: str) -> Any:
    if key in _EVALUATED:
        return _EVALUATED[key]
    leaf = await _evaluate_once(key)
    _EVALUATED[key] = leaf
    return leaf


async def _evaluate_once(key: str) -> Any:
    mechanic = BY_KEY[key]
    node, _ = _build(
        mechanic,
        _probe_values(mechanic),
        source_turn_id="producer-audit",
        node_id="card_1",
        required=True,
    )
    draft = StrategyDraftV2(
        mode=DraftMode.MONITOR,
        name="Producer audit",
        market_scope=MarketScopeV2(),
        universe=StrategyUniverseV2(included_symbols=[SYMBOL]),
        sharia_policy=ShariaPolicyV2(
            universe_mode=ShariaUniverseMode.EXPLICIT_ASSETS,
            explicit_symbols=[SYMBOL],
        ),
        condition_ast=ConditionNodeV2(
            node_id="root",
            node_type=ConditionNodeType.AND,
            children=[node],
        ),
    )
    strategy = compile_strategy_draft_v2(draft)
    strategy.universe.min_historical_candles = 1
    # Risk off is the ordinary monitor a beginner builds. A card that only answers with
    # the risk model switched on must say so in the registry, not be offered anyway.
    strategy.risk.enabled = False
    candle_sets = {
        timeframe: HISTORY
        for timeframe in {strategy.base_timeframe, *strategy.supporting_timeframes}
    }
    snapshot = market_snapshot_from_candles(strategy, SYMBOL, candle_sets, NOW, METADATA)
    context = await ProviderContextService(_Provider()).build(
        strategy,
        SYMBOL,
        candle_sets,
        NOW,
        base_context=_scan_context(node),
    )
    result = StrategyRuleEngine().evaluate(
        strategy,
        snapshot,
        candle_sets,
        evaluation_time=NOW,
        strategy_version="1",
        condition_context=context,
    )
    assert result.conditions, (
        f"{key} produced no condition at all; the market was skipped for "
        f"{result.market_filters.reasons}"
    )
    return result.conditions[0]


@pytest.mark.parametrize("key", OFFERED_KEYS)
def test_every_offered_card_is_answered_by_a_real_producer(key: str) -> None:
    """Build the card the way the Builder does, then run a real scan's context."""

    leaf = asyncio.run(_evaluate(key))
    assert leaf.state not in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}, (
        f"{key} is offered in the Builder but nothing in the product supplies its "
        f"value: {leaf.state.value} — {leaf.error_code} — {leaf.explanation}"
    )


@pytest.mark.parametrize("key", OFFERED_KEYS)
def test_every_offered_card_reads_something(key: str) -> None:
    """An answer of ``None`` is not an answer, however green the state looks."""

    leaf = asyncio.run(_evaluate(key))
    if leaf.state is EvaluationState.PENDING:
        return
    assert leaf.actual_value is not None, (
        f"{key} was evaluated as {leaf.state.value} without reading any value"
    )

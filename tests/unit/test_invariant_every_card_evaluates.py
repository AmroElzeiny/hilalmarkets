"""A card the dashboard offers must be a card the runtime can run.

The Builder decides whether to offer a card by *building* one and checking it validates
and compiles. It never checked the last and only question that matters to the person
using it: **can the engine actually read it?**

Seven of the ten core cards could not, and nothing said so:

* four percentage cards stored the name of their formula and nothing else, so the engine
  compared a candle's close with its own close and measured ``0.00%`` forever;
* "price passes the last candle's high", "price reaches the highest of recent candles"
  and "price crosses a level" all built a level named ``previous_candle_high`` or
  ``lookback_high``, which no reader in the engine has ever known;
* "price dips under a level then comes back" was filed as a market metric while its
  reader lives in price action, so it reported "unavailable" on every candle;
* every registered **indicator** card died on
  ``rsi() got an unexpected keyword argument 'threshold'``;
* three date-bounded cards demanded a timestamp their form never asked for.

Each of those is one card. The rule is the same for all of them, so it is asserted for
every card at once, with the same values the availability check itself uses.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.builder_operations import (
    _build,
    _probe_values,
    mechanic_catalog,
)
from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import EvaluationState
from ai_market_monitor.engine.price_action import PRICE_ACTION_NAMES
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
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

SYMBOL = "BTC/USDT"

#: A market that passes every universe filter, so a card is judged on the card.
METADATA = {
    "asset_name": "BTC",
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


def _candles(count: int = 400) -> list[Candle]:
    """A long, wavy, closed history — enough warm-up for every card's lookback."""

    generator = random.Random(7)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    rows: list[Candle] = []
    for index in range(count):
        drift = math.sin(index / 9) * 1.5 + generator.uniform(-0.8, 0.8)
        open_price = price
        close = max(1.0, price + drift)
        high = max(open_price, close) + abs(generator.uniform(0, 0.6))
        low = min(open_price, close) - abs(generator.uniform(0, 0.6))
        rows.append(
            Candle(
                timestamp=start + timedelta(minutes=15 * index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1000 + generator.uniform(0, 500),
                is_closed=True,
            )
        )
        price = close
    return rows


HISTORY = _candles()
NOW = HISTORY[-1].timestamp


def _scan_context(node: ConditionNodeV2) -> dict:
    """The kinds of fact the scanner puts beside a rule before it is evaluated.

    Supplied so a card is judged on whether the engine can *read* it, not on whether
    this test happened to hand it a market snapshot or an alert history.
    """

    named = {operand.name: 1.0 for operand in node.operands if operand.name}
    return {
        "market_context": {**named, "quote_volume_24h": 5_000_000_000.0},
        "cross_market": dict(named),
        "order_book": dict(named),
        "derivatives": dict(named),
        "event_context": dict(named),
        "risk_context": dict(named),
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


def test_the_dashboard_offers_cards_at_all() -> None:
    """Guards the test itself: an empty catalogue would make every case below vacuous."""

    assert len(OFFERED) > 300


@pytest.mark.parametrize("key", OFFERED_KEYS)
def test_every_offered_card_can_be_evaluated(key: str) -> None:
    """Build the card the way the Builder does, then ask the engine to read it."""

    mechanic = BY_KEY[key]
    node, _ = _build(
        mechanic,
        _probe_values(mechanic),
        source_turn_id="card-audit",
        node_id="card_1",
        required=True,
    )
    draft = StrategyDraftV2(
        mode=DraftMode.MONITOR,
        name="Card audit",
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
    strategy.risk.enabled = False
    candle_sets = {
        timeframe: HISTORY
        for timeframe in {strategy.base_timeframe, *strategy.supporting_timeframes}
    }
    snapshot = market_snapshot_from_candles(strategy, SYMBOL, candle_sets, NOW, METADATA)

    result = StrategyRuleEngine().evaluate(
        strategy,
        snapshot,
        candle_sets,
        evaluation_time=NOW,
        strategy_version="1",
        condition_context=_scan_context(node),
    )

    assert result.conditions, (
        f"{key} produced no condition at all; the market was skipped for "
        f"{result.market_filters.reasons}"
    )
    leaf = result.conditions[0]
    assert leaf.state not in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}, (
        f"{key} cannot be evaluated: {leaf.state.value} — {leaf.error_code} — "
        f"{leaf.explanation}"
    )


def test_no_market_metric_shares_a_name_with_a_price_action_reading() -> None:
    """The engine may only look in two places for one name if the names cannot clash.

    A market-metric operand whose name the price-action reader also knows is read as a
    price action, because `sweep_and_reclaim` is filed under the wrong kind by every one
    of its producers. That shortcut is safe only while no other name is ambiguous.
    """

    market_metrics = {
        capability.operand_name or capability.key
        for capability in all_capabilities()
        if (capability.operand_kind or "") == "market_metric"
    }
    clashing = sorted(market_metrics & PRICE_ACTION_NAMES)
    assert clashing == [], (
        "these names mean one thing as a market metric and another as a price action: "
        f"{clashing}"
    )

"""A number the form invites must never break the rule that reads it.

The Builder draws a number box from the capability's declared range. The code that reads
the value has a range of its own. When the two are not the same range, the trader types
something the form accepted, and the failure arrives much later — at scan time, as a
condition in the **error** state.

That is not a theoretical worry. ``technical_patterns`` held the real ranges inline —
``lookback`` 12 to 500, ``pivot_bars`` 1 to 8, and eight more — and the registry declared
none of them. So the form showed an open number box for all ten fields on all ten chart
pattern cards, and a ``lookback`` of 5, or a ``pivot_bars`` of 20, produced a bare
``ValueError`` inside the reader. What reached the trader was a condition with no error
code, no explanation and no way to tell what had gone wrong.

So this pushes every card to the very edges of what its form allows and checks the engine
still answers. Warming up is a fine answer — a 500-candle window on a young coin has to
wait. Erroring is not.

Every card goes into one strategy, so the whole catalogue is covered in a handful of
scans rather than one scan per card.
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
#: A draft holds at most 100 conditions, so the catalogue goes through in batches.
BATCH = 90

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


def _candles(count: int = 600, seed: int = 5) -> list[Candle]:
    generator = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0
    rows: list[Candle] = []
    for index in range(count):
        rate = (math.sin(index / 9) * 1.5 + generator.uniform(-0.8, 0.8) + 0.4) / 100
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
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return [SYMBOL, "BTC/USDT", "ETH/USDT"]

    async def fetch_ohlcv(
        self, exchange: str, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]:
        seed = {"BTC/USDT": 11, "ETH/USDT": 13}.get(symbol, 5)
        return _candles(max(limit, 600), seed)[-limit:]

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


OFFERED = [mechanic for mechanic in mechanic_catalog() if mechanic.available]

#: Only fields whose range the form actually states. A field with no declared range is
#: a separate problem and belongs to a different check.
BOUNDED = [
    mechanic
    for mechanic in OFFERED
    if any(
        parameter.kind in {"integer", "number"}
        and not parameter.choices
        and (parameter.minimum is not None or parameter.maximum is not None)
        for parameter in mechanic.parameters
    )
]


def _at_edge(mechanic: Any, edge: str) -> dict[str, Any]:
    values = _probe_values(mechanic)
    for parameter in mechanic.parameters:
        if parameter.kind not in {"integer", "number"} or parameter.choices:
            continue
        bound = parameter.minimum if edge == "minimum" else parameter.maximum
        if bound is None:
            continue
        values[parameter.name] = int(bound) if parameter.kind == "integer" else float(bound)
    return values


async def _states_at(edge: str) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for start in range(0, len(BOUNDED), BATCH):
        batch = BOUNDED[start : start + BATCH]
        children: list[ConditionNodeV2] = []
        by_node: dict[str, str] = {}
        for index, mechanic in enumerate(batch):
            try:
                node, _ = _build(
                    mechanic,
                    _at_edge(mechanic, edge),
                    source_turn_id="range-audit",
                    node_id=f"n{index}",
                    required=False,
                )
            except Exception as error:  # noqa: BLE001
                # Refusing at build time is the correct, visible outcome; it is the
                # silent error at scan time this file is about.
                answers[mechanic.key] = ("refused_at_build", str(error)[:120])
                continue
            children.append(node)
            by_node[node.node_id] = mechanic.key
        if not children:
            continue
        draft = StrategyDraftV2(
            mode=DraftMode.MONITOR,
            name="Range audit",
            market_scope=MarketScopeV2(),
            universe=StrategyUniverseV2(included_symbols=[SYMBOL]),
            sharia_policy=ShariaPolicyV2(
                universe_mode=ShariaUniverseMode.EXPLICIT_ASSETS, explicit_symbols=[SYMBOL]
            ),
            condition_ast=ConditionNodeV2(
                node_id="root", node_type=ConditionNodeType.OR, children=children
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
        context = await ProviderContextService(_Provider()).build(
            strategy,
            SYMBOL,
            candle_sets,
            NOW,
            base_context={
                "last_triggered_at": NOW - timedelta(hours=3),
                "last_symbol_triggered_at": NOW - timedelta(hours=3),
                "last_strategy_triggered_at": NOW - timedelta(hours=3),
                "setup_first_detected_at": NOW - timedelta(hours=2),
                "condition_first_true_at_by_key": {
                    node_id: NOW - timedelta(hours=1) for node_id in by_node
                },
                "alerts_last_hour": 0,
                "alerts_last_day": 0,
            },
        )
        result = StrategyRuleEngine().evaluate(
            strategy,
            snapshot,
            candle_sets,
            evaluation_time=NOW,
            strategy_version="1",
            condition_context=context,
        )
        for leaf in result.conditions:
            key = by_node.get(
                getattr(leaf, "condition_id", "") or getattr(leaf, "node_id", "")
            )
            if key is not None:
                answers[key] = (leaf.state, leaf.error_code, (leaf.explanation or "")[:160])
    return answers


#: Two scans' worth of work, shared by every case below.
_EDGES = {edge: asyncio.run(_states_at(edge)) for edge in ("minimum", "maximum")}


def test_there_are_cards_with_a_stated_range() -> None:
    """Guards the cases below against passing because nothing was checked."""

    assert len(BOUNDED) > 100, len(BOUNDED)
    assert len(_EDGES["minimum"]) > 100


@pytest.mark.parametrize("key", sorted({mechanic.key for mechanic in BOUNDED}))
@pytest.mark.parametrize("edge", ("minimum", "maximum"))
def test_the_edge_of_the_form_still_answers(edge: str, key: str) -> None:
    """The smallest and largest values the form allows must not break the reading."""

    answer = _EDGES[edge].get(key)
    if answer is None:
        pytest.skip(f"{key} produced no condition at the {edge}")
    if answer[0] == "refused_at_build":
        return
    state = answer[0]
    assert state is not EvaluationState.ERROR, (
        f"{key} at its declared {edge} came back as an error the trader cannot act on: "
        f"code={answer[1]!r} — {answer[2]!r}. The form offered this value, so either the "
        "form's range or the reader's range is wrong."
    )

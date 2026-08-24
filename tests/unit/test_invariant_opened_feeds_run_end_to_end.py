"""A feed the platform serves itself must run the whole way, on the real producers.

84 cards were hidden from every surface of the product because
``capability_compatibility._availability`` returned ``provider_required`` the moment a
capability carried a provider label, and never asked whether the data could be read. It
could not ask; nothing answered the question. Meanwhile the scanner was already reading
every one of those feeds on every candle of every coin.

Opening them is one line. **Proving they run is this file.** The danger in a change like
this is a card that is offered, compiles, and then reports "unavailable" for ever —
exactly the class of defect
``tests/unit/test_invariant_every_card_evaluates.py`` was written for. That test hands
each card an invented context value, which proves the *reader* works. This one never
does: every value comes from the same producer the live scan uses, so a card passes here
only if the whole path works.

The path each case walks:

    the Builder offers it
      → the Builder builds a node from it, with only the fields its own form shows
      → the draft compiles to a strategy
      → `ProviderContextService.build` — the service the scanner holds — produces the
        value from a market-data adapter, or `_risk_context` inside the engine does, or
        `runtime_context_metric` answers it from the scanner's own context keys
      → `StrategyRuleEngine.evaluate` reads it
      → the condition proof is a real reading, not "unavailable"

Nothing here is parametrised over one example. The rule is asserted for every key in
every family that was opened, so a regression in one card fails one case by name.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ai_market_monitor.engine.builder_operations import (
    _build,
    _probe_values,
    mechanic_catalog,
)
from ai_market_monitor.engine.capabilities import all_capabilities
from ai_market_monitor.engine.capability_compatibility import compatibility_by_key
from ai_market_monitor.engine.evaluator import (
    StrategyRuleEngine,
    strategy_evaluation_directions,
)
from ai_market_monitor.engine.models import EvaluationState
from ai_market_monitor.engine.provider_families import (
    PROVIDER_FAMILY_BY_KEY,
    availability_from_settings,
)
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
from ai_market_monitor.provider_context import (
    ProviderContextService,
    requested_context_operands,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    DraftMode,
    MarketScopeV2,
    ShariaPolicyV2,
    ShariaUniverseMode,
    StrategyBias,
    StrategyDraftV2,
    StrategyUniverseV2,
)
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.market_preview import market_snapshot_from_candles
from tests.support.card_setups import BOTH_SIDES, with_bias
from tests.support.market_feeds import (
    TEST_SETTINGS,
    ScanMarketDataAdapter,
    wavy_candles,
)

SYMBOL = "SOL/USDT"
EXCHANGE = "binance"
TIMEFRAME = "15m"

#: The families this change opened, grouped by who produces the value at scan time.
#:
#: `platform` in `engine/provider_families.py` is the register; this splits that register
#: by producer, because the three producers are reached along three different paths and a
#: test that only walked one of them would prove a third of the claim.
CONTEXT_SERVICE_FAMILIES = ("cross_market", "market_breadth", "universe_ranking", "order_book")
ENGINE_RISK_FAMILY = "risk_context"
SCANNER_RUNTIME_FAMILIES = ("alert_behavior", "setup_lifecycle")

OPENED_FAMILIES = (
    *CONTEXT_SERVICE_FAMILIES,
    ENGINE_RISK_FAMILY,
    *SCANNER_RUNTIME_FAMILIES,
)


def _keys_for(families: tuple[str, ...]) -> list[str]:
    return sorted(
        capability.key
        for capability in all_capabilities()
        if capability.provider_required in families
    )


CONTEXT_SERVICE_KEYS = _keys_for(CONTEXT_SERVICE_FAMILIES)
RISK_KEYS = _keys_for((ENGINE_RISK_FAMILY,))
RUNTIME_KEYS = _keys_for(SCANNER_RUNTIME_FAMILIES)
OPENED_KEYS = _keys_for(OPENED_FAMILIES)

#: A market that clears every universe filter, so a card is judged on the card.
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


HISTORY = wavy_candles(400)
NOW = HISTORY[-1].timestamp


def _scanner_runtime_context() -> dict[str, Any]:
    """The keys `ScannerService` puts beside a rule before the engine reads it.

    Copied in shape from `services/scanner.py`, and held to it by
    `test_scanner_states_every_runtime_context_key_this_file_relies_on` below, so this
    cannot quietly drift into a context the scanner never builds.
    """

    return {
        "last_strategy_triggered_at": NOW - timedelta(hours=3),
        "last_symbol_triggered_at": NOW - timedelta(hours=3),
        "last_triggered_at": NOW - timedelta(hours=3),
        "setup_first_detected_at": NOW - timedelta(hours=2),
        "setup_state": "forming",
        "setup_exists": True,
        "setup_expires_at": NOW + timedelta(hours=6),
        "setup_entry_zone_active": True,
        "setup_state_changed": True,
        "alerts_last_hour": 0,
        "alerts_last_day": 0,
        "condition_first_true_at_by_key": {},
    }


CATALOG = {mechanic.key: mechanic for mechanic in mechanic_catalog()}


def _evaluate_like_the_scanner(
    strategy,
    snapshot,
    candle_sets: dict[str, list[Candle]],
    *,
    evaluated_at: datetime,
    condition_context: dict[str, Any],
):
    """Run the engine the way `ScannerService` runs it: once per evaluation direction.

    The direction is not decoration. A strategy compiled as ``both`` has no side, and
    the risk model refuses to guess one — ``direction_ambiguous``, which is the correct
    answer and exactly the "never invert" rule. The scanner therefore fans out to long
    and short and evaluates each. A test that skipped that step would see no risk
    calculation at all and would blame the cards for it.
    """

    results = [
        StrategyRuleEngine().evaluate(
            strategy,
            snapshot,
            candle_sets,
            evaluation_time=evaluated_at,
            strategy_version="1",
            evaluation_direction=direction,
            account_balance=10_000.0,
            condition_context=condition_context,
        )
        for direction in strategy_evaluation_directions(strategy)
    ]
    # The best-read result, so one direction being unreadable does not hide the other
    # having read the card perfectly well.
    readable = [
        result
        for result in results
        if result.conditions
        and result.conditions[0].state
        not in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}
    ]
    return readable[0] if readable else results[0]


#: The sides a trade-quality card is tried on.
#:
#: A card like "how far is the next support" is produced **only** for a sell setup, and
#: its sibling "how far is the next resistance" only for a buy one — that is the reading,
#: not a gap. So a risk card passes when it is readable in the setup it belongs to, and
#: the test discovers which that is rather than carrying a hand-written map that would go
#: stale the moment another side-specific card is registered.
TRADE_SIDES = BOTH_SIDES


def _strategy_for(key: str, bias: StrategyBias = StrategyBias.NEUTRAL):
    """Build the card the way the Builder does, then compile it like the product does.

    A trade-quality card is marked as belonging to a buy or a sell setup, because that is
    what it is: "my stop is no wider than two ATR" is a sentence about a trade, and a
    trade has a side. The compiler reads that mark and gives the strategy a direction,
    and only then can the risk model work — a monitor with no side is refused a stop
    distance on purpose, and inventing a side for it would be exactly the mistake the
    "never invert" rule exists to stop.
    """

    mechanic = CATALOG[f"capability:{key}"]
    node, _ = _build(
        mechanic,
        _probe_values(mechanic),
        source_turn_id="opened-feed-audit",
        node_id="card_1",
        required=True,
    )
    node = with_bias(node, bias)
    draft = StrategyDraftV2(
        mode=DraftMode.MONITOR,
        name="Opened feed audit",
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
    return strategy


# ── The register itself ──────────────────────────────────────────────────────────


def test_every_opened_family_is_served_by_the_platform() -> None:
    """Guards the split above: a family listed here must really be platform-served."""

    for family in OPENED_FAMILIES:
        assert PROVIDER_FAMILY_BY_KEY[family].served_by == "platform", (
            f"{family} is grouped with the feeds this product serves itself, but "
            "provider_families says otherwise"
        )


def test_the_opened_set_is_the_size_it_should_be() -> None:
    """Guards every case below: an empty set would make all of them vacuous."""

    assert len(OPENED_KEYS) == 84, f"expected 84 opened cards, found {len(OPENED_KEYS)}"
    assert len(CONTEXT_SERVICE_KEYS) == 49
    assert len(RISK_KEYS) == 23
    assert len(RUNTIME_KEYS) == 12


@pytest.mark.parametrize("key", OPENED_KEYS)
def test_opened_card_is_available_and_offered(key: str) -> None:
    """Step one: the registry publishes it and the Builder will draw a form for it."""

    row = compatibility_by_key()[key]
    assert row.availability == "available", (
        f"{key} is still withheld as {row.availability}: {row.notes}"
    )
    mechanic = CATALOG.get(f"capability:{key}")
    assert mechanic is not None, f"{key} is not in the Builder catalogue at all"
    assert mechanic.available, f"{key} is drawn but not offerable: {mechanic.unavailable_reason}"


# ── The producers, on their own ──────────────────────────────────────────────────


@pytest.mark.parametrize("key", CONTEXT_SERVICE_KEYS)
async def test_provider_context_service_produces_the_value(key: str) -> None:
    """`ProviderContextService` — the object the scanner holds — answers this card.

    The adapter below returns candles, metadata and a raw order book and nothing else, so
    a value under the card's own name can only have been computed by the service.
    """

    strategy = _strategy_for(key)
    requested = requested_context_operands(strategy)
    family = PROVIDER_FAMILY_BY_KEY[
        next(c.provider_required for c in all_capabilities() if c.key == key) or ""
    ].key
    assert key in requested.get(family, {}), (
        f"{key} compiled without asking for {family}; the scanner would never fetch it"
    )

    adapter = ScanMarketDataAdapter(symbol=SYMBOL)
    candles = await adapter.fetch_ohlcv(EXCHANGE, SYMBOL, TIMEFRAME, 320)
    context = await ProviderContextService(adapter, TEST_SETTINGS).build(
        strategy,
        SYMBOL,
        {TIMEFRAME: candles},
        NOW,
    )
    produced = context.get(family, {})
    assert key in produced, (
        f"{family} answered {sorted(k for k in produced if not k.startswith('_'))} "
        f"but not {key}"
    )
    assert produced[key] is not None


@pytest.mark.parametrize("key", RISK_KEYS)
def test_the_engine_produces_the_risk_number(key: str) -> None:
    """`_risk_context` runs inside `evaluate`, before the condition tree is read.

    Nothing is placed under ``risk_context`` by this test, so a card that resolves here
    resolved against a number the engine itself worked out.
    """

    failures: list[str] = []
    for bias in TRADE_SIDES:
        strategy = _strategy_for(key, bias)
        assert strategy.risk.enabled, (
            f"{key} reads a risk number, but the compiler left the risk model switched "
            "off; the engine would never produce it"
        )
        candle_sets = {
            timeframe: HISTORY
            for timeframe in {strategy.base_timeframe, *strategy.supporting_timeframes}
        }
        snapshot = market_snapshot_from_candles(strategy, SYMBOL, candle_sets, NOW, METADATA)
        result = _evaluate_like_the_scanner(
            strategy,
            snapshot,
            candle_sets,
            evaluated_at=NOW,
            condition_context=_scanner_runtime_context(),
        )
        if not result.conditions:
            failures.append(f"{bias.value}: skipped for {result.market_filters.reasons}")
            continue
        leaf = result.conditions[0]
        if leaf.state not in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}:
            return
        failures.append(f"{bias.value}: {leaf.state.value} — {leaf.error_code}")
    raise AssertionError(f"{key} could not be read on either side of a trade: {failures}")


@pytest.mark.parametrize("key", RUNTIME_KEYS)
def test_the_scanner_context_answers_the_runtime_card(key: str) -> None:
    """Alert budgets and setup age are answered from the scanner's own keys.

    The context handed in is the scanner's, key for key — never a value named after the
    card, which is the shortcut that would make this test prove nothing.
    """

    strategy = _strategy_for(key)
    strategy.risk.enabled = False
    candle_sets = {
        timeframe: HISTORY
        for timeframe in {strategy.base_timeframe, *strategy.supporting_timeframes}
    }
    snapshot = market_snapshot_from_candles(strategy, SYMBOL, candle_sets, NOW, METADATA)
    result = _evaluate_like_the_scanner(
        strategy,
        snapshot,
        candle_sets,
        evaluated_at=NOW,
        condition_context=_scanner_runtime_context(),
    )
    assert result.conditions
    leaf = result.conditions[0]
    assert leaf.state not in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}, (
        f"{key} could not be read: {leaf.state.value} — {leaf.error_code} — {leaf.explanation}"
    )


# ── The whole path, for every opened card ────────────────────────────────────────


@pytest.mark.parametrize("key", OPENED_KEYS)
async def test_card_runs_from_the_builder_to_a_proof(key: str) -> None:
    """The claim in full: offered → built → compiled → fetched → read → proved.

    Every value the engine reads here was produced by the same code the live scan runs.
    """

    sides = TRADE_SIDES if key in set(RISK_KEYS) else (StrategyBias.NEUTRAL,)
    failures: list[str] = []
    for bias in sides:
        strategy = _strategy_for(key, bias)
        adapter = ScanMarketDataAdapter(symbol=SYMBOL)
        candles = await adapter.fetch_ohlcv(EXCHANGE, SYMBOL, TIMEFRAME, 320)
        candle_sets = {
            timeframe: candles
            for timeframe in {strategy.base_timeframe, *strategy.supporting_timeframes}
        }
        evaluated_at = candles[-1].timestamp

        context = await ProviderContextService(adapter, TEST_SETTINGS).build(
            strategy,
            SYMBOL,
            candle_sets,
            evaluated_at,
            base_context=_scanner_runtime_context(),
        )
        snapshot = market_snapshot_from_candles(
            strategy, SYMBOL, candle_sets, evaluated_at, METADATA
        )
        result = _evaluate_like_the_scanner(
            strategy,
            snapshot,
            candle_sets,
            evaluated_at=evaluated_at,
            condition_context=context,
        )

        if not result.conditions:
            failures.append(f"{bias.value}: skipped for {result.market_filters.reasons}")
            continue
        leaf = result.conditions[0]
        if leaf.state in {EvaluationState.ERROR, EvaluationState.UNAVAILABLE}:
            failures.append(f"{bias.value}: {leaf.state.value} — {leaf.error_code}")
            continue
        assert leaf.actual_value is not None, (
            f"{key} was read but proved nothing; an alert on it would carry no evidence"
        )
        return
    raise AssertionError(f"{key} cannot be run end to end: {failures}")


# ── The switch has to work in both directions ────────────────────────────────────


def test_switching_a_feed_off_withdraws_its_cards() -> None:
    """Availability is a question about the deployment, so the answer must be able to
    be no. A gate that can only open is not a gate."""

    class _OrderBookOff:
        binance_order_book_enabled = False

    off = compatibility_by_key(availability_from_settings(_OrderBookOff()))
    order_book_keys = _keys_for(("order_book",))
    assert order_book_keys
    for key in order_book_keys:
        assert off[key].availability == "provider_required", (
            f"{key} stayed available with the order book switched off"
        )
    # And the families the platform serves outright are unaffected by that switch.
    for key in RISK_KEYS:
        assert off[key].availability == "available"


def test_scanner_states_every_runtime_context_key_this_file_relies_on() -> None:
    """`_scanner_runtime_context` above must stay a copy of the scanner's own context.

    If the scanner stops writing one of these keys, the runtime cards go back to
    "unavailable" in production while this file keeps passing. Reading the source is
    blunt, and it is the only check that fails at the moment the two drift apart.
    """

    from pathlib import Path

    source = Path("src/ai_market_monitor/services/scanner.py").read_text(encoding="utf-8")
    for name in _scanner_runtime_context():
        assert f'"{name}"' in source, (
            f"the scanner no longer states {name}; the cards reading it are unreadable "
            "on a live monitor even though this file still passes"
        )

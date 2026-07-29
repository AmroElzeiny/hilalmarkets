"""A value the trader stated must survive, and one they never stated must not appear.

Four defects shared this shape, each in a different reader:

* `price moved up 2% over the last 3 candles` compiled a **1**-candle rule. The window
  was read on one compile branch out of six; the rest took a dataclass default.
* `price moved down 2%` produced a **long** strategy. `_direction` was a seventh
  movement vocabulary that knew `bearish` but not `down`.
* `alert me on a dump this week` compiled **up 5%**. The capability catalogue's example
  values were shipped as though the trader had chosen them.
* `monitor head & shoulders on halal coins` lost the pattern. One policy branch claimed
  the whole fragment because a word in it looked like labelling policy.

The cases below assert the rule across the whole family — every window unit, every
movement word, every capability carrying an example size — so a fix that only helps the
reported sentence fails.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.engine.formula_compiler import parse_percentage_formula
from ai_market_monitor.engine.grounded_patch import (
    TRADER_CHOSEN_QUANTITIES,
    ungrounded_quantities,
)
from ai_market_monitor.engine.lookback import read_lookback
from ai_market_monitor.engine.turn_fragments import classify_fragment
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    StrategyDirection,
)
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter

TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
COUNTS = (2, 3, 5, 7, 12, 20, 50)
#: Every spelling of "a bar" the shared reader claims to understand.
BAR_WORDS = ("candle", "candles", "bar", "bars", "candlestick", "candlesticks")
#: Every phrase that introduces a backward window.
WINDOW_MARKERS = (
    "over the last",
    "over the past",
    "in the last",
    "during the last",
    "the previous",
)


def leaves(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionGroup):
        return [rule for child in node.children for rule in leaves(child)]
    return [node]


async def compile_prompt(text: str, timeframe: str = "15m"):
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe=timeframe,
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    return await RuleBasedStrategyInterpreter().interpret(request)


# --------------------------------------------------------------------------------
# D1  the stated window
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("count", COUNTS)
@pytest.mark.parametrize("word", BAR_WORDS)
def test_a_bar_count_is_read_for_every_spelling(count: int, word: str) -> None:
    reading = read_lookback(f"price rose 2% over the last {count} {word}", timeframe="15m")
    assert reading is not None
    assert reading.candles == count


@pytest.mark.parametrize("marker", WINDOW_MARKERS)
def test_a_bar_count_is_read_after_every_window_marker(marker: str) -> None:
    reading = read_lookback(f"price rose 2% {marker} 9 candles", timeframe="15m")
    assert reading is not None
    assert reading.candles == 9


@pytest.mark.parametrize(
    ("phrase", "timeframe", "candles"),
    [
        ("over the past hour", "1m", 60),
        ("over the past 2 hours", "1m", 120),
        ("over the last 3 days", "1h", 72),
        ("over the past week", "1h", 168),
        ("today", "15m", 96),
    ],
)
def test_a_wall_clock_window_converts_with_the_timeframe(
    phrase: str, timeframe: str, candles: int
) -> None:
    reading = read_lookback(f"price rose 2% {phrase}", timeframe=timeframe)
    assert reading is not None
    assert reading.candles == candles


@pytest.mark.parametrize("timeframe", TIMEFRAMES)
def test_a_timeframe_is_not_a_window(timeframe: str) -> None:
    """`on the 1h` says where to look, not how far back. Reading it as a window made
    every rule that named its timeframe search a wall-clock period instead."""
    assert read_lookback(f"RSI below 30 on the {timeframe}", timeframe=timeframe) is None


@pytest.mark.parametrize("count", COUNTS)
@pytest.mark.parametrize("timeframe", TIMEFRAMES)
def test_every_formula_branch_keeps_the_stated_window(count: int, timeframe: str) -> None:
    """The window used to be read on one branch out of six."""
    spec = parse_percentage_formula(
        f"price moved up 2% over the last {count} candles",
        default_timeframe=timeframe,
        default_direction=StrategyDirection.BOTH,
    )
    assert spec is not None
    assert spec.lookback == count


@pytest.mark.parametrize("count", (3, 10, 25))
async def test_the_compiled_rule_carries_the_stated_window(count: int) -> None:
    preview = await compile_prompt(f"price moved up 2% over the last {count} candles on 5m", "5m")
    windows = {
        rule.left.parameters.get("lookback")
        for rule in leaves(preview.strategy.conditions)
        if rule.left.parameters and "lookback" in rule.left.parameters
    }
    assert windows == {count}, windows


# --------------------------------------------------------------------------------
# D2  the stated side
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word",
    ["down", "drops", "dropped", "falls", "fell", "decreasing", "dumped", "sell-off", "declines"],
)
async def test_downward_wording_never_compiles_a_long(word: str) -> None:
    preview = await compile_prompt(f"alert me when price {word} at least 3% on the 1h", "1h")
    assert preview.strategy.direction is StrategyDirection.SHORT


@pytest.mark.parametrize(
    "word",
    ["up", "rises", "rose", "gains", "gained", "increasing", "pumped", "rally", "climbs"],
)
async def test_upward_wording_compiles_a_long(word: str) -> None:
    preview = await compile_prompt(f"alert me when price {word} at least 3% on the 1h", "1h")
    assert preview.strategy.direction is StrategyDirection.LONG


@pytest.mark.parametrize(("side", "expected"), [("long", "long"), ("short", "short")])
async def test_an_explicitly_stated_side_outranks_movement_wording(
    side: str, expected: str
) -> None:
    """`direction=short` names the side; a move word only describes a move."""
    preview = await compile_prompt(
        f"direction={side}: alert when price rises at least 3% on the 1h", "1h"
    )
    assert preview.strategy.direction.value == expected


@pytest.mark.parametrize(
    ("prompt", "side"),
    [
        ("coins decreasing by 3% near midnight", "down"),
        ("coins increasing by 3% near midnight", "up"),
        ("find symbols dumping 4% today", "down"),
        ("find symbols pumping 4% today", "up"),
    ],
)
async def test_an_operand_name_never_contradicts_its_own_direction(
    prompt: str, side: str
) -> None:
    """Some operands spell the side into their name (`percent_change_up`). Setting only
    the parameter left the name saying the opposite, and every reader that trusts the
    name — evaluator dispatch, coverage audit, the label a beginner reads — would still
    have shown a rise for a fall."""
    preview = await compile_prompt(prompt)
    for rule in leaves(preview.strategy.conditions):
        name = rule.left.name or ""
        stated = (rule.left.parameters or {}).get("direction")
        if not name.endswith(("_up", "_down")) or stated not in {"up", "down"}:
            continue
        assert name.endswith(f"_{stated}"), (name, stated)
        assert stated == side, (name, stated, side)


# --------------------------------------------------------------------------------
# D3  no invented size
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "alert me on a dump this week",
        "find any coin that pumped today",
        "show me coins increasing by a lot",
        "watch for a big drop",
    ],
)
async def test_a_move_size_is_never_invented(text: str) -> None:
    """The trader named a move but no size. A catalogue example is not their answer."""
    preview = await compile_prompt(text)
    for rule in leaves(preview.strategy.conditions):
        assert not ungrounded_quantities(dict(rule.left.parameters or {}), text), (
            rule.key,
            rule.left.parameters,
        )


@pytest.mark.parametrize("size", ["3", "7.5", "0.5", "12"])
async def test_a_stated_move_size_is_kept(size: str) -> None:
    """The refusal must not silence a trader who did give the number."""
    preview = await compile_prompt(f"alert me when price drops at least {size}% on the 1h", "1h")
    thresholds = {
        value
        for rule in leaves(preview.strategy.conditions)
        for key, value in (rule.left.parameters or {}).items()
        if key in TRADER_CHOSEN_QUANTITIES
    }
    right_values = {
        rule.right.value for rule in leaves(preview.strategy.conditions) if rule.right is not None
    }
    assert float(size) in thresholds | right_values, (thresholds, right_values)


@pytest.mark.parametrize(
    "text",
    [
        "set up a scanner",
        "sets up the watchlist",
        "setting up alerts",
        "back up the config",
        "give up on that",
        "follow up tomorrow",
        "open up the chart",
    ],
)
def test_a_phrasal_verb_particle_is_not_a_market_direction(text: str) -> None:
    """`set up a scanner` states no move. Reading the `up` inside it as one made a scan
    request compile as a percentage-move formula, so it never reached the resolver."""
    from ai_market_monitor.engine.price_movement import movement_direction

    assert movement_direction(text) is None, text


@pytest.mark.parametrize(
    ("text", "side"),
    [
        ("price is up 5%", "up"),
        ("price went down 5%", "down"),
        ("up 5% today", "up"),
        ("down 3% on the hour", "down"),
    ],
)
def test_a_real_particle_direction_still_reads(text: str, side: str) -> None:
    """The guard must not silence the ordinary use of the same word."""
    from ai_market_monitor.engine.price_movement import movement_direction

    assert movement_direction(text) == side


def test_grounding_reads_numeric_equality_not_spelling() -> None:
    assert ungrounded_quantities({"threshold_percent": 7.5}, "a move of 7.50%") == ()
    assert ungrounded_quantities({"threshold_percent": 5}, "a move of 3%") == ("threshold_percent",)


def test_definitional_parameters_are_not_treated_as_trader_choices() -> None:
    """An RSI period of 14 is what RSI *is*; refusing it would silence named mechanics."""
    assert ungrounded_quantities({"period": 14, "multiple": 2}, "RSI below 30") == ()


@pytest.mark.parametrize("size", ["4", "8", "12.5"])
async def test_a_stated_size_replaces_the_catalogue_example(size: str) -> None:
    """Refusing outright would punish the trader for the catalogue's example: they
    gave a number, it just was not the one the registry ships."""
    text = f"find coins that pumped {size}% today"
    preview = await compile_prompt(text)
    sizes = {
        value
        for rule in leaves(preview.strategy.conditions)
        for key, value in (rule.left.parameters or {}).items()
        if key in TRADER_CHOSEN_QUANTITIES
    }
    assert sizes <= {float(size)}, sizes
    for rule in leaves(preview.strategy.conditions):
        assert not ungrounded_quantities(dict(rule.left.parameters or {}), text)


# --------------------------------------------------------------------------------
# One mechanic, one rule — the cost of two operands for the same thing
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "size"),
    [
        ("alert me on a bearish move of at most 2.5% on the 1h", 2.5),
        ("find coins that rose no more than 3% today", 3.0),
        ("price drops at most 4% over the last 5 candles on 15m", 4.0),
    ],
)
async def test_an_upper_bound_percent_move_compiles_instead_of_blocking(
    text: str, size: float
) -> None:
    """This was refused as unrepresentable. It never was: the boolean operand fixes its
    comparison at "at least", but the numeric one carries the comparison on the
    condition. Fail closed is for meaning that cannot be represented — not for meaning
    one of two operands happens not to cover."""
    preview = await compile_prompt(text, "1h")
    codes = {i.code for i in preview.unsupported_conditions if i.blocking}
    assert "percent_move_upper_bound_unsupported" not in codes, codes
    bounded = [
        rule
        for rule in leaves(preview.strategy.conditions)
        if rule.right is not None and rule.right.value == size
    ]
    assert bounded, [r.key for r in leaves(preview.strategy.conditions)]
    assert all(rule.comparator.value in {"lte", "lt"} for rule in bounded)


@pytest.mark.parametrize(
    "text",
    [
        "find coins that rose at least 3% today",
        "alert me when price drops at least 2% on the 1h",
        "coins up 5% over the last 10 candles on 5m",
    ],
)
async def test_a_percent_move_compiles_exactly_once(text: str) -> None:
    """Two parsers recognise a percentage move and build it with different operands, so
    the same requirement was compiled twice and joined with AND."""
    from ai_market_monitor.engine.formula_compiler import PERCENT_MOVE_OPERANDS

    preview = await compile_prompt(text)
    moves = [
        rule
        for rule in leaves(preview.strategy.conditions)
        if rule.left.name in PERCENT_MOVE_OPERANDS
    ]
    assert len(moves) == 1, [(rule.key, rule.left.name) for rule in moves]


# --------------------------------------------------------------------------------
# D4  a policy word must not swallow a mechanic
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "capability"),
    [
        ("monitor every forming head and shoulders on halal coins", "head_and_shoulders"),
        ("watch RSI below 30 on sharia compliant pairs", "rsi"),
        ("alert on a liquidity sweep for islamic assets", "sweep"),
        ("volume above 2x average on halal pairs", "volume"),
    ],
)
def test_a_universe_filter_leaves_the_mechanic_intact(text: str, capability: str) -> None:
    """`halal coins` says which coins to watch. It is not a request to assign a status,
    and treating it as one discarded the rule stated in the same sentence."""
    keys = " ".join(CapabilityResolver().resolve_prompt(text).candidate_keys)
    assert capability in keys.casefold(), keys


@pytest.mark.parametrize(
    "text",
    [
        "do not attach any religious status",
        "confirm you will not assign any ethical status",
        "no extra tags/labels/statuses of any kind",
        "don't attach any religious status to LTCUSDT",
    ],
)
def test_labelling_policy_is_still_recognised(text: str) -> None:
    """The branch still has to catch what it was built for (INV-10)."""
    assert classify_fragment(text).enters_capability_resolution is False

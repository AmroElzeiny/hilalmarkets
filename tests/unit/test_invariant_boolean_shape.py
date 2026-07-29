"""INV-08: an OR or a bracket must not be silently turned into an AND.

`nested_boolean_logic-001` and `precedence_grouping-001` both failed `grouping_accuracy`
(0.05 and 0.0 against a 0.98 gate). The compiler joins every condition with AND, so
`(A or B) and C` compiled as `A and B and C` — a rule that fires on different markets,
in an artifact that passes schema validation and looks complete.

The parser recovers the shape the trader wrote, and the compiler now *rebuilds* it:
each branch is compiled from its own wording and reassembled into the same
`or`/`and`/`not` tree. `or` was always a first-class operator in the schema and in the
evaluator — only the compiler never emitted one.

A branch that compiles nothing still blocks. Dropping it would leave an OR with fewer
alternatives than the trader asked for, which is the same silent substitution by
another route.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.db.models.enums import LogicalOperator
from ai_market_monitor.engine.boolean_expression import (
    has_explicit_structure,
    parse_boolean_expression,
)
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.strategy import ConditionGroup, ConditionRule
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


@pytest.mark.parametrize(
    ("text", "shape"),
    [
        ("A or B", "or(leaf,leaf)"),
        ("A and B or C", "or(and(leaf,leaf),leaf)"),
        ("(A or B) and C", "and(or(leaf,leaf),leaf)"),
        ("A and (B or C)", "and(leaf,or(leaf,leaf))"),
        ("(A or B) and (C or D)", "and(or(leaf,leaf),or(leaf,leaf))"),
        ("A or B or C", "or(leaf,leaf,leaf)"),
    ],
)
def test_the_written_shape_is_recovered(text: str, shape: str) -> None:
    node = parse_boolean_expression(text)
    assert node is not None, text
    assert node.shape() == shape


def test_brackets_beat_default_precedence() -> None:
    """Without brackets, AND binds tighter than OR. With them, the trader wins."""
    assert parse_boolean_expression("A and B or C").shape() == "or(and(leaf,leaf),leaf)"
    assert parse_boolean_expression("A and (B or C)").shape() == "and(leaf,or(leaf,leaf))"


@pytest.mark.parametrize(
    "text",
    [
        "watch BTCUSDT on the 15m when RSI is below 30",
        "RSI below 30 and volume above 2x average",
        "",
        # The `or` inside a comparison phrase is not a choice between two rules.
        "find any symbol that grew 5% or more today",
        "coins that fell 3% or less today",
        "RSI at or below 30",
        "price at or above 1000",
        "volume 2x or higher",
    ],
)
def test_plain_wording_reports_no_structure(text: str) -> None:
    """A sentence with no OR and no brackets loses nothing by flattening, so it is
    deliberately not reported — only real structure is worth defending."""
    assert has_explicit_structure(text) is False
    assert parse_boolean_expression(text) is None


async def _interpret(text: str):
    request = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=text,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )
    return await RuleBasedStrategyInterpreter().interpret(request)


async def _issue_codes(text: str) -> set[str]:
    preview = await _interpret(text)
    return {issue.code for issue in preview.unsupported_conditions if issue.blocking}


def _compiled_shape(node: ConditionRule | ConditionGroup) -> str:
    """The compiled tree's structure, in the same notation as `BooleanNode.shape`."""
    if isinstance(node, ConditionRule):
        return "leaf"
    inner = ",".join(_compiled_shape(child) for child in node.children)
    return f"{node.operator.value}({inner})"


def _find_or(node: ConditionRule | ConditionGroup) -> ConditionGroup | None:
    if isinstance(node, ConditionRule):
        return None
    if node.operator is LogicalOperator.OR:
        return node
    for child in node.children:
        found = _find_or(child)
        if found is not None:
            return found
    return None


def _rules(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    return [rule for child in node.children for rule in _rules(child)]


@pytest.mark.parametrize(
    "text",
    [
        "watch BTCUSDT on the 15m when price rises at least 2% or price falls at least 3% today",
        "on the 15m alert when RSI is below 30 or RSI is above 70",
        "on the 4h when price rises at least 2% or a bullish engulfing appears",
    ],
)
async def test_a_requested_or_is_built_as_an_or(text: str) -> None:
    """The trader asked for either-or. They get either-or, not both-at-once."""
    preview = await _interpret(text)
    group = _find_or(preview.strategy.conditions)
    assert group is not None, _compiled_shape(preview.strategy.conditions)
    assert len(group.children) == 2
    assert "boolean_grouping_not_preserved" not in {
        issue.code for issue in preview.unsupported_conditions if issue.blocking
    }


async def test_brackets_survive_into_the_compiled_tree() -> None:
    """`(A or B) and C` must not compile as `A and B and C`."""
    preview = await _interpret(
        "(RSI below 30 or volume above 2x average) and price above the 50 EMA"
    )
    group = _find_or(preview.strategy.conditions)
    assert group is not None, _compiled_shape(preview.strategy.conditions)
    # The OR holds exactly the bracketed pair; the third rule sits outside it.
    or_keys = {rule.key for rule in _rules(group)}
    all_keys = {rule.key for rule in _rules(preview.strategy.conditions)}
    assert len(or_keys) == 2, or_keys
    assert all_keys - or_keys, "the unbracketed condition must stay outside the OR"


async def test_a_branch_that_compiles_nothing_still_blocks() -> None:
    """Fail closed: an OR missing one alternative is a different rule."""
    codes = await _issue_codes("alert when RSI below 30 or the vibes are good")
    assert "boolean_grouping_not_preserved" in codes, codes


async def test_the_unbuildable_branch_is_named_in_the_message() -> None:
    """A blocking finding the trader cannot locate is one they cannot clear."""
    preview = await _interpret("alert when RSI below 30 or the vibes are good")
    messages = [
        issue.message
        for issue in preview.unsupported_conditions
        if issue.code == "boolean_grouping_not_preserved"
    ]
    assert messages, preview.unsupported_conditions
    assert "the vibes are good" in messages[0]


async def test_each_rule_in_the_tree_keeps_a_unique_key() -> None:
    """Two branches can compile the same mechanic; duplicate keys would collide."""
    preview = await _interpret(
        "on the 15m when price rises at least 2% or price rises at least 2%"
    )
    keys = [rule.key for rule in _rules(preview.strategy.conditions)]
    assert len(keys) == len(set(keys)), keys


async def test_a_plain_and_setup_is_not_reported() -> None:
    codes = await _issue_codes("watch BTCUSDT on the 15m when price rises at least 2% today")
    assert "boolean_grouping_not_preserved" not in codes, codes


async def test_a_plain_and_setup_builds_no_or_group() -> None:
    """Flattening is right when nothing was grouped; an invented OR fires too often."""
    preview = await _interpret("on the 15m when RSI is below 30 and volume is above 2x average")
    assert _find_or(preview.strategy.conditions) is None


async def test_a_comparison_phrase_does_not_become_an_or() -> None:
    """`5% or more` states one threshold, not a choice between two rules."""
    preview = await _interpret("find any symbol that grew 5% or more today")
    assert _find_or(preview.strategy.conditions) is None

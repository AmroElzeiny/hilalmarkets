"""INV-BOOL: the way a trader joins rules is the way the monitor joins them.

Evaluator runs 20260802T232050Z (nested AND/OR) and 20260803T000036Z (precedence)
measured 10% and 20% strict pass on this. The cause was not a hard sentence: an
explicit ``A AND (B OR C)`` came back from the planner as two or three *unrelated*
rules, and the draft joined whatever existed with the registry's implicit AND. The
artifact validated, every rule was individually correct, and the monitor watched a
different market. Nothing in the pipeline compared what was written with what was built.

These tests assert the rule, not the reported sentence:

* every expression shape, not only ``A AND (B OR C)``
* every connective the shared vocabulary knows, not only the word "and"
* scope and role sentences are never Boolean logic, whatever words they contain
* a single rule stays a single rule and is never wrapped in an invented group

They run the **real** compiler on a **real** ``PlannerIntentEnvelope``. A test that
starts from a prebuilt ``SetupAgentTurnPlan`` proves nothing about this path.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_market_monitor.engine.boolean_topology import (
    BooleanTopologyError,
    compare_topology,
    executable_span,
    parse_stated_topology,
    validate_boolean_topology,
)
from ai_market_monitor.engine.planner_intent_compiler import (
    IntentCompileError,
    compile_planner_intents,
)
from ai_market_monitor.schemas.planner_intent import (
    BooleanStrategyIntent,
    PlannerIntentEnvelope,
)
from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeType, StrategyDraftV2

TURN_ID = "turn-boolean-0001"


def _leaf(reference: str, segment_ref: str, quote: str, timeframe: str, threshold: float) -> dict:
    return {
        "leaf_ref": reference,
        "segment_ref": segment_ref,
        "condition": {
            "source_quote": quote,
            "formula_key": "close_to_close_percentage",
            "movement_direction": "up",
            "comparator": "gte",
            "threshold": threshold,
            "unit": "percent",
            "trigger_timeframe": timeframe,
        },
    }


def _envelope(message: str, structure: dict[str, Any]) -> PlannerIntentEnvelope:
    return PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": message,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                }
            ],
            "semantic_intents": [
                {
                    "segment_ref": "s1",
                    "payload": {
                        "action": "replace_boolean_structure",
                        "boolean_structure": structure,
                    },
                }
            ],
            "overall_confidence": 0.99,
        }
    )


def _compile(envelope: PlannerIntentEnvelope, message: str):
    return compile_planner_intents(
        envelope,
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id=TURN_ID,
    )


def _shape(node) -> str:
    if node.node_type == ConditionNodeType.CONDITION:
        return "leaf"
    inner = ",".join(sorted(_shape(child) for child in node.children))
    return f"{node.node_type.value}({inner})"


# ---------------------------------------------------------------------------------
# Reading the structure a trader wrote
# ---------------------------------------------------------------------------------

#: One case per shape the closure brief names, plus the ones that used to be lost.
STATED_EXPRESSIONS: tuple[tuple[str, str], ...] = (
    (
        "Alert when the 15m move is bullish at least 2% AND "
        "(the 1h move is bearish at least 1% OR the 4h move is bullish at least 5%).",
        "and(leaf,or(leaf,leaf))",
    ),
    (
        "Alert when (the 15m move is bullish at least 2% OR the 1h move is bearish at "
        "least 1%) AND the 4h move is bullish at least 5%.",
        "and(or(leaf,leaf),leaf)",
    ),
    (
        "Alert when NOT the 15m move is bullish at least 2% OR the 1h move is bearish "
        "at least 1%.",
        "or(not(leaf),leaf)",
    ),
    (
        "Alert when the 15m move is bullish at least 2% AND NOT "
        "(the 1h move is bearish at least 1% OR the 4h move is bullish at least 5%).",
        "and(leaf,not(or(leaf,leaf)))",
    ),
)


@pytest.mark.parametrize(("message", "expected"), STATED_EXPRESSIONS)
def test_every_stated_shape_is_read_exactly(message: str, expected: str) -> None:
    """The server reads the structure itself, before any model output is trusted."""

    topology = parse_stated_topology(message)
    assert topology is not None, message
    assert topology.root.shape() == expected


def test_a_and_b_or_c_is_not_the_same_as_a_or_b_and_c() -> None:
    """Two shapes that fire on different markets must never read as the same shape."""

    first = parse_stated_topology(
        "Alert when the 15m move is bullish at least 2% AND "
        "(the 1h move is bearish at least 1% OR the 4h move is bullish at least 5%)."
    )
    second = parse_stated_topology(
        "Alert when (the 15m move is bullish at least 2% AND "
        "the 1h move is bearish at least 1%) OR the 4h move is bullish at least 5%."
    )
    assert first is not None and second is not None
    assert first.root.shape() != second.root.shape()


#: Sentences that contain connective words but state scope, roles or workflow. Reading
#: any of them as Boolean algebra would turn a watchlist into an expression, which is
#: the separation the closure brief requires and the compiler must never cross.
SCOPE_SENTENCES: tuple[str, ...] = (
    "I want a simple watchlist for ETHUSDT, not BTCUSDT.",
    "Build a Watchlist for ETHUSDT only and exclude SOLUSDT.",
    "Use the 1-minute chart for context and the 1-hour chart for the trigger.",
    "Use 1h as context and 15m as the trigger timeframe.",
    "Watch SOLUSDT and ADAUSDT, and keep approval explicit.",
    "Use the 4-hour chart for the bigger picture and the 1-minute chart for the entry trigger.",
)


@pytest.mark.parametrize("message", SCOPE_SENTENCES)
def test_scope_and_role_sentences_are_never_boolean_logic(message: str) -> None:
    assert parse_stated_topology(message) is None
    assert executable_span(message) is None


def test_one_rule_is_never_wrapped_in_an_invented_group() -> None:
    message = "Alert me when the 15m close-to-close move is bullish at least 2%."
    assert parse_stated_topology(message) is None


# ---------------------------------------------------------------------------------
# Compiling the flat graph into the canonical tree
# ---------------------------------------------------------------------------------


def test_flat_graph_compiles_into_the_exact_nested_tree() -> None:
    message = (
        "Alert when the 15m move is bullish at least 2% AND "
        "(the 1h move is bearish at least 1% OR the 4h move is bullish at least 5%)."
    )
    envelope = _envelope(
        message,
        {
            "condition_leaves": [
                _leaf("l1", "s1", "the 15m move is bullish at least 2%", "15m", 2.0),
                _leaf("l2", "s1", "the 1h move is bearish at least 1%", "1h", 1.0),
                _leaf("l3", "s1", "the 4h move is bullish at least 5%", "4h", 5.0),
            ],
            "boolean_groups": [
                {
                    "group_ref": "g1",
                    "operator": "or",
                    "child_refs": ["l2", "l3"],
                    "source_quote": (
                        "the 1h move is bearish at least 1% "
                        "OR the 4h move is bullish at least 5%"
                    ),
                },
                {
                    "group_ref": "g2",
                    "operator": "and",
                    "child_refs": ["l1", "g1"],
                    "source_quote": message,
                },
            ],
            "root_ref": "g2",
        },
    )
    compiled = _compile(envelope, message)
    node = compiled.plan.operations[0].condition
    assert node is not None
    assert _shape(node) == "and(leaf,or(leaf,leaf))"
    assert compiled.topology_check is not None
    assert compiled.topology_check.matches


def test_a_flattened_answer_is_refused_not_accepted() -> None:
    """The defect the runs measured: the OR disappears and nothing notices."""

    message = (
        "Alert when the 15m move is bullish at least 2% AND "
        "(the 1h move is bearish at least 1% OR the 4h move is bullish at least 5%)."
    )
    envelope = _envelope(
        message,
        {
            "condition_leaves": [
                _leaf("l1", "s1", "the 15m move is bullish at least 2%", "15m", 2.0),
                _leaf("l2", "s1", "the 1h move is bearish at least 1%", "1h", 1.0),
                _leaf("l3", "s1", "the 4h move is bullish at least 5%", "4h", 5.0),
            ],
            "boolean_groups": [
                {
                    "group_ref": "g1",
                    "operator": "and",
                    "child_refs": ["l1", "l2", "l3"],
                    "source_quote": message,
                }
            ],
            "root_ref": "g1",
        },
    )
    with pytest.raises(IntentCompileError) as captured:
        _compile(envelope, message)
    assert captured.value.code == "BOOLEAN_TOPOLOGY_MISSING"
    assert captured.value.target_paths == ("boolean_structure",)


def test_separate_rules_are_joined_the_way_the_words_join_them() -> None:
    """No model call is needed to keep a stated shape: the words already state it.

    The planner returning three correct but unrelated rules is the exact shape of the
    run 11 failure. Nothing about the rules is changed here; only the arrangement is
    read from the trader's own sentence.
    """

    message = (
        "Alert when the 15m move is bullish at least 2% AND "
        "(the 1h move is bearish at least 1% OR the 4h move is bullish at least 5%)."
    )
    envelope = PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": message,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                }
            ],
            "semantic_intents": [
                {
                    "segment_ref": "s1",
                    "payload": {
                        "action": "add_condition",
                        "condition": _leaf("x", "s1", quote, timeframe, threshold)["condition"],
                    },
                }
                for quote, timeframe, threshold in (
                    ("the 15m move is bullish at least 2%", "15m", 2.0),
                    ("the 1h move is bearish at least 1%", "1h", 1.0),
                    ("the 4h move is bullish at least 5%", "4h", 5.0),
                )
            ],
            "overall_confidence": 0.99,
        }
    )
    compiled = _compile(envelope, message)
    assert any("deterministic_assembly" in item for item in compiled.derivations)
    node = compiled.plan.operations[0].condition
    assert node is not None
    assert _shape(node) == "and(leaf,or(leaf,leaf))"


# ---------------------------------------------------------------------------------
# The flat graph must be one finite tree
# ---------------------------------------------------------------------------------

MALFORMED_GRAPHS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "a node placed inside two groups",
        {
            "condition_leaves": [
                _leaf("l1", "s1", "a", "15m", 1.0),
                _leaf("l2", "s1", "b", "1h", 2.0),
            ],
            "boolean_groups": [
                {
                    "group_ref": "g1",
                    "operator": "or",
                    "child_refs": ["l1", "l2"],
                    "source_quote": "x",
                },
                {
                    "group_ref": "g2",
                    "operator": "and",
                    "child_refs": ["g1", "l1"],
                    "source_quote": "x",
                },
            ],
            "root_ref": "g2",
        },
    ),
    (
        "a leaf connected to nothing",
        {
            "condition_leaves": [
                _leaf("l1", "s1", "a", "15m", 1.0),
                _leaf("l2", "s1", "b", "1h", 2.0),
                _leaf("l3", "s1", "c", "4h", 3.0),
            ],
            "boolean_groups": [
                {
                    "group_ref": "g1",
                    "operator": "or",
                    "child_refs": ["l1", "l2"],
                    "source_quote": "x",
                },
            ],
            "root_ref": "g1",
        },
    ),
    (
        "a root that is also somebody's child",
        {
            "condition_leaves": [
                _leaf("l1", "s1", "a", "15m", 1.0),
                _leaf("l2", "s1", "b", "1h", 2.0),
            ],
            "boolean_groups": [
                {
                    "group_ref": "g1",
                    "operator": "or",
                    "child_refs": ["l1", "l2"],
                    "source_quote": "x",
                },
                {
                    "group_ref": "g2",
                    "operator": "and",
                    "child_refs": ["g1", "g1"],
                    "source_quote": "x",
                },
            ],
            "root_ref": "g1",
        },
    ),
)


@pytest.mark.parametrize(
    ("label", "structure"),
    MALFORMED_GRAPHS,
    ids=[item[0] for item in MALFORMED_GRAPHS],
)
def test_a_graph_that_is_not_one_tree_is_refused(label: str, structure: dict[str, Any]) -> None:
    """Refusing is the point: a graph with two readings has no single meaning."""

    try:
        intent = BooleanStrategyIntent.model_validate(structure)
    except ValueError:
        return  # the shape itself already refused it, which is the same protection
    with pytest.raises(BooleanTopologyError) as captured:
        validate_boolean_topology(intent)
    assert captured.value.code == "BOOLEAN_TOPOLOGY_AMBIGUOUS"


@pytest.mark.parametrize(
    ("operator", "children"),
    (("and", ["l1"]), ("or", ["l1"]), ("not", ["l1", "l2"])),
)
def test_group_arity_is_enforced_by_the_shape(operator: str, children: list[str]) -> None:
    with pytest.raises(ValueError):
        BooleanStrategyIntent.model_validate(
            {
                "condition_leaves": [
                    _leaf("l1", "s1", "a", "15m", 1.0),
                    _leaf("l2", "s1", "b", "1h", 2.0),
                ],
                "boolean_groups": [
                    {
                        "group_ref": "g1",
                        "operator": operator,
                        "child_refs": children,
                        "source_quote": "x",
                    }
                ],
                "root_ref": "g1",
            }
        )


def test_every_leaf_must_quote_its_own_words() -> None:
    """Without a per-leaf quote, a neighbouring rule's timeframe looks like this one's."""

    with pytest.raises(ValueError):
        BooleanStrategyIntent.model_validate(
            {
                "condition_leaves": [
                    {
                        "leaf_ref": "l1",
                        "segment_ref": "s1",
                        "condition": {
                            "formula_key": "close_to_close_percentage",
                            "comparator": "gte",
                            "threshold": 1.0,
                            "unit": "percent",
                            "trigger_timeframe": "15m",
                        },
                    }
                ],
                "boolean_groups": [],
                "root_ref": "l1",
            }
        )


def test_the_comparison_never_reports_a_match_when_nothing_was_compiled() -> None:
    expected = parse_stated_topology(
        "Alert when the 15m move is bullish at least 2% AND "
        "(the 1h move is bearish at least 1% OR the 4h move is bullish at least 5%)."
    )
    assert expected is not None
    result = compare_topology(expected, None)
    assert not result.matches
    assert result.code == "BOOLEAN_TOPOLOGY_MISSING"

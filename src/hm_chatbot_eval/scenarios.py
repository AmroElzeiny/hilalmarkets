from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from .models import ScenarioSpec, TopicSpec

SYMBOLS = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"]
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
PERSONAS = [
    {
        "type": "methodical swing trader",
        "experience": "advanced",
        "style": "precise but revises details",
    },
    {"type": "busy scalper", "experience": "intermediate", "style": "short fragmented messages"},
    {"type": "ICT-influenced trader", "experience": "advanced", "style": "uses domain shorthand"},
    {
        "type": "new trader",
        "experience": "beginner",
        "style": "describes visual ideas, not formulas",
    },
    {"type": "price-action trader", "experience": "intermediate", "style": "casual and skeptical"},
]


#: Topics whose whole point is the shape of an expression. A generic
#: symbol/timeframe/threshold goal cannot measure them: it contains no expression, so a
#: "nested groups exact" criterion has nothing to be exact about and the score is
#: meaningless either way. Runs 10 and 11 both reported 0.98-threshold grouping
#: failures against goals that never asked for a group.
BOOLEAN_TOPICS = frozenset({"nested_boolean_logic", "precedence_grouping", "canvas_grouping_fidelity"})

#: Expression shapes, written the way a person writes them. Each is a template over
#: three independent leaves; the leaves themselves are ordinary percentage rules so the
#: topic measures *structure* and nothing else.
_EXPRESSION_SHAPES: tuple[tuple[str, str], ...] = (
    ("A_and_B_or_C", "{a} AND ({b} OR {c})"),
    ("A_or_B_and_C", "({a} OR {b}) AND {c}"),
    ("not_A_or_B", "NOT {a} OR {b}"),
    ("A_and_not_B_or_C", "{a} AND NOT ({b} OR {c})"),
    ("A_or_B_and_C_or_D", "({a} OR {b}) AND ({c} OR {d})"),
    ("A_and_B_and_C", "{a} AND {b} AND {c}"),
)

#: One node of an expected expression: either a leaf name, or an operator with children.
ExpressionNode = str | tuple[str, tuple["ExpressionNode", ...]]

#: The exact tree each shape states. This is the expected topology the deterministic
#: comparator checks against — not something an AI judge decides.
_EXPRESSION_TREES: dict[str, ExpressionNode] = {
    "A_and_B_or_C": ("and", ("a", ("or", ("b", "c")))),
    "A_or_B_and_C": ("and", (("or", ("a", "b")), "c")),
    "not_A_or_B": ("or", (("not", ("a",)), "b")),
    "A_and_not_B_or_C": ("and", ("a", ("not", (("or", ("b", "c")),)))),
    "A_or_B_and_C_or_D": ("and", (("or", ("a", "b")), ("or", ("c", "d")))),
    "A_and_B_and_C": ("and", ("a", "b", "c")),
}


def _leaf_clause(timeframe: str, direction_word: str, operator_word: str, threshold: float) -> str:
    return f"the {timeframe} close-to-close move is {direction_word} {operator_word} {threshold:g}%"


def _flatten_tree(
    node: ExpressionNode,
    groups: list[dict[str, Any]],
    counter: list[int],
) -> str:
    """Turn the expected tree into flat leaf/group expectations."""

    if isinstance(node, str):
        return node
    operator, children = node
    child_refs = [_flatten_tree(child, groups, counter) for child in children]
    counter[0] += 1
    reference = f"g{counter[0]}"
    groups.append({"group_ref": reference, "operator": operator, "child_refs": child_refs})
    return reference


def _boolean_expectations(rng: random.Random) -> tuple[str, dict[str, object]]:
    """One genuine multi-leaf expression, and exactly what it must compile to."""

    shape_id, template = rng.choice(_EXPRESSION_SHAPES)
    tree = _EXPRESSION_TREES[shape_id]
    needed = sorted({name for name in ("a", "b", "c", "d") if "{" + name + "}" in template})
    timeframes = rng.sample(TIMEFRAMES, k=len(needed))
    clauses: dict[str, str] = {}
    leaf_contract: dict[str, dict[str, object]] = {}
    for name, timeframe in zip(needed, timeframes, strict=True):
        direction_word, direction = rng.choice([("bullish", "up"), ("bearish", "down")])
        operator_word, operator = rng.choice([("at least", "gte"), ("at most", "lte")])
        threshold = rng.choice([0.5, 1.0, 2.5, 5.0, 7.5])
        clauses[name] = _leaf_clause(timeframe, direction_word, operator_word, threshold)
        leaf_contract[name] = {
            "trigger_timeframe": timeframe,
            "movement_direction": direction,
            "operator": operator,
            "threshold_percent": threshold,
            "formula": "close_to_close_percentage",
        }
    expression = template.format(**clauses)
    groups: list[dict[str, Any]] = []
    root_ref = _flatten_tree(tree, groups, [0])
    return expression, {
        "boolean_expression": expression,
        "boolean_shape_id": shape_id,
        "expected_condition_leaves": leaf_contract,
        "expected_boolean_groups": groups,
        "expected_root_ref": root_ref,
        "expected_operator_by_group": {item["group_ref"]: item["operator"] for item in groups},
        "expected_child_membership": {
            item["group_ref"]: list(item["child_refs"]) for item in groups
        },
        "expected_negation": any(item["operator"] == "not" for item in groups),
    }


def build_scenario(
    topic: TopicSpec, index: int, global_seed: int, max_turns: int | None = None
) -> ScenarioSpec:
    seed = int.from_bytes(f"{global_seed}:{topic.id}:{index}".encode(), "little") % (2**31 - 1)
    rng = random.Random(seed)
    symbol = rng.choice(SYMBOLS)
    alt_symbol = rng.choice([x for x in SYMBOLS if x != symbol])
    timeframe = rng.choice(TIMEFRAMES)
    context_tf = rng.choice([x for x in TIMEFRAMES if x != timeframe])
    threshold = rng.choice([0.5, 1.0, 2.5, 5.0, 7.5])
    direction_word, direction = rng.choice([("bullish", "long"), ("bearish", "short")])
    operator_word, operator = rng.choice([("at least", "gte"), ("at most", "lte")])
    expected = {
        "symbol": symbol,
        "excluded_symbol": alt_symbol,
        "timeframe": timeframe,
        "context_timeframe": context_tf,
        "threshold_percent": threshold,
        "direction": direction,
        "operator": operator,
        "requires_explicit_approval": True,
        "must_not_assign_sharia_status": True,
    }
    if topic.id in BOOLEAN_TOPICS:
        # A structure topic gets a structure goal. The universe and role fields are
        # dropped from the contract because they are not what this topic measures, and
        # scoring a grouping topic on a missing exclusion is how a real grouping defect
        # stayed hidden behind an unrelated failure.
        expression, boolean_contract = _boolean_expectations(rng)
        expected = {
            "symbol": symbol,
            "requires_explicit_approval": True,
            "must_not_assign_sharia_status": True,
            **boolean_contract,
        }
        goal = (
            f"Build a watchlist for {symbol} and alert when {expression}. "
            "Keep approval explicit. The way the rules are joined must be preserved "
            "exactly; a recap that flattens the brackets is wrong."
        )
        return ScenarioSpec(
            id=f"{topic.id}-{index:03d}-{seed}",
            topic_id=topic.id,
            seed=seed,
            persona=rng.choice(PERSONAS),
            hidden_goal=goal,
            expected_contract=expected,
            success_criteria=[asdict(c) for c in topic.criteria],
            max_turns=max_turns or topic.max_turns,
            fault=topic.fault,
        )
    if topic.id in {"confirmation_integrity", "version_immutability"}:
        corrected_threshold = rng.choice(
            [candidate for candidate in (0.5, 1.0, 2.5, 5.0, 7.5) if candidate != threshold]
        )
        expected["workflow"] = {
            "kind": "approval_rebind",
            "material_edit": {
                "field": "threshold_percent",
                "from": threshold,
                "to": corrected_threshold,
            },
            "final_expected": {"threshold_percent": corrected_threshold},
        }
    goal = (
        f"Build a watchlist for {symbol}, exclude {alt_symbol}, use {context_tf} context and {timeframe} "
        f"trigger logic, require a {direction_word} move of {operator_word} {threshold}%, "
        "and keep approval explicit."
    )
    return ScenarioSpec(
        id=f"{topic.id}-{index:03d}-{seed}",
        topic_id=topic.id,
        seed=seed,
        persona=rng.choice(PERSONAS),
        hidden_goal=goal,
        expected_contract=expected,
        success_criteria=[asdict(c) for c in topic.criteria],
        max_turns=max_turns or topic.max_turns,
        fault=topic.fault,
    )


def build_randomized_scenario_plan(
    topics: Sequence[TopicSpec],
    *,
    count_per_topic: int,
    global_seed: int,
    selection_seed: int,
    max_turns_by_topic: Mapping[str, int] | None = None,
) -> list[ScenarioSpec]:
    """Build a reproducible random sample instead of reusing the same prefix."""
    if count_per_topic < 1:
        raise ValueError("count_per_topic must be positive")

    rng = random.Random(selection_seed)
    scenarios: list[ScenarioSpec] = []
    for topic in topics:
        pool_size = max(topic.max_cases, count_per_topic)
        indexes = rng.sample(range(1, pool_size + 1), k=count_per_topic)
        for index in indexes:
            scenarios.append(
                build_scenario(
                    topic,
                    index,
                    global_seed,
                    max_turns=(max_turns_by_topic or {}).get(topic.id),
                )
            )
    rng.shuffle(scenarios)
    return scenarios

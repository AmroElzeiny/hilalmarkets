"""What the Builder offers as Boolean logic must be exactly what the compiler runs.

The Builder knew the word ``NOT`` but could only produce one root ``AND``/``OR``: every
structural edit went through ``rebuild_tree``, which takes a flat list of rule ids and a
single join. A trader who wrote ``A and (B or C)`` got ``A and B and C`` on the next
arrange — a different strategy that still compiles and still fires.

Two failures are asserted here, both across the family rather than for one example:

* offering a shape the compiler cannot execute (over-deep, over-large, wrong arity);
* flattening or reordering a shape the person built.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.builder_boolean import (
    GROUP_ARITY,
    GROUP_TYPES,
    BooleanStructureError,
    boolean_limits,
    describe_structure,
    group_conditions,
    move_condition,
    node_count,
    set_group_operator,
    tree_depth,
    ungroup,
)
from ai_market_monitor.schemas.planner_intent import BOOLEAN_MAX_DEPTH, BOOLEAN_MAX_NODES
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
)


def leaf(node_id: str) -> ConditionNodeV2:
    """One minimal but genuinely valid rule. The schema validates it on construction."""

    return ConditionNodeV2(
        node_id=node_id,
        node_type=ConditionNodeType.CONDITION,
        formula=FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
        operator=Comparator.GREATER_THAN_OR_EQUAL,
        threshold=1.0,
        trigger_timeframe="1h",
        source_turn_id="boolean-test",
        source_fragment="price rises at least one percent",
    )


def group(
    node_id: str,
    node_type: ConditionNodeType,
    *children: ConditionNodeV2,
) -> ConditionNodeV2:
    return ConditionNodeV2(node_id=node_id, node_type=node_type, children=list(children))


# ---------------------------------------------------------------------------
# The offered capability is the compiler's, not a second copy of it.
# ---------------------------------------------------------------------------


def test_the_limits_the_builder_publishes_are_the_compilers_own() -> None:
    limits = boolean_limits()

    assert limits.max_depth == BOOLEAN_MAX_DEPTH
    assert limits.max_nodes == BOOLEAN_MAX_NODES


def test_every_group_type_the_schema_accepts_is_offered() -> None:
    """A new operator added to the compiler must not be quietly missing from the Builder."""

    schema_groups = {
        item for item in ConditionNodeType if item is not ConditionNodeType.CONDITION
    }
    assert set(GROUP_TYPES) == schema_groups
    assert set(GROUP_ARITY) == schema_groups
    assert set(boolean_limits().operators) == {str(item.value) for item in schema_groups}


@pytest.mark.parametrize("node_type", list(GROUP_TYPES), ids=lambda item: str(item.value))
def test_each_group_type_declares_an_arity_the_schema_agrees_with(node_type) -> None:
    """``NOT`` takes one child; the schema enforces it. The Builder must say so too."""

    minimum, maximum = GROUP_ARITY[node_type]
    if node_type is ConditionNodeType.NOT:
        assert (minimum, maximum) == (1, 1)
        with pytest.raises(ValueError):
            group("bad_not", node_type, leaf("a"), leaf("b"))
    else:
        assert minimum == 2
        assert maximum is None


# ---------------------------------------------------------------------------
# Nested logic survives editing.
# ---------------------------------------------------------------------------


def test_grouping_two_rules_nests_them_without_touching_the_others() -> None:
    root = group("root_and", ConditionNodeType.AND, leaf("a"), leaf("b"), leaf("c"))

    result = group_conditions(root, node_ids=["b", "c"], operator="or")

    assert result.node_type is ConditionNodeType.AND
    assert [child.node_id for child in result.children] == ["a", "group_or_1"]
    inner = result.children[1]
    assert inner.node_type is ConditionNodeType.OR
    assert [child.node_id for child in inner.children] == ["b", "c"]
    # A and (B or C) is depth 3 and five nodes. Nothing was flattened.
    assert tree_depth(result) == 3
    assert node_count(result) == 5


def test_a_nested_shape_round_trips_through_every_structural_edit() -> None:
    """Group, change the operator, move a rule, ungroup — and nothing is lost."""

    root = group("root_and", ConditionNodeType.AND, leaf("a"), leaf("b"), leaf("c"), leaf("d"))
    original_leaves = {"a", "b", "c", "d"}

    def leaves(node) -> set[str]:
        return {
            item.node_id
            for item in node.walk()
            if item.node_type is ConditionNodeType.CONDITION
        }

    grouped = group_conditions(root, node_ids=["b", "c"], operator="or")
    assert leaves(grouped) == original_leaves

    switched = set_group_operator(grouped, group_id="group_or_1", operator="and")
    assert leaves(switched) == original_leaves

    inner_id = next(
        item.node_id
        for item in switched.walk()
        if item.node_type is ConditionNodeType.AND and item.node_id != "root_and"
    )
    moved = move_condition(switched, node_id="d", target_group_id=inner_id, position=0)
    assert leaves(moved) == original_leaves
    inner = next(item for item in moved.walk() if item.node_id == inner_id)
    assert [child.node_id for child in inner.children] == ["d", "b", "c"]

    flattened = ungroup(moved, group_id=inner_id)
    assert leaves(flattened) == original_leaves
    assert tree_depth(flattened) == 2


def test_a_group_is_never_silently_flattened_into_the_root() -> None:
    """The defect this module exists for: nested logic must survive an edit elsewhere."""

    root = group(
        "root_and",
        ConditionNodeType.AND,
        leaf("a"),
        group("group_or_1", ConditionNodeType.OR, leaf("b"), leaf("c")),
    )

    result = move_condition(root, node_id="a", target_group_id="group_or_1")

    inner = next(item for item in result.walk() if item.node_id == "group_or_1")
    assert inner.node_type is ConditionNodeType.OR
    assert {child.node_id for child in inner.children} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Fail closed.
# ---------------------------------------------------------------------------


def test_a_group_deeper_than_the_compiler_runs_is_refused() -> None:
    """Built one level at a time, the edit that crosses the limit is the one refused."""

    node: ConditionNodeV2 = group(
        "root_and", ConditionNodeType.AND, leaf("leaf_0"), leaf("leaf_1")
    )
    depth = 2
    index = 2
    while depth < BOOLEAN_MAX_DEPTH:
        node = group(f"g_{index}", ConditionNodeType.AND, node, leaf(f"leaf_{index}"))
        depth = tree_depth(node)
        index += 1
    assert tree_depth(node) == BOOLEAN_MAX_DEPTH

    deepest = next(
        item.node_id
        for item in node.walk()
        if item.node_type is ConditionNodeType.CONDITION
        and _depth_of(node, item.node_id) == BOOLEAN_MAX_DEPTH
    )
    sibling = next(
        child.node_id
        for child in _parent(node, deepest).children
        if child.node_id != deepest
    )

    with pytest.raises(BooleanStructureError) as raised:
        group_conditions(node, node_ids=[deepest, sibling], operator="or")
    assert raised.value.code == "BOOLEAN_TOO_DEEP"
    assert str(BOOLEAN_MAX_DEPTH) in raised.value.message


def test_an_expression_larger_than_the_compiler_runs_is_refused() -> None:
    children = [leaf(f"leaf_{index}") for index in range(BOOLEAN_MAX_NODES - 1)]
    root = group("root_and", ConditionNodeType.AND, *children)
    assert node_count(root) == BOOLEAN_MAX_NODES

    # Grouping adds one node, which is one too many.
    with pytest.raises(BooleanStructureError) as raised:
        group_conditions(root, node_ids=["leaf_0", "leaf_1"], operator="or")
    assert raised.value.code == "BOOLEAN_TOO_LARGE"


def test_none_of_these_takes_exactly_one_thing() -> None:
    """``NOT`` has fixed arity in the schema, so the Builder refuses before building."""

    root = group("root_and", ConditionNodeType.AND, leaf("a"), leaf("b"), leaf("c"))

    with pytest.raises(BooleanStructureError) as raised:
        group_conditions(root, node_ids=["a", "b"], operator="not")
    assert raised.value.code == "GROUP_TOO_LARGE"

    negated = group_conditions(root, node_ids=["a"], operator="not")
    inner = next(item for item in negated.walk() if item.node_type is ConditionNodeType.NOT)
    assert [child.node_id for child in inner.children] == ["a"]


def test_an_operator_the_compiler_does_not_have_is_refused() -> None:
    root = group("root_and", ConditionNodeType.AND, leaf("a"), leaf("b"))

    for bogus in ("xor", "nand", "condition", "", "AND "):
        with pytest.raises(BooleanStructureError) as raised:
            group_conditions(root, node_ids=["a", "b"], operator=bogus)
        assert raised.value.code == "LOGIC_NOT_OFFERED", bogus


def test_rules_in_different_branches_cannot_be_grouped_together() -> None:
    """Grouping across branches would move rules out of the logic their author chose."""

    root = group(
        "root_and",
        ConditionNodeType.AND,
        group("group_or_1", ConditionNodeType.OR, leaf("a"), leaf("b")),
        leaf("c"),
    )

    with pytest.raises(BooleanStructureError) as raised:
        group_conditions(root, node_ids=["a", "c"], operator="or")
    assert raised.value.code == "SELECTION_SPLIT"


def test_a_group_cannot_be_moved_inside_itself() -> None:
    root = group(
        "root_and",
        ConditionNodeType.AND,
        group("group_or_1", ConditionNodeType.OR, leaf("a"), leaf("b")),
        leaf("c"),
    )

    with pytest.raises(BooleanStructureError) as raised:
        move_condition(root, node_id="group_or_1", target_group_id="group_or_1")
    assert raised.value.code == "MOVE_INTO_SELF"


def test_an_unknown_node_is_refused_rather_than_ignored() -> None:
    root = group("root_and", ConditionNodeType.AND, leaf("a"), leaf("b"))

    for call in (
        lambda: group_conditions(root, node_ids=["a", "ghost"], operator="or"),
        lambda: ungroup(root, group_id="ghost"),
        lambda: set_group_operator(root, group_id="ghost", operator="or"),
        lambda: move_condition(root, node_id="ghost", target_group_id="root_and"),
    ):
        with pytest.raises(BooleanStructureError):
            call()


# ---------------------------------------------------------------------------
# Stable ids, and a view the client can draw.
# ---------------------------------------------------------------------------


def test_new_groups_never_reuse_an_id_already_in_the_tree() -> None:
    """Two groups sharing an id would make an edit land on whichever was found first."""

    root = group(
        "root_and",
        ConditionNodeType.AND,
        group("group_or_1", ConditionNodeType.OR, leaf("a"), leaf("b")),
        leaf("c"),
        leaf("d"),
    )

    result = group_conditions(root, node_ids=["c", "d"], operator="or")

    ids = [item.node_id for item in result.walk()]
    assert len(ids) == len(set(ids))
    assert "group_or_2" in ids


def test_the_structure_view_carries_the_parent_of_every_node() -> None:
    root = group(
        "root_and",
        ConditionNodeType.AND,
        leaf("a"),
        group("group_or_1", ConditionNodeType.OR, leaf("b"), leaf("c")),
    )

    rows = describe_structure(root)
    by_id = {row["node_id"]: row for row in rows}

    assert by_id["root_and"]["parent_id"] is None
    assert by_id["root_and"]["depth"] == 1
    assert by_id["group_or_1"]["parent_id"] == "root_and"
    assert by_id["b"]["parent_id"] == "group_or_1"
    assert by_id["b"]["depth"] == 3
    assert by_id["a"]["kind"] == "condition"
    assert by_id["group_or_1"]["kind"] == "group"
    assert by_id["group_or_1"]["operator"] == "or"
    assert by_id["a"]["operator"] is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parent(root: ConditionNodeV2, node_id: str) -> ConditionNodeV2:
    return next(
        item for item in root.walk() if any(c.node_id == node_id for c in item.children)
    )


def _depth_of(root: ConditionNodeV2, node_id: str, *, _depth: int = 1) -> int:
    if root.node_id == node_id:
        return _depth
    for child in root.children:
        found = _depth_of(child, node_id, _depth=_depth + 1)
        if found:
            return found
    return 0

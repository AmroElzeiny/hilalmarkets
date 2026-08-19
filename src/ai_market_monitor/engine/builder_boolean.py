"""Boolean structure for the Guided Builder, bounded by what the compiler can run.

The Builder knew the word ``NOT`` but could only produce one root ``AND``/``OR``:
``rebuild_tree`` took a flat list of rule ids and a single join, so every nested shape a
person had — or that the assistant had written for them — was flattened on the next
arrange. A trader who wrote "A and (B or C)" got "A and B and C", which is a different
strategy that still compiles and still fires.

This module owns the Builder's side of Boolean structure. It does three things and
nothing else:

* it reports the compiler's own limits rather than a second copy of them, so the Builder
  can never offer a shape the compiler would refuse;
* it edits the tree by **stable node id**, so an operation targets one exact canonical
  object rather than a position in a list that the previous edit may have moved;
* it fails closed. An edit that would exceed the depth or node budget, orphan a rule or
  leave a group the compiler cannot execute is refused with a reason, never repaired by
  guessing.

Every limit here is imported from :mod:`ai_market_monitor.schemas.planner_intent` and
:class:`ConditionNodeType`. Writing them out again is exactly the duplicate-vocabulary
mistake this codebase keeps paying for.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_market_monitor.schemas.planner_intent import (
    BOOLEAN_MAX_DEPTH,
    BOOLEAN_MAX_NODES,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    GROUP_ARITY,
    ConditionNodeType,
    ConditionNodeV2,
)


class BooleanStructureError(ValueError):
    """A structural edit the compiler could not execute. Carries a readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


#: The group types the Builder may create. Taken from the node types the draft schema
#: accepts, minus the leaf, so a new operator added to the compiler shows up here rather
#: than being silently unavailable in the Builder.
GROUP_TYPES: tuple[ConditionNodeType, ...] = tuple(
    item for item in ConditionNodeType if item is not ConditionNodeType.CONDITION
)

# How many rules each way of joining takes — ``GROUP_ARITY``, imported above and never
# re-declared here. The one answer lives beside ``ConditionNodeType`` in the draft
# schema, so the Builder cannot refuse an edit the schema would have stored, or allow one
# it would have rejected. A second copy in this file is how those two came to disagree.


@dataclass(frozen=True, slots=True)
class BooleanLimits:
    """What the compiler will actually accept, for the Builder to render against."""

    max_depth: int
    max_nodes: int
    operators: tuple[str, ...]
    #: ``{"and": [1, null], "or": [1, null], "not": [1, 1]}`` — how many rules each
    #: grouping takes, so the Builder can grey out "Group" until enough are selected.
    arity: dict[str, tuple[int, int | None]]

    def to_dict(self) -> dict[str, object]:
        return {
            "max_depth": self.max_depth,
            "max_nodes": self.max_nodes,
            "operators": list(self.operators),
            "arity": {key: list(value) for key, value in self.arity.items()},
        }


def boolean_limits() -> BooleanLimits:
    """The compiler's Boolean budget, read from the compiler's own constants."""

    return BooleanLimits(
        max_depth=BOOLEAN_MAX_DEPTH,
        max_nodes=BOOLEAN_MAX_NODES,
        operators=tuple(str(item.value) for item in GROUP_TYPES),
        arity={str(key.value): value for key, value in GROUP_ARITY.items()},
    )


# ---------------------------------------------------------------------------
# Reading the tree
# ---------------------------------------------------------------------------


def tree_depth(node: ConditionNodeV2 | None, *, _depth: int = 1) -> int:
    """How many levels deep the expression goes. A single rule is depth 1."""

    if node is None:
        return 0
    if not node.children:
        return _depth
    return max(tree_depth(child, _depth=_depth + 1) for child in node.children)


def node_count(node: ConditionNodeV2 | None) -> int:
    """Every node, groups included — the same thing the compiler's budget counts."""

    return 0 if node is None else len(node.walk())


def find_node(root: ConditionNodeV2 | None, node_id: str) -> ConditionNodeV2 | None:
    if root is None:
        return None
    return next((item for item in root.walk() if item.node_id == node_id), None)


def parent_of(root: ConditionNodeV2 | None, node_id: str) -> ConditionNodeV2 | None:
    if root is None:
        return None
    for candidate in root.walk():
        if any(child.node_id == node_id for child in candidate.children):
            return candidate
    return None


def describe_structure(root: ConditionNodeV2 | None) -> list[dict[str, object]]:
    """The tree as the Builder draws it: one flat list carrying each node's parent.

    Flat rather than nested because the client renders indentation from ``depth`` and
    needs to look a node up by id without walking. The shape is not the authority — the
    stored tree is — this is a view of it.
    """

    rows: list[dict[str, object]] = []

    def walk(node: ConditionNodeV2, parent_id: str | None, depth: int) -> None:
        is_group = node.node_type is not ConditionNodeType.CONDITION
        rows.append(
            {
                "node_id": node.node_id,
                "parent_id": parent_id,
                "depth": depth,
                "kind": "group" if is_group else "condition",
                "operator": str(node.node_type.value) if is_group else None,
                "child_ids": [child.node_id for child in node.children],
            }
        )
        for child in node.children:
            walk(child, node.node_id, depth + 1)

    if root is not None:
        walk(root, None, 1)
    return rows


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _require_within_budget(root: ConditionNodeV2) -> ConditionNodeV2:
    """Refuse a shape the compiler would reject, before it can be stored.

    Checked after every structural edit rather than only on approval: a draft that has
    already grown past the budget cannot be saved and then repaired, because the person
    would have no way to know which edit broke it.
    """

    depth = tree_depth(root)
    if depth > BOOLEAN_MAX_DEPTH:
        raise BooleanStructureError(
            "BOOLEAN_TOO_DEEP",
            f"That puts groups {depth} levels deep. Hilal Markets can run "
            f"{BOOLEAN_MAX_DEPTH} levels. Move some rules up a level first.",
        )
    count = node_count(root)
    if count > BOOLEAN_MAX_NODES:
        raise BooleanStructureError(
            "BOOLEAN_TOO_LARGE",
            f"That makes {count} parts in one setup. Hilal Markets can run "
            f"{BOOLEAN_MAX_NODES}. Remove a rule or a group first.",
        )
    return root


def _require_arity(node_type: ConditionNodeType, children: list[ConditionNodeV2]) -> None:
    minimum, maximum = GROUP_ARITY[node_type]
    if len(children) < minimum:
        word = "rule" if minimum == 1 else "rules"
        raise BooleanStructureError(
            "GROUP_TOO_SMALL",
            f"“{_label(node_type)}” needs at least {minimum} {word}.",
        )
    if maximum is not None and len(children) > maximum:
        raise BooleanStructureError(
            "GROUP_TOO_LARGE",
            f"“{_label(node_type)}” takes exactly {maximum}. "
            "Group the rules together first, then apply it to the group.",
        )


def _label(node_type: ConditionNodeType) -> str:
    return {
        ConditionNodeType.AND: "All of these",
        ConditionNodeType.OR: "Any of these",
        ConditionNodeType.NOT: "None of these",
    }[node_type]


def _require_group_type(operator: str) -> ConditionNodeType:
    try:
        node_type = ConditionNodeType(operator)
    except ValueError:
        raise BooleanStructureError(
            "LOGIC_NOT_OFFERED",
            "Rules can be joined with “all of these”, “any of these” or “none of these”.",
        ) from None
    if node_type is ConditionNodeType.CONDITION:
        raise BooleanStructureError(
            "LOGIC_NOT_OFFERED",
            "That is not a way of joining rules.",
        )
    return node_type


def _next_group_id(root: ConditionNodeV2 | None, operator: str) -> str:
    """A stable, unique id for a new group.

    Stable matters: every later edit names this id. Reusing one that already exists
    would make two different groups indistinguishable, and an edit meant for one would
    silently land on the other.
    """

    used = {item.node_id for item in (root.walk() if root else [])}
    index = 1
    while f"group_{operator}_{index}" in used:
        index += 1
    return f"group_{operator}_{index}"


# ---------------------------------------------------------------------------
# Editing the tree
# ---------------------------------------------------------------------------


def _replace(
    node: ConditionNodeV2,
    target_id: str,
    new: ConditionNodeV2 | None,
) -> ConditionNodeV2 | None:
    """Rebuild the tree with one node replaced, or removed when ``new`` is None."""

    if node.node_id == target_id:
        return new
    if not node.children:
        return node
    children = [
        rebuilt
        for child in node.children
        if (rebuilt := _replace(child, target_id, new)) is not None
    ]
    if not children:
        return None
    # A group left holding one child is kept as a group. It used to be collapsed into
    # that child, because a one-rule "all of these" was refused by ``_require_arity`` on
    # the next validation — that floor is one now, so collapsing would delete a group the
    # person made and still wants, the moment they took one rule out of it. An empty
    # group is still removed, above.
    return node.model_copy(update={"children": children})


def group_conditions(
    root: ConditionNodeV2 | None,
    *,
    node_ids: list[str],
    operator: str,
) -> ConditionNodeV2:
    """Wrap existing nodes in a new group, in place, keeping every other rule where it is.

    The selected nodes must share a parent. Grouping across two different branches would
    have to move rules out of the logic their author put them in, which changes the
    strategy — so it is refused rather than guessed at.
    """

    if root is None:
        raise BooleanStructureError("NO_RULES_YET", "There are no rules to group yet.")
    node_type = _require_group_type(operator)

    unique = list(dict.fromkeys(node_ids))
    if len(unique) != len(node_ids):
        raise BooleanStructureError(
            "SELECTION_REPEATED", "A rule was selected twice. Reload and try again."
        )
    selected = [find_node(root, node_id) for node_id in unique]
    if any(item is None for item in selected):
        raise BooleanStructureError(
            "NODE_UNKNOWN", "One of those rules is no longer here. Reload and try again."
        )
    members = [item for item in selected if item is not None]
    _require_arity(node_type, members)

    parents = {
        (parent.node_id if (parent := parent_of(root, node_id)) else None) for node_id in unique
    }
    if len(parents) != 1:
        raise BooleanStructureError(
            "SELECTION_SPLIT",
            "Those rules are in different groups. Group rules that sit together.",
        )
    parent_id = parents.pop()

    group = ConditionNodeV2(
        node_id=_next_group_id(root, str(node_type.value)),
        node_type=node_type,
        children=members,
    )

    if parent_id is None:
        # The selection is the whole root.
        return _require_within_budget(group)

    parent = find_node(root, parent_id)
    assert parent is not None
    kept = [child for child in parent.children if child.node_id not in set(unique)]
    first = min(
        index
        for index, child in enumerate(parent.children)
        if child.node_id in set(unique)
    )
    remaining_before = sum(
        1 for child in parent.children[:first] if child.node_id not in set(unique)
    )
    kept.insert(remaining_before, group)
    rebuilt = _replace(root, parent_id, parent.model_copy(update={"children": kept}))
    if rebuilt is None:
        raise BooleanStructureError("NODE_UNKNOWN", "That group is no longer here.")
    return _require_within_budget(rebuilt)


def ungroup(root: ConditionNodeV2 | None, *, group_id: str) -> ConditionNodeV2:
    """Dissolve one group, lifting its children into its parent, in order."""

    if root is None:
        raise BooleanStructureError("NO_RULES_YET", "There are no rules to change yet.")
    group = find_node(root, group_id)
    if group is None or group.node_type is ConditionNodeType.CONDITION:
        raise BooleanStructureError("NODE_UNKNOWN", "That group is no longer here.")

    parent = parent_of(root, group_id)
    if parent is None:
        if len(group.children) == 1:
            return _require_within_budget(group.children[0])
        raise BooleanStructureError(
            "ROOT_REQUIRED",
            "The outermost grouping cannot be removed. Change how it joins instead.",
        )
    children: list[ConditionNodeV2] = []
    for child in parent.children:
        if child.node_id == group_id:
            children.extend(group.children)
        else:
            children.append(child)
    rebuilt = _replace(root, parent.node_id, parent.model_copy(update={"children": children}))
    if rebuilt is None:
        raise BooleanStructureError("NODE_UNKNOWN", "That group is no longer here.")
    return _require_within_budget(rebuilt)


def set_group_operator(
    root: ConditionNodeV2 | None,
    *,
    group_id: str,
    operator: str,
) -> ConditionNodeV2:
    """Change one group from all/any/none, leaving every rule inside it untouched."""

    if root is None:
        raise BooleanStructureError("NO_RULES_YET", "There are no rules to change yet.")
    node_type = _require_group_type(operator)
    group = find_node(root, group_id)
    if group is None or group.node_type is ConditionNodeType.CONDITION:
        raise BooleanStructureError("NODE_UNKNOWN", "That group is no longer here.")

    children = list(group.children)
    _require_arity(node_type, children)
    changed = group.model_copy(
        update={
            "node_type": node_type,
            # The id carries the operator so a reader of the stored draft can see the
            # shape without walking it. Keeping the old one would make a group called
            # "group_and_1" behave as an OR.
            "node_id": (
                group.node_id
                if group.node_id.startswith(f"group_{node_type.value}_")
                else _next_group_id(root, str(node_type.value))
            ),
        }
    )
    rebuilt = _replace(root, group_id, changed)
    if rebuilt is None:
        raise BooleanStructureError("NODE_UNKNOWN", "That group is no longer here.")
    return _require_within_budget(rebuilt)


def move_condition(
    root: ConditionNodeV2 | None,
    *,
    node_id: str,
    target_group_id: str,
    position: int | None = None,
) -> ConditionNodeV2:
    """Move one rule or group into another group, at an exact position."""

    if root is None:
        raise BooleanStructureError("NO_RULES_YET", "There are no rules to move yet.")
    moving = find_node(root, node_id)
    target = find_node(root, target_group_id)
    if moving is None:
        raise BooleanStructureError("NODE_UNKNOWN", "That rule is no longer here.")
    if target is None or target.node_type is ConditionNodeType.CONDITION:
        raise BooleanStructureError("NODE_UNKNOWN", "That group is no longer here.")
    if node_id == target_group_id:
        raise BooleanStructureError("MOVE_INTO_SELF", "A group cannot be moved into itself.")
    if any(item.node_id == target_group_id for item in moving.walk()):
        raise BooleanStructureError(
            "MOVE_INTO_SELF",
            "A group cannot be moved inside one of its own rules.",
        )

    detached = _replace(root, node_id, None)
    if detached is None:
        raise BooleanStructureError(
            "LAST_RULE",
            "That is the only rule left. Add another before moving this one.",
        )
    # The target may have gone when the rule left it — a one-child group losing its only
    # child is removed. Moving into a group that no longer exists must fail here, not
    # produce a tree with the rule silently dropped.
    landing = find_node(detached, target_group_id)
    if landing is None or landing.node_type is ConditionNodeType.CONDITION:
        raise BooleanStructureError(
            "GROUP_COLLAPSED",
            "That group no longer exists once the rule leaves it. Reload and try again.",
        )
    children = list(landing.children)
    index = len(children) if position is None else max(0, min(int(position), len(children)))
    children.insert(index, moving)
    _require_arity(landing.node_type, children)
    rebuilt = _replace(detached, target_group_id, landing.model_copy(update={"children": children}))
    if rebuilt is None:
        raise BooleanStructureError("NODE_UNKNOWN", "That group is no longer here.")
    return _require_within_budget(rebuilt)

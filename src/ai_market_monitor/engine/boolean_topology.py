"""The one owner of "how do these rules combine".

Three jobs, kept together because they must agree exactly:

1. **Validate** a flat :class:`BooleanStrategyIntent` — one root, every reference
   resolved exactly once, no cycles, correct arity, bounded size and depth.
2. **Scope** the part of a message that states executable logic at all, so a
   watchlist sentence containing the word "and" never becomes Boolean algebra.
3. **Compare** the structure the trader stated with the structure that was compiled,
   so a stated expression that came back flattened is a typed failure rather than a
   quietly different monitor.

The wording of AND / OR / NOT and the precedence between them is **not** owned here.
``engine/boolean_expression.py`` already reads that shape, brackets included, and this
module calls it. A second tokenizer is exactly the duplicate-parser failure this
codebase keeps paying for: two readers, each understanding a different subset of the
connective words.

Why this exists
---------------

Evaluator runs 20260802T232050Z and 20260803T000036Z measured the same thing. The
model was given a self-referencing ``child_intents`` schema and never used it: an
explicit ``A AND (B OR C)`` came back as two unrelated flat rules and one unsupported
note, and the compiled draft joined every rule with the registry's implicit AND.
``A AND (B OR C)`` became ``A AND B`` — a monitor that fires on a different market,
with nothing in the artifact showing that the shape had changed.

The separation rule
-------------------

An executable predicate is something that can be true or false about the market on
its own. A symbol you want watched, a symbol you want left out, and the timeframe a
rule reads for context are **not** predicates — they are scope and role metadata. The
word "and" joining two of them is English, not Boolean algebra. :func:`executable_span`
draws that line once, and nothing outside this module may turn scope into logic.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ai_market_monitor.engine.boolean_expression import (
    BooleanNode,
    leading_prefix_span,
    parse_boolean_expression,
)
from ai_market_monitor.schemas.planner_intent import (
    BOOLEAN_MAX_DEPTH,
    BOOLEAN_MAX_NODES,
    BooleanGroupIntent,
    BooleanStrategyIntent,
    ConditionLeafIntent,
)

__all__ = [
    "BooleanOperator",
    "BooleanTopology",
    "BooleanTopologyError",
    "ExpectedTopology",
    "TopologyComparison",
    "compare_topology",
    "executable_span",
    "parse_stated_topology",
    "states_explicit_boolean_logic",
    "topology_fingerprint",
    "validate_boolean_topology",
]


class BooleanOperator(StrEnum):
    AND = "and"
    OR = "or"
    NOT = "not"


class BooleanTopologyError(ValueError):
    """A flat Boolean graph that cannot be one tree, with the exact reason."""

    def __init__(self, code: str, message: str, *, details: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True, slots=True)
class BooleanTopology:
    """A validated flat graph, plus the derived facts callers need."""

    intent: BooleanStrategyIntent
    #: Every node reference in evaluation order, root first.
    order: tuple[str, ...]
    #: ``child ref -> parent ref``. The root has no entry.
    parents: Mapping[str, str]
    depth: int

    @property
    def root_ref(self) -> str:
        return self.intent.root_ref

    @property
    def leaves(self) -> Mapping[str, ConditionLeafIntent]:
        return {item.leaf_ref: item for item in self.intent.condition_leaves}

    @property
    def groups(self) -> Mapping[str, BooleanGroupIntent]:
        return {item.group_ref: item for item in self.intent.boolean_groups}

    @property
    def is_single_leaf(self) -> bool:
        """True when the trader stated one rule and no combination at all."""

        return not self.intent.boolean_groups

    def shape(self) -> str:
        """A compact description of the structure alone, for evidence and hashing."""

        return _compiled_shape(self.root_ref, self)


def validate_boolean_topology(intent: BooleanStrategyIntent) -> BooleanTopology:
    """Prove the flat graph is one finite tree, or refuse with the exact reason.

    Refusing is the point. A graph with two roots, a cycle, or an orphan leaf has no
    single meaning, and choosing one of its readings would be the platform inventing a
    strategy the trader never described.
    """

    leaves = {item.leaf_ref: item for item in intent.condition_leaves}
    groups = {item.group_ref: item for item in intent.boolean_groups}
    known = set(leaves) | set(groups)

    referenced: dict[str, list[str]] = {}
    for group in intent.boolean_groups:
        for child in group.child_refs:
            referenced.setdefault(child, []).append(group.group_ref)

    twice = sorted(ref for ref, owners in referenced.items() if len(owners) > 1)
    if twice:
        raise BooleanTopologyError(
            "BOOLEAN_TOPOLOGY_AMBIGUOUS",
            "One part of that logic was placed inside two different groups.",
            details=tuple(f"node:{ref}:claimed_by:{','.join(referenced[ref])}" for ref in twice),
        )

    orphans = sorted(ref for ref in known if ref not in referenced and ref != intent.root_ref)
    if orphans:
        raise BooleanTopologyError(
            "BOOLEAN_TOPOLOGY_AMBIGUOUS",
            "Part of that logic was not connected to the rest of the expression.",
            details=tuple(f"node:{ref}:unreferenced" for ref in orphans),
        )
    if intent.root_ref in referenced:
        raise BooleanTopologyError(
            "BOOLEAN_TOPOLOGY_AMBIGUOUS",
            "The outermost group was also placed inside another group.",
            details=(f"root:{intent.root_ref}:has_parent",),
        )

    parents = {ref: owners[0] for ref, owners in referenced.items()}
    order: list[str] = []
    seen: set[str] = set()
    max_depth = 0

    def walk(ref: str, depth: int, path: tuple[str, ...]) -> None:
        nonlocal max_depth
        if ref in path:
            raise BooleanTopologyError(
                "BOOLEAN_TOPOLOGY_AMBIGUOUS",
                "That logic refers back to itself and cannot be evaluated.",
                details=(f"cycle:{'>'.join((*path, ref))}",),
            )
        if depth > BOOLEAN_MAX_DEPTH:
            raise BooleanTopologyError(
                "BOOLEAN_TOPOLOGY_AMBIGUOUS",
                "That logic is nested more deeply than one turn may carry.",
                details=(f"depth:{depth}:max:{BOOLEAN_MAX_DEPTH}",),
            )
        max_depth = max(max_depth, depth)
        order.append(ref)
        seen.add(ref)
        group = groups.get(ref)
        if group is None:
            return
        for child in group.child_refs:
            walk(child, depth + 1, (*path, ref))

    walk(intent.root_ref, 1, ())

    unreachable = sorted(known - seen)
    if unreachable:
        raise BooleanTopologyError(
            "BOOLEAN_TOPOLOGY_AMBIGUOUS",
            "Part of that logic could not be reached from the outermost group.",
            details=tuple(f"node:{ref}:unreachable" for ref in unreachable),
        )
    if len(order) > BOOLEAN_MAX_NODES:
        raise BooleanTopologyError(
            "BOOLEAN_TOPOLOGY_AMBIGUOUS",
            "That logic has more parts than one turn may carry.",
            details=(f"nodes:{len(order)}:max:{BOOLEAN_MAX_NODES}",),
        )
    return BooleanTopology(intent=intent, order=tuple(order), parents=parents, depth=max_depth)


# --------------------------------------------------------------------------------------
# Which part of a message states executable logic at all
# --------------------------------------------------------------------------------------

_SENTENCE_BREAK_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?;\n])\s+")

#: Wording that marks scope, role, or workflow rather than a market predicate. A
#: clause made only of these joined by "and" is a watchlist or a preference, not an
#: expression, and must never become executable Boolean structure.
_SCOPE_ONLY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:watchlist|watch|watching|include|included|including|exclude|excluded|excluding|"
    r"only|except|universe|symbol|symbols|exchange|quote|"
    r"context|trigger|confirmation|reference|chart|timeframe|"
    r"approve|approval|approved|confirm|confirmation|review)\b",
    re.IGNORECASE,
)

#: Wording that marks a market predicate: something that is true or false about the
#: market on its own. Deliberately narrow — a span that matches nothing here is not
#: treated as an operand, so scope text can never be counted as logic.
_PREDICATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\d+(?:\.\d+)?\s*%)"
    r"|\b(?:rsi|macd|ema|sma|atr|adx|vwap|stochastic|stoch|bollinger|volume|"
    r"price|close|open|high|low|move|moves|moved|movement|breakout|breakdown|"
    r"cross|crosses|crossing|crossed|sweep|sweeps|reclaim|candle|engulfing|"
    r"bullish|bearish|oversold|overbought|gap|trend|momentum)\b",
    re.IGNORECASE,
)

#: The connective tokens that could start a Boolean reading. Only used to decide
#: whether a clause is *worth* handing to the shared parser.
_CONNECTIVE_RE: Final[re.Pattern[str]] = re.compile(
    r"[()\[\]]|\b(?:and|or|not|either|neither|nor|without|except)\b|&&|\|\|",
    re.IGNORECASE,
)


def _sentence_spans(message: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_BREAK_RE.finditer(message):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, len(message)))
    return [(start, end) for start, end in spans if end > start]


def _predicate_count(text: str) -> int:
    """How many independent market predicates a clause states.

    Scope wording is removed before counting, so ``the 1-hour chart for the trigger``
    contributes nothing even though it contains a number, and
    ``bullish move of at least 2%`` contributes one even though it sits in the same
    sentence as a timeframe role.
    """

    pieces = re.split(r"\band\b|\bor\b|\bnot\b|[()\[\],;]", text, flags=re.IGNORECASE)
    count = 0
    for piece in pieces:
        stripped = _SCOPE_ONLY_RE.sub(" ", piece)
        if _PREDICATE_RE.search(stripped):
            count += 1
    return count


def executable_span(message: str) -> tuple[int, int] | None:
    """The part of ``message`` that states executable Boolean logic, or ``None``.

    A sentence qualifies only when it both contains a connective and states at least
    two independent market predicates once scope wording is removed. That is what
    keeps ``I want a simple watchlist for ETHUSDT, not BTCUSDT`` — an inclusion and an
    exclusion — from being read as ``ETHUSDT AND NOT BTCUSDT``.
    """

    if not message:
        return None
    span: tuple[int, int] | None = None
    for start, end in _sentence_spans(message):
        sentence = message[start:end]
        if not _CONNECTIVE_RE.search(sentence):
            continue
        if _predicate_count(sentence) < 2:
            continue
        span = (start, end) if span is None else (min(span[0], start), max(span[1], end))
    return span


# --------------------------------------------------------------------------------------
# The structure the trader actually stated
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpectedTopology:
    """The Boolean structure the message states, read before any model output."""

    #: The shared parser's tree. Leaves carry the trader's exact wording.
    root: BooleanNode
    #: The span of the message this structure was read from.
    span: tuple[int, int]
    #: True when the message used brackets to group explicitly.
    explicit_grouping: bool

    @property
    def leaf_texts(self) -> tuple[str, ...]:
        return tuple(leaf.text for leaf in self.root.leaves)

    @property
    def shape(self) -> str:
        return self.root.shape()

    def readable(self) -> str:
        """The structure written back out, for a message a beginner can check."""

        return _readable(self.root)


def _readable(node: BooleanNode) -> str:
    if node.is_leaf:
        return f"[{' '.join(node.text.split())[:48]}]"
    if node.operator == "not":
        return f"NOT {_readable(node.children[0])}"
    joined = f" {(node.operator or '').upper()} ".join(_readable(child) for child in node.children)
    return f"({joined})"


def parse_stated_topology(message: str) -> ExpectedTopology | None:
    """Read the Boolean structure the trader wrote, or ``None`` when they wrote none.

    Precedence and brackets come from ``engine/boolean_expression``; the only thing
    added here is the scope filter, so a watchlist sentence is never parsed as logic.

    ``None`` means "no combination was stated". A single rule stays a single rule and
    is never wrapped in an invented group.
    """

    span = executable_span(message)
    if span is None:
        return None
    region = _without_lead_in(" ".join(message[span[0] : span[1]].split()))
    node = parse_boolean_expression(region)
    if node is None or node.is_leaf:
        return None
    if len(node.leaves) < 2:
        return None
    return ExpectedTopology(
        root=node,
        span=span,
        explicit_grouping=bool(re.search(r"[(\[]", region)),
    )


def _without_lead_in(region: str) -> str:
    """Drop an introductory phrase that states no market predicate of its own.

    ``Alert me when (A or B) and C`` opens with words that introduce the expression.
    Left in place they become a dangling first operand and the whole parse is thrown
    away, which is how an explicit ``(A OR B) AND C`` used to reach the compiler as
    "no structure stated" and get flattened.

    The span is identified structurally by ``engine/boolean_expression``; the decision
    to drop it is made here, where the market vocabulary lives, so a real first
    operand — ``RSI above 70 (my overbought line) and volume high`` — is never lost.
    """

    prefix_length = leading_prefix_span(region)
    if not prefix_length:
        return region
    prefix = region[:prefix_length]
    if _PREDICATE_RE.search(_SCOPE_ONLY_RE.sub(" ", prefix)):
        return region
    return region[prefix_length:].lstrip(" ,;:-–—")


def states_explicit_boolean_logic(message: str) -> bool:
    """True when the trader wrote a combination the server must preserve exactly."""

    return parse_stated_topology(message) is not None


# --------------------------------------------------------------------------------------
# Comparing what was stated with what was compiled
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TopologyComparison:
    """Whether the compiled structure is the structure the trader stated."""

    matches: bool
    code: str | None = None
    #: Plain, safe reasons: what was expected, and what was built instead.
    details: tuple[str, ...] = field(default_factory=tuple)
    expected_shape: str = ""
    compiled_shape: str = ""

    def as_evidence(self) -> dict[str, object]:
        return {
            "boolean_topology_match": self.matches,
            "boolean_topology_code": self.code,
            "boolean_topology_expected": self.expected_shape,
            "boolean_topology_compiled": self.compiled_shape,
            "boolean_topology_details": list(self.details),
        }


def _compiled_shape(ref: str, topology: BooleanTopology) -> str:
    group = topology.groups.get(ref)
    if group is None:
        leaf = topology.leaves.get(ref)
        excerpt = " ".join((leaf.source_quote if leaf else ref).split())
        return f"[{excerpt[:48]}]"
    if group.operator == "not":
        return f"NOT {_compiled_shape(group.child_refs[0], topology)}"
    joined = f" {group.operator.upper()} ".join(
        _compiled_shape(child, topology) for child in group.child_refs
    )
    return f"({joined})"


def compare_topology(
    expected: ExpectedTopology,
    topology: BooleanTopology | None,
) -> TopologyComparison:
    """Compare stated structure with compiled structure, node for node.

    Leaves are matched through their wording — a compiled leaf's quote against the
    stated operand's text — and groups through operator and child membership. AND and
    OR are commutative, so children are compared as a set; membership is meaning, order
    is not. ``NOT`` has exactly one child and is compared directly.
    """

    expected_shape = expected.readable()
    if topology is None:
        return TopologyComparison(
            matches=False,
            code="BOOLEAN_TOPOLOGY_MISSING",
            details=("compiled:no_boolean_structure",),
            expected_shape=expected_shape,
        )
    compiled_shape = _compiled_shape(topology.root_ref, topology)
    leaves = topology.leaves
    assignments: dict[int, str] = {}
    unmatched: list[str] = []
    for index, leaf_node in enumerate(expected.root.leaves):
        candidates = [
            ref
            for ref, leaf in leaves.items()
            if _quotes_overlap(leaf_node.text, leaf.source_quote)
        ]
        if len(candidates) != 1:
            excerpt = " ".join(leaf_node.text.split())[:40]
            unmatched.append(f"operand:{excerpt!r}:compiled_matches={len(candidates)}")
            continue
        assignments[index] = candidates[0]
    if unmatched:
        return TopologyComparison(
            matches=False,
            code="BOOLEAN_TOPOLOGY_MISSING",
            details=tuple(unmatched[:6]),
            expected_shape=expected_shape,
            compiled_shape=compiled_shape,
        )
    if len(set(assignments.values())) != len(leaves):
        return TopologyComparison(
            matches=False,
            code="BOOLEAN_TOPOLOGY_MISSING",
            details=(f"leaves:stated={len(assignments)}:compiled={len(leaves)}",),
            expected_shape=expected_shape,
            compiled_shape=compiled_shape,
        )
    problems = _compare_node(expected.root, topology.root_ref, topology, assignments, counter=[0])
    if problems:
        return TopologyComparison(
            matches=False,
            code="BOOLEAN_TOPOLOGY_MISSING",
            details=tuple(problems[:6]),
            expected_shape=expected_shape,
            compiled_shape=compiled_shape,
        )
    return TopologyComparison(
        matches=True,
        expected_shape=expected_shape,
        compiled_shape=compiled_shape,
    )


def _quotes_overlap(operand: str, quote: str) -> bool:
    """Whether a stated operand and a compiled leaf quote name the same rule."""

    left = " ".join(operand.split()).casefold().strip(" ,.;:-–—")
    right = " ".join(quote.split()).casefold().strip(" ,.;:-–—")
    if not left or not right:
        return False
    return left in right or right in left


def _leaf_indices(node: BooleanNode, counter: list[int]) -> list[int]:
    if node.is_leaf:
        index = counter[0]
        counter[0] += 1
        return [index]
    return [item for child in node.children for item in _leaf_indices(child, counter)]


def _compare_node(
    expected: BooleanNode,
    ref: str,
    topology: BooleanTopology,
    assignments: Mapping[int, str],
    *,
    counter: list[int],
) -> list[str]:
    if expected.is_leaf:
        index = counter[0]
        counter[0] += 1
        wanted = assignments.get(index)
        if wanted != ref:
            return [f"node:{ref}:expected_leaf:{wanted}"]
        return []
    group = topology.groups.get(ref)
    if group is None:
        _leaf_indices(expected, counter)
        return [f"node:{ref}:expected_{expected.operator}_group:found_single_rule"]
    if group.operator != expected.operator:
        _leaf_indices(expected, counter)
        return [f"node:{ref}:operator:stated={expected.operator}:compiled={group.operator}"]
    if len(group.child_refs) != len(expected.children):
        _leaf_indices(expected, counter)
        return [
            f"node:{ref}:children:stated={len(expected.children)}"
            f":compiled={len(group.child_refs)}"
        ]
    problems: list[str] = []
    remaining = list(group.child_refs)
    for child in expected.children:
        matched: str | None = None
        for candidate in remaining:
            probe = [counter[0]]
            if not _compare_node(child, candidate, topology, assignments, counter=probe):
                matched = candidate
                counter[0] = probe[0]
                break
        if matched is None:
            _leaf_indices(child, counter)
            problems.append(f"node:{ref}:child_not_matched:{child.operator or 'rule'}")
            continue
        remaining.remove(matched)
    return problems


def topology_fingerprint(topology: BooleanTopology | None) -> str:
    """A stable description of structure alone, for approval binding.

    Two drafts with the same rules joined differently must not share a fingerprint:
    that is what makes an approval invalid when the structure is edited.
    """

    if topology is None:
        return "none"
    return topology.shape()


def leaf_order(topology: BooleanTopology) -> Sequence[str]:
    """Leaf references in evaluation order, for stable canonical node identity."""

    return [ref for ref in topology.order if ref in topology.leaves]

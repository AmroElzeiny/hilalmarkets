"""Read the AND / OR / NOT shape a trader wrote, including their brackets.

The compiler joins every condition it finds with AND. That is right for most setups
and wrong for any setup containing an OR: `(A or B) and C` becomes `A and B and C`,
which fires far less often and on different markets. The artifact is schema-valid, the
condition list looks complete, and nothing downstream notices the shape changed.

This module recovers the shape as a tree. It does not compile anything — it reports
what the trader asked for, so the compiler can either reproduce that shape or say
plainly that it could not.

`OR` binds loosest, then `AND`, then `NOT`, which is the usual reading and the one
brackets are written to override.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ai_market_monitor.engine.comparators import comparator_terms

BoolOperator = Literal["and", "or", "not"]

#: Words traders use for each connective. `&&`/`||` appear when people paste code.
_AND_RE = re.compile(r"\b(?:and|plus|also|as\s+well\s+as|together\s+with|&&)\b|&", re.IGNORECASE)
_OR_RE = re.compile(r"\b(?:or|either|otherwise|alternatively|\|\|)\b|\|", re.IGNORECASE)
_NOT_RE = re.compile(r"\b(?:not|never|without|except|excluding)\b", re.IGNORECASE)

_TOKEN_RE = re.compile(
    r"(?P<open>[(\[])|(?P<close>[)\]])|"
    r"(?P<and>\b(?:and|plus|also|as\s+well\s+as|together\s+with)\b|&&|&)|"
    r"(?P<or>\b(?:or|either|otherwise|alternatively)\b|\|\||\|)|"
    r"(?P<not>\b(?:not|never|without|except|excluding)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BooleanNode:
    """One node of the shape the trader wrote.

    A leaf carries the exact span of their wording, so the compiler can match a
    compiled condition back to the part of the sentence it came from.
    """

    operator: BoolOperator | None = None
    text: str = ""
    children: tuple[BooleanNode, ...] = field(default_factory=tuple)

    @property
    def is_leaf(self) -> bool:
        return self.operator is None

    @property
    def leaves(self) -> tuple[BooleanNode, ...]:
        if self.is_leaf:
            return (self,)
        return tuple(leaf for child in self.children for leaf in child.leaves)

    def shape(self) -> str:
        """A compact, comparable description of the structure alone.

        Used to check whether a compiled strategy kept the requested shape without
        depending on how the leaves were worded or ordered in the artifact.
        """
        if self.is_leaf:
            return "leaf"
        inner = ",".join(child.shape() for child in self.children)
        return f"{self.operator}({inner})"


def has_explicit_structure(text: str) -> bool:
    """Whether the text states a boolean shape that flattening would destroy.

    A sentence with no OR and no brackets flattens to AND without losing anything, so
    it is deliberately not reported: only real structure is worth defending.
    """
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return False
    protected = _protected_spans(collapsed)
    for match in _OR_RE.finditer(collapsed):
        if not any(start <= match.start() and match.end() <= end for start, end in protected):
            return True
    return bool(re.search(r"[(\[][^)\]]{4,}[)\]]", collapsed) and _AND_RE.search(collapsed))


def parse_boolean_expression(text: str) -> BooleanNode | None:
    """Parse the shape, or ``None`` when the text states none.

    Returns ``None`` rather than guessing. A caller that gets ``None`` should keep its
    existing behaviour; a caller that gets a tree must either reproduce that tree or
    refuse.
    """
    collapsed = " ".join((text or "").split())
    if not has_explicit_structure(collapsed):
        return None
    tokens = _tokenize(collapsed)
    if not tokens:
        return None
    node, index = _parse_or(tokens, 0)
    if node is None or index != len(tokens):
        return None
    return node if not node.is_leaf else None


#: Comparison phrases that contain a connective word. `grew 5% or more` states one
#: threshold, not a choice between two rules, and `at or below 30` is a single
#: comparator. Reading their `or` as a boolean split invented structure that was
#: never written — and then reported the setup as unbuildable.
_PROTECTED_SPAN_RE = re.compile(
    "|".join(
        rf"(?<![a-z]){re.escape(term)}(?![a-z])"
        for term in comparator_terms()
        if _AND_RE.search(term) or _OR_RE.search(term) or _NOT_RE.search(term)
    )
    or r"(?!)",
    re.IGNORECASE,
)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in _PROTECTED_SPAN_RE.finditer(text)]


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Split into operator tokens and the leaf spans between them."""
    protected = _protected_spans(text)
    tokens: list[tuple[str, str]] = []
    cursor = 0
    for match in _TOKEN_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in protected):
            continue
        leaf = text[cursor : match.start()].strip(" ,;:-")
        if leaf:
            tokens.append(("leaf", leaf))
        kind = match.lastgroup or ""
        tokens.append((kind, match.group(0)))
        cursor = match.end()
    tail = text[cursor:].strip(" ,;:-")
    if tail:
        tokens.append(("leaf", tail))
    return tokens


def _parse_or(tokens: list[tuple[str, str]], index: int) -> tuple[BooleanNode | None, int]:
    node, index = _parse_and(tokens, index)
    if node is None:
        return None, index
    children = [node]
    while index < len(tokens) and tokens[index][0] == "or":
        right, index = _parse_and(tokens, index + 1)
        if right is None:
            return None, index
        children.append(right)
    if len(children) == 1:
        return node, index
    return BooleanNode(operator="or", children=tuple(children)), index


def _parse_and(tokens: list[tuple[str, str]], index: int) -> tuple[BooleanNode | None, int]:
    node, index = _parse_unary(tokens, index)
    if node is None:
        return None, index
    children = [node]
    while index < len(tokens) and tokens[index][0] == "and":
        right, index = _parse_unary(tokens, index + 1)
        if right is None:
            return None, index
        children.append(right)
    if len(children) == 1:
        return node, index
    return BooleanNode(operator="and", children=tuple(children)), index


def _parse_unary(tokens: list[tuple[str, str]], index: int) -> tuple[BooleanNode | None, int]:
    if index < len(tokens) and tokens[index][0] == "not":
        child, index = _parse_unary(tokens, index + 1)
        if child is None:
            return None, index
        return BooleanNode(operator="not", children=(child,)), index
    return _parse_primary(tokens, index)


def _parse_primary(tokens: list[tuple[str, str]], index: int) -> tuple[BooleanNode | None, int]:
    if index >= len(tokens):
        return None, index
    kind, value = tokens[index]
    if kind == "open":
        node, index = _parse_or(tokens, index + 1)
        if node is None or index >= len(tokens) or tokens[index][0] != "close":
            return None, index
        return node, index + 1
    if kind == "leaf":
        return BooleanNode(text=value), index + 1
    return None, index

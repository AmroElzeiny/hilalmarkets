"""A number is grounded only when its *role* is grounded too.

Value grounding asks "did the trader write 14?". That is not enough when a capability
takes two trader-controlled parameters of the same unit:

    RSI period 14 and confirmation for 3 candles

Both 14 and 3 are candle counts, and both appear in the text. Checking values alone
accepts ``period=3, confirmation_candles=14`` — an inverted rule that grounds perfectly
and monitors something the trader never described.

So each parameter also has to show *why that number is that parameter*: a phrase naming
the role near the number. The registry owns the vocabulary through optional metadata on
``parameter_schema``:

``x-semantic-unit``        what the number measures, so a clash can be detected at all
``x-source-aliases``       what the trader might call this role, in any supported language
``x-semantic-aliases``     canonical synonyms for logging and clarification wording
``x-requires-role-phrase`` force role evidence even when no other parameter shares a unit

A registry default the user never overrode stays exempt: they did not choose it, so
there is nothing of theirs to find.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: How far from the number a role phrase may sit and still be talking about it. Wide
#: enough for `period of 14` and `14-period`, narrow enough that the *next* parameter's
#: name in the same sentence does not claim this number.
_ROLE_WINDOW = 32


@dataclass(frozen=True, slots=True)
class ParameterRoleSpec:
    """What the registry says about one parameter's role vocabulary."""

    name: str
    semantic_unit: str | None = None
    source_aliases: tuple[str, ...] = ()
    semantic_aliases: tuple[str, ...] = ()
    requires_role_phrase: bool = False

    @property
    def phrases(self) -> tuple[str, ...]:
        """Everything that can name this role, longest first so the specific wins."""
        merged = {
            self.name.replace("_", " "),
            self.name,
            *self.source_aliases,
            *self.semantic_aliases,
        }
        return tuple(sorted((item for item in merged if item.strip()), key=len, reverse=True))


def role_specs(parameter_schema: dict[str, Any]) -> dict[str, ParameterRoleSpec]:
    """Read role metadata off the registry's own parameter schema."""

    specs: dict[str, ParameterRoleSpec] = {}
    for name, rules in (parameter_schema or {}).items():
        if not isinstance(rules, dict):
            specs[name] = ParameterRoleSpec(name=name)
            continue
        specs[name] = ParameterRoleSpec(
            name=name,
            semantic_unit=rules.get("x-semantic-unit"),
            source_aliases=tuple(rules.get("x-source-aliases") or ()),
            semantic_aliases=tuple(rules.get("x-semantic-aliases") or ()),
            requires_role_phrase=bool(rules.get("x-requires-role-phrase")),
        )
    return specs


def _number_positions(text: str, value: float) -> list[tuple[int, int]]:
    """Where this exact number appears, on token boundaries."""

    rendered = f"{value:g}"
    pattern = re.compile(rf"(?<![\w.]){re.escape(rendered)}(?![\d.])")
    return [(match.start(), match.end()) for match in pattern.finditer(text)]


def _role_phrase_near(text: str, spec: ParameterRoleSpec, span: tuple[int, int]) -> bool:
    lowered = text.casefold()
    start = max(0, span[0] - _ROLE_WINDOW)
    end = min(len(text), span[1] + _ROLE_WINDOW)
    window = lowered[start:end]
    return any(phrase.casefold() in window for phrase in spec.phrases)


def _unambiguous_grammar(text: str, spec: ParameterRoleSpec, value: float) -> bool:
    """`14-period` and `period 14` bind the number to the role by position alone."""

    rendered = re.escape(f"{value:g}")
    for phrase in spec.phrases:
        escaped = re.escape(phrase.casefold())
        adjacent = rf"(?:{rendered}[\s-]*{escaped}|{escaped}[\s:=-]*(?:of\s+)?{rendered})"
        if re.search(adjacent, text.casefold()):
            return True
    return False


def role_grounding_errors(
    *,
    node_id: str,
    supplied: dict[str, Any],
    defaults: dict[str, Any],
    parameter_schema: dict[str, Any],
    authorizing_text: str,
) -> list[str]:
    """Refuse a number whose role the authorising text does not establish.

    Only trader-controlled values are checked. A value equal to the registry default is
    treated as not overridden: the trader did not choose it, so there is nothing of
    theirs to find.
    """

    specs = role_specs(parameter_schema)
    numeric = {
        name: float(value)
        for name, value in supplied.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    overridden = {
        name: value
        for name, value in numeric.items()
        if name not in defaults or defaults.get(name) != supplied.get(name)
    }
    if not overridden:
        return []

    units: dict[str, list[str]] = {}
    for name in overridden:
        unit = (specs.get(name) or ParameterRoleSpec(name=name)).semantic_unit
        units.setdefault(unit or f"__untyped__{name}", []).append(name)

    errors: list[str] = []
    for name, value in overridden.items():
        spec = specs.get(name) or ParameterRoleSpec(name=name)
        unit_key = spec.semantic_unit or f"__untyped__{name}"
        shares_unit = len(units.get(unit_key, [])) > 1
        if not shares_unit and not spec.requires_role_phrase:
            # No other parameter could claim this number, so the value check suffices.
            continue
        positions = _number_positions(authorizing_text, value)
        if not positions:
            errors.append(f"{node_id}:parameter_not_grounded:{name}")
            continue
        if _unambiguous_grammar(authorizing_text, spec, value):
            continue
        if any(_role_phrase_near(authorizing_text, spec, span) for span in positions):
            continue
        errors.append(f"{node_id}:parameter_role_not_grounded:{name}")
    return errors


def ambiguous_role_pairs(
    *,
    supplied: dict[str, Any],
    parameter_schema: dict[str, Any],
) -> list[tuple[str, str]]:
    """Parameter pairs that share a unit, for the clarification the server may ask."""

    specs = role_specs(parameter_schema)
    by_unit: dict[str, list[str]] = {}
    for name, value in supplied.items():
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue
        unit = (specs.get(name) or ParameterRoleSpec(name=name)).semantic_unit
        if unit:
            by_unit.setdefault(unit, []).append(name)
    pairs: list[tuple[str, str]] = []
    for names in by_unit.values():
        ordered = sorted(names)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                pairs.append((first, second))
    return pairs

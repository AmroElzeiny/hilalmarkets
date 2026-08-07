"""Ready-made starting points, built from mechanics the platform really runs.

A beginner opening an empty Builder does not know what a Watch Plan can contain. A
starter answers that by filling the same form they would have filled themselves — it is
not a separate kind of setup and it has no execution path of its own. Applying one
produces exactly the operations the person's own clicks would have produced.

Every starter is checked at import time against the offered catalogue. A starter naming
a mechanic the platform cannot run, or a value that mechanic does not accept, stops the
application from starting rather than shipping a button that fails when pressed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_market_monitor.engine.builder_operations import (
    BuilderActionError,
    _catalog_by_key,
    build_condition,
)


@dataclass(frozen=True, slots=True)
class StarterRule:
    """One rule inside a starting point."""

    mechanic_key: str
    values: dict[str, Any]
    required: bool = True


@dataclass(frozen=True, slots=True)
class BuilderStarter:
    """A named starting point, in the person's own language."""

    key: str
    label: str
    explanation: str
    #: ``monitor`` or ``scanner``. A starter that only makes sense as a one-off market
    #: sweep says so rather than leaving the person to work it out.
    mode: str
    rules: tuple[StarterRule, ...]
    #: ``and`` when every rule must match, ``or`` when any one is enough.
    join: str = "and"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "explanation": self.explanation,
            "mode": self.mode,
            "join": self.join,
            "rules": [
                {
                    "mechanic_key": rule.mechanic_key,
                    "values": dict(rule.values),
                    "required": rule.required,
                }
                for rule in self.rules
            ],
        }


STARTERS: tuple[BuilderStarter, ...] = (
    BuilderStarter(
        key="big_move_up",
        label="A coin jumps",
        explanation="Tells you when a coin rises by an amount you choose inside one candle.",
        mode="monitor",
        rules=(
            StarterRule(
                mechanic_key="open_to_close_percentage",
                values={
                    "direction": "up",
                    "comparator": "gte",
                    "threshold": 5,
                    "timeframe": "1h",
                },
            ),
        ),
    ),
    BuilderStarter(
        key="big_move_down",
        label="A coin drops",
        explanation="Tells you when a coin falls by an amount you choose inside one candle.",
        mode="monitor",
        rules=(
            StarterRule(
                mechanic_key="open_to_close_percentage",
                values={
                    "direction": "down",
                    "comparator": "gte",
                    "threshold": 5,
                    "timeframe": "1h",
                },
            ),
        ),
    ),
    BuilderStarter(
        key="breaks_previous_high",
        label="Price breaks the last candle's high",
        explanation=(
            "Tells you when the price pushes above the high of the candle before it."
        ),
        mode="monitor",
        rules=(
            StarterRule(
                mechanic_key="previous_candle_reference",
                values={"reference_field": "high", "comparator": "gt", "timeframe": "15m"},
            ),
        ),
    ),
    BuilderStarter(
        key="sweep_and_reclaim",
        label="Price dips under the last low, then comes back",
        explanation=(
            "Two things in order: the price drops below the last candle's low, then "
            "closes back above it."
        ),
        mode="monitor",
        rules=(
            StarterRule(
                mechanic_key="sweep_and_reclaim",
                values={
                    "reference_field": "low",
                    "comparator": "is_true",
                    "timeframe": "15m",
                },
            ),
        ),
    ),
    BuilderStarter(
        key="price_reaches_level",
        label="Price reaches a number I choose",
        explanation="Tells you the moment the price reaches an exact level you set.",
        mode="monitor",
        rules=(
            StarterRule(
                mechanic_key="fixed_reference_level",
                values={"comparator": "gte", "threshold": 100, "timeframe": "15m"},
            ),
        ),
    ),
    BuilderStarter(
        key="breaks_recent_range",
        label="Price passes the highest point of recent candles",
        explanation=(
            "Looks back over the last twenty candles and tells you when the price "
            "passes the highest point in that stretch."
        ),
        mode="monitor",
        rules=(
            StarterRule(
                mechanic_key="lookback_reference_level",
                values={
                    "reference_field": "high",
                    "lookback": 20,
                    "comparator": "gt",
                    "timeframe": "1h",
                },
            ),
        ),
    ),
    BuilderStarter(
        key="unusual_trading_value",
        label="Unusually busy trading",
        explanation=(
            "Tells you when the money traded in one candle passes an amount you choose."
        ),
        mode="monitor",
        rules=(
            StarterRule(
                mechanic_key="capability:dollar_volume",
                values={"comparator": "gte", "threshold": 1000000, "timeframe": "1h"},
            ),
        ),
    ),
    BuilderStarter(
        key="scanner_movers",
        label="Show me everything moving now",
        explanation=(
            "Looks across every eligible coin once and lists the ones that moved by "
            "the amount you choose."
        ),
        mode="scanner",
        rules=(
            StarterRule(
                mechanic_key="open_to_close_percentage",
                values={
                    "direction": "up",
                    "comparator": "gte",
                    "threshold": 5,
                    "timeframe": "1d",
                },
            ),
        ),
    ),
    BuilderStarter(
        key="break_with_size",
        label="A break that is also a real move",
        explanation=(
            "Two rules together: the price breaks the last candle's high, and the "
            "candle itself moved at least two percent."
        ),
        mode="monitor",
        join="and",
        rules=(
            StarterRule(
                mechanic_key="previous_candle_reference",
                values={"reference_field": "high", "comparator": "gt", "timeframe": "15m"},
            ),
            StarterRule(
                mechanic_key="open_to_close_percentage",
                values={
                    "direction": "up",
                    "comparator": "gte",
                    "threshold": 2,
                    "timeframe": "15m",
                },
            ),
        ),
    ),
)


def find_starter(key: str) -> BuilderStarter | None:
    return next((item for item in STARTERS if item.key == key), None)


def _guard_starters_are_buildable() -> None:
    """Every starter must name a real mechanic and pass its own form's rules.

    A starter is a button. A button that produces a refusal is worse than no button,
    because the person has no way to tell whether they did something wrong.
    """

    catalog = _catalog_by_key()
    problems: list[str] = []
    seen: set[str] = set()
    for starter in STARTERS:
        if starter.key in seen:
            problems.append(f"{starter.key}: two starters share this name")
        seen.add(starter.key)
        if starter.mode not in {"monitor", "scanner"}:
            problems.append(f"{starter.key}: mode {starter.mode!r} is not a real mode")
        if starter.join not in {"and", "or"}:
            problems.append(f"{starter.key}: join {starter.join!r} is not a real join")
        for rule in starter.rules:
            mechanic = catalog.get(rule.mechanic_key)
            if mechanic is None:
                problems.append(f"{starter.key}: no mechanic named {rule.mechanic_key!r}")
                continue
            if not mechanic.available:
                problems.append(
                    f"{starter.key}: {rule.mechanic_key} is not available "
                    f"({mechanic.unavailable_reason})"
                )
                continue
            try:
                build_condition(
                    mechanic_key=rule.mechanic_key,
                    values=dict(rule.values),
                    source_turn_id="starter_check",
                    required=rule.required,
                )
            except BuilderActionError as exc:
                problems.append(f"{starter.key}: {rule.mechanic_key} refused — {exc.code}")
    if problems:  # pragma: no cover - import-time guard
        raise RuntimeError(
            "starting points must use mechanics the platform runs: " + "; ".join(problems)
        )


_guard_starters_are_buildable()

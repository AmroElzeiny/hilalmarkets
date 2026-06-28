from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LogicParameter:
    name: str
    type: str
    default: Any
    description: str
    minimum: int | float | None = None
    options: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LogicOperatorSpec:
    key: str
    display_name: str
    description: str
    minimum_children: int
    maximum_children: int | None = None
    parameters: tuple[LogicParameter, ...] = ()
    example_sentence: str = ""
    visual_card_sentence: str = ""
    implementation_status: str = "implemented"
    risk_notes: str = ""
    test_cases: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["parameters"] = [parameter.to_dict() for parameter in self.parameters]
        return payload


LOGIC_OPERATORS: tuple[LogicOperatorSpec, ...] = (
    LogicOperatorSpec(
        key="and",
        display_name="All of",
        description="Every required child must pass.",
        minimum_children=1,
        example_sentence="Price is above EMA 200 and volume is above average.",
        visual_card_sentence="All child conditions must pass.",
        test_cases=("all pass", "one required child fails", "optional child fails"),
    ),
    LogicOperatorSpec(
        key="or",
        display_name="Any of",
        description="At least one child branch must pass.",
        minimum_children=1,
        example_sentence="Bullish engulfing or hammer appears.",
        visual_card_sentence="At least one child branch must pass.",
        test_cases=("first branch passes", "second branch passes", "all fail"),
    ),
    LogicOperatorSpec(
        key="not",
        display_name="Not",
        description="The group passes only when its child condition is false.",
        minimum_children=1,
        maximum_children=1,
        example_sentence="Not price below EMA 200.",
        visual_card_sentence="This child must not be true.",
        test_cases=("child passes", "child fails", "child unavailable"),
    ),
    LogicOperatorSpec(
        key="sequence",
        display_name="Sequence / Then",
        description="Children must become true in order within the configured candle gap.",
        minimum_children=2,
        parameters=(
            LogicParameter(
                "max_candles_between",
                "integer",
                5,
                "Maximum closed candles allowed between each step.",
                minimum=1,
            ),
            LogicParameter(
                "same_symbol_required",
                "boolean",
                True,
                "Require all steps to belong to the symbol being evaluated.",
            ),
            LogicParameter(
                "reset_on_failure",
                "boolean",
                True,
                "Restart the sequence when the next step cannot be found in time.",
            ),
        ),
        example_sentence="Liquidity sweep then bullish engulfing within five candles.",
        visual_card_sentence="Children must pass from left to right.",
        test_cases=("ordered events pass", "reversed events fail", "gap exceeded"),
    ),
    LogicOperatorSpec(
        key="within_last",
        display_name="Within last",
        description="The child passed at least once in the most recent N closed candles.",
        minimum_children=1,
        maximum_children=1,
        parameters=(
            LogicParameter(
                "lookback_candles",
                "integer",
                3,
                "Number of recent closed candles to search.",
                minimum=1,
            ),
        ),
        example_sentence="Bullish engulfing happened within the last three candles.",
        visual_card_sentence="The child must have passed recently.",
        test_cases=("event on latest candle", "event inside window", "event before window"),
    ),
    LogicOperatorSpec(
        key="persisted_for",
        display_name="Persisted for",
        description="The child has remained true for N consecutive closed candles.",
        minimum_children=1,
        maximum_children=1,
        parameters=(
            LogicParameter(
                "candles_count",
                "integer",
                3,
                "Required number of consecutive closed candles.",
                minimum=1,
            ),
        ),
        example_sentence="Price stayed above EMA 200 for ten candles.",
        visual_card_sentence="The child must stay true without interruption.",
        test_cases=("all bars pass", "latest bar fails", "middle bar fails"),
    ),
    LogicOperatorSpec(
        key="count_of",
        display_name="Count of",
        description="At least the configured number of child conditions must pass.",
        minimum_children=1,
        parameters=(
            LogicParameter(
                "minimum_pass_count",
                "integer",
                1,
                "Minimum number of passing children.",
                minimum=1,
            ),
        ),
        example_sentence="At least three of five momentum conditions are true.",
        visual_card_sentence="A minimum number of children must pass.",
        test_cases=("minimum met", "minimum missed", "pending child"),
    ),
    LogicOperatorSpec(
        key="cooldown_condition",
        display_name="Cooldown condition",
        description="Blocks a passing setup when the configured trigger scope fired recently.",
        minimum_children=1,
        parameters=(
            LogicParameter(
                "cooldown_minutes",
                "integer",
                120,
                "Minutes before the same scope may pass again.",
                minimum=0,
            ),
            LogicParameter(
                "scope",
                "choice",
                "per_symbol",
                "Cooldown scope.",
                options=("per_symbol", "per_strategy"),
            ),
        ),
        example_sentence="Do not alert the same symbol again for two hours.",
        visual_card_sentence="Children pass only when the cooldown has elapsed.",
        risk_notes="This is alert-fatigue control, not market evidence.",
        test_cases=("no prior trigger", "cooldown active", "cooldown elapsed"),
    ),
    LogicOperatorSpec(
        key="first_time_true",
        display_name="First time true",
        description="Passes only on the transition from false to true.",
        minimum_children=1,
        maximum_children=1,
        example_sentence="RSI crosses above 50 for the first time after being below it.",
        visual_card_sentence="Pass only on the first true candle.",
        test_cases=("false then true", "true then true", "false then false"),
    ),
    LogicOperatorSpec(
        key="changed_state",
        display_name="Changed state",
        description="Passes when the child moves from failed or pending to passed.",
        minimum_children=1,
        maximum_children=1,
        example_sentence="The setup moved from forming to confirmed.",
        visual_card_sentence="Pass only when the child changes into passed.",
        test_cases=("failed to passed", "pending to passed", "passed to passed"),
    ),
    LogicOperatorSpec(
        key="cross_with_confirmation",
        display_name="Cross with confirmation",
        description="A cross must occur and remain confirmed for the configured bars.",
        minimum_children=1,
        maximum_children=1,
        parameters=(
            LogicParameter(
                "confirmation_bars",
                "integer",
                2,
                "Bars that must hold after the cross, including the cross bar.",
                minimum=1,
            ),
        ),
        example_sentence="EMA 20 crosses EMA 50 and remains above for two candles.",
        visual_card_sentence="The cross must hold after it occurs.",
        test_cases=("cross holds", "cross reverses", "no cross"),
    ),
    LogicOperatorSpec(
        key="conditional_branch",
        display_name="If / Otherwise",
        description="Evaluate the second child when the first passes, otherwise the third child.",
        minimum_children=3,
        maximum_children=3,
        example_sentence="If BTC trend is bearish, allow only short setups.",
        visual_card_sentence="IF child one, THEN child two, OTHERWISE child three.",
        risk_notes=(
            "Branch conditions must remain deterministic and symbol context must be explicit."
        ),
        test_cases=("if branch selected", "otherwise branch selected", "condition unavailable"),
    ),
)


def logic_operator_payload() -> list[dict[str, Any]]:
    return [operator.to_dict() for operator in LOGIC_OPERATORS]


def logic_operator_by_key() -> dict[str, LogicOperatorSpec]:
    return {operator.key: operator for operator in LOGIC_OPERATORS}

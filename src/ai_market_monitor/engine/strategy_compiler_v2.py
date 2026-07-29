from __future__ import annotations

import re

from ai_market_monitor.db.models.enums import (
    ConditionType,
    LogicalOperator,
    MarketType,
    TriggerMode,
)
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.strategy_draft_v2 import validate_draft_semantics
from ai_market_monitor.schemas.strategy import (
    AlertPolicy,
    Comparator,
    ConditionGroup,
    ConditionRule,
    Operand,
    OperandKind,
    RiskPolicy,
    StrategyDefinition,
    StrategyDirection,
    UniverseDefinition,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    DraftDirection,
    DraftMode,
    FormulaKind,
    OperandV2,
    StrategyDraftV2,
)


class StrategyV2CompileError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


_FORMULA_RUNTIME_NAME = {
    FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: "open_to_close",
    FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: "close_to_close",
    FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: "reference_to_current",
    FormulaKind.HIGH_TO_LOW_PERCENTAGE: "high_to_low",
    FormulaKind.LOW_TO_HIGH_PERCENTAGE: "low_to_high",
    FormulaKind.PREVIOUS_CANDLE_REFERENCE: "previous_candle",
    FormulaKind.FIXED_REFERENCE_LEVEL: "fixed_reference_level",
    FormulaKind.LOOKBACK_REFERENCE_LEVEL: "lookback_reference_level",
    FormulaKind.CROSS: "cross",
    FormulaKind.SWEEP_AND_RECLAIM: "sweep_and_reclaim",
    FormulaKind.CAPABILITY: "capability",
}


def compile_strategy_draft_v2(draft: StrategyDraftV2) -> StrategyDefinition:
    violations = validate_draft_semantics(draft)
    if violations:
        raise StrategyV2CompileError("semantic_validation_failed", "; ".join(violations))
    if draft.blocking:
        raise StrategyV2CompileError("draft_blocked", "The draft has unresolved requirements.")
    if draft.condition_ast is None:
        raise StrategyV2CompileError("conditions_missing", "At least one condition is required.")

    conditions = _compile_node(draft.condition_ast)
    timeframes = [
        item.trigger_timeframe
        for item in draft.condition_ast.walk()
        if item.node_type == ConditionNodeType.CONDITION and item.trigger_timeframe
    ]
    if not timeframes:
        raise StrategyV2CompileError("timeframe_missing", "A trigger timeframe is required.")
    base_timeframe = timeframes[0]
    supporting = list(
        dict.fromkeys(
            timeframe
            for item in draft.condition_ast.walk()
            for timeframe in [
                *item.context_timeframes,
                *item.confirmation_timeframes,
                *([item.reference_timeframe] if item.reference_timeframe else []),
            ]
            if timeframe != base_timeframe
        )
    )
    direction = {
        DraftDirection.LONG: StrategyDirection.LONG,
        DraftDirection.SHORT: StrategyDirection.SHORT,
        DraftDirection.NEUTRAL: StrategyDirection.BOTH,
    }[_overall_direction(draft.condition_ast)]
    return StrategyDefinition(
        name=draft.name,
        description="Compiled from the authenticated HilalMarkets Setup Chat V2 draft.",
        direction=direction,
        base_timeframe=base_timeframe,
        supporting_timeframes=supporting,
        trigger_mode=(
            TriggerMode.INTRABAR
            if draft.mode == DraftMode.SCANNER
            else TriggerMode.CANDLE_CLOSE
        ),
        universe=UniverseDefinition(
            exchange=draft.market_scope.exchange,
            market_type=MarketType(draft.market_scope.market_type),
            quote_currencies=[draft.market_scope.quote_asset],
            include_symbols=draft.universe.included_symbols,
            exclude_symbols=draft.universe.excluded_symbols,
        ),
        conditions=(
            conditions
            if isinstance(conditions, ConditionGroup)
            else ConditionGroup(
                key="all_conditions",
                operator=LogicalOperator.AND,
                children=[conditions],
            )
        ),
        risk=RiskPolicy(enabled=False),
        alerts=AlertPolicy(
            channels=["web"] if draft.mode == DraftMode.SCANNER else ["telegram", "web"]
        ),
    )


def _compile_node(node: ConditionNodeV2) -> ConditionRule | ConditionGroup:
    if node.node_type != ConditionNodeType.CONDITION:
        operator = {
            ConditionNodeType.AND: LogicalOperator.AND,
            ConditionNodeType.OR: LogicalOperator.OR,
            ConditionNodeType.NOT: LogicalOperator.NOT,
        }[node.node_type]
        return ConditionGroup(
            key=_key(node.node_id),
            operator=operator,
            children=[_compile_node(child) for child in node.children],
        )

    assert node.formula is not None
    assert node.operator is not None
    assert node.trigger_timeframe is not None
    if node.formula == FormulaKind.CAPABILITY:
        return _compile_exact_capability(node)

    operands = [_compile_operand(item) for item in node.operands]
    if not operands:
        operands = [
            Operand(
                kind=OperandKind.MARKET_METRIC,
                name="percentage_change",
                parameters=_formula_parameters(node),
            )
        ]
    elif node.formula != FormulaKind.CAPABILITY:
        operands[0] = operands[0].model_copy(
            update={
                "parameters": {
                    **operands[0].parameters,
                    **_formula_parameters(node),
                }
            }
        )
    left = operands[0]
    if node.operator in {Comparator.IS_TRUE, Comparator.IS_FALSE}:
        right = None
    elif len(operands) > 1:
        right = operands[1]
    elif node.threshold is not None:
        right = Operand(kind=OperandKind.CONSTANT, value=node.threshold)
    else:
        raise StrategyV2CompileError(
            "threshold_missing",
            f"Condition {node.node_id} has no right operand or threshold.",
        )
    return ConditionRule(
        key=_key(node.node_id),
        label=_condition_label(node),
        condition_type=_condition_type(node),
        timeframe=node.trigger_timeframe,
        left=left,
        comparator=node.operator,
        right=right,
        required=node.required,
        resolved_parameters=_formula_parameters(node),
        required_data=["ohlcv"],
        source_fragment=node.source_fragment,
        confidence=1.0,
        ai_interpreted=False,
    )


def _compile_exact_capability(node: ConditionNodeV2) -> ConditionRule:
    assert node.capability_key is not None
    assert node.trigger_timeframe is not None
    resolver = get_capability_index().resolver
    try:
        capability = resolver.get(node.capability_key)
    except KeyError as exc:
        raise StrategyV2CompileError(
            "unsupported_capability",
            f"Capability {node.capability_key!r} is not registered.",
        ) from exc
    if not capability.executable or capability.availability != "available":
        raise StrategyV2CompileError(
            "capability_unavailable",
            f"Capability {node.capability_key!r} is not executable.",
        )
    assert node.operator is not None
    if node.operator.value not in {
        *capability.supported_comparators,
        "is_true",
        "is_false",
    }:
        raise StrategyV2CompileError(
            "capability_contract_mismatch",
            f"Capability {node.capability_key!r} does not support {node.operator.value}.",
        )
    if node.direction.value not in capability.direction_support and not (
        node.direction == DraftDirection.NEUTRAL
        and "neutral" in capability.direction_support
    ):
        raise StrategyV2CompileError(
            "capability_contract_mismatch",
            f"Capability {node.capability_key!r} does not support {node.direction.value}.",
        )
    kind = {
        "indicator": OperandKind.INDICATOR,
        "price": OperandKind.PRICE,
        "market_metric": OperandKind.MARKET_METRIC,
        "price_action": OperandKind.PRICE_ACTION,
        "candle_pattern": OperandKind.CANDLE_PATTERN,
        "risk_metric": OperandKind.RISK_METRIC,
    }.get(capability.operand_kind or "", OperandKind.MARKET_METRIC)
    left = Operand(
        kind=kind,
        name=capability.operand_name or capability.key,
        parameters={
            **capability.default_parameters,
            **{
                key: value
                for operand in node.operands
                for key, value in operand.parameters.items()
            },
        },
    )
    right = (
        None
        if node.operator in {Comparator.IS_TRUE, Comparator.IS_FALSE}
        else Operand(kind=OperandKind.CONSTANT, value=node.threshold)
    )
    if node.operator not in {Comparator.IS_TRUE, Comparator.IS_FALSE} and node.threshold is None:
        raise StrategyV2CompileError(
            "capability_contract_mismatch",
            f"Capability {node.capability_key!r} requires a numerical threshold.",
        )
    return ConditionRule(
        key=_key(node.node_id),
        capability_key=capability.key,
        capability_version=capability.capability_version,
        resolved_parameters=dict(left.parameters),
        label=capability.label,
        condition_type=ConditionType(capability.condition_type),
        timeframe=node.trigger_timeframe,
        left=left,
        comparator=node.operator,
        right=right,
        required=node.required,
        required_data=list(capability.required_data),
        source_fragment=node.source_fragment,
        confidence=1.0,
        ai_interpreted=True,
    )


def _compile_operand(operand: OperandV2) -> Operand:
    kind = {
        "price": OperandKind.PRICE,
        "constant": OperandKind.CONSTANT,
        "market_metric": OperandKind.MARKET_METRIC,
        "indicator": OperandKind.INDICATOR,
        "reference": OperandKind.PRICE_ACTION,
    }[operand.kind]
    return Operand(
        kind=kind,
        field=operand.field,
        name=operand.name,
        value=operand.value,
        parameters=dict(operand.parameters),
    )


def _formula_parameters(
    node: ConditionNodeV2,
) -> dict[str, int | float | str | bool | list[int | float | str | bool]]:
    assert node.formula is not None
    parameters: dict[
        str,
        int | float | str | bool | list[int | float | str | bool],
    ] = {}
    for operand in node.operands:
        parameters.update(operand.parameters)
    parameters.update(
        {
        "formula": _FORMULA_RUNTIME_NAME[node.formula],
        "direction": node.direction.value,
        "unit": node.unit,
        "trigger_timeframe": node.trigger_timeframe or "",
        }
    )
    if node.reference_timeframe:
        parameters["reference_timeframe"] = node.reference_timeframe
    if node.reference_definition:
        parameters["reference_definition"] = node.reference_definition
    return parameters


def _condition_type(node: ConditionNodeV2) -> ConditionType:
    if any(item.kind == "indicator" for item in node.operands):
        return ConditionType.INDICATOR
    return ConditionType.PRICE_ACTION


def _overall_direction(root: ConditionNodeV2) -> DraftDirection:
    directions = {
        item.direction
        for item in root.walk()
        if item.node_type == ConditionNodeType.CONDITION
        and item.direction != DraftDirection.NEUTRAL
    }
    return directions.pop() if len(directions) == 1 else DraftDirection.NEUTRAL


def _key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]", "_", value.casefold()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"condition_{cleaned}"
    return cleaned[:100]


def _condition_label(node: ConditionNodeV2) -> str:
    assert node.formula is not None
    assert node.operator is not None
    threshold = f" {node.threshold:g} {node.unit}" if node.threshold is not None else ""
    return f"{node.formula.value.replace('_', ' ').title()} {node.operator.value}{threshold}"[:240]

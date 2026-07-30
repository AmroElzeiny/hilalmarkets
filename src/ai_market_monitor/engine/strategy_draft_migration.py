from __future__ import annotations

from typing import Any, Literal

from ai_market_monitor.db.models.enums import LogicalOperator
from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    OperandKind,
    StrategyDefinition,
    StrategyDirection,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    DraftMode,
    FormulaKind,
    MarketScopeV2,
    MovementDirection,
    OperandV2,
    StrategyBias,
    StrategyDraftV2,
    StrategyUniverseV2,
    UnsupportedRequirementV2,
)


def migrate_legacy_draft(
    payload: dict[str, Any] | None,
    *,
    setup_mode: str | None,
    unsupported: list[dict[str, Any]] | None = None,
) -> StrategyDraftV2:
    """Read a legacy compiled draft once; V2 remains the only writable state."""

    if not payload:
        return StrategyDraftV2(
            mode=DraftMode.SCANNER if setup_mode == "scanner" else DraftMode.MONITOR
        )
    definition = StrategyDefinition.model_validate(payload)
    root = _migrate_node(definition.conditions)
    strategy_bias = {
        StrategyDirection.LONG: StrategyBias.LONG,
        StrategyDirection.SHORT: StrategyBias.SHORT,
        StrategyDirection.NEUTRAL: StrategyBias.NEUTRAL,
        StrategyDirection.BOTH: StrategyBias.NEUTRAL,
    }[definition.direction]
    root = _apply_strategy_bias(root, strategy_bias)
    blocked = [
        UnsupportedRequirementV2(
            key=str(item.get("code") or f"legacy_unsupported_{index}"),
            source_turn_id="legacy-migration",
            source_fragment=str(item.get("source_fragment") or item.get("message") or "Unknown"),
            missing_contract=str(item.get("message") or "Legacy requirement needs review."),
        )
        for index, item in enumerate(unsupported or [], start=1)
    ]
    blocked.extend(_legacy_group_blocks(definition.conditions))
    blocked.extend(_legacy_condition_blocks(definition.conditions))
    return StrategyDraftV2(
        mode=DraftMode.SCANNER if setup_mode == "scanner" else DraftMode.MONITOR,
        name=definition.name,
        universe=StrategyUniverseV2(
            included_symbols=definition.universe.include_symbols,
            excluded_symbols=definition.universe.exclude_symbols,
        ),
        market_scope=MarketScopeV2(
            exchange=definition.universe.exchange,
            quote_asset=definition.universe.quote_currencies[0],
            market_type=definition.universe.market_type.value,
        ),
        condition_ast=root,
        unsupported_requirements=blocked,
    )


def _migrate_node(node: ConditionRule | ConditionGroup) -> ConditionNodeV2:
    if isinstance(node, ConditionGroup):
        operator = {
            LogicalOperator.AND: ConditionNodeType.AND,
            LogicalOperator.OR: ConditionNodeType.OR,
            LogicalOperator.NOT: ConditionNodeType.NOT,
        }.get(node.operator)
        if operator is None:
            # Preserve the children for review, but block compilation in
            # ``migrate_legacy_draft`` because V2 does not execute this operator.
            return ConditionNodeV2(
                node_id=f"legacy_{node.key}",
                node_type=ConditionNodeType.AND,
                children=[_migrate_node(child) for child in node.children],
            )
        return ConditionNodeV2(
            node_id=node.key,
            node_type=operator,
            children=[_migrate_node(child) for child in node.children],
        )
    parameters = dict(node.resolved_parameters)
    parameters.update(node.left.parameters)
    formula = _formula_kind(parameters.get("formula"), node.capability_key)
    capability_key = node.capability_key
    if formula == FormulaKind.CAPABILITY and not capability_key:
        # Keep the historical node readable, but make it impossible to resolve or
        # execute until a reviewer maps its exact semantic contract.
        capability_key = "legacy_unmapped_condition"
    operands = [_migrate_operand(node.left)]
    if node.right is not None and node.right.kind != OperandKind.CONSTANT:
        operands.append(_migrate_operand(node.right))
    threshold = (
        float(node.right.value)
        if node.right is not None
        and node.right.kind == OperandKind.CONSTANT
        and isinstance(node.right.value, int | float)
        else None
    )
    return ConditionNodeV2(
        node_id=node.key,
        node_type=ConditionNodeType.CONDITION,
        source_turn_id="legacy-migration",
        source_fragment=node.source_fragment or node.label,
        required=node.required,
        movement_direction=_legacy_movement_direction(parameters.get("direction")),
        strategy_bias=StrategyBias.NEUTRAL,
        formula=formula,
        operands=operands,
        operator=node.comparator,
        threshold=threshold,
        unit="percent" if "percent" in str(parameters.get("unit", "")) else "none",
        trigger_timeframe=node.timeframe,
        reference_timeframe=(
            str(parameters["reference_timeframe"])
            if parameters.get("reference_timeframe")
            else None
        ),
        reference_definition=(
            str(parameters["reference_definition"])
            if parameters.get("reference_definition")
            else None
        ),
        capability_key=capability_key,
        capability_version=node.capability_version,
        capability_parameters=dict(parameters),
    )


def _legacy_movement_direction(value: Any) -> MovementDirection:
    lowered = str(value or "").casefold()
    if lowered in {"up", "long", "bullish"}:
        return MovementDirection.UP
    if lowered in {"down", "short", "bearish"}:
        return MovementDirection.DOWN
    return MovementDirection.NEUTRAL


def _apply_strategy_bias(
    node: ConditionNodeV2,
    bias: StrategyBias,
) -> ConditionNodeV2:
    if node.node_type == ConditionNodeType.CONDITION:
        return node.model_copy(update={"strategy_bias": bias})
    return node.model_copy(
        update={
            "children": [
                _apply_strategy_bias(child, bias)
                for child in node.children
            ]
        }
    )


def _migrate_operand(operand: Any) -> OperandV2:
    kinds: dict[
        OperandKind,
        Literal["price", "constant", "market_metric", "indicator", "reference"],
    ] = {
        OperandKind.PRICE: "price",
        OperandKind.CONSTANT: "constant",
        OperandKind.MARKET_METRIC: "market_metric",
        OperandKind.INDICATOR: "indicator",
        OperandKind.PRICE_ACTION: "reference",
        OperandKind.CANDLE_PATTERN: "reference",
        OperandKind.RISK_METRIC: "market_metric",
    }
    kind = kinds[operand.kind]
    return OperandV2(
        role="left",
        kind=kind,
        field=operand.field,
        name=operand.name,
        value=operand.value,
        parameters=dict(operand.parameters),
    )


def _formula_kind(value: Any, capability_key: str | None) -> FormulaKind:
    mapping = {
        "open_to_close": FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
        "close_to_close": FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
        "reference_to_current": FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
        "high_to_low": FormulaKind.HIGH_TO_LOW_PERCENTAGE,
        "low_to_high": FormulaKind.LOW_TO_HIGH_PERCENTAGE,
        "previous_candle": FormulaKind.PREVIOUS_CANDLE_REFERENCE,
        "fixed_reference_level": FormulaKind.FIXED_REFERENCE_LEVEL,
        "lookback_reference_level": FormulaKind.LOOKBACK_REFERENCE_LEVEL,
        "cross": FormulaKind.CROSS,
        "sweep_and_reclaim": FormulaKind.SWEEP_AND_RECLAIM,
    }
    return mapping.get(str(value), FormulaKind.CAPABILITY)


def _legacy_condition_blocks(
    node: ConditionRule | ConditionGroup,
) -> list[UnsupportedRequirementV2]:
    if isinstance(node, ConditionGroup):
        return [
            item
            for child in node.children
            for item in _legacy_condition_blocks(child)
        ]
    parameters = {**node.resolved_parameters, **node.left.parameters}
    formula = str(parameters.get("formula") or "")
    known_formulas = {
        "open_to_close",
        "close_to_close",
        "reference_to_current",
        "high_to_low",
        "low_to_high",
        "previous_candle",
        "fixed_reference_level",
        "lookback_reference_level",
        "cross",
        "sweep_and_reclaim",
    }
    if formula in known_formulas or node.capability_key:
        return []
    return [
        UnsupportedRequirementV2(
            key=f"legacy_condition_{node.key}",
            source_turn_id="legacy-migration",
            source_fragment=node.source_fragment or node.label,
            missing_contract=(
                "This legacy condition has no exact V2 formula or registered "
                "capability contract. Restate it before approval."
            ),
        )
    ]


def _legacy_group_blocks(
    node: ConditionRule | ConditionGroup,
) -> list[UnsupportedRequirementV2]:
    if isinstance(node, ConditionRule):
        return []
    blocked: list[UnsupportedRequirementV2] = []
    if node.operator not in {
        LogicalOperator.AND,
        LogicalOperator.OR,
        LogicalOperator.NOT,
    }:
        blocked.append(
            UnsupportedRequirementV2(
                key=f"legacy_group_{node.key}",
                source_turn_id="legacy-migration",
                source_fragment=f"Legacy {node.operator.value} group {node.key}",
                missing_contract=(
                    f"The launch compiler does not execute {node.operator.value} groups. "
                    "Review and restate this rule with AND, OR, or NOT."
                ),
            )
        )
    for child in node.children:
        blocked.extend(_legacy_group_blocks(child))
    return blocked

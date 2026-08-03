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
    ShariaPolicyDefinition,
    StrategyDefinition,
    StrategyDirection,
    UniverseDefinition,
)
from ai_market_monitor.schemas.strategy_draft_v2 import (
    CapabilityParameterValue,
    ConditionNodeType,
    ConditionNodeV2,
    DraftMode,
    FormulaKind,
    MovementDirection,
    OperandV2,
    StrategyBias,
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
    if draft.authoring_blocking:
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
                *([item.trigger_timeframe] if item.trigger_timeframe else []),
                *item.context_timeframes,
                *item.confirmation_timeframes,
                *([item.reference_timeframe] if item.reference_timeframe else []),
            ]
            if timeframe != base_timeframe
        )
    )
    direction = {
        StrategyBias.LONG: StrategyDirection.LONG,
        StrategyBias.SHORT: StrategyDirection.SHORT,
        StrategyBias.NEUTRAL: StrategyDirection.NEUTRAL,
    }[_overall_bias(draft.condition_ast)]
    definition = StrategyDefinition(
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
            include_symbols=(
                draft.sharia_policy.explicit_symbols
                if draft.sharia_policy.universe_mode.value == "explicit_assets"
                else draft.universe.included_symbols
            ),
            exclude_symbols=draft.universe.excluded_symbols,
            sharia_policy=ShariaPolicyDefinition(
                universe_mode=draft.sharia_policy.universe_mode,
                methodology_id=draft.sharia_policy.methodology_id,
                methodology_version=draft.sharia_policy.methodology_version,
                allowed_statuses=draft.sharia_policy.allowed_statuses,
                qualification_policy=draft.sharia_policy.qualification_policy,
                disputed_asset_policy=draft.sharia_policy.disputed_asset_policy,
                compliance_change_behavior=(
                    draft.sharia_policy.compliance_change_behavior
                ),
                approved_watchlist_id=draft.sharia_policy.approved_watchlist_id,
                approved_watchlist_version=(
                    draft.sharia_policy.approved_watchlist_version
                ),
            ),
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
    equivalence_errors = validate_compiled_equivalence(draft, definition)
    if equivalence_errors:
        raise StrategyV2CompileError(
            "compiled_semantics_mismatch",
            "; ".join(equivalence_errors),
        )
    return definition


def _compile_node(
    node: ConditionNodeV2,
    ast_path: tuple[int, ...] = (),
) -> ConditionRule | ConditionGroup:
    if node.node_type != ConditionNodeType.CONDITION:
        operator = {
            ConditionNodeType.AND: LogicalOperator.AND,
            ConditionNodeType.OR: LogicalOperator.OR,
            ConditionNodeType.NOT: LogicalOperator.NOT,
        }[node.node_type]
        return ConditionGroup(
            key=_key(node.node_id),
            operator=operator,
            children=[
                _compile_node(child, (*ast_path, index))
                for index, child in enumerate(node.children)
            ],
        )

    assert node.formula is not None
    assert node.operator is not None
    assert node.trigger_timeframe is not None
    if node.formula == FormulaKind.CAPABILITY:
        return _compile_exact_capability(node, ast_path)

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
        context_timeframes=node.context_timeframes,
        confirmation_timeframes=node.confirmation_timeframes,
        reference_timeframe=node.reference_timeframe,
        left=left,
        comparator=node.operator,
        right=right,
        source_operands=[_compile_operand(item) for item in node.operands],
        condition_symbols=node.condition_symbols,
        required=node.required,
        resolved_parameters=_formula_parameters(node),
        required_data=["ohlcv"],
        source_turn_id=node.source_turn_id,
        source_fragment=node.source_fragment,
        ast_path=list(ast_path),
        confidence=1.0,
        ai_interpreted=False,
    )


def _compile_exact_capability(
    node: ConditionNodeV2,
    ast_path: tuple[int, ...],
) -> ConditionRule:
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
    if node.movement_direction.value not in capability.direction_support and not (
        node.movement_direction == MovementDirection.NEUTRAL
        and "neutral" in capability.direction_support
    ):
        raise StrategyV2CompileError(
            "capability_contract_mismatch",
            "Capability "
            f"{node.capability_key!r} does not support {node.movement_direction.value}.",
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
            **node.capability_parameters,
            **{
                key: value
                for operand in node.operands
                for key, value in operand.parameters.items()
            },
            **_formula_parameters(node),
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
        context_timeframes=node.context_timeframes,
        confirmation_timeframes=node.confirmation_timeframes,
        reference_timeframe=node.reference_timeframe,
        left=left,
        comparator=node.operator,
        right=right,
        source_operands=[_compile_operand(item) for item in node.operands],
        condition_symbols=node.condition_symbols,
        required=node.required,
        required_data=list(capability.required_data),
        source_turn_id=node.source_turn_id,
        source_fragment=node.source_fragment,
        ast_path=list(ast_path),
        confidence=1.0,
        # Exact capability selection is accepted only after the compiler's full
        # semantic-contract equivalence check. It is deterministic compilation, not
        # a separate AI interpretation for the user to accept invisibly.
        ai_interpreted=False,
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
) -> dict[str, CapabilityParameterValue]:
    assert node.formula is not None
    parameters: dict[str, CapabilityParameterValue] = {}
    for operand in node.operands:
        parameters.update(operand.parameters)
    parameters.update(
        {
            "formula": _FORMULA_RUNTIME_NAME[node.formula],
            "direction": (
                node.movement_direction.value
                if node.movement_direction != MovementDirection.NEUTRAL
                else "signed"
            ),
            "movement_direction": node.movement_direction.value,
            "strategy_bias": node.strategy_bias.value,
            "unit": node.unit,
            "trigger_timeframe": node.trigger_timeframe or "",
            "context_timeframes": list(node.context_timeframes),
            "confirmation_timeframes": list(node.confirmation_timeframes),
        }
    )
    if node.reference_timeframe:
        parameters["reference_timeframe"] = node.reference_timeframe
    if node.reference_definition:
        parameters["reference_definition"] = node.reference_definition
    if node.lookback is not None:
        parameters["lookback"] = node.lookback
    return parameters


def _condition_type(node: ConditionNodeV2) -> ConditionType:
    if any(item.kind == "indicator" for item in node.operands):
        return ConditionType.INDICATOR
    return ConditionType.PRICE_ACTION


def _overall_bias(root: ConditionNodeV2) -> StrategyBias:
    directions = {
        item.strategy_bias
        for item in root.walk()
        if item.node_type == ConditionNodeType.CONDITION
        and item.strategy_bias != StrategyBias.NEUTRAL
    }
    return directions.pop() if len(directions) == 1 else StrategyBias.NEUTRAL


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


def validate_compiled_equivalence(
    draft: StrategyDraftV2,
    definition: StrategyDefinition,
) -> list[str]:
    """Prove that compilation preserved the executable draft exactly."""

    errors: list[str] = []
    if draft.condition_ast is None:
        return ["conditions_missing"]

    expected_direction = {
        StrategyBias.LONG: StrategyDirection.LONG,
        StrategyBias.SHORT: StrategyDirection.SHORT,
        StrategyBias.NEUTRAL: StrategyDirection.NEUTRAL,
    }[_overall_bias(draft.condition_ast)]
    if definition.direction != expected_direction:
        errors.append(
            f"direction:{expected_direction.value}:{definition.direction.value}"
        )

    if definition.universe.exchange != draft.market_scope.exchange:
        errors.append("exchange")
    if definition.universe.market_type.value != draft.market_scope.market_type:
        errors.append("market_type")
    if definition.universe.quote_currencies != [draft.market_scope.quote_asset]:
        errors.append("quote_asset")
    expected_included = (
        draft.sharia_policy.explicit_symbols
        if draft.sharia_policy.universe_mode.value == "explicit_assets"
        else draft.universe.included_symbols
    )
    if definition.universe.include_symbols != expected_included:
        errors.append("included_symbols")
    if definition.universe.exclude_symbols != draft.universe.excluded_symbols:
        errors.append("excluded_symbols")
    expected_policy = ShariaPolicyDefinition(
        universe_mode=draft.sharia_policy.universe_mode,
        methodology_id=draft.sharia_policy.methodology_id,
        methodology_version=draft.sharia_policy.methodology_version,
        allowed_statuses=draft.sharia_policy.allowed_statuses,
        qualification_policy=draft.sharia_policy.qualification_policy,
        disputed_asset_policy=draft.sharia_policy.disputed_asset_policy,
        compliance_change_behavior=draft.sharia_policy.compliance_change_behavior,
        approved_watchlist_id=draft.sharia_policy.approved_watchlist_id,
        approved_watchlist_version=draft.sharia_policy.approved_watchlist_version,
    )
    if definition.universe.sharia_policy != expected_policy:
        errors.append("sharia_policy")

    trigger_timeframes = [
        item.trigger_timeframe
        for item in draft.condition_ast.walk()
        if item.node_type == ConditionNodeType.CONDITION and item.trigger_timeframe
    ]
    if not trigger_timeframes:
        errors.append("trigger_timeframes")
    else:
        expected_supporting = list(
            dict.fromkeys(
                timeframe
                for item in draft.condition_ast.walk()
                for timeframe in [
                    *([item.trigger_timeframe] if item.trigger_timeframe else []),
                    *item.context_timeframes,
                    *item.confirmation_timeframes,
                    *([item.reference_timeframe] if item.reference_timeframe else []),
                ]
                if timeframe != trigger_timeframes[0]
            )
        )
        if definition.base_timeframe != trigger_timeframes[0]:
            errors.append("base_timeframe")
        if definition.supporting_timeframes != expected_supporting:
            errors.append("supporting_timeframes")

    compiled_root: ConditionRule | ConditionGroup = definition.conditions
    if (
        draft.condition_ast.node_type == ConditionNodeType.CONDITION
        and isinstance(compiled_root, ConditionGroup)
        and compiled_root.key == "all_conditions"
        and len(compiled_root.children) == 1
    ):
        compiled_root = compiled_root.children[0]
    errors.extend(_node_equivalence_errors(draft.condition_ast, compiled_root))
    return list(dict.fromkeys(errors))


def _node_equivalence_errors(
    draft_node: ConditionNodeV2,
    compiled_node: ConditionRule | ConditionGroup,
    ast_path: tuple[int, ...] = (),
) -> list[str]:
    errors: list[str] = []
    if compiled_node.key != _key(draft_node.node_id):
        errors.append(f"node_key:{draft_node.node_id}")

    if draft_node.node_type != ConditionNodeType.CONDITION:
        if not isinstance(compiled_node, ConditionGroup):
            return [*errors, f"node_type:{draft_node.node_id}"]
        expected_operator = {
            ConditionNodeType.AND: LogicalOperator.AND,
            ConditionNodeType.OR: LogicalOperator.OR,
            ConditionNodeType.NOT: LogicalOperator.NOT,
        }[draft_node.node_type]
        if compiled_node.operator != expected_operator:
            errors.append(f"group_operator:{draft_node.node_id}")
        if len(compiled_node.children) != len(draft_node.children):
            return [*errors, f"group_children:{draft_node.node_id}"]
        for index, (source_child, compiled_child) in enumerate(
            zip(
                draft_node.children,
                compiled_node.children,
                strict=True,
            )
        ):
            errors.extend(
                _node_equivalence_errors(
                    source_child,
                    compiled_child,
                    (*ast_path, index),
                )
            )
        return errors

    if not isinstance(compiled_node, ConditionRule):
        return [*errors, f"node_type:{draft_node.node_id}"]
    if compiled_node.timeframe != draft_node.trigger_timeframe:
        errors.append(f"trigger_timeframe:{draft_node.node_id}")
    if compiled_node.context_timeframes != draft_node.context_timeframes:
        errors.append(f"context_timeframes:{draft_node.node_id}")
    if compiled_node.confirmation_timeframes != draft_node.confirmation_timeframes:
        errors.append(f"confirmation_timeframes:{draft_node.node_id}")
    if compiled_node.reference_timeframe != draft_node.reference_timeframe:
        errors.append(f"reference_timeframe_field:{draft_node.node_id}")
    if compiled_node.comparator != draft_node.operator:
        errors.append(f"operator:{draft_node.node_id}")
    if compiled_node.required != draft_node.required:
        errors.append(f"required:{draft_node.node_id}")
    if compiled_node.source_fragment != draft_node.source_fragment:
        errors.append(f"provenance:{draft_node.node_id}")
    if compiled_node.source_turn_id != draft_node.source_turn_id:
        errors.append(f"source_turn_id:{draft_node.node_id}")
    if compiled_node.ast_path != list(ast_path):
        errors.append(f"ast_path:{draft_node.node_id}")
    if compiled_node.source_operands != [
        _compile_operand(item) for item in draft_node.operands
    ]:
        errors.append(f"operands:{draft_node.node_id}")
    if compiled_node.condition_symbols != draft_node.condition_symbols:
        errors.append(f"condition_symbols:{draft_node.node_id}")
    actual_threshold = (
        compiled_node.right.value
        if compiled_node.right is not None
        and compiled_node.right.kind == OperandKind.CONSTANT
        else None
    )
    if actual_threshold != draft_node.threshold:
        errors.append(f"threshold:{draft_node.node_id}")
    if draft_node.formula is not None:
        expected_formula = _FORMULA_RUNTIME_NAME[draft_node.formula]
        if compiled_node.resolved_parameters.get("formula") != expected_formula:
            errors.append(f"formula:{draft_node.node_id}")
    if (
        compiled_node.resolved_parameters.get("movement_direction")
        != draft_node.movement_direction.value
    ):
        errors.append(f"movement_direction:{draft_node.node_id}")
    if (
        compiled_node.resolved_parameters.get("strategy_bias")
        != draft_node.strategy_bias.value
    ):
        errors.append(f"strategy_bias:{draft_node.node_id}")
    if compiled_node.resolved_parameters.get("unit") != draft_node.unit:
        errors.append(f"unit:{draft_node.node_id}")
    if (
        compiled_node.resolved_parameters.get("reference_timeframe")
        != draft_node.reference_timeframe
    ):
        errors.append(f"reference_timeframe:{draft_node.node_id}")
    if (
        compiled_node.resolved_parameters.get("reference_definition")
        != draft_node.reference_definition
    ):
        errors.append(f"reference_definition:{draft_node.node_id}")
    if (
        draft_node.lookback is not None
        and compiled_node.resolved_parameters.get("lookback") != draft_node.lookback
    ):
        errors.append(f"lookback:{draft_node.node_id}")
    if draft_node.formula == FormulaKind.CAPABILITY:
        if compiled_node.capability_key != draft_node.capability_key:
            errors.append(f"capability_key:{draft_node.node_id}")
        if compiled_node.capability_version != draft_node.capability_version:
            errors.append(f"capability_version:{draft_node.node_id}")
        expected_parameters = {
            **draft_node.capability_parameters,
            **{
                key: value
                for operand in draft_node.operands
                for key, value in operand.parameters.items()
            },
        }
        if any(
            compiled_node.resolved_parameters.get(key) != value
            for key, value in expected_parameters.items()
        ):
            errors.append(f"capability_parameters:{draft_node.node_id}")
    elif compiled_node.capability_key is not None:
        errors.append(f"unrequested_capability:{draft_node.node_id}")
    return errors

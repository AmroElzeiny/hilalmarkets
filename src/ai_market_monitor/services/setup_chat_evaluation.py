from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from ai_market_monitor.schemas.setup_chat_evaluation import (
    EvaluationApprovalState,
    EvaluationCanvasEdge,
    EvaluationCanvasNode,
    EvaluationCanvasPayload,
    EvaluationCondition,
    EvaluationConditionGroup,
    SetupChatEvaluationContract,
)
from ai_market_monitor.schemas.strategy import (
    ConditionGroup,
    ConditionRule,
    OperandKind,
    StrategyDefinition,
)


def build_setup_chat_evaluation_contract(
    strategy: StrategyDefinition,
    *,
    session_status: str,
    approval_eligible: bool,
    assumptions: Iterable[str],
    confidence: Iterable[dict[str, Any]],
    unsupported_capabilities: Iterable[dict[str, Any]],
    strategy_id: UUID | None = None,
    strategy_version_id: UUID | None = None,
    strategy_version_number: int | None = None,
    immutable_version_hash: str | None = None,
) -> SetupChatEvaluationContract:
    """Project a validated strategy into a stable evaluator and Canvas contract."""

    canonical_hash = strategy.canonical_hash()
    groups: list[EvaluationConditionGroup] = []
    conditions: list[EvaluationCondition] = []
    canvas_nodes: list[EvaluationCanvasNode] = []
    canvas_edges: list[EvaluationCanvasEdge] = []
    _walk_group(
        strategy.conditions,
        path="strategy.conditions",
        parent_group_key=None,
        parent_node_id=None,
        groups=groups,
        conditions=conditions,
        canvas_nodes=canvas_nodes,
        canvas_edges=canvas_edges,
    )
    group_nodes = [node for node in canvas_nodes if node.node_type == "group"]
    operators = list(
        dict.fromkeys(
            [
                *(group.operator for group in groups),
                *(condition.comparator for condition in conditions),
            ]
        )
    )
    thresholds = [
        condition.threshold for condition in conditions if condition.threshold is not None
    ]
    filters = [condition for condition in conditions if condition.condition_type == "market_filter"]
    provider_required = [
        condition
        for condition in conditions
        if condition.provider_required or condition.availability != "available"
    ]
    timeframes = list(
        dict.fromkeys(
            [
                strategy.base_timeframe,
                *strategy.supporting_timeframes,
                *(condition.timeframe for condition in conditions),
            ]
        )
    )
    return SetupChatEvaluationContract(
        strategy=strategy,
        canonical_hash=canonical_hash,
        symbols=list(strategy.universe.include_symbols),
        exclusions=list(strategy.universe.exclude_symbols),
        direction=strategy.direction.value,
        timeframes=timeframes,
        operators=operators,
        thresholds=thresholds,
        condition_groups=groups,
        conditions=conditions,
        filters=filters,
        alerts=strategy.alerts.model_dump(mode="json"),
        assumptions=list(assumptions),
        confidence=[dict(item) for item in confidence],
        unsupported_capabilities=[dict(item) for item in unsupported_capabilities],
        provider_required_capabilities=provider_required,
        approval=EvaluationApprovalState(
            session_status=session_status,
            eligible=approval_eligible,
            approved=strategy_version_id is not None,
            schema_hash=canonical_hash,
            strategy_id=strategy_id,
            strategy_version_id=strategy_version_id,
            strategy_version_number=strategy_version_number,
            immutable_version_hash=immutable_version_hash,
        ),
        canvas=EvaluationCanvasPayload(
            root_node_id=_node_id(strategy.conditions),
            nodes=canvas_nodes,
            groups=group_nodes,
            edges=canvas_edges,
        ),
    )


def _walk_group(
    group: ConditionGroup,
    *,
    path: str,
    parent_group_key: str | None,
    parent_node_id: str | None,
    groups: list[EvaluationConditionGroup],
    conditions: list[EvaluationCondition],
    canvas_nodes: list[EvaluationCanvasNode],
    canvas_edges: list[EvaluationCanvasEdge],
) -> None:
    group_node_id = _node_id(group)
    groups.append(
        EvaluationConditionGroup(
            key=group.key,
            path=path,
            parent_group_key=parent_group_key,
            operator=group.operator.value,
            child_keys=[child.key for child in group.children],
        )
    )
    canvas_nodes.append(
        EvaluationCanvasNode(
            id=group_node_id,
            key=group.key,
            node_type="group",
            label=group.key.replace("_", " ").title(),
            parent_id=parent_node_id,
            operator=group.operator.value,
        )
    )
    if parent_node_id is not None:
        canvas_edges.append(
            EvaluationCanvasEdge(
                id=f"{parent_node_id}->{group_node_id}",
                source=parent_node_id,
                target=group_node_id,
                order=len(canvas_edges),
            )
        )
    for index, child in enumerate(group.children):
        child_path = f"{path}.children.{index}"
        if isinstance(child, ConditionGroup):
            _walk_group(
                child,
                path=child_path,
                parent_group_key=group.key,
                parent_node_id=group_node_id,
                groups=groups,
                conditions=conditions,
                canvas_nodes=canvas_nodes,
                canvas_edges=canvas_edges,
            )
            continue
        condition = _condition_contract(child, path=child_path, parent_group_key=group.key)
        conditions.append(condition)
        condition_node_id = _node_id(child)
        canvas_nodes.append(
            EvaluationCanvasNode(
                id=condition_node_id,
                key=child.key,
                node_type="condition",
                label=child.label,
                parent_id=group_node_id,
                required=child.required,
                capability_key=child.capability_key,
            )
        )
        canvas_edges.append(
            EvaluationCanvasEdge(
                id=f"{group_node_id}->{condition_node_id}",
                source=group_node_id,
                target=condition_node_id,
                order=len(canvas_edges),
            )
        )


def _condition_contract(
    condition: ConditionRule,
    *,
    path: str,
    parent_group_key: str,
) -> EvaluationCondition:
    return EvaluationCondition(
        key=condition.key,
        path=path,
        parent_group_key=parent_group_key,
        label=condition.label,
        condition_type=condition.condition_type.value,
        capability_key=condition.capability_key,
        capability_version=condition.capability_version,
        timeframe=condition.timeframe,
        comparator=condition.comparator.value,
        threshold=_threshold(condition),
        required=condition.required,
        source_fragment=condition.source_fragment,
        confidence=condition.confidence,
        provider_required=condition.provider_required,
        availability=condition.availability,
    )


def _threshold(condition: ConditionRule) -> float | str | bool | dict[str, Any] | None:
    right = condition.right
    if right is None:
        return None
    if right.kind == OperandKind.CONSTANT:
        value = right.value
        return value if isinstance(value, float | str | bool) else None
    return right.model_dump(mode="json", exclude_none=True)


def _node_id(node: ConditionGroup | ConditionRule) -> str:
    return f"{node.node_type}:{node.key}"

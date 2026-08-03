"""Independent golden proof for semantic intent to canonical state."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_market_monitor.engine.capability_shortlist import (
    CapabilityShortlist,
    capability_contract,
)
from ai_market_monitor.engine.planner_intent_compiler import compile_planner_intents
from ai_market_monitor.engine.planner_references import (
    MethodologyReference,
    PlannerReferenceContext,
    SnapshotReference,
    WatchlistReference,
)
from ai_market_monitor.engine.setup_turn_execution import SetupTurnRequest, apply_setup_turn
from ai_market_monitor.engine.strategy_compiler_v2 import validate_compiled_equivalence
from ai_market_monitor.schemas.planner_intent import PlannerIntentEnvelope
from ai_market_monitor.schemas.screening_execution import ScreeningExecutionResult
from ai_market_monitor.schemas.setup_agent import SetupConversationContext
from ai_market_monitor.schemas.setup_authorization import ClarificationContract
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    FormulaKind,
    ProviderRuntimeStatusV2,
    StrategyDraftV2,
    UnresolvedFieldV2,
    UnsupportedRequirementV2,
)

GOLDENS = Path(__file__).parents[1] / "fixtures" / "setup_chat_compact_golden.json"
ACTION_GOLDENS = Path(__file__).parents[1] / "fixtures" / "setup_chat_compact_action_goldens.json"


def _public_operation(operation: Any) -> dict[str, object]:
    row = operation.model_dump(mode="json", exclude_none=True)
    row.pop("operation_id", None)
    row.pop("authorizing_segment_id", None)
    fields = row.get("fields")
    if isinstance(fields, dict):
        row["fields"] = {key: value for key, value in fields.items() if value is not None}
    return row


def _golden_state(outcome: Any) -> dict[str, Any]:
    draft = outcome.draft
    return {
        "final_draft": {
            "mode": draft.mode.value,
            "name": draft.name,
            "included_symbols": draft.universe.included_symbols,
            "excluded_symbols": draft.universe.excluded_symbols,
            "exchange": draft.market_scope.exchange,
            "quote_asset": draft.market_scope.quote_asset,
            "market_type": draft.market_scope.market_type,
            "unsupported": [item.missing_contract for item in draft.unsupported_requirements],
            "approval_intent_received": draft.approval_intent_received,
            "executable_version": draft.executable_version,
            "workflow_revision": draft.workflow_revision,
            "sharia_policy": draft.sharia_policy.model_dump(mode="json"),
        },
        "requirement_states": [
            {
                "semantic_type": item.semantic_type,
                "target_path": item.target_path,
                "normalized_value": item.normalized_value,
                "value_role": item.value_role,
            }
            for item in draft.requirement_states
            if item.explicit
        ],
        "semantic_role_assignments": [
            {
                "semantic_type": item.semantic_type,
                "role": item.role,
                "normalized_value": item.normalized_value,
                "target_path": item.target_path,
            }
            for item in draft.semantic_role_assignments
            if item.explicit
        ],
        "executable_hash": draft.executable_hash,
        "workflow_state_hash": draft.workflow_state_hash,
        "compile_status": outcome.result.compile_status,
        "approval_eligible": outcome.result.approval_eligible,
        "screening_status": outcome.result.screening_status,
        "provider_status": outcome.result.provider_status,
        "preflight_behavior": (
            (
                outcome.result.preflight_manifest.model_dump(mode="json")
                if hasattr(outcome.result.preflight_manifest, "model_dump")
                else outcome.result.preflight_manifest
            )
            if outcome.result.preflight_manifest
            else None
        ),
    }


def _fixture_references(fixture: dict[str, Any]) -> PlannerReferenceContext:
    raw = fixture.get("references") or {}
    return PlannerReferenceContext(
        methodologies=tuple(MethodologyReference(**item) for item in raw.get("methodologies", [])),
        watchlists=tuple(WatchlistReference(**item) for item in raw.get("watchlists", [])),
    )


async def test_independent_literal_action_compiler_matrix() -> None:
    """Literal trader-level fixtures cover every shallow non-condition action family."""

    fixtures = json.loads(ACTION_GOLDENS.read_text(encoding="utf-8"))
    for fixture in fixtures:
        before = StrategyDraftV2.model_validate(fixture["starting_draft"])
        compiled = compile_planner_intents(
            PlannerIntentEnvelope.model_validate(fixture["envelope"]),
            draft=before,
            message=fixture["message"],
            source_turn_id=fixture["source_turn_id"],
            references=_fixture_references(fixture),
        )
        assert [_public_operation(item) for item in compiled.plan.operations] == fixture[
            "expected_operations"
        ], fixture["name"]
        assert (compiled.plan.approval_intent is not None) is fixture.get(
            "expected_approval_intent", False
        )
        outcome = await apply_setup_turn(
            SetupTurnRequest(
                plan=compiled.plan,
                message=fixture["message"],
                draft=before,
                source_turn_id=fixture["source_turn_id"],
                planner_references=_fixture_references(fixture),
            )
        )
        actual_state = _golden_state(outcome)
        if "sharia_policy" not in fixture["expected_state"]["final_draft"]:
            actual_state["final_draft"].pop("sharia_policy")
        assert actual_state == fixture["expected_state"], fixture["name"]


def _condition(
    node_id: str,
    *,
    threshold: float,
    capability_key: str | None = None,
) -> ConditionNodeV2:
    return ConditionNodeV2(
        node_id=node_id,
        node_type=ConditionNodeType.CONDITION,
        source_turn_id="existing-turn",
        source_fragment=(
            f"RSI period 14 is at least {threshold:g} on trigger 15m"
            if capability_key
            else f"open-to-close rises at least {threshold:g} percent on trigger 15m"
        ),
        movement_direction="neutral" if capability_key else "up",
        formula=FormulaKind.CAPABILITY if capability_key else FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
        operator="gte",
        threshold=threshold,
        unit="index" if capability_key else "percent",
        trigger_timeframe="15m",
        capability_key=capability_key,
        capability_version="1.0" if capability_key else None,
        capability_parameters={"period": 14} if capability_key else {},
        operands=(
            []
            if capability_key
            else [
                {
                    "role": "measured_value",
                    "kind": "market_metric",
                    "name": "percentage_change",
                    "unit": "percent",
                    "parameters": {
                        "formula": "open_to_close",
                        "reference_field": "open",
                        "current_field": "close",
                        "lookback": 1,
                        "scale": "percent",
                        "closed_only": True,
                    },
                }
            ]
        ),
    )


def _literal_envelope(
    message: str,
    *,
    intents: list[dict[str, Any]] | None = None,
    answers: list[dict[str, Any]] | None = None,
) -> PlannerIntentEnvelope:
    return PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": message,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                }
            ],
            "semantic_intents": intents or [],
            "clarification_answers": answers or [],
            "questions_to_answer": [],
            "unsupported_intents": [],
            "approval_intent": None,
            "overall_confidence": 0.99,
        }
    )


async def test_literal_partial_update_inherits_capability_identity() -> None:
    existing = _condition("condition_rsi", threshold=30, capability_key="rsi_threshold")
    draft = StrategyDraftV2(condition_ast=existing)
    message = "Make that RSI threshold 35"
    envelope = _literal_envelope(
        message,
        intents=[
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "update_condition",
                    "target_reference": "condition_1",
                    "condition": {"threshold": 35},
                },
            }
        ],
    )
    compiled = compile_planner_intents(
        envelope,
        draft=draft,
        message=message,
        source_turn_id="golden-update-1",
        references=PlannerReferenceContext(condition_ids={"condition_1": existing.node_id}),
    )
    replacement = compiled.plan.operations[0].condition
    assert replacement is not None
    assert replacement.threshold == 35
    assert replacement.capability_key == "rsi_threshold"
    assert replacement.capability_version == "1.0"
    assert replacement.capability_parameters == {"period": 14}
    assert replacement.trigger_timeframe == "15m"
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=draft,
            source_turn_id="golden-update-1",
            planner_references=PlannerReferenceContext(
                condition_ids={"condition_1": existing.node_id}
            ),
            allowed_capability_keys={"rsi_threshold"},
        )
    )
    assert outcome.draft.condition_ast == replacement
    assert (outcome.draft.executable_version, outcome.draft.workflow_revision) == (2, 2)
    assert outcome.draft.executable_hash == (
        "073012e40dd78151339e523737215bbacf06be408390a1f7efd671b5459f7883"
    )
    assert outcome.draft.workflow_state_hash == (
        "70cd290aca2d195ebc8799203ba2745faddf474e2313d00c921304e8f8c3aa32"
    )
    assert [
        (item.target_path, item.provenance_kind)
        for item in outcome.draft.requirement_states
        if item.explicit or item.inherited
    ] == [
        ("condition_ast.condition_rsi.capability_key", "inherited_existing"),
        ("condition_ast.condition_rsi.formula", "inherited_existing"),
        ("condition_ast.condition_rsi.operator", "inherited_existing"),
        ("condition_ast.condition_rsi.threshold", "user_explicit"),
        ("condition_ast.condition_rsi.trigger_timeframe", "inherited_existing"),
        ("condition_ast.condition_rsi.unit", "inherited_existing"),
    ]
    assert [
        (item.role, item.normalized_value, item.explicit, item.inherited)
        for item in outcome.draft.semantic_role_assignments
        if item.explicit or item.inherited
    ] == [
        ("formula", "capability", False, True),
        ("threshold", 35.0, True, False),
        ("trigger", "15m", False, True),
    ]
    assert (
        outcome.result.compile_status,
        outcome.result.approval_eligible,
        outcome.result.screening_status,
        outcome.result.provider_status,
    ) == ("compiled", True, "not_required", "not_required")


async def test_literal_reference_definition_and_lookback_survive_compilation() -> None:
    message = (
        "Add a required rule where close is at least 100 USDT using the highest high "
        "of the previous 20 candles as the reference definition, on trigger 15m "
        "with reference timeframe 1h."
    )
    envelope = _literal_envelope(
        message,
        intents=[
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "add_condition",
                    "condition": {
                        "source_quote": message,
                        "formula_key": "lookback_reference_level",
                        "movement_direction": "neutral",
                        "comparator": "gte",
                        "threshold": 100,
                        "unit": "price",
                        "trigger_timeframe": "15m",
                        "reference_timeframe": "1h",
                        "reference_definition": "highest high of the previous 20 candles",
                        "lookback": 20,
                        "measured_price_field": "close",
                        "required": True,
                    },
                },
            }
        ],
    )
    compiled = compile_planner_intents(
        envelope,
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="golden-reference-1",
    )
    operation = compiled.plan.operations[0]
    assert operation.kind == "add_condition"
    assert operation.condition is not None
    assert operation.condition.reference_definition == (
        "highest high of the previous 20 candles"
    )
    assert operation.condition.lookback == 20
    assert operation.condition.reference_timeframe == "1h"
    assert operation.condition.operands[0].field == "close"

    checked_at = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    screened_definitions: list[Any] = []
    preflight_definitions: list[Any] = []

    async def frozen_screening(definition: Any) -> tuple[ScreeningExecutionResult, None]:
        screened_definitions.append(definition)
        return (
            ScreeningExecutionResult(
                authored_definition=definition,
                resolved_at=checked_at,
                considered_symbols=["BTC/USDT"],
                included_symbols=["BTC/USDT"],
            ),
            None,
        )

    async def frozen_preflight(definition: Any) -> list[ProviderRuntimeStatusV2]:
        preflight_definitions.append(definition)
        return [
            ProviderRuntimeStatusV2(
                provider="frozen-golden-provider",
                capability="ohlcv:BTC/USDT:15m,1h",
                status="available",
                checked_at=checked_at,
            )
        ]

    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id="golden-reference-1",
            screening=frozen_screening,
            runtime_preflight=frozen_preflight,
        )
    )
    node = outcome.draft.condition_ast
    assert node is not None
    assert node.reference_definition == "highest high of the previous 20 candles"
    assert node.lookback == 20
    assert node.reference_timeframe == "1h"
    assert outcome.result.compile_status == "compiled"
    assert outcome.definition is not None
    compiled_node = outcome.definition.conditions.children[0]
    assert compiled_node.resolved_parameters["reference_definition"] == (
        "highest high of the previous 20 candles"
    )
    assert compiled_node.resolved_parameters["lookback"] == 20
    assert compiled_node.resolved_parameters["reference_timeframe"] == "1h"
    assert validate_compiled_equivalence(outcome.draft, outcome.definition) == []
    assert outcome.result.screening_status == "passed"
    assert outcome.result.provider_status == "available"
    assert len(screened_definitions) == 1
    assert screened_definitions[0].universe.include_symbols == []
    assert len(preflight_definitions) == 1
    assert preflight_definitions[0].universe.include_symbols == ["BTC/USDT"]


def _literal_capability_shortlist() -> CapabilityShortlist:
    candidate = capability_contract("rsi_threshold")
    assert candidate is not None
    return CapabilityShortlist(candidates=(candidate,))


async def test_literal_typed_capability_parameters_reach_canonical_execution() -> None:
    message = "Use RSI period 14 at or below 30 on trigger timeframe 1h."
    envelope = _literal_envelope(
        message,
        intents=[
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "add_condition",
                    "condition": {
                        "source_quote": message,
                        "capability_key": "rsi_threshold",
                        "comparator": "lte",
                        "threshold": 30,
                        "unit": "index",
                        "trigger_timeframe": "1h",
                        "capability_parameters": [
                            {"name": "period", "number_value": 14},
                            {"name": "threshold", "number_value": 30},
                            {"name": "timeframe", "string_value": "1h"},
                        ],
                    },
                },
            }
        ],
    )
    shortlist = _literal_capability_shortlist()
    compiled = compile_planner_intents(
        envelope,
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="golden-capability-1",
        shortlist=shortlist,
    )
    operation = compiled.plan.operations[0]
    assert operation.condition is not None
    assert operation.condition.capability_key == "rsi_threshold"
    assert operation.condition.capability_version == "1.0"
    assert operation.condition.capability_parameters == {
        "period": 14,
        "threshold": 30,
        "timeframe": "1h",
    }

    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id="golden-capability-1",
            allowed_capability_keys={"rsi_threshold"},
        )
    )
    node = outcome.draft.condition_ast
    assert node is not None
    assert node.capability_key == "rsi_threshold"
    assert node.capability_version == "1.0"
    assert node.capability_parameters == operation.condition.capability_parameters
    assert outcome.result.compile_status == "compiled"
    assert outcome.result.approval_eligible is True
    assert outcome.definition is not None
    assert validate_compiled_equivalence(outcome.draft, outcome.definition) == []


async def test_literal_boolean_replacement_preserves_offered_owned_nodes() -> None:
    rsi = _condition("condition_rsi", threshold=30, capability_key="rsi_threshold")
    price = _condition("condition_price", threshold=2)
    before = StrategyDraftV2(
        condition_ast=ConditionNodeV2(
            node_id="group_before",
            node_type=ConditionNodeType.AND,
            children=[rsi, price],
        )
    )
    message = "Use the RSI rule or the price rule"
    envelope = _literal_envelope(
        message,
        intents=[
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "replace_boolean_structure",
                    "boolean_structure": {
                        "root_ref": "g1",
                        "condition_leaves": [
                            {
                                "leaf_ref": "l1",
                                "segment_ref": "s1",
                                "condition": {
                                    "target_reference": "condition_1",
                                    "source_quote": "the RSI rule",
                                },
                            },
                            {
                                "leaf_ref": "l2",
                                "segment_ref": "s1",
                                "condition": {
                                    "target_reference": "condition_2",
                                    "source_quote": "the price rule",
                                },
                            },
                        ],
                        "boolean_groups": [
                            {
                                "group_ref": "g1",
                                "operator": "or",
                                "child_refs": ["l1", "l2"],
                                "source_quote": message,
                            }
                        ],
                    },
                },
            }
        ],
    )
    compiled = compile_planner_intents(
        envelope,
        draft=before,
        message=message,
        source_turn_id="golden-boolean-1",
        references=PlannerReferenceContext(
            condition_ids={
                "condition_1": rsi.node_id,
                "condition_2": price.node_id,
            }
        ),
    )
    replacement = compiled.plan.operations[0].condition
    assert replacement is not None
    assert replacement.node_type == ConditionNodeType.OR
    assert [item.node_id for item in replacement.children] == [rsi.node_id, price.node_id]
    for preserved, original in zip(replacement.children, (rsi, price), strict=True):
        assert preserved.source_turn_id == original.source_turn_id
        assert preserved.source_fragment == original.source_fragment
        assert preserved.formula == original.formula
        assert preserved.threshold == original.threshold
        assert preserved.trigger_timeframe == original.trigger_timeframe
        assert preserved.capability_key == original.capability_key
        assert preserved.capability_version == original.capability_version
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=before,
            source_turn_id="golden-boolean-1",
            planner_references=PlannerReferenceContext(
                condition_ids={
                    "condition_1": rsi.node_id,
                    "condition_2": price.node_id,
                }
            ),
            allowed_capability_keys={"rsi_threshold"},
        )
    )
    assert outcome.draft.condition_ast == replacement
    assert (outcome.draft.executable_version, outcome.draft.workflow_revision) == (2, 2)
    assert outcome.draft.executable_hash == (
        "a5d74511e170406097641aac8765e31998d6b2d9de1a4183aa3a33e36bc04e77"
    )
    assert outcome.draft.workflow_state_hash == (
        "73ef8fa7879625d495aadc780d09de6dedbb8ef270be816c8ae1bee3d407a6ce"
    )
    inherited_or_explicit = [
        (item.target_path, item.provenance_kind)
        for item in outcome.draft.requirement_states
        if item.explicit or item.inherited
    ]
    assert inherited_or_explicit == [
        ("condition_ast.boolean_structure", "user_explicit"),
        ("condition_ast.condition_price.formula", "inherited_existing"),
        ("condition_ast.condition_price.movement_direction", "inherited_existing"),
        ("condition_ast.condition_price.operator", "inherited_existing"),
        ("condition_ast.condition_price.threshold", "inherited_existing"),
        ("condition_ast.condition_price.trigger_timeframe", "inherited_existing"),
        ("condition_ast.condition_price.unit", "inherited_existing"),
        ("condition_ast.condition_rsi.capability_key", "inherited_existing"),
        ("condition_ast.condition_rsi.formula", "inherited_existing"),
        ("condition_ast.condition_rsi.operator", "inherited_existing"),
        ("condition_ast.condition_rsi.threshold", "inherited_existing"),
        ("condition_ast.condition_rsi.trigger_timeframe", "inherited_existing"),
        ("condition_ast.condition_rsi.unit", "inherited_existing"),
    ]
    assert outcome.result.compile_status == "compiled"
    assert outcome.result.approval_eligible is True


async def test_literal_condition_removal_reaches_canonical_execution() -> None:
    existing = _condition("condition_price", threshold=2)
    before = StrategyDraftV2(condition_ast=existing)
    message = "Remove the price rule"
    envelope = _literal_envelope(
        message,
        intents=[
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "remove_condition",
                    "target_reference": "condition_1",
                },
            }
        ],
    )
    references = PlannerReferenceContext(
        condition_ids={"condition_1": existing.node_id}
    )
    compiled = compile_planner_intents(
        envelope,
        draft=before,
        message=message,
        source_turn_id="golden-remove-1",
        references=references,
    )
    assert [_public_operation(item) for item in compiled.plan.operations] == [
        {"kind": "remove_condition", "target_condition_id": "condition_price"}
    ]
    assert compiled.plan.segments[0].target_condition_id == "condition_price"

    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=before,
            source_turn_id="golden-remove-1",
            planner_references=references,
        )
    )

    assert outcome.draft.condition_ast is None
    assert (outcome.draft.executable_version, outcome.draft.workflow_revision) == (2, 2)
    assert outcome.draft.executable_hash == (
        "0060ebac51106dba091f8f5056b394f73755d8a069b71eb22e9de95be8a3c914"
    )
    assert outcome.draft.workflow_state_hash == (
        "345a3d1f082d7d2c5f1e2d5e48eab521c7ab5864a74ec64c16f4fb518c21af7a"
    )
    assert [
        (item.semantic_type, item.target_path, item.normalized_value, item.value_role)
        for item in outcome.draft.requirement_states
        if item.explicit
    ] == [
        (
            "condition_membership",
            "condition_ast.condition_price.membership",
            None,
            "condition_removal",
        )
    ]
    assert outcome.result.compile_status == "not_attempted"
    assert outcome.result.approval_eligible is False


async def test_literal_completed_rule_closes_only_matching_unsupported_blocker() -> None:
    before = StrategyDraftV2(
        unsupported_requirements=[
            UnsupportedRequirementV2(
                key="strong-move",
                source_turn_id="earlier-turn",
                source_fragment="Alert on a strong bearish close-to-close move.",
                missing_contract="The strong move needs a threshold and comparator.",
            ),
            UnsupportedRequirementV2(
                key="moon-phase",
                source_turn_id="earlier-turn",
                source_fragment="Also use a moon-phase rule.",
                missing_contract="Moon-phase conditions are unsupported.",
            ),
        ]
    )
    message = (
        "By strong I mean a bearish close-to-close percentage move of at least "
        "7.5% on the 1h trigger timeframe, with 1m as context."
    )
    envelope = _literal_envelope(
        message,
        intents=[
            {
                "segment_ref": "s1",
                "payload": {
                    "action": "add_condition",
                    "condition": {
                        "source_quote": message,
                        "formula_key": "close_to_close_percentage",
                        "movement_direction": "down",
                        "comparator": "gte",
                        "threshold": 7.5,
                        "unit": "percent",
                        "trigger_timeframe": "1h",
                        "context_timeframes": ["1m"],
                    },
                },
            }
        ],
    )
    compiled = compile_planner_intents(
        envelope,
        draft=before,
        message=message,
        source_turn_id="golden-supported-resolution-1",
    )
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=before,
            source_turn_id="golden-supported-resolution-1",
        )
    )

    assert [item.key for item in outcome.draft.unsupported_requirements] == ["moon-phase"]
    resolved = next(
        item
        for item in outcome.result.requirement_assessments
        if item.target_path == "unsupported.strong-move"
    )
    assert resolved.satisfied is True
    assert resolved.authorization_grounded is True
    assert resolved.satisfying_operation_ids == [compiled.plan.operations[0].operation_id]
    # The unrelated moon-phase blocker remains fail-closed.
    assert outcome.result.compile_status == "blocked"
    assert outcome.result.approval_eligible is False


async def test_literal_clarification_and_snapshot_aliases_execute_server_side() -> None:
    answer_message = "Use 15m"
    unresolved = UnresolvedFieldV2(
        unresolved_id="confirm-timeframe",
        source_turn_id="earlier-turn",
        source_fragment="Confirm the trigger timeframe.",
        target_type="condition_field",
        target_field="trigger_timeframe",
        target_condition_id="condition_price",
        expected_answer_schema={"type": "string", "format": "timeframe"},
        question="Confirm the trigger timeframe.",
        reason="The existing trigger timeframe needs explicit confirmation.",
    )
    existing = _condition("condition_price", threshold=2)
    answer_before = StrategyDraftV2(
        condition_ast=existing,
        unresolved_fields=[unresolved],
    )
    answer_envelope = PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": answer_message,
                    "segment_kind": "CLARIFICATION_ANSWER",
                }
            ],
            "semantic_intents": [],
            "clarification_answers": [
                {
                    "segment_ref": "s1",
                    "clarification_ref": "clarification_1",
                    "answer_text": "15m",
                }
            ],
            "questions_to_answer": [],
            "unsupported_intents": [],
            "approval_intent": None,
            "overall_confidence": 0.99,
        }
    )
    answer_references = PlannerReferenceContext(
        clarification_ids={"clarification_1": "confirm-timeframe"}
    )
    answer = compile_planner_intents(
        answer_envelope,
        draft=answer_before,
        message=answer_message,
        source_turn_id="golden-answer-1",
        references=answer_references,
    )
    assert answer.plan.clarification_answers[0].question_id == "confirm-timeframe"
    contract = ClarificationContract(
        question_id="confirm-timeframe",
        question="Confirm the trigger timeframe.",
        reason="The existing trigger timeframe needs explicit confirmation.",
        target_type="condition_field",
        target_field="trigger_timeframe",
        target_condition_id="condition_price",
        expected_answer_schema="a timeframe",
    )
    answer_outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=answer.plan,
            message=answer_message,
            draft=answer_before,
            source_turn_id="golden-answer-1",
            planner_references=answer_references,
            conversation=SetupConversationContext().with_question(contract),
        )
    )
    # An exact no-op confirmation closes workflow state without consuming an
    # executable version.
    assert answer_outcome.draft.unresolved_fields == []
    assert answer_outcome.draft.executable_version == answer_before.executable_version
    assert answer_outcome.draft.workflow_revision == answer_before.workflow_revision + 1
    assert answer_outcome.draft.executable_hash == answer_before.executable_hash

    restore_message = "Restore the offered earlier version"
    original = StrategyDraftV2(
        name="Original setup",
        condition_ast=_condition("condition_original", threshold=2),
        executable_version=2,
        workflow_revision=2,
    )
    changed = StrategyDraftV2(
        name="Changed setup",
        condition_ast=_condition("condition_changed", threshold=3),
        executable_version=3,
        workflow_revision=3,
    )
    restore_references = PlannerReferenceContext(
        snapshots=(
            SnapshotReference(
                reference="snapshot_1",
                snapshot_id="internal-snapshot-id",
                executable_version=original.executable_version,
            ),
        )
    )
    restored = compile_planner_intents(
        _literal_envelope(
            restore_message,
            intents=[
                {
                    "segment_ref": "s1",
                    "payload": {
                        "action": "restore_owned_version",
                        "target_reference": "snapshot_1",
                    },
                }
            ],
        ),
        draft=changed,
        message=restore_message,
        source_turn_id="golden-restore-1",
        references=restore_references,
    )
    operation = restored.plan.operations[0]
    assert operation.target_snapshot_id == "internal-snapshot-id"
    assert operation.target_executable_version == 2
    restore_outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=restored.plan,
            message=restore_message,
            draft=changed,
            source_turn_id="golden-restore-1",
            planner_references=restore_references,
            history=[
                {
                    "snapshot_id": "internal-snapshot-id",
                    "executable_version": original.executable_version,
                    "draft": original.model_dump(mode="json"),
                }
            ],
        )
    )
    assert restore_outcome.draft.name == "Original setup"
    assert restore_outcome.draft.executable_hash == (
        "3e892d3efe654bd1e0206f8725f10d70213c05b26bbd613fd6d8b30a01588adc"
    )
    assert restore_outcome.draft.workflow_state_hash == (
        "e47c8a9ba821bf6e8abe1ba5d924cf7cb834d502f1a0d0fbe7fa303ec26e9742"
    )
    assert (
        restore_outcome.draft.executable_version,
        restore_outcome.draft.workflow_revision,
    ) == (4, 4)
    assert restore_outcome.result.compile_status == "compiled"
    assert restore_outcome.result.approval_eligible is True


async def test_independently_reviewed_compact_golden_fixtures() -> None:
    fixtures = json.loads(GOLDENS.read_text(encoding="utf-8"))
    for fixture in fixtures:
        before = StrategyDraftV2()
        envelope = PlannerIntentEnvelope.model_validate(fixture["envelope"])
        compiled = compile_planner_intents(
            envelope,
            draft=before,
            message=fixture["message"],
            source_turn_id=fixture["source_turn_id"],
        )
        expected = fixture["expected"]
        assert [item.kind for item in compiled.plan.operations] == expected["operation_kinds"]

        outcome = await apply_setup_turn(
            SetupTurnRequest(
                plan=compiled.plan,
                message=fixture["message"],
                draft=before,
                source_turn_id=fixture["source_turn_id"],
            )
        )
        draft = outcome.draft
        node = draft.condition_ast
        assert node is not None
        assert draft.mode.value == expected["mode"]
        assert draft.name == expected["name"]
        assert draft.universe.included_symbols == expected["included_symbols"]
        assert node.node_id == expected["condition_node_id"]
        for field in (
            "formula",
            "movement_direction",
            "strategy_bias",
            "operator",
            "unit",
        ):
            value = getattr(node, field)
            assert (
                getattr(value, "value", value)
                == expected["comparator" if field == "operator" else field]
            )
        assert node.threshold == expected["threshold"]
        for field in (
            "trigger_timeframe",
            "context_timeframes",
            "confirmation_timeframes",
            "reference_timeframe",
        ):
            assert getattr(node, field) == expected[field]
        assert draft.executable_version == expected["executable_version"]
        assert draft.workflow_revision == expected["workflow_revision"]
        assert draft.executable_hash == expected["executable_hash"]
        assert draft.workflow_state_hash == expected["workflow_state_hash"]
        assert [
            {
                "semantic_type": item.semantic_type,
                "target_path": item.target_path,
                "normalized_value": item.normalized_value,
                "value_role": item.value_role,
            }
            for item in draft.requirement_states
            if item.explicit
        ] == expected["requirement_states"]
        assert [
            {
                "semantic_type": item.semantic_type,
                "role": item.role,
                "normalized_value": item.normalized_value,
                "target_path": item.target_path,
            }
            for item in draft.semantic_role_assignments
            if item.explicit
        ] == expected["semantic_role_assignments"]
        assert outcome.result.compile_status == expected["compile_status"]
        assert outcome.result.approval_eligible is expected["approval_eligible"]
        assert outcome.result.screening_status == expected["screening_status"]
        assert outcome.result.provider_status == expected["provider_status"]
        assert outcome.result.final_chat_status == expected["final_chat_status"]
        assert (outcome.result.preflight_manifest or None) == expected["preflight_behavior"]
        assert outcome.definition is not None
        assert validate_compiled_equivalence(draft, outcome.definition) == []

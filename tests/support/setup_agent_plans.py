"""Helpers for building authorised turn plans in tests.

Every mutation now names the segment that authorised it, so tests build
:class:`AuthorizedPatchOperation` values rather than a free-floating patch. These
helpers convert what the deterministic parser produces into that shape, which is also
the closest available stand-in for a well-behaved model.
"""

from __future__ import annotations

from typing import Any

from ai_market_monitor.schemas.setup_agent import (
    SegmentKind,
    SetupAgentTurnPlan,
    StrategyInstructionPlan,
    TurnSegment,
)
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy_draft_v2 import (
    DraftFieldPatch,
    StrategyDraftV2,
    StrategyPatch,
)
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch


def segment(
    message: str,
    text: str,
    kind: SegmentKind,
    *,
    segment_id: str,
    action: bool = False,
    reply: bool = False,
    target: str | None = None,
) -> TurnSegment:
    """One segment quoting ``text`` out of ``message``.

    Offsets are computed here for readability; the server locates spans itself, so a
    wrong offset is not what these tests are about.
    """
    start = message.index(text)
    return TurnSegment(
        segment_id=segment_id,
        exact_source_text=text,
        start_offset=start,
        end_offset=start + len(text),
        kind=kind,
        action_required=action,
        reply_required=reply,
        confidence=0.95,
        target_condition_id=target,
    )


def operations_from_patch(
    patch: StrategyPatch,
    *,
    segment_id: str,
) -> list[AuthorizedPatchOperation]:
    """Split a deterministic patch into one authorised operation per change."""

    operations: list[AuthorizedPatchOperation] = []
    if patch.set_fields != DraftFieldPatch():
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="set_fields",
                fields=patch.set_fields,
            )
        )
    for node in patch.add_conditions:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="add_condition",
                condition=node,
            )
        )
    for update in patch.update_conditions:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="update_condition",
                condition=update.replacement,
                target_condition_id=update.node_id,
            )
        )
    for node_id in patch.remove_conditions:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="remove_condition",
                target_condition_id=node_id,
            )
        )
    if patch.replace_groups is not None:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="replace_groups",
                condition=patch.replace_groups,
            )
        )
    for symbol in patch.add_inclusions:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="add_inclusion",
                symbol=symbol,
            )
        )
    for symbol in patch.add_exclusions:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="add_exclusion",
                symbol=symbol,
            )
        )
    for symbol in patch.remove_inclusions:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="remove_inclusion",
                symbol=symbol,
            )
        )
    for symbol in patch.remove_exclusions:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="remove_exclusion",
                symbol=symbol,
            )
        )
    for item in patch.unsupported_requirements:
        operations.append(
            AuthorizedPatchOperation(
                authorizing_segment_id=segment_id,
                kind="add_unsupported",
                missing_contract=item.missing_contract,
            )
        )
    return operations


def instruction_plan(
    message: str,
    *,
    turn_id: str,
    draft: StrategyDraftV2 | None = None,
    quoted: str | None = None,
    summary: str = "one rule",
) -> SetupAgentTurnPlan | None:
    """A single-instruction plan built from the deterministic parser, or ``None``.

    Returns ``None`` when the parser finds no mechanic, which is how a caller knows the
    turn is conversational.
    """
    text = quoted or message
    patch = deterministic_strategy_patch(draft or StrategyDraftV2(), text, source_turn_id=turn_id)
    if patch is None:
        return None
    return SetupAgentTurnPlan(
        source_turn_id=turn_id,
        segments=[
            segment(
                message,
                text,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
            )
        ],
        operations=operations_from_patch(patch, segment_id="s1"),
        strategy_instructions=[StrategyInstructionPlan(segment_id="s1", intent_summary=summary)],
        overall_confidence=0.95,
    )


def conversation_plan(message: str, *, turn_id: str, kind: SegmentKind) -> SetupAgentTurnPlan:
    """A plan with one non-actionable segment and no operations."""

    return SetupAgentTurnPlan(
        source_turn_id=turn_id,
        segments=[segment(message, message, kind, segment_id="s1", reply=True)],
        overall_confidence=0.99,
    )


def responses_body(text: str) -> dict[str, Any]:
    """A Responses-API payload carrying one structured answer."""

    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 20, "output_tokens": 8},
    }


# ---------------------------------------------------------------------------
# Compact transport adapters used by existing integration tests. Independent literal
# golden fixtures, not this canonical-plan adapter, prove semantic equivalence.
# ---------------------------------------------------------------------------


_PRICE_OPERAND_FORMULAS = {
    "previous_candle_reference",
    "fixed_reference_level",
    "lookback_reference_level",
    "cross",
}


def condition_intent(
    node: Any,
    condition_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The trader-level meaning of one canonical condition node."""

    if node.node_type.value in {"and", "or", "not"}:
        raise ValueError("Boolean groups must use the flat boolean_structure adapter")
    stated: dict[str, Any] = {}
    if condition_refs and node.node_id in condition_refs:
        stated["target_reference"] = condition_refs[node.node_id]
    if node.source_fragment:
        stated["source_quote"] = node.source_fragment
    if node.formula is not None:
        stated["formula_key"] = node.formula.value
    if node.movement_direction.value != "neutral":
        stated["movement_direction"] = node.movement_direction.value
    if node.strategy_bias.value != "neutral":
        stated["strategy_bias"] = node.strategy_bias.value
    if node.operator is not None:
        stated["comparator"] = node.operator.value
    if node.threshold is not None:
        stated["threshold"] = node.threshold
    if node.unit != "none":
        stated["unit"] = node.unit
    if node.trigger_timeframe:
        stated["trigger_timeframe"] = node.trigger_timeframe
    if node.context_timeframes:
        stated["context_timeframes"] = list(node.context_timeframes)
    if node.confirmation_timeframes:
        stated["confirmation_timeframes"] = list(node.confirmation_timeframes)
    if node.reference_timeframe:
        stated["reference_timeframe"] = node.reference_timeframe
    if node.reference_definition:
        stated["reference_definition"] = node.reference_definition
    if node.lookback is not None:
        stated["lookback"] = node.lookback
    if node.capability_key:
        stated["capability_key"] = node.capability_key
        stated["capability_parameters"] = [
            _compact_capability_parameter(name, value)
            for name, value in node.capability_parameters.items()
        ]
    if not node.required:
        stated["required"] = False
    if node.condition_symbols:
        stated["condition_symbols"] = list(node.condition_symbols)
    if node.formula is not None and node.formula.value in _PRICE_OPERAND_FORMULAS:
        left = next((item for item in node.operands if item.kind == "price"), None)
        stated["measured_price_field"] = (left.field if left else None) or "close"
    return stated


def boolean_structure_intent(
    root: Any,
    condition_refs: dict[str, str] | None = None,
    *,
    segment_ref: str = "segment_1",
) -> dict[str, Any]:
    """Render a canonical test tree through the real non-recursive wire contract."""

    leaves: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []

    def visit(node: Any) -> str:
        if node.node_type.value == "condition":
            reference = f"leaf_{len(leaves) + 1}"
            condition = condition_intent(node, condition_refs)
            quote = condition.get("source_quote") or node.source_fragment
            if not quote:
                raise ValueError("a Boolean fixture leaf needs exact source text")
            condition["source_quote"] = quote
            leaves.append(
                {
                    "leaf_ref": reference,
                    "segment_ref": segment_ref,
                    "condition": condition,
                }
            )
            return reference
        child_refs = [visit(child) for child in node.children]
        reference = f"group_{len(groups) + 1}"
        quote = node.source_fragment or " ".join(
            child.source_fragment for child in node.children if child.source_fragment
        )
        groups.append(
            {
                "group_ref": reference,
                "operator": node.node_type.value,
                "child_refs": child_refs,
                "source_quote": quote or node.node_type.value,
            }
        )
        return reference

    root_ref = visit(root)
    return {
        "condition_leaves": leaves,
        "boolean_groups": groups,
        "root_ref": root_ref,
    }


def _compact_capability_parameter(name: str, value: Any) -> dict[str, Any]:
    """Render a canonical parameter through the compact typed planner boundary."""

    item: dict[str, Any] = {"name": name}
    if isinstance(value, bool):
        item["boolean_value"] = value
    elif isinstance(value, int | float):
        item["number_value"] = value
    elif isinstance(value, str):
        item["string_value"] = value
    elif isinstance(value, list):
        if all(isinstance(entry, bool) for entry in value):
            item["boolean_items"] = value
        elif all(isinstance(entry, int | float) and not isinstance(entry, bool) for entry in value):
            item["number_items"] = value
        elif all(isinstance(entry, str) for entry in value):
            item["string_items"] = value
        else:
            raise ValueError(f"unsupported compact parameter list: {name}")
    elif isinstance(value, dict):
        fields: list[dict[str, Any]] = []
        for field_name, field_value in value.items():
            field: dict[str, Any] = {"name": field_name}
            if isinstance(field_value, bool):
                field["boolean_value"] = field_value
            elif isinstance(field_value, int | float):
                field["number_value"] = field_value
            elif isinstance(field_value, str):
                field["string_value"] = field_value
            else:
                raise ValueError(f"unsupported compact object parameter value: {name}.{field_name}")
            fields.append(field)
        item["object_fields"] = fields
    else:
        raise ValueError(f"unsupported compact parameter value: {name}")
    return item


_SYMBOL_ACTION = {
    "add_inclusion": "include_symbol",
    "add_exclusion": "exclude_symbol",
    "remove_inclusion": "remove_included_symbol",
    "remove_exclusion": "remove_excluded_symbol",
}


def planner_envelope_json(envelope: Any) -> str:
    """Serialise an old-style plan envelope as the compact contract on the wire.

    Tests keep composing canonical plans, which reads clearly and exercises the same
    canonical shapes; this converts one at the transport boundary, where the real model's
    answer arrives. One conversion point, so a test cannot accidentally assert against a
    contract the production path no longer speaks.
    """

    import json

    if envelope.plan is None:
        return json.dumps(
            {
                "segments": [
                    {
                        "segment_ref": "segment_1",
                        "exact_source_text": (envelope.direct_reply or "ok")[:200],
                        "segment_kind": SegmentKind.CONVERSATIONAL_CONTEXT.value,
                    }
                ],
                "semantic_intents": [],
                "clarification_answers": [],
                "questions_to_answer": [],
                "unsupported_intents": [],
                "approval_intent": None,
                "overall_confidence": 0.99,
            }
        )
    return json.dumps(_compact_envelope_from_plan(envelope.plan))


def _compact_envelope_from_plan(plan: SetupAgentTurnPlan) -> dict[str, Any]:
    """Test adapter for the real compact wire contract.

    This remains a transport convenience, not semantic-equivalence evidence. Independent
    golden fixtures exercise production compilation without passing through this helper.
    """

    segment_refs = {
        item.segment_id: f"segment_{index + 1}" for index, item in enumerate(plan.segments)
    }
    target_ids = list(
        dict.fromkeys(
            item.target_condition_id for item in plan.operations if item.target_condition_id
        )
    )
    condition_refs = {
        condition_id: f"condition_{index + 1}" for index, condition_id in enumerate(target_ids)
    }
    intents: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    field_actions = {
        "mode": "set_mode",
        "name": "set_name",
        "exchange": "set_exchange",
        "quote_asset": "set_quote_asset",
        "market_type": "set_market_type",
    }
    for operation in plan.operations:
        segment_ref = segment_refs[operation.authorizing_segment_id]
        payload: dict[str, Any] | None = None
        if operation.kind in _SYMBOL_ACTION:
            payload = {
                "action": _SYMBOL_ACTION[operation.kind],
                "symbol": operation.symbol,
            }
        elif operation.kind == "set_fields" and operation.fields is not None:
            for name, value in operation.fields.model_dump(mode="json", exclude_none=True).items():
                action = field_actions[name]
                intents.append(
                    {
                        "segment_ref": segment_ref,
                        "payload": {"action": action, name: value},
                    }
                )
            continue
        elif operation.kind == "add_condition" and operation.condition is not None:
            if operation.condition.node_type.value in {"and", "or", "not"}:
                payload = {
                    "action": "replace_boolean_structure",
                    "boolean_structure": boolean_structure_intent(
                        operation.condition,
                        condition_refs,
                        segment_ref=segment_ref,
                    ),
                }
            else:
                payload = {
                    "action": "add_condition",
                    "condition": condition_intent(operation.condition, condition_refs),
                }
        elif operation.kind == "update_condition" and operation.condition is not None:
            payload = {
                "action": "update_condition",
                "target_reference": condition_refs[operation.target_condition_id],
                "condition": condition_intent(operation.condition),
            }
        elif operation.kind == "remove_condition":
            payload = {
                "action": "remove_condition",
                "target_reference": condition_refs[operation.target_condition_id],
            }
        elif operation.kind == "replace_groups" and operation.condition is not None:
            payload = {
                "action": "replace_boolean_structure",
                "boolean_structure": boolean_structure_intent(
                    operation.condition,
                    condition_refs,
                    segment_ref=segment_ref,
                ),
            }
        elif operation.kind == "restore_snapshot":
            payload = {
                "action": "restore_owned_version",
                "target_reference": "snapshot_1",
            }
        elif operation.kind == "add_unsupported":
            unsupported.append(
                {
                    "segment_ref": segment_ref,
                    "missing_contract": operation.missing_contract,
                }
            )
        if payload is not None:
            intents.append({"segment_ref": segment_ref, "payload": payload})

    question_refs = [
        segment_refs[item.segment_id]
        for item in plan.segments
        if item.kind
        in {
            SegmentKind.USER_QUESTION,
            SegmentKind.EXPLANATION_REQUEST,
            SegmentKind.PRODUCT_QUESTION,
        }
    ]
    if plan.questions_to_answer and not question_refs and plan.segments:
        question_refs = [segment_refs[plan.segments[0].segment_id]]
    return {
        "segments": [
            {
                "segment_ref": segment_refs[item.segment_id],
                "exact_source_text": item.exact_source_text,
                "segment_kind": item.kind.value,
            }
            for item in plan.segments
        ],
        "semantic_intents": intents,
        "clarification_answers": [
            {
                "segment_ref": segment_refs[item.segment_id],
                "clarification_ref": "clarification_1",
                "answer_text": item.answer_text,
            }
            for item in plan.clarification_answers
        ],
        "questions_to_answer": question_refs,
        "unsupported_intents": unsupported,
        "approval_intent": (
            {"segment_ref": segment_refs[plan.approval_intent.segment_id]}
            if plan.approval_intent is not None
            else None
        ),
        "overall_confidence": plan.overall_confidence,
    }

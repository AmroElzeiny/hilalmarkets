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
        strategy_instructions=[
            StrategyInstructionPlan(segment_id="s1", intent_summary=summary)
        ],
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

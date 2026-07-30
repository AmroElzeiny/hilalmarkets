"""The one place a Setup Chat turn can change executable state.

The model reads the turn; this module decides what may happen. Every check here is a
check the model cannot weaken, skip or argue with, and each one exists because its
absence produced a wrong monitor:

* a span that is not in the user's own message is a paraphrase, and compiling a
  paraphrase compiles something nobody wrote
* a capability key outside the server's shortlist is either invented or a nearby
  mechanic standing in for the real one
* a threshold or timeframe that appears nowhere in the turn was supplied by the
  model, not chosen by the trader

The output is :class:`SetupTurnExecutionResult` — the only thing the final reply may
state as fact. Before it existed, replies were assembled from templates around the
compiler, so a confident sentence could describe a change that never landed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from ai_market_monitor.engine.comparators import detect_comparator
from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
)
from ai_market_monitor.engine.strategy_draft_v2 import (
    DraftPatchError,
    apply_strategy_patch,
    validate_draft_semantics,
)
from ai_market_monitor.schemas.setup_agent import (
    ACTIONABLE_SEGMENT_KINDS,
    AppliedInstruction,
    IgnoredSegment,
    SetupAgentTurnPlan,
    SetupConversationContext,
    SetupTurnExecutionResult,
    TurnSegment,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    StrategyDraftV2,
    StrategyPatch,
    UnresolvedFieldV2,
    UnsupportedRequirementV2,
)

ExecutionStatus = Literal["applied", "no_change", "rejected", "blocked", "conversation_only"]
CompileStatus = Literal["compiled", "blocked", "not_attempted", "failed"]
ApprovalStatus = Literal["not_eligible", "eligible", "approved", "invalidated_by_edit"]


class SetupTurnRejected(ValueError):
    """The plan failed a check the model is not allowed to bypass."""

    def __init__(self, code: str, message: str, *, details: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True, slots=True)
class SetupTurnRequest:
    """Everything the tool needs, and nothing it could be talked out of."""

    plan: SetupAgentTurnPlan
    #: The user's message exactly as received. Every actionable span is checked
    #: against this string, not against a normalised copy.
    message: str
    draft: StrategyDraftV2
    source_turn_id: str
    #: Keys the server offered this turn. A plan may name no others.
    allowed_capability_keys: frozenset[str] = frozenset()
    #: Earlier draft snapshots, for a reversion.
    history: list[dict[str, Any]] = field(default_factory=list)
    conversation: SetupConversationContext = field(default_factory=SetupConversationContext)


@dataclass(frozen=True, slots=True)
class SetupTurnOutcome:
    """The execution result plus the new state it produced."""

    result: SetupTurnExecutionResult
    draft: StrategyDraftV2
    conversation: SetupConversationContext
    definition: StrategyDefinition | None
    #: Draft snapshot to append to history when the draft moved on.
    history_snapshot: dict[str, Any] | None = None


def apply_setup_turn(request: SetupTurnRequest) -> SetupTurnOutcome:
    """Validate one turn plan, apply what survives, and report exactly what happened."""

    plan = _locate_spans(request.plan, request.message)
    _verify_actionable_spans(plan, request.message)
    _verify_capability_keys(plan, request.allowed_capability_keys)
    plan = _verify_condition_references(plan, request.draft)

    draft = request.draft
    previous_version = draft.version
    previous_hash = draft.semantic_hash
    applied_instructions: list[AppliedInstruction] = []
    semantic_diff: tuple[str, ...] = ()
    strategy_mutated = False
    history_snapshot: dict[str, Any] | None = None
    safe_errors: list[str] = []

    patch = _patch_with_unsupported(plan, request.source_turn_id)
    if patch is not None:
        _verify_patch_grounding(patch, request.message, request.source_turn_id, request.draft)
        try:
            outcome = apply_strategy_patch(draft, patch, history=request.history)
        except (DraftPatchError, ValidationError) as exc:
            raise SetupTurnRejected(
                "PATCH_REJECTED",
                "That change could not be applied to the current draft.",
                details=(str(exc)[:500],),
            ) from exc
        if outcome.material_change:
            history_snapshot = draft.model_dump(mode="json")
            strategy_mutated = True
        draft = outcome.draft
        semantic_diff = outcome.changed_fields
        applied_instructions = _applied_instructions(plan, outcome.changed_fields, draft)

    answered = [item.question_id for item in plan.clarification_answers if item.resolves_question]
    violations = validate_draft_semantics(draft)
    definition: StrategyDefinition | None = None
    compile_status: CompileStatus = "not_attempted"
    if draft.condition_ast is None:
        compile_status = "not_attempted"
    elif violations or draft.blocking:
        compile_status = "blocked"
    else:
        try:
            definition = compile_strategy_draft_v2(draft)
            compile_status = "compiled"
        except StrategyV2CompileError as exc:
            compile_status = "failed"
            # A compiler refusal is information for the trader, not an internal fault.
            safe_errors.append(_safe_compile_message(exc))

    approval_eligible = compile_status == "compiled" and not draft.blocking
    approval_status: ApprovalStatus = "eligible" if approval_eligible else "not_eligible"
    if plan.approval_intent is not None and strategy_mutated:
        # Approval binds to one exact version and hash. An edit in the same turn moves
        # both, so any approval the user asked for in this turn is already stale.
        approval_status = "invalidated_by_edit"

    status = _status(
        strategy_mutated=strategy_mutated,
        patch_present=patch is not None,
        answered=bool(answered),
        blocked=compile_status in {"blocked", "failed"},
    )
    result = SetupTurnExecutionResult(
        status=status,
        applied=bool(applied_instructions or answered),
        strategy_mutated=strategy_mutated,
        draft_id=draft.draft_id,
        previous_version=previous_version,
        current_version=draft.version,
        previous_semantic_hash=previous_hash,
        current_semantic_hash=draft.semantic_hash,
        semantic_diff=list(semantic_diff),
        applied_instructions=applied_instructions,
        ignored_non_actionable_segments=_ignored_segments(plan),
        answered_questions=answered,
        unresolved_fields=[
            {
                "key": item.key,
                "question": item.question,
                "source_fragment": item.source_fragment,
            }
            for item in draft.unresolved_fields
        ],
        unsupported_requirements=[
            {
                "key": item.key,
                "missing_contract": item.missing_contract,
                "source_fragment": item.source_fragment,
            }
            for item in draft.unsupported_requirements
        ],
        semantic_violations=violations,
        compile_status=compile_status,
        approval_eligible=approval_eligible,
        approval_status=approval_status,
        safe_errors=safe_errors,
        suggested_next_actions=_next_actions(
            draft,
            compile_status=compile_status,
            approval_eligible=approval_eligible,
        ),
    )
    return SetupTurnOutcome(
        result=result,
        draft=draft,
        conversation=_next_conversation(request, plan, result),
        definition=definition,
        history_snapshot=history_snapshot,
    )


def _locate_spans(plan: SetupAgentTurnPlan, message: str) -> SetupAgentTurnPlan:
    """Find every quoted span in the real message and fix its offsets.

    The model supplies the quote; the server supplies the position. A quote that is
    not in the message keeps its offsets untouched so the check below refuses it with
    the segment named.
    """

    located: list[TurnSegment] = []
    cursor = 0
    for segment in plan.segments:
        resolved = segment.located_in(message, search_from=cursor)
        if resolved is None:
            located.append(segment)
            continue
        cursor = resolved.end_offset
        located.append(resolved)
    return plan.model_copy(update={"segments": located})


def _verify_actionable_spans(plan: SetupAgentTurnPlan, message: str) -> None:
    """Every span that can change state must be the user's own words, exactly.

    Quotes are checked against the real message rather than trusted, and actionable
    spans may not overlap: two instructions claiming the same characters means at
    least one of them was invented to justify a change.
    """

    problems: list[str] = []
    spans: list[tuple[int, int, str]] = []
    for segment in plan.segments:
        quoted = message[segment.start_offset : segment.end_offset]
        if quoted != segment.exact_source_text:
            problems.append(
                f"{segment.segment_id}: {segment.exact_source_text[:80]!r} "
                "is not in this message"
            )
            continue
        if segment.kind in ACTIONABLE_SEGMENT_KINDS or segment.action_required:
            spans.append((segment.start_offset, segment.end_offset, segment.segment_id))
    for (start, end, first), (other_start, other_end, second) in zip(
        sorted(spans), sorted(spans)[1:], strict=False
    ):
        if other_start < end:
            problems.append(
                f"{first} and {second} claim overlapping text "
                f"({start}-{end} and {other_start}-{other_end})"
            )
    if problems:
        raise SetupTurnRejected(
            "SPAN_NOT_GROUNDED",
            "Part of that turn could not be matched to your exact words.",
            details=tuple(problems[:12]),
        )


def _verify_capability_keys(plan: SetupAgentTurnPlan, allowed: frozenset[str]) -> None:
    """A plan may only name a key the server put in front of it this turn."""

    named = {
        item.capability_key
        for item in plan.strategy_instructions
        if item.capability_key is not None
    }
    if plan.strategy_patch is not None:
        for root in _patch_roots(plan.strategy_patch):
            for node in root.walk():
                if node.capability_key:
                    named.add(node.capability_key)
    unknown = sorted(key for key in named if key not in allowed)
    if unknown:
        raise SetupTurnRejected(
            "CAPABILITY_NOT_OFFERED",
            "That request named a mechanic this platform did not offer for this turn.",
            details=tuple(f"capability_key {key!r} was not in the shortlist" for key in unknown),
        )


def _verify_condition_references(
    plan: SetupAgentTurnPlan,
    draft: StrategyDraftV2,
) -> SetupAgentTurnPlan:
    """Edits must name a rule that exists. Hints that do not are dropped, not fatal.

    The distinction matters. ``update_conditions`` and ``remove_conditions`` *change*
    a specific rule, so an id that does not exist means the change would land somewhere
    unintended — refuse it. The ``target_condition_id`` on a segment or an instruction
    is only a pointer for the reply and the trace; a real model sometimes fills it with
    a name for the rule it is *creating*. Failing the whole turn over a label is worse
    for the user than ignoring the label.
    """

    existing = {
        node.node_id
        for node in (draft.condition_ast.walk() if draft.condition_ast else [])
    }
    mutating: set[str] = set()
    if plan.strategy_patch is not None:
        mutating.update(item.node_id for item in plan.strategy_patch.update_conditions)
        mutating.update(plan.strategy_patch.remove_conditions)
    missing = sorted(item for item in mutating if item not in existing)
    if missing:
        raise SetupTurnRejected(
            "CONDITION_NOT_FOUND",
            "That change referred to a rule that is not in the current draft.",
            details=tuple(f"condition {item!r} does not exist" for item in missing),
        )
    return plan.model_copy(
        update={
            "segments": [
                item
                if item.target_condition_id in existing or item.target_condition_id is None
                else item.model_copy(update={"target_condition_id": None})
                for item in plan.segments
            ],
            "strategy_instructions": [
                item
                if item.target_condition_id in existing or item.target_condition_id is None
                else item.model_copy(update={"target_condition_id": None})
                for item in plan.strategy_instructions
            ],
        }
    )


def _patch_with_unsupported(
    plan: SetupAgentTurnPlan,
    source_turn_id: str,
) -> StrategyPatch | None:
    """Fold the plan's unsupported segments into the patch that gets applied.

    A requirement the platform cannot express exactly has to land *in the draft* or it
    cannot block eligibility, and the draft would compile as though the request had
    been fully understood. Recording it in the plan alone was not enough.
    """

    if not plan.unsupported_segments:
        return plan.strategy_patch
    by_id = {segment.segment_id: segment for segment in plan.segments}
    requirements = [
        UnsupportedRequirementV2(
            key=f"unsupported_{item.segment_id}",
            source_turn_id=source_turn_id,
            source_fragment=by_id[item.segment_id].exact_source_text,
            missing_contract=item.missing_contract,
            blocking=item.blocking,
        )
        for item in plan.unsupported_segments
        if item.segment_id in by_id
    ]
    if not requirements:
        return plan.strategy_patch
    if plan.strategy_patch is None:
        return StrategyPatch(
            source_turn_id=source_turn_id,
            unsupported_requirements=requirements,
        )
    return plan.strategy_patch.model_copy(
        update={
            "unsupported_requirements": [
                *plan.strategy_patch.unsupported_requirements,
                *requirements,
            ]
        }
    )


def _verify_patch_grounding(
    patch: StrategyPatch,
    message: str,
    source_turn_id: str,
    draft: StrategyDraftV2,
) -> None:
    """Every value in the patch must come from this turn, or from the rule it edits.

    New conditions are held to the full grounding bar: a threshold or timeframe the
    message never contains was chosen by the model, not by the trader.

    An *edit* is different. ``change that to at least 8%`` names one existing rule and
    changes one field; the timeframe it keeps was grounded in the turn that created it
    and is not restated. Requiring the whole replacement to appear in this short
    message refused every ordinary correction.
    """

    errors: list[str] = []
    roots = [*patch.add_conditions]
    if patch.replace_groups is not None:
        roots.append(patch.replace_groups)
    for root in roots:
        for node in root.walk():
            if node.node_type == ConditionNodeType.CONDITION:
                errors.extend(_condition_grounding_errors(node, message, source_turn_id))
    evidenced: list[UnresolvedFieldV2 | UnsupportedRequirementV2] = [
        *patch.unresolved_references,
        *patch.unsupported_requirements,
    ]
    for item in evidenced:
        if item.source_turn_id not in {None, source_turn_id}:
            errors.append(f"{item.key}:source_turn")
        if not _quoted_in(item.source_fragment, message):
            errors.append(f"{item.key}:source_fragment")
    existing = {
        node.node_id: node
        for node in (draft.condition_ast.walk() if draft.condition_ast else [])
    }
    for update in patch.update_conditions:
        errors.extend(
            _update_grounding_errors(
                existing.get(update.node_id),
                update.replacement,
                message=message,
                source_turn_id=source_turn_id,
            )
        )
    if errors:
        raise SetupTurnRejected(
            "VALUE_NOT_GROUNDED",
            "A value in that change does not appear in your message.",
            details=tuple(dict.fromkeys(errors))[:12],
        )


#: Fields a correction can change. Each one, when changed, must be visible in the
#: turn that changed it; when unchanged it is inherited from the edited rule.
_GROUNDED_UPDATE_FIELDS = ("threshold", "trigger_timeframe", "operator", "formula", "direction")


def _update_grounding_errors(
    existing: ConditionNodeV2 | None,
    replacement: ConditionNodeV2,
    *,
    message: str,
    source_turn_id: str,
) -> list[str]:
    """Only what the edit *changes* has to appear in the turn that changed it."""

    errors: list[str] = []
    if replacement.node_type != ConditionNodeType.CONDITION:
        for child in replacement.children:
            errors.extend(
                _update_grounding_errors(
                    None,
                    child,
                    message=message,
                    source_turn_id=source_turn_id,
                )
            )
        return errors
    if replacement.source_turn_id != source_turn_id:
        errors.append(f"{replacement.node_id}:source_turn")
    fragment = replacement.source_fragment or ""
    normalized = " ".join(message.split()).casefold()
    if not fragment or " ".join(fragment.split()).casefold() not in normalized:
        errors.append(f"{replacement.node_id}:source_fragment")
        return errors
    if existing is None:
        # Nothing to inherit from, so the full bar applies.
        return [*errors, *_condition_grounding_errors(replacement, message, source_turn_id)]
    for name in _GROUNDED_UPDATE_FIELDS:
        new_value = getattr(replacement, name)
        if new_value == getattr(existing, name):
            continue
        rendered = new_value.value if hasattr(new_value, "value") else new_value
        if rendered is None:
            continue
        if not _mentions(message, rendered):
            errors.append(f"{replacement.node_id}:{name}")
    return errors


def _quoted_in(fragment: str, message: str) -> bool:
    return " ".join((fragment or "").split()).casefold() in " ".join(message.split()).casefold()


def _condition_grounding_errors(
    node: ConditionNodeV2,
    message: str,
    source_turn_id: str,
) -> list[str]:
    """Nothing in a new condition may be absent from the turn that authored it.

    Values are checked against the **whole message**, which is what "grounded in the
    current message" means. Checking each value against the one span the model chose to
    quote was stricter than the contract and refused correct work: in ``on the 15m when
    the candle rises 5%`` a model reasonably quotes ``the candle rises 5%``, and the
    timeframe it correctly read from the same sentence is not inside that quote.

    An operator written *inside* the quote is still compared to the compiled operator,
    because that is where an inversion would show.
    """

    errors: list[str] = []
    if node.source_turn_id != source_turn_id:
        errors.append(f"{node.node_id}:source_turn")
    fragment = node.source_fragment or ""
    if not fragment or not _quoted_in(fragment, message):
        errors.append(f"{node.node_id}:source_fragment")
        return errors
    if node.threshold is not None and not _mentions(message, node.threshold):
        errors.append(f"{node.node_id}:threshold")
    if node.trigger_timeframe is not None and not _mentions(message, node.trigger_timeframe):
        errors.append(f"{node.node_id}:trigger_timeframe")
    for timeframe in (*node.context_timeframes, *node.confirmation_timeframes):
        if not _mentions(message, timeframe):
            errors.append(f"{node.node_id}:supporting_timeframe:{timeframe}")
    stated_operator = detect_comparator(fragment)
    if (
        stated_operator is not None
        and node.operator is not None
        and stated_operator != node.operator
    ):
        errors.append(f"{node.node_id}:operator")
    return errors


def _mentions(message: str, value: Any) -> bool:
    """Is this exact value written in the message?"""

    lowered = message.casefold()
    if isinstance(value, float):
        candidates = {f"{value:g}", f"{value:.1f}", f"{int(value)}" if value.is_integer() else ""}
        return any(item and item in lowered for item in candidates)
    return str(value).casefold() in lowered


def _patch_roots(patch: Any) -> list[ConditionNodeV2]:
    roots = [*patch.add_conditions, *(item.replacement for item in patch.update_conditions)]
    if patch.replace_groups is not None:
        roots.append(patch.replace_groups)
    return roots


def _applied_instructions(
    plan: SetupAgentTurnPlan,
    changed_fields: tuple[str, ...],
    draft: StrategyDraftV2,
) -> list[AppliedInstruction]:
    """What landed, tied back to the words that caused it."""

    if not changed_fields:
        return []
    by_id = {item.segment_id: item for item in plan.segments}
    condition_ids = [
        node.node_id
        for node in (draft.condition_ast.walk() if draft.condition_ast else [])
        if node.node_type == ConditionNodeType.CONDITION
    ]
    applied: list[AppliedInstruction] = []
    for instruction in plan.strategy_instructions:
        segment = by_id.get(instruction.segment_id)
        if segment is None:
            continue
        applied.append(
            AppliedInstruction(
                segment_id=instruction.segment_id,
                source_text=segment.exact_source_text,
                summary=instruction.intent_summary,
                condition_ids=(
                    [instruction.target_condition_id]
                    if instruction.target_condition_id
                    else condition_ids[:8]
                ),
            )
        )
    if applied:
        return applied
    # A patch that changed the universe or the mode carries no per-condition
    # instruction. It still changed something, so it is still reported.
    actionable = plan.actionable_segments
    return [
        AppliedInstruction(
            segment_id=segment.segment_id,
            source_text=segment.exact_source_text,
            summary="; ".join(changed_fields[:6]),
            condition_ids=[],
        )
        for segment in actionable[:4]
    ]


def _ignored_segments(plan: SetupAgentTurnPlan) -> list[IgnoredSegment]:
    """Conversation, questions and refusals — recorded, never compiled."""

    reasons = {
        "SOCIAL_REPLY": "greeting or courtesy, answered in words only",
        "ACKNOWLEDGEMENT_NO_ACTION": "acknowledgement, no rule implied",
        "CONVERSATIONAL_CONTEXT": "background wording, not a measurable rule",
        "USER_QUESTION": "question, answered in words only",
        "EXPLANATION_REQUEST": "explanation request, answered in words only",
        "PRODUCT_QUESTION": "product question, answered in words only",
        "APPROVAL_INTENT": "approval happens only through the Review and approve control",
        "UNSUPPORTED_REQUEST": "no exact supported mechanic expresses this",
    }
    return [
        IgnoredSegment(
            segment_id=segment.segment_id,
            source_text=segment.exact_source_text,
            kind=segment.kind,
            reason=reasons.get(segment.kind.value, "not an executable instruction"),
        )
        for segment in plan.segments
        if segment.kind.value in reasons
    ]


def _status(
    *,
    strategy_mutated: bool,
    patch_present: bool,
    answered: bool,
    blocked: bool,
) -> ExecutionStatus:
    if strategy_mutated and blocked:
        return "blocked"
    if strategy_mutated:
        return "applied"
    if answered:
        return "applied"
    if patch_present:
        return "no_change"
    return "conversation_only"


def _safe_compile_message(exc: StrategyV2CompileError) -> str:
    """A compiler refusal in the trader's terms, with no internal detail."""

    if exc.code == "draft_blocked":
        return "The draft still has an item that must be resolved before it can run."
    if exc.code == "timeframe_missing":
        return "One rule still needs the timeframe it should be measured on."
    if exc.code == "conditions_missing":
        return "The draft has no measurable rule yet."
    if exc.code == "semantic_validation_failed":
        return "One rule does not yet hold together well enough to run."
    return "This draft could not be turned into runnable rules yet."


def _next_actions(
    draft: StrategyDraftV2,
    *,
    compile_status: str,
    approval_eligible: bool,
) -> list[str]:
    if approval_eligible:
        return ["review_and_approve"]
    actions: list[str] = []
    if any(item.blocking for item in draft.unresolved_fields):
        actions.append("answer_open_question")
    if any(item.blocking for item in draft.unsupported_requirements):
        actions.append("restate_unsupported_requirement")
    if compile_status == "not_attempted" and draft.condition_ast is None:
        actions.append("describe_one_measurable_rule")
    return actions[:6]


def _next_conversation(
    request: SetupTurnRequest,
    plan: SetupAgentTurnPlan,
    result: SetupTurnExecutionResult,
) -> SetupConversationContext:
    """Carry forward what the next turn needs to resolve ordinary references."""

    context = request.conversation
    if result.answered_questions and context.active_question_id in result.answered_questions:
        context = context.cleared_question()
    if plan.clarifications_to_ask:
        context = context.with_question(plan.clarifications_to_ask[0])
    changed = [
        node_id
        for instruction in result.applied_instructions
        for node_id in instruction.condition_ids
    ]
    references = [
        segment.exact_source_text
        for segment in plan.segments
        if segment.kind in ACTIONABLE_SEGMENT_KINDS
    ]
    return context.model_copy(
        update={
            "last_changed_condition_ids": list(dict.fromkeys(changed))[:24],
            "recent_references": list(
                dict.fromkeys([*references, *context.recent_references])
            )[:12],
        }
    )


def conversation_from_segments(
    context: SetupConversationContext,
    segments: list[TurnSegment],
    *,
    assistant_summary: str | None = None,
) -> SetupConversationContext:
    """Update language context after a turn that changed no executable state."""

    explained = [
        segment.target_condition_id
        for segment in segments
        if segment.target_condition_id
        and segment.kind.value in {"EXPLANATION_REQUEST", "USER_QUESTION"}
    ]
    return context.model_copy(
        update={
            "last_explained_condition_ids": list(dict.fromkeys(explained))[:24],
            "last_assistant_summary": (
                assistant_summary[:1000] if assistant_summary else context.last_assistant_summary
            ),
        }
    )

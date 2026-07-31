"""The one place a Setup Chat turn can change executable state.

The model reads the turn; this module decides what may happen, and it finishes deciding
*before* anyone writes a reply. Each rule here exists because its absence produced a
wrong monitor or a wrong sentence:

* a change with no authorising segment is a change nobody asked for — and grounding a
  value against the whole message let a number written inside a *question* justify a rule
* a capability key outside the server's shortlist is invented or a near-miss standing in
  for the real mechanic
* evidence taken from the model's own summary described an intention, not an outcome, so
  a reply could announce a change the compiler had refused
* screening and provider gates that ran *after* composition let a reply announce a ready
  draft that the platform then blocked
* an approval reset on a turn that changed nothing threw away work the user had already
  signed off

The output is :class:`SetupTurnExecutionResult`: the complete platform result, including
the final chat status, and the only thing the reply may state as fact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, cast, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

from ai_market_monitor.db.models.enums import ShariaUniverseMode
from ai_market_monitor.engine.action_grounding import SemanticAction, action_is_grounded
from ai_market_monitor.engine.capability_contract import (
    capability_condition_errors,
    grounded_operator_and_timeframe,
)
from ai_market_monitor.engine.draft_diff import DraftChange, diff_drafts
from ai_market_monitor.engine.operation_reconciliation import (
    TurnReconciliation,
    reconcile_turn,
)
from ai_market_monitor.engine.operation_target import (
    merged_unresolved_key,
    targets_by_operation_id,
    unsupported_key_for,
)
from ai_market_monitor.engine.semantic_grounding import (
    grounds_boolean_shape,
    grounds_direction,
    grounds_formula,
    grounds_lookback,
    grounds_number,
    grounds_operator,
    grounds_strategy_bias,
    grounds_symbol,
    grounds_text_value,
    grounds_timeframe,
)
from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
)
from ai_market_monitor.engine.strategy_draft_v2 import (
    DraftPatchError,
    apply_strategy_patch,
    finalize_strategy_turn,
    validate_draft_semantics,
)
from ai_market_monitor.schemas.screening_execution import (
    PreflightManifest,
    ScreeningExecutionResult,
)
from ai_market_monitor.schemas.setup_agent import (
    ACTIONABLE_SEGMENT_KINDS,
    AppliedInstruction,
    IgnoredSegment,
    OperationExecutionResult,
    ReconciledOperationRecord,
    SegmentKind,
    SetupAgentTurnPlan,
    SetupConversationContext,
    SetupTurnExecutionResult,
    TurnSegment,
)
from ai_market_monitor.schemas.setup_authorization import (
    ClarificationContract,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionNodeType,
    ConditionNodeV2,
    ConditionUpdateV2,
    DraftFieldPatch,
    DraftMode,
    FormulaKind,
    ProviderRequirementV2,
    ProviderRuntimeStatusV2,
    ReversionV2,
    StrategyDraftV2,
    StrategyPatch,
    UnsupportedRequirementV2,
)

ExecutionStatus = Literal["applied", "no_change", "rejected", "blocked", "conversation_only"]
CompileStatus = Literal["compiled", "blocked", "not_attempted", "failed"]
ScreeningStatus = Literal["passed", "blocked", "not_required", "not_attempted"]
ProviderStatus = Literal[
    "available",
    "unavailable",
    "runtime_unverified",
    "not_required",
]
ApprovalStatus = Literal["not_eligible", "eligible", "approved", "invalidated_by_edit"]

#: How many questions one draft may ask.
MAX_CLARIFICATIONS_PER_DRAFT = 3

#: Segment kinds that can never author a change, however the plan labels them.
#: A boundary refusal, an approval request or a question is answered in words only.
REPLY_ONLY_KINDS: frozenset[SegmentKind] = frozenset(
    {
        SegmentKind.SOCIAL_REPLY,
        SegmentKind.ACKNOWLEDGEMENT_NO_ACTION,
        SegmentKind.CONVERSATIONAL_CONTEXT,
        SegmentKind.USER_QUESTION,
        SegmentKind.EXPLANATION_REQUEST,
        SegmentKind.PRODUCT_QUESTION,
        SegmentKind.APPROVAL_INTENT,
        SegmentKind.UNSUPPORTED_REQUEST,
    }
)


class SetupTurnRejected(ValueError):
    """The plan failed a check the model is not allowed to bypass."""

    def __init__(self, code: str, message: str, *, details: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


#: Applies the Sharia policy and resolves the screened universe.
#:
#: Returns a :class:`ScreeningExecutionResult` whose ``secured_definition`` holds exactly
#: the permitted symbols, or ``None`` plus a plain reason when screening blocks the
#: draft. It used to return the *original* definition, so preflight, the preview and
#: approval all ran against a universe screening had never approved.
ScreeningGate = Callable[
    [StrategyDefinition], Awaitable[tuple[ScreeningExecutionResult | None, str | None]]
]

#: Marks each provider requirement available or not.
ProviderGate = Callable[
    [list[ProviderRequirementV2]], Awaitable[list[ProviderRuntimeStatusV2]]
]
RuntimePreflight = Callable[
    [StrategyDefinition], Awaitable[list[ProviderRuntimeStatusV2]]
]


def _preflight_evidence(request: SetupTurnRequest) -> dict[str, Any]:
    """What the market-data check really covered, including its own content hash.

    The hash is a property rather than a stored field, so it has to be added here: an
    approval that binds to "the preflight the user reviewed" needs a value it can compare,
    and re-deriving it later from a re-serialised payload would defeat the point.
    """

    if request.preflight_manifest is None:
        return {}
    manifest = request.preflight_manifest()
    if manifest is None:
        return {}
    payload = manifest.model_dump(mode="json")
    payload["manifest_hash"] = manifest.manifest_hash
    payload["description"] = manifest.describe()
    return payload


def _provider_status(
    statuses: list[ProviderRuntimeStatusV2],
) -> ProviderStatus:
    if not statuses:
        return "not_required"
    if any(item.status == "unavailable" for item in statuses):
        return "unavailable"
    if any(item.status == "unknown" for item in statuses):
        return "runtime_unverified"
    return "available"


@dataclass(frozen=True, slots=True)
class SetupTurnRequest:
    """Everything the tool needs, and nothing it could be talked out of."""

    plan: SetupAgentTurnPlan
    #: The user's message exactly as received, line breaks and all. Every authorising
    #: span is located in this string, not in a normalised copy.
    message: str
    draft: StrategyDraftV2
    source_turn_id: str
    allowed_capability_keys: frozenset[str] = frozenset()
    history: list[dict[str, Any]] = field(default_factory=list)
    conversation: SetupConversationContext = field(default_factory=SetupConversationContext)
    #: Final gates. Absent in pure unit tests, where their statuses read
    #: ``not_required`` rather than being quietly assumed to have passed.
    screening: ScreeningGate | None = None
    providers: ProviderGate | None = None
    runtime_preflight: RuntimePreflight | None = None
    #: Reads back exactly what the preflight checked, so the promise can be stored and
    #: displayed instead of assumed. Returns ``None`` when no preflight ran this turn —
    #: an absent manifest is reported as absent, never as a passed check.
    preflight_manifest: Callable[[], PreflightManifest | None] | None = None
    #: True only for an allowlisted server-rendered UI control. It bypasses language
    #: grounding, never operation validation, lifecycle gates, or canonical mutation.
    server_owned_option: bool = False


@dataclass(frozen=True, slots=True)
class SetupTurnOutcome:
    """The execution result plus the new state it produced."""

    result: SetupTurnExecutionResult
    draft: StrategyDraftV2
    conversation: SetupConversationContext
    definition: StrategyDefinition | None
    history_snapshot: dict[str, Any] | None = None
    #: True when the turn materially changed the draft, so the caller knows whether to
    #: archive a previous approval. Derived from the net executable change.
    material_change: bool = False
    #: The resolved execution universe, for the approval record and the preview.
    screening: ScreeningExecutionResult | None = None


async def apply_setup_turn(request: SetupTurnRequest) -> SetupTurnOutcome:
    """Validate one turn plan, apply what survives, run every gate, then report."""

    plan = _locate_spans(request.plan, request.message)
    segments = {item.segment_id: item for item in plan.segments}
    _verify_actionable_spans(plan, request.message)
    _verify_capability_keys(plan, request.allowed_capability_keys, request.draft)
    plan = _verify_condition_references(plan, request.draft)
    _verify_authorization(plan, segments, request)

    before = request.draft
    draft = before
    operation_results: list[OperationExecutionResult] = []
    changes: list[DraftChange] = []
    for operation in plan.operations:
        operation_before = draft
        operation_plan = plan.model_copy(
            update={"operations": [operation], "unsupported_segments": []}
        )
        patch = _build_patch(operation_plan, segments, request)
        if patch is None:
            operation_results.append(
                _operation_result(
                    operation,
                    before=operation_before,
                    after=operation_before,
                    changes=[],
                    applied=False,
                )
            )
            continue
        try:
            outcome = apply_strategy_patch(
                operation_before,
                patch,
                history=request.history,
                finalize_versions=False,
            )
        except (DraftPatchError, ValidationError) as exc:
            raise SetupTurnRejected(
                "PATCH_REJECTED",
                "That change could not be applied to the current draft.",
                details=(f"{operation.operation_id}:{str(exc)[:450]}",),
            ) from exc
        draft = outcome.draft
        operation_changes = diff_drafts(operation_before, draft)
        changes.extend(operation_changes)
        operation_results.append(
            _operation_result(
                operation,
                before=operation_before,
                after=draft,
                changes=operation_changes,
                applied=bool(operation_changes),
            )
        )

    finalized = finalize_strategy_turn(before, draft)
    draft = finalized.draft
    _verify_resolved_targets(
        plan,
        before,
        draft,
        server_owned_option=request.server_owned_option,
    )
    strategy_mutated = draft.executable_hash != before.executable_hash
    workflow_mutated = draft.workflow_state_hash != before.workflow_state_hash
    changes = diff_drafts(before, draft)
    material = strategy_mutated
    history_snapshot = before.model_dump(mode="json") if strategy_mutated else None

    answered = _resolved_questions(plan, before, draft, request.conversation)
    violations = validate_draft_semantics(draft)
    safe_errors: list[str] = []

    # --- Every gate, before anything is composed. -------------------------------
    capability_errors, provider_requirements = _capability_gate(
        plan,
        segments,
        before,
        draft,
        request,
    )
    violations.extend(capability_errors)

    definition: StrategyDefinition | None = None
    compile_status: CompileStatus = "not_attempted"
    if draft.condition_ast is None:
        compile_status = "not_attempted"
    elif violations or draft.authoring_blocking:
        compile_status = "blocked"
    else:
        try:
            definition = compile_strategy_draft_v2(draft)
            compile_status = "compiled"
        except StrategyV2CompileError as exc:
            compile_status = "failed"
            safe_errors.append(_safe_compile_message(exc))

    screening_status: ScreeningStatus = "not_required"
    screening_result: ScreeningExecutionResult | None = None
    #: What the market-data check runs against. Identical to `definition` until screening
    #: resolves a universe, after which it carries the permitted markets and `definition`
    #: keeps the authored policy.
    preflight_definition: StrategyDefinition | None = definition
    if definition is not None and request.screening is not None:
        screening_status = "not_attempted"
        screening_result, reason = await request.screening(definition)
        if screening_result is None:
            screening_status = "blocked"
            definition = None
            preflight_definition = None
            safe_errors.append(reason or "Choose and validate a Halal Market first.")
        else:
            screening_status = "passed"
            # Two objects, deliberately not one.
            #
            # `definition` stays **authored** — it is what gets persisted, hashed and
            # approved, and what the runtime re-resolves its universe from every cycle.
            # Overwriting it with the resolved symbols pinned an `eligible_market`
            # monitor to whatever happened to be eligible the day it was approved.
            #
            # `preflight_definition` carries the resolved symbols and exists only for
            # the data check. It is never persisted and never compiled.
            definition = screening_result.authored_definition
            preflight_definition = screening_result.preflight_definition

    provider_status: ProviderStatus = "not_required"
    resolved_provider_statuses: list[ProviderRuntimeStatusV2] = []
    if provider_requirements:
        resolved_provider_statuses = (
            await request.providers(provider_requirements)
            if request.providers is not None
            else [
                ProviderRuntimeStatusV2(
                    provider=item.provider,
                    capability=item.capability,
                    status="unknown",
                )
                for item in provider_requirements
            ]
        )
        provider_status = _provider_status(resolved_provider_statuses)
        if provider_status == "unavailable":
            definition = None
            preflight_definition = None
            safe_errors.append(
                "One rule needs a data feed this account cannot use yet, so the draft "
                "cannot run."
            )
        elif provider_status == "runtime_unverified":
            safe_errors.append(
                "The rule compiles, but its market-data runtime could not be verified "
                "yet. Retry before approval."
            )
    if preflight_definition is not None and request.runtime_preflight is not None:
        preflight_statuses = await request.runtime_preflight(preflight_definition)
        resolved_provider_statuses.extend(preflight_statuses)
        provider_status = _provider_status(resolved_provider_statuses)
        if provider_status == "unavailable":
            definition = None
            preflight_definition = None
            safe_errors.append(
                "The selected exchange, symbols, or timeframes are unavailable from "
                "the configured market-data provider."
            )
        elif provider_status == "runtime_unverified":
            safe_errors.append(
                "The strategy is compile-ready, but exchange runtime availability "
                "could not be verified yet."
            )
    draft = StrategyDraftV2.model_validate(
        draft.model_copy(
            update={
                "runtime_state": draft.runtime_state.model_copy(
                    update={"provider_status": resolved_provider_statuses}
                ),
                "executable_hash": "",
                "workflow_state_hash": "",
            }
        ).model_dump(mode="json")
    )

    approval_eligible = (
        compile_status == "compiled"
        and definition is not None
        and screening_status in {"passed", "not_required"}
        and provider_status in {"available", "not_required"}
        and not draft.blocking
        and not violations
    )
    approval_status = _approval_status(
        draft,
        strategy_mutated=strategy_mutated,
        material=material,
        approval_eligible=approval_eligible,
        approval_requested=plan.approval_intent is not None,
    )
    final_chat_status = _final_chat_status(
        draft,
        approval_status=approval_status,
        approval_eligible=approval_eligible,
    )
    _assert_lifecycle(
        before=before,
        after=draft,
        material=material,
        approval_status=approval_status,
        final_chat_status=final_chat_status,
    )

    # Judge every operation against the turn's *final* state before any of it becomes
    # user-facing evidence. A step undone later in the same turn is not something a
    # reply may claim happened, and its condition ids must not become references.
    #
    # The targets are passed with it: without them two operations of one kind — "add BTC
    # and ETH" — each collect both changes, and the evidence for one names the other.
    reconciliation = reconcile_turn(
        before,
        draft,
        operation_results,
        targets_by_operation_id(list(plan.operations), before),
    )
    applied_instructions = _applied_instructions(plan, segments, reconciliation)
    allowed = _allowed_clarifications(draft, request.conversation, answered)
    result = SetupTurnExecutionResult(
        status=_status(
            state_mutated=strategy_mutated or workflow_mutated,
            patch_present=bool(plan.operations),
            answered=bool(answered),
            blocked=compile_status in {"blocked", "failed"}
            or screening_status == "blocked"
            or provider_status in {"unavailable", "runtime_unverified"},
        ),
        applied=bool(applied_instructions or answered),
        strategy_mutated=strategy_mutated,
        draft_id=draft.draft_id,
        previous_executable_version=before.executable_version,
        current_executable_version=draft.executable_version,
        previous_workflow_revision=before.workflow_revision,
        current_workflow_revision=draft.workflow_revision,
        previous_executable_hash=before.executable_hash,
        current_executable_hash=draft.executable_hash,
        # The net difference between the draft as the turn started and the draft that
        # survived it. The per-operation diffs are kept in `operation_results` for audit;
        # they are a sequence of intermediate states and must not be read as what changed.
        semantic_diff=[change.kind for change in reconciliation.net_changes],
        applied_instructions=applied_instructions,
        operation_results=operation_results,
        reconciled_operations=[
            ReconciledOperationRecord(
                operation_id=item.operation_id,
                authorizing_segment_id=item.authorizing_segment_id,
                operation_kind=item.operation_kind,
                net_effect=item.net_effect,
                final_condition_ids=list(item.final_condition_ids),
                summary="; ".join(change.describe() for change in item.net_changes)[:400],
                changes=[change.to_dict() for change in item.net_changes],
                targets=item.serialised_targets(),
                superseded_by=item.superseded_by,
                safe_error=item.safe_error,
            )
            for item in reconciliation.operations
        ],
        ignored_non_actionable_segments=_ignored_segments(plan),
        answered_questions=answered,
        unresolved_fields=[
            {"key": item.key, "question": item.question, "source_fragment": item.source_fragment}
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
        screening_status=screening_status,
        provider_status=provider_status,
        final_chat_status=final_chat_status,
        approval_eligible=approval_eligible,
        approval_status=approval_status,
        safe_errors=safe_errors,
        suggested_next_actions=_next_actions(
            draft,
            compile_status=compile_status,
            approval_eligible=approval_eligible,
        ),
        allowed_clarifications=allowed,
        draft_read_model=draft_read_model(draft, list(reconciliation.net_changes)),
        screening_evidence=(
            screening_result.evidence() if screening_result is not None else {}
        ),
        preflight_manifest=_preflight_evidence(request),
    )
    return SetupTurnOutcome(
        result=result,
        draft=draft,
        conversation=_next_conversation(request, plan, result, answered, reconciliation),
        definition=definition,
        history_snapshot=history_snapshot,
        # Approval invalidation follows the *net* executable change, never a temporary
        # intermediate state inside the turn.
        material_change=material and reconciliation.executable_changed,
        screening=screening_result,
    )


# --------------------------------------------------------------------------------
# Authorization: who permitted this change, and were the values in their words?
# --------------------------------------------------------------------------------


def _verify_capability_keys(
    plan: SetupAgentTurnPlan,
    allowed: frozenset[str],
    draft: StrategyDraftV2,
) -> None:
    """A plan may only name a key the server put in front of it this turn.

    Checked at plan level, not only on nodes that reach the draft: a key named in
    `strategy_instructions` is a claim about what the platform supports, and an invented
    one has to be refused even when it produced no node.
    """
    existing = _existing_conditions(draft)
    named: set[str] = set()
    for item in plan.strategy_instructions:
        key = item.capability_key
        if key is None:
            continue
        inherited = existing.get(item.target_condition_id or "")
        if inherited is None or inherited.capability_key != key:
            named.add(key)
    for operation in plan.operations:
        if operation.condition is None:
            continue
        for node in operation.condition.walk():
            if not node.capability_key:
                continue
            inherited = existing.get(
                operation.target_condition_id or node.node_id
            )
            if (
                operation.kind == "update_condition"
                and inherited is not None
                and inherited.capability_key == node.capability_key
                and inherited.capability_version == node.capability_version
            ):
                continue
            named.add(node.capability_key)
    unknown = sorted(key for key in named if key not in allowed)
    if unknown:
        raise SetupTurnRejected(
            "CAPABILITY_NOT_OFFERED",
            "That request named a mechanic this platform did not offer for this turn.",
            details=tuple(f"capability_key {key!r} was not in the shortlist" for key in unknown),
        )


def _verify_authorization(
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    request: SetupTurnRequest,
) -> None:
    """Each operation's authorising segment must exist and be allowed to act."""

    problems: list[str] = []
    for operation in plan.operations:
        segment = segments.get(operation.authorizing_segment_id)
        if segment is None:
            problems.append(f"{operation.kind}: no segment {operation.authorizing_segment_id}")
            continue
        if segment.kind in REPLY_ONLY_KINDS:
            # A question, a greeting, a boundary refusal or an approval request can
            # never author executable state, whatever the plan claims.
            problems.append(
                f"{operation.kind}: a {segment.kind.value} segment cannot authorize a change"
            )
            continue
        if segment.kind not in ACTIONABLE_SEGMENT_KINDS:
            problems.append(f"{operation.kind}: {segment.kind.value} is not an actionable kind")
    if problems:
        raise SetupTurnRejected(
            "UNAUTHORIZED_OPERATION",
            "Part of that turn tried to change the setup without instructing it.",
            details=tuple(problems[:12]),
        )
    _verify_operation_grounding(plan, segments, request)


def _existing_conditions(draft: StrategyDraftV2) -> dict[str, ConditionNodeV2]:
    if draft.condition_ast is None:
        return {}
    return {node.node_id: node for node in draft.condition_ast.walk()}


def _verify_operation_grounding(
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    request: SetupTurnRequest,
) -> None:
    """Every value must come from the authorising segment, or be inherited.

    Scoped deliberately. Message-wide grounding is not authorization: a threshold the
    user wrote about a different rule, or inside a question, is not permission to change
    this one.
    """

    existing = _existing_conditions(request.draft)
    unresolved = {item.key: item for item in request.draft.unresolved_fields}
    unsupported = {item.key: item for item in request.draft.unsupported_requirements}
    # The unresolved handler judges an open question against the turn's *other*
    # operations, so it needs the plan; every other handler is scoped to one segment.
    ground_unresolved = partial(_ground_unresolved_operation, plan=plan)
    handlers: dict[str, Callable[..., list[str]]] = {
        "set_fields": _ground_set_fields,
        "set_sharia_policy": _ground_sharia_policy,
        "add_condition": _ground_condition_operation,
        "update_condition": _ground_condition_operation,
        "remove_condition": _ground_remove_condition,
        "replace_groups": _ground_condition_operation,
        "add_inclusion": _ground_symbol_operation,
        "add_exclusion": _ground_symbol_operation,
        "remove_inclusion": _ground_symbol_operation,
        "remove_exclusion": _ground_symbol_operation,
        "add_unresolved": ground_unresolved,
        "update_unresolved": ground_unresolved,
        "resolve_unresolved_key": _ground_resolve_unresolved,
        "add_unsupported": _ground_unsupported_operation,
        "remove_unsupported_key": _ground_remove_unsupported,
        "restore_snapshot": _ground_restore_snapshot,
    }
    errors: list[str] = []
    for operation in plan.operations:
        segment = segments[operation.authorizing_segment_id]
        if request.server_owned_option:
            continue
        handler = handlers[operation.kind]
        errors.extend(
            handler(
                operation,
                segment,
                request,
                existing,
                unresolved,
                unsupported,
            )
        )
    if errors:
        raise SetupTurnRejected(
            "VALUE_NOT_GROUNDED",
            "A value in that change does not appear in the words that asked for it.",
            details=tuple(dict.fromkeys(errors))[:12],
        )


def _ground_set_fields(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del request, existing, unresolved, unsupported
    assert operation.fields is not None
    text = segment.exact_source_text.casefold()
    errors: list[str] = []
    for field_name, value in operation.fields.model_dump(exclude_none=True).items():
        rendered = str(value.value if hasattr(value, "value") else value).casefold()
        if field_name == "name":
            grounded = rendered in text
        elif field_name == "market_type":
            grounded = "spot" in text
        elif field_name == "mode":
            grounded = rendered in text
        else:
            grounded = bool(re.search(rf"(?<!\w){re.escape(rendered)}(?!\w)", text))
        if not grounded:
            errors.append(f"{operation.operation_id}:set_fields:{field_name}:not_grounded")
    return errors


def _ground_sharia_policy(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del existing, unresolved, unsupported
    policy = operation.sharia_policy
    if policy is None:
        return [f"{operation.operation_id}:set_sharia_policy:missing"]
    old = request.draft.sharia_policy
    text = segment.exact_source_text
    lowered = text.casefold()
    errors: list[str] = []
    if policy.universe_mode != old.universe_mode:
        mode_words = {
            "eligible_market": (
                "all eligible",
                "eligible market",
                "كل المؤهلة",
                "kol el eligible",
            ),
            "approved_watchlist": (
                "favorites",
                "favourites",
                "watchlist",
                "مفض",
                "mofadala",
            ),
            "explicit_assets": (
                "specific",
                "explicit",
                "محدد",
                "بعين",
                "mo7adad",
            ),
        }[policy.universe_mode.value]
        if not any(word in lowered for word in mode_words):
            errors.append(f"{operation.operation_id}:sharia_policy:universe_mode")
    for field_name in ("methodology_id", "methodology_version"):
        previous = getattr(old, field_name)
        current = getattr(policy, field_name)
        if current == previous:
            continue
        if current is None:
            grounded = action_is_grounded(text, "clear")
        else:
            grounded = str(current).casefold() in lowered
        if not grounded:
            errors.append(f"{operation.operation_id}:sharia_policy:{field_name}")

    status_aliases = {
        "eligible": ("eligible", "مؤهل", "mo2ahal"),
        "eligible_with_qualifications": (
            "eligible with qualifications",
            "qualified eligible",
            "مؤهل بشروط",
            "mo2ahal b shoroot",
        ),
        "disputed": ("disputed", "مختلف عليه", "mokhtalaf 3aleh"),
        "under_review": ("under review", "قيد المراجعة", "ta7t el morag3a"),
        "excluded": ("excluded", "مستبعد", "mostab3ad"),
        "insufficient_information": (
            "insufficient information",
            "معلومات غير كافية",
            "malomat mesh kefaya",
        ),
    }
    old_statuses = {item.value for item in old.allowed_statuses}
    new_statuses = {item.value for item in policy.allowed_statuses}
    for status in new_statuses - old_statuses:
        if not any(alias in lowered for alias in status_aliases[status]):
            errors.append(
                f"{operation.operation_id}:sharia_policy:allowed_statuses:{status}"
            )
    if old_statuses - new_statuses and not (
        action_is_grounded(text, "change") or action_is_grounded(text, "clear")
    ):
        errors.append(
            f"{operation.operation_id}:sharia_policy:allowed_statuses:removal"
        )

    choice_aliases: dict[str, dict[str, tuple[str, ...]]] = {
        "qualification_policy": {
            "include_with_warning": (
                "include with warning",
                "تحذير",
                "warning",
            ),
            "exclude": ("exclude", "استبعد", "estab3ed"),
            "require_acknowledgement": (
                "require acknowledgement",
                "require acknowledgment",
                "تأكيد",
                "ta2keed",
            ),
        },
        "disputed_asset_policy": {
            "exclude": ("exclude", "استبعد", "estab3ed"),
            "include_with_warning": (
                "include with warning",
                "تحذير",
                "warning",
            ),
        },
        "compliance_change_behavior": {
            "pause_asset": ("pause asset", "وقف الأصل", "wa2af el asset"),
            "remove_asset": ("remove asset", "شيل الأصل", "sheel el asset"),
            "pause_monitor_if_any_asset_changes": (
                "pause monitor",
                "وقف المراقب",
                "wa2af el monitor",
            ),
            "notify_only": ("notify only", "تنبيه فقط", "notification bas"),
        },
    }
    for field_name, aliases_by_value in choice_aliases.items():
        previous = getattr(old, field_name)
        current = getattr(policy, field_name)
        if current == previous:
            continue
        rendered = current.value if hasattr(current, "value") else str(current)
        if not any(
            alias in lowered for alias in aliases_by_value[str(rendered)]
        ):
            errors.append(f"{operation.operation_id}:sharia_policy:{field_name}")

    for field_name in ("approved_watchlist_id", "approved_watchlist_version"):
        previous = getattr(old, field_name)
        current = getattr(policy, field_name)
        if current == previous:
            continue
        if current is None:
            grounded = action_is_grounded(text, "clear")
        else:
            grounded = str(current).casefold() in lowered
        if not grounded:
            errors.append(f"{operation.operation_id}:sharia_policy:{field_name}")

    for symbol in set(policy.explicit_symbols) - set(old.explicit_symbols):
        if not grounds_symbol(text, symbol):
            errors.append(f"{operation.operation_id}:sharia_policy:symbol:{symbol}")
    removed_symbols = set(old.explicit_symbols) - set(policy.explicit_symbols)
    if removed_symbols and not action_is_grounded(text, "clear"):
        errors.append(f"{operation.operation_id}:sharia_policy:symbols:removal")
    elif removed_symbols and policy.explicit_symbols:
        for symbol in removed_symbols:
            if not grounds_symbol(text, symbol):
                errors.append(
                    f"{operation.operation_id}:sharia_policy:symbol:{symbol}:removal"
                )
    return errors


def _ground_symbol_operation(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del existing, unresolved, unsupported
    text = segment.exact_source_text
    symbol = operation.symbol or ""
    if not grounds_symbol(text, symbol):
        return [f"{operation.operation_id}:{operation.kind}:{symbol}:not_grounded"]
    action_name = cast(
        SemanticAction,
        {
        "add_inclusion": "include",
        "add_exclusion": "exclude",
        "remove_inclusion": "remove_inclusion",
        "remove_exclusion": "remove_exclusion",
        }[operation.kind],
    )
    action_grounded = action_is_grounded(text, action_name)
    if operation.kind == "add_inclusion" and not action_grounded:
        # A condition scoped to BTC/USDT necessarily includes BTC/USDT. This is
        # allowed only when that same actionable segment also creates the rule.
        action_grounded = any(
            candidate.authorizing_segment_id == operation.authorizing_segment_id
            and candidate.kind in {"add_condition", "replace_groups"}
            for candidate in request.plan.operations
        )
    return [] if action_grounded else [
        f"{operation.operation_id}:{operation.kind}:action_not_grounded"
    ]


def _ground_condition_operation(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del unresolved, unsupported
    assert operation.condition is not None
    text = segment.exact_source_text
    if operation.kind == "update_condition":
        return _update_grounding(
            existing.get(operation.target_condition_id or ""),
            operation.condition,
            text,
            request.source_turn_id,
        )
    errors: list[str] = []
    nodes = operation.condition.walk()
    if operation.condition.node_type != ConditionNodeType.CONDITION:
        shape = _boolean_shape(operation.condition)
        if not grounds_boolean_shape(text, shape):
            errors.append(f"{operation.operation_id}:boolean_shape:not_grounded")
    for node in nodes:
        if node.node_type != ConditionNodeType.CONDITION:
            continue
        previous = existing.get(node.node_id)
        if operation.kind == "replace_groups" and previous == node:
            continue
        errors.extend(
            _update_grounding(previous, node, text, request.source_turn_id)
            if previous is not None
            else _condition_grounding(node, text, request.source_turn_id)
        )
    return errors


def _ground_remove_condition(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del unresolved, unsupported
    target = operation.target_condition_id or ""
    if target not in existing:
        return [f"{operation.operation_id}:remove_condition:target_missing"]
    allowed_references = {
        segment.target_condition_id,
        *request.conversation.last_changed_condition_ids,
        *request.conversation.last_explained_condition_ids,
    }
    if target not in allowed_references:
        return [f"{operation.operation_id}:remove_condition:target_not_resolved_by_server"]
    if not action_is_grounded(segment.exact_source_text, "remove_condition"):
        return [f"{operation.operation_id}:remove_condition:action_not_grounded"]
    return []


#: Which JSON answer types can fill each kind of open target. Read from the target's own
#: declared type where one exists, so the question a user is asked can actually be
#: answered with something the field accepts.
_ANSWER_TYPES_BY_TARGET: dict[str, frozenset[str]] = {
    "universe": frozenset({"string", "array"}),
    "market_scope": frozenset({"string"}),
    "boolean_structure": frozenset({"string", "object", "array"}),
    "condition_creation": frozenset({"string", "object", "array"}),
    "reference_definition": frozenset({"string"}),
    "unsupported_resolution": frozenset({"string", "boolean"}),
}

#: Operation kinds that, when authorised by the same segment, mean that segment already
#: determined the thing the question asks about.
_DETERMINING_KINDS: dict[str, frozenset[str]] = {
    "condition_creation": frozenset({"add_condition", "replace_groups"}),
    "universe": frozenset(
        {"set_sharia_policy", "add_inclusion", "add_exclusion", "remove_inclusion"}
    ),
    "market_scope": frozenset({"set_fields", "set_sharia_policy"}),
    "draft_field": frozenset({"set_fields"}),
    "boolean_structure": frozenset({"replace_groups"}),
}


def _ground_unresolved_operation(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
    *,
    plan: SetupAgentTurnPlan | None = None,
) -> list[str]:
    """An open question is valid when the *target* is missing, not when the user hedged.

    This used to require an uncertainty word — `maybe`, `which`, `not sure` — before it
    would let a question be opened. Real incomplete intent rarely carries one:

        alert me on a strong move
        watch for a large volume spike
        notify me when BTC breaks support
        make the first rule stricter

    Each of those states a strategy intent and leaves a required value undetermined, and
    each was refused, so the turn either failed or a number had to be invented for it.
    Worse, the same word list decided the question in every language the product serves,
    so an Arabic turn that hedged perfectly clearly did not match and was refused too.

    The test is now typed and structural, and follows the five criteria in order:

    1. the segment expresses a strategy intent — it is an actionable segment;
    2. the target is one the executable actually needs;
    3. the target is absent or underdetermined, both in this turn's own operations and
       in the draft as it stands;
    4. the answer schema can hold what the target accepts;
    5. no equivalent target is already open.

    Ambiguity wording survives only as an optional signal, used in one place: it lets a
    user *reopen* a slot that already holds a value. It never permits a question by
    itself and never blocks one.
    """
    del unsupported
    item = operation.unresolved
    if item is None or not _quoted_in(item.source_fragment, segment.exact_source_text):
        return [f"{operation.operation_id}:{operation.kind}:source_not_grounded"]
    if operation.kind == "update_unresolved" and operation.target_key not in unresolved:
        return [f"{operation.operation_id}:update_unresolved:target_missing"]

    # (1) intent. A question can only come out of a segment that asked for something.
    if segment.kind not in ACTIONABLE_SEGMENT_KINDS:
        return [f"{operation.operation_id}:{operation.kind}:segment_states_no_intent"]

    # (5) the same target may not already be open under a different key.
    duplicate = next(
        (
            key
            for key, open_item in unresolved.items()
            if key != operation.target_key
            and getattr(open_item, "target_type", None) == item.target_type
            and getattr(open_item, "target_field", None) == item.target_field
            and getattr(open_item, "target_condition_id", None) == item.target_condition_id
        ),
        None,
    )
    if duplicate is not None:
        return [f"{operation.operation_id}:{operation.kind}:duplicate_target:{duplicate}"]

    # (3a) the same segment must not have determined this target in this very turn.
    if _turn_determines_target(plan, segment.segment_id, item):
        return [f"{operation.operation_id}:{operation.kind}:determined_by_this_turn"]

    # (3b) and the draft must not already hold it — unless the turn reopens it.
    if _draft_determines_target(item, request.draft, existing) and not _source_is_ambiguous(
        item.source_fragment
    ):
        return [f"{operation.operation_id}:{operation.kind}:target_already_determined"]

    if item.target_type in {
        "condition_field",
        "capability_parameter",
        "reference_definition",
    } and (item.target_condition_id or "") not in existing:
        return [f"{operation.operation_id}:{operation.kind}:condition_target_missing"]

    # (4) the answer has to be something the target can hold.
    schema_error = _answer_schema_mismatch(item)
    if schema_error is not None:
        return [f"{operation.operation_id}:{operation.kind}:{schema_error}"]
    return []


def _turn_determines_target(
    plan: SetupAgentTurnPlan | None,
    segment_id: str,
    item: Any,
) -> bool:
    """Did this turn's own operations already fill the slot the question asks about?

    Asking "which timeframe?" in the same breath as setting that timeframe leaves an open
    question nobody can clear, because it is already answered.
    """

    if plan is None:
        return False
    same_segment = [
        operation
        for operation in plan.operations
        if operation.authorizing_segment_id == segment_id
    ]
    if not same_segment:
        return False

    target_type = str(item.target_type)
    if target_type in {"condition_field", "capability_parameter", "reference_definition"}:
        field_name = _unresolved_field_name(item)
        for operation in same_segment:
            if operation.kind not in {"add_condition", "update_condition", "replace_groups"}:
                continue
            node = operation.condition
            if node is None:
                continue
            written = (
                node
                if operation.kind != "update_condition"
                or operation.target_condition_id == item.target_condition_id
                else None
            )
            if written is None:
                continue
            if not _target_is_undetermined(written, field_name, target_type):
                return True
        return False

    if target_type == "draft_field":
        field_name = _unresolved_field_name(item)
        return any(
            operation.kind == "set_fields"
            and operation.fields is not None
            and getattr(operation.fields, field_name, None) not in (None, "", [], {})
            for operation in same_segment
        )

    determining = _DETERMINING_KINDS.get(target_type, frozenset())
    return any(operation.kind in determining for operation in same_segment)


def _draft_determines_target(
    item: Any,
    draft: StrategyDraftV2,
    existing: dict[str, ConditionNodeV2],
) -> bool:
    """Does the draft, as it stands, already hold a value for this target?"""

    target_type = str(item.target_type)
    if target_type in {"condition_field", "capability_parameter", "reference_definition"}:
        node = existing.get(item.target_condition_id or "")
        if node is None:
            return False
        return not _target_is_undetermined(node, _unresolved_field_name(item), target_type)
    if target_type == "draft_field":
        field_name = _unresolved_field_name(item)
        return _authored(draft, field_name) and not _is_model_default(
            StrategyDraftV2, field_name, getattr(draft, field_name, None)
        )
    if target_type == "universe":
        return _universe_is_determined(draft)
    if target_type == "market_scope":
        return _authored(draft, "market_scope")
    # ``condition_creation``, ``boolean_structure`` and ``unsupported_resolution`` are
    # not single slots on the draft — a draft full of rules can still be missing the one
    # this segment describes. Only this turn's own operations can determine those, which
    # ``_turn_determines_target`` already checked.
    return False


def _unresolved_field_name(item: Any) -> str:
    if item.target_type == "reference_definition":
        return item.target_field or "reference_definition"
    return item.target_field or ""


def _is_model_default(model: type[BaseModel], field_name: str, value: Any) -> bool:
    """Is this value simply what the model starts with?

    A model default is not a decision anybody made. Treating one as a filled-in value is
    how a legitimate question gets refused: a rule whose direction is still the neutral
    default would count as "already has a direction", so ``which direction?`` could never
    be asked.
    """

    field = model.model_fields.get(field_name)
    if field is None:
        return False
    if field.default_factory is not None:
        try:
            return bool(value == field.default_factory())  # type: ignore[call-arg]
        except TypeError:  # pragma: no cover - factories needing validated data
            return False
    if field.default is PydanticUndefined:
        return False
    return bool(value == field.default)


def _target_is_undetermined(
    node: ConditionNodeV2,
    field_name: str,
    target_type: str,
) -> bool:
    """Is the named slot actually empty — or still holding what the model started with?"""

    if not field_name:
        return True
    if target_type == "capability_parameter":
        if field_name not in node.capability_parameters:
            return True
        return node.capability_parameters.get(field_name) in (None, "", [], {})
    if field_name not in ConditionNodeV2.model_fields:
        return True
    value = getattr(node, field_name, None)
    return value in (None, "", [], {}) or _is_model_default(ConditionNodeV2, field_name, value)


def _answer_schema_mismatch(item: Any) -> str | None:
    """Refuse a question whose answer could not be stored in the slot it names.

    The type comes from the target's own declaration — the condition model's field, the
    draft model's field — never from a list of expected questions.
    """

    declared = str(item.expected_answer_schema.get("type") or "")
    if not declared:
        return "answer_schema_missing_type"
    target_type = str(item.target_type)
    field_name = _unresolved_field_name(item)

    accepted: frozenset[str] | None = _ANSWER_TYPES_BY_TARGET.get(target_type)
    if target_type == "condition_field":
        accepted = _json_types_for_model_field(ConditionNodeV2, field_name)
    elif target_type == "draft_field":
        accepted = _json_types_for_model_field(StrategyDraftV2, field_name)
    elif target_type == "capability_parameter":
        # Parameter types belong to the capability registry and are checked there when
        # the answer is applied; any scalar can name one here.
        accepted = frozenset({"string", "number", "integer", "boolean", "array"})

    if accepted is None or declared in accepted:
        return None
    return f"answer_schema_cannot_fill_target:{field_name or target_type}:{declared}"


#: Python annotations to the JSON answer types that can carry them.
_JSON_TYPES_BY_PYTHON: dict[type, frozenset[str]] = {
    bool: frozenset({"boolean"}),
    int: frozenset({"integer", "number"}),
    float: frozenset({"number", "integer"}),
    str: frozenset({"string"}),
    list: frozenset({"array"}),
    dict: frozenset({"object"}),
}


def _json_types_for_model_field(model: type[Any], field_name: str) -> frozenset[str] | None:
    """Which answer types could fill this pydantic field. ``None`` means unknown."""

    field = model.model_fields.get(field_name)
    if field is None:
        return None
    annotation = field.annotation
    collected: set[str] = set()
    for candidate in (annotation, *get_args(annotation)):
        origin = get_origin(candidate) or candidate
        if origin is Literal:
            collected.update({"string"})
            continue
        if isinstance(origin, type):
            for python_type, json_types in _JSON_TYPES_BY_PYTHON.items():
                if origin is python_type or (
                    isinstance(origin, type) and issubclass(origin, python_type)
                ):
                    collected.update(json_types)
    # An enum or a nested model is answered by naming it.
    return frozenset(collected) or frozenset({"string", "object"})


#: Which recorded provenance entries mean the user themselves chose a universe. A draft
#: that has never been touched still carries a default Sharia policy; that default is the
#: platform's starting point, not an answer the user gave.
_UNIVERSE_AUTHORED_PREFIXES = (
    "sharia_policy",
    "universe.include",
    "universe.exclude",
    "universe.remove_inclusions",
)


def _authored(draft: StrategyDraftV2, *prefixes: str) -> bool:
    """Did any past turn actually write one of these fields?

    ``source_provenance`` records the field names each turn applied, so this answers
    "did a person choose this?" rather than "does this field hold something?".
    """

    return any(
        applied.startswith(prefix)
        for record in draft.source_provenance
        for applied in record.applied_fields
        for prefix in prefixes
    )


def _universe_is_determined(draft: StrategyDraftV2) -> bool:
    """A universe is determined when the user chose one *and* it names something.

    The old test was ``if draft.sharia_policy.universe_mode``. That field always holds a
    value — it defaults to the whole eligible market — so the test was always true and
    every universe question was refused unless the user happened to hedge in English.
    """

    if not _authored(draft, *_UNIVERSE_AUTHORED_PREFIXES):
        return False
    policy = draft.sharia_policy
    if policy.universe_mode == ShariaUniverseMode.EXPLICIT_ASSETS:
        return bool(policy.explicit_symbols or draft.universe.included_symbols)
    if policy.universe_mode == ShariaUniverseMode.APPROVED_WATCHLIST:
        return policy.approved_watchlist_id is not None
    # A deliberately chosen "everything eligible" names a universe on its own.
    return True


def _source_is_ambiguous(text: str) -> bool:
    """An optional signal that a turn is hedging. Never the authority for a question.

    Kept for one narrow job: letting a user reopen a slot that already holds a value.
    Because it is only ever a *widening* signal, a language it does not cover loses
    nothing — the typed checks above still open the question on their own.
    """
    lowered = text.casefold()
    return bool(
        re.search(
            r"\b(?:maybe|either|or|unsure|not sure|which|what|unclear)\b"
            r"|(?:يمكن|ولا|او|أو|مش عارف|غير واضح)"
            r"|\b(?:momken|wala|msh 3aref|mesh 3aref)\b",
            lowered,
        )
    )


def _ground_resolve_unresolved(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del existing, unsupported
    target = operation.target_key or ""
    item = unresolved.get(target)
    if item is None:
        return [f"{operation.operation_id}:resolve_unresolved_key:target_missing"]
    active = request.conversation.active_question
    if active is not None and active.question_id == target:
        return []
    if segment.kind != SegmentKind.CLARIFICATION_ANSWER:
        return [f"{operation.operation_id}:resolve_unresolved_key:not_an_answer"]
    return []


def _ground_unsupported_operation(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del request, existing, unresolved, unsupported
    if segment.kind != SegmentKind.STRATEGY_INSTRUCTION:
        return [f"{operation.operation_id}:add_unsupported:reply_only_segment"]
    return [] if operation.missing_contract else [
        f"{operation.operation_id}:add_unsupported:missing_contract"
    ]


def _ground_remove_unsupported(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del request, existing, unresolved
    target = operation.target_key or ""
    if target not in unsupported:
        return [f"{operation.operation_id}:remove_unsupported_key:target_missing"]
    if not action_is_grounded(segment.exact_source_text, "remove_unsupported"):
        return [f"{operation.operation_id}:remove_unsupported_key:action_not_grounded"]
    return []


def _ground_restore_snapshot(
    operation: Any,
    segment: TurnSegment,
    request: SetupTurnRequest,
    existing: dict[str, ConditionNodeV2],
    unresolved: dict[str, Any],
    unsupported: dict[str, Any],
) -> list[str]:
    del existing, unresolved, unsupported
    if not action_is_grounded(segment.exact_source_text, "restore"):
        return [f"{operation.operation_id}:restore_snapshot:action_not_grounded"]
    owned = next(
        (
            item
            for item in request.history
            if str(item.get("snapshot_id") or "") == operation.target_snapshot_id
            and int(item.get("executable_version") or 0)
            == operation.target_executable_version
            and isinstance(item.get("draft"), dict)
        ),
        None,
    )
    if owned is None:
        return [f"{operation.operation_id}:restore_snapshot:target_not_owned"]
    return []


def _quoted_in(fragment: str, text: str) -> bool:
    return " ".join((fragment or "").split()).casefold() in " ".join(text.split()).casefold()


def _condition_grounding(
    node: ConditionNodeV2,
    text: str,
    source_turn_id: str,
) -> list[str]:
    """A new rule's every stated part must be in the segment that authorised it."""

    if node.node_type != ConditionNodeType.CONDITION:
        return [
            error
            for child in node.children
            for error in _condition_grounding(child, text, source_turn_id)
        ]
    errors: list[str] = []
    if node.source_turn_id != source_turn_id:
        errors.append(f"{node.node_id}:source_turn")
    fragment = node.source_fragment or ""
    if not fragment or not _quoted_in(fragment, text):
        errors.append(f"{node.node_id}:source_fragment")
        return errors
    # The rule's own clause, not the whole segment. One instruction can hold three
    # clauses — `A at least 2% AND (B at least 3% OR NOT C at least 4%)` — and checking
    # each value against the whole segment would let clause A's 2% ground clause B's 3%.
    # The clause is already proven to sit inside the authorising segment above.
    if node.formula is not None and not grounds_formula(fragment, node.formula):
        errors.append(f"{node.node_id}:formula")
    if node.operator is not None and not grounds_operator(fragment, node.operator):
        errors.append(f"{node.node_id}:operator")
    if node.threshold is not None and not grounds_number(
        fragment, node.threshold, unit=_threshold_unit(node)
    ):
        errors.append(f"{node.node_id}:threshold")
    # A timeframe is often stated once for the whole instruction — `on the 15m when X
    # and Y` — so it may be grounded anywhere in the authorising segment.
    if node.trigger_timeframe and not grounds_timeframe(text, node.trigger_timeframe):
        errors.append(f"{node.node_id}:trigger_timeframe")
    for timeframe in (*node.context_timeframes, *node.confirmation_timeframes):
        if not grounds_timeframe(text, timeframe):
            errors.append(f"{node.node_id}:supporting_timeframe:{timeframe}")
    if not grounds_direction(fragment, node.movement_direction):
        errors.append(f"{node.node_id}:movement_direction")
    if not grounds_strategy_bias(fragment, node.strategy_bias):
        errors.append(f"{node.node_id}:strategy_bias")
    if not node.required and not action_is_grounded(fragment, "optional"):
        errors.append(f"{node.node_id}:required")
    if node.lookback is not None and not grounds_lookback(
        fragment,
        node.lookback,
        timeframe=node.reference_timeframe or node.trigger_timeframe,
    ):
        errors.append(f"{node.node_id}:lookback")
    for symbol in node.condition_symbols:
        if not grounds_symbol(fragment, symbol):
            errors.append(f"{node.node_id}:condition_symbol:{symbol}")
    errors.extend(_operand_grounding(node, fragment))
    errors.extend(_reference_grounding(node, fragment))
    return errors


def _threshold_unit(node: ConditionNodeV2) -> str:
    """What the threshold measures, so a percent can only match a percent."""
    return {"percent": "percent", "price": "price", "ratio": "multiple"}.get(node.unit, "plain")


#: Fields a correction may change. Anything it leaves alone is inherited from the rule
#: it names and does not have to be restated.
_CONDITION_SEMANTIC_FIELDS = (
    "node_type",
    "required",
    "movement_direction",
    "strategy_bias",
    "formula",
    "operands",
    "operator",
    "threshold",
    "unit",
    "trigger_timeframe",
    "context_timeframes",
    "confirmation_timeframes",
    "reference_timeframe",
    "reference_definition",
    "lookback",
    "capability_key",
    "capability_version",
    "capability_parameters",
    "condition_symbols",
    "children",
)


def diff_condition_fields(
    previous: ConditionNodeV2 | None,
    resulting: ConditionNodeV2,
) -> tuple[str, ...]:
    """Every semantic field changed by a condition replacement."""

    if previous is None:
        return tuple(
            name
            for name in _CONDITION_SEMANTIC_FIELDS
            if getattr(resulting, name) not in (None, (), [], {}, "")
        )
    return tuple(
        name
        for name in _CONDITION_SEMANTIC_FIELDS
        if getattr(previous, name) != getattr(resulting, name)
    )


def _update_grounding(
    existing: ConditionNodeV2 | None,
    replacement: ConditionNodeV2,
    text: str,
    source_turn_id: str,
) -> list[str]:
    """Only what an edit *changes* has to appear in the words that changed it."""

    if existing is None:
        return _condition_grounding(replacement, text, source_turn_id)
    errors: list[str] = []
    if replacement.source_turn_id != source_turn_id:
        errors.append(f"{replacement.node_id}:source_turn")
    if not replacement.source_fragment or not _quoted_in(replacement.source_fragment, text):
        errors.append(f"{replacement.node_id}:source_fragment")
        return errors
    changed = diff_condition_fields(existing, replacement)
    for name in changed:
        now = getattr(replacement, name)
        if name == "node_type":
            errors.append(f"{replacement.node_id}:node_type_requires_group_replacement")
        elif name == "threshold":
            if now is None:
                if not action_is_grounded(text, "clear"):
                    errors.append(f"{replacement.node_id}:threshold:clear")
            elif not grounds_number(text, now, unit=_threshold_unit(replacement)):
                errors.append(f"{replacement.node_id}:threshold")
        elif name == "unit" and not _grounds_unit(text, now):
            errors.append(f"{replacement.node_id}:unit")
        elif name == "trigger_timeframe":
            if now is None:
                if not action_is_grounded(text, "clear"):
                    errors.append(f"{replacement.node_id}:trigger_timeframe:clear")
            elif not grounds_timeframe(text, now):
                errors.append(f"{replacement.node_id}:trigger_timeframe")
        elif name == "operator" and now is not None:
            if not grounds_operator(text, now):
                errors.append(f"{replacement.node_id}:operator")
        elif name == "formula" and now is not None:
            if not grounds_formula(text, now):
                errors.append(f"{replacement.node_id}:formula")
        elif name == "movement_direction":
            if not grounds_direction(text, now) or (
                now.value in {"neutral", "not_applicable"}
                and not action_is_grounded(text, "clear")
            ):
                errors.append(f"{replacement.node_id}:movement_direction")
        elif name == "strategy_bias":
            if not grounds_strategy_bias(text, now) or (
                now.value == "neutral" and not action_is_grounded(text, "clear")
            ):
                errors.append(f"{replacement.node_id}:strategy_bias")
        elif name in {"context_timeframes", "confirmation_timeframes"}:
            old_values = set(getattr(existing, name))
            for timeframe in set(now) - old_values:
                if not grounds_timeframe(text, timeframe):
                    errors.append(f"{replacement.node_id}:{name}:{timeframe}")
            if old_values - set(now) and not action_is_grounded(text, "clear"):
                errors.append(f"{replacement.node_id}:{name}:removal")
        elif name == "reference_timeframe":
            if now is None:
                if not action_is_grounded(text, "clear"):
                    errors.append(f"{replacement.node_id}:reference_timeframe:clear")
            elif not grounds_timeframe(text, now):
                errors.append(f"{replacement.node_id}:reference_timeframe")
        elif name == "lookback":
            if now is None:
                if not action_is_grounded(text, "clear"):
                    errors.append(f"{replacement.node_id}:lookback:clear")
            elif not grounds_lookback(
                text,
                now,
                timeframe=replacement.reference_timeframe
                or replacement.trigger_timeframe,
            ):
                errors.append(f"{replacement.node_id}:lookback")
        elif name == "operands":
            errors.extend(_operand_grounding(replacement, text))
            if (
                len(replacement.operands) < len(existing.operands)
                and not action_is_grounded(text, "clear")
            ):
                errors.append(f"{replacement.node_id}:operands:removal")
        elif name == "reference_definition":
            if now is None:
                if not action_is_grounded(text, "clear"):
                    errors.append(f"{replacement.node_id}:reference_definition:clear")
            else:
                errors.extend(_reference_grounding(replacement, text))
        elif name == "condition_symbols":
            old_symbols = set(existing.condition_symbols)
            for symbol in set(replacement.condition_symbols) - old_symbols:
                if not grounds_symbol(text, symbol):
                    errors.append(f"{replacement.node_id}:condition_symbol:{symbol}")
            if old_symbols - set(replacement.condition_symbols) and not action_is_grounded(
                text, "clear"
            ):
                errors.append(f"{replacement.node_id}:condition_symbols:removal")
        elif name == "children":
            if not grounds_boolean_shape(text, _boolean_shape(replacement)):
                errors.append(f"{replacement.node_id}:boolean_shape")
        elif (
            name == "required"
            and not (
                action_is_grounded(text, "optional")
                if now is False
                else action_is_grounded(text, "required")
            )
        ):
            errors.append(f"{replacement.node_id}:required")
        elif name == "capability_key":
            if now is None:
                if not action_is_grounded(text, "clear"):
                    errors.append(f"{replacement.node_id}:capability_key:clear")
            elif not (
                grounds_text_value(text, now)
                or grounds_text_value(text, now.split("_", 1)[0])
            ):
                errors.append(f"{replacement.node_id}:capability_key")
        elif name == "capability_version":
            # The registry owns the exact version for a selected mechanic. A changed
            # mechanic is separately grounded and shortlist-gated; requiring a user to
            # recite an internal semantic version would invent a trader-controlled
            # value where none exists.
            if now is None and not action_is_grounded(text, "clear"):
                errors.append(f"{replacement.node_id}:capability_version:clear")
        elif name == "capability_parameters":
            removed = set(existing.capability_parameters) - set(
                replacement.capability_parameters
            )
            if removed and not action_is_grounded(text, "clear"):
                errors.append(f"{replacement.node_id}:capability_parameters:removal")
        else:
            errors.append(f"{replacement.node_id}:{name}:no_grounding_rule")
    return errors


def _grounds_unit(text: str, unit: str) -> bool:
    lowered = text.casefold()
    patterns = {
        "percent": r"%|\bpercent(?:age)?\b",
        "price": r"\bprice\b|\bopen\b|\bclose\b|\bhigh\b|\blow\b|\blevel\b",
        "ratio": r"\bratio\b|\bmultiple\b|\bx\b",
        "count": r"\bcount\b|\bcandles?\b|\bperiods?\b|\blookback\b",
        "index": r"\bindex\b|\brsi\b",
        "boolean": r"\btrue\b|\bfalse\b|\bsweep\b|\breclaim\b|\bmatch\b",
        "none": r"\bno threshold\b|\bboolean\b",
    }
    return bool(re.search(patterns[unit], lowered))


def _operand_grounding(node: ConditionNodeV2, text: str) -> list[str]:
    errors: list[str] = []
    errors.extend(_primitive_operand_contract_errors(node))
    for index, operand in enumerate(node.operands):
        if operand.field and not grounds_text_value(text, operand.field):
            errors.append(f"{node.node_id}:operand:{index}:field")
        if (
            operand.name
            and operand.kind not in {"market_metric", "reference"}
            and not grounds_text_value(text, operand.name)
        ):
            errors.append(f"{node.node_id}:operand:{index}:name")
        if operand.kind == "constant" and isinstance(operand.value, int | float):
            unit = operand.unit or node.unit
            if not grounds_number(text, float(operand.value), unit=_grounding_number_unit(unit)):
                errors.append(f"{node.node_id}:operand:{index}:constant")
        for parameter_name, value in operand.parameters.items():
            # Capability-specific units are checked by the registry validator.
            if (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and node.formula != FormulaKind.CAPABILITY
                and not _platform_owns_primitive_parameter(
                    node,
                    parameter_name=parameter_name,
                    value=value,
                )
                and not grounds_number(text, float(value), unit="plain")
            ):
                errors.append(f"{node.node_id}:operand:{index}:parameter")
    return errors


def _primitive_operand_contract_errors(node: ConditionNodeV2) -> list[str]:
    """Reject operand shapes that change a core formula's deterministic meaning."""

    if node.formula == FormulaKind.CAPABILITY:
        return []
    percentage_formulas = {
        FormulaKind.OPEN_TO_CLOSE_PERCENTAGE,
        FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
        FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE,
        FormulaKind.HIGH_TO_LOW_PERCENTAGE,
        FormulaKind.LOW_TO_HIGH_PERCENTAGE,
    }
    if node.formula in percentage_formulas:
        if len(node.operands) != 1:
            return [f"{node.node_id}:operands:percentage_requires_one_metric"]
        operand = node.operands[0]
        expected = {
            FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: "open_to_close",
            FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: "close_to_close",
            FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: "reference_to_current",
            FormulaKind.HIGH_TO_LOW_PERCENTAGE: "high_to_low",
            FormulaKind.LOW_TO_HIGH_PERCENTAGE: "low_to_high",
        }[node.formula]
        if (
            operand.kind != "market_metric"
            or operand.name != "percentage_change"
            or operand.parameters.get("formula") not in {expected, node.formula.value}
        ):
            return [f"{node.node_id}:operands:percentage_contract"]
    if node.formula == FormulaKind.SWEEP_AND_RECLAIM:
        if len(node.operands) != 1:
            return [f"{node.node_id}:operands:sweep_requires_one_metric"]
        operand = node.operands[0]
        if (
            operand.kind != "market_metric"
            or operand.name != "sweep_and_reclaim"
            or operand.parameters.get("pierce_required") is not True
            or operand.parameters.get("reclaim_required") is not True
        ):
            return [f"{node.node_id}:operands:sweep_contract"]
    if node.formula in {
        FormulaKind.PREVIOUS_CANDLE_REFERENCE,
        FormulaKind.FIXED_REFERENCE_LEVEL,
        FormulaKind.LOOKBACK_REFERENCE_LEVEL,
        FormulaKind.CROSS,
    }:
        if not node.operands or node.operands[0].kind != "price":
            return [f"{node.node_id}:operands:left_price_required"]
        if len(node.operands) > 2:
            return [f"{node.node_id}:operands:too_many_reference_operands"]
        if len(node.operands) == 2 and node.operands[1].kind != "reference":
            return [f"{node.node_id}:operands:right_reference_required"]
    return []


def _platform_owns_primitive_parameter(
    node: ConditionNodeV2,
    *,
    parameter_name: str,
    value: object,
) -> bool:
    """Defaults implied by a deterministic core formula, not trader parameters."""

    if node.formula == FormulaKind.CAPABILITY:
        return False
    return (parameter_name == "lookback" and value == 1) or (
        parameter_name == "closed_only" and value is True
    )


def _grounding_number_unit(unit: str | None) -> str:
    return {
        "percent": "percent",
        "price": "price",
        "ratio": "multiple",
        "count": "count",
        "index": "index",
    }.get(str(unit or ""), "plain")


def _reference_grounding(node: ConditionNodeV2, text: str) -> list[str]:
    if not node.reference_definition:
        return []
    lowered = text.casefold()
    reference = node.reference_definition.casefold()
    formula_patterns = {
        FormulaKind.OPEN_TO_CLOSE_PERCENTAGE: r"\bopen[\s-]*to[\s-]*close\b",
        FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE: r"\bclose[\s-]*to[\s-]*close\b",
        FormulaKind.REFERENCE_TO_CURRENT_PERCENTAGE: (
            r"\breference\b.*\bcurrent\b|\bfrom\b.*\bto\b.*\bcurrent\b"
        ),
        FormulaKind.HIGH_TO_LOW_PERCENTAGE: r"\bhigh[\s-]*to[\s-]*low\b",
        FormulaKind.LOW_TO_HIGH_PERCENTAGE: r"\blow[\s-]*to[\s-]*high\b",
        FormulaKind.PREVIOUS_CANDLE_REFERENCE: (
            r"\bprevious candle\b|\bprior candle\b|\blast closed\b"
        ),
        FormulaKind.FIXED_REFERENCE_LEVEL: r"\bfixed\b|\blevel\b|\bprice\b",
        FormulaKind.LOOKBACK_REFERENCE_LEVEL: (
            r"\blookback\b|\bprevious\s+\d+\s+candles?\b|\bhighest\b|\blowest\b"
        ),
    }
    formula_pattern = (
        formula_patterns.get(node.formula) if node.formula is not None else None
    )
    if formula_pattern is not None:
        return (
            []
            if re.search(formula_pattern, lowered)
            else [f"{node.node_id}:reference_definition"]
        )
    markers = {
        "previous_candle": r"\bprevious candle\b|\bprior candle\b|\blast closed\b",
        "previous_day": r"\bprevious day\b|\byesterday\b",
        "previous_week": r"\bprevious week\b|\blast week\b",
        "swing_high": r"\bswing high\b",
        "swing_low": r"\bswing low\b",
        "session_high": r"\bsession high\b",
        "session_low": r"\bsession low\b",
    }
    matching = next((pattern for key, pattern in markers.items() if key in reference), None)
    if matching and not re.search(matching, lowered):
        return [f"{node.node_id}:reference_definition"]
    if not matching and not _quoted_in(node.reference_definition, text):
        return [f"{node.node_id}:reference_definition"]
    return []


def _boolean_shape(node: ConditionNodeV2) -> str:
    if node.node_type == ConditionNodeType.CONDITION:
        return node.node_id
    return (
        f"{node.node_type.value}("
        + ",".join(_boolean_shape(child) for child in node.children)
        + ")"
    )


def _capability_gate(
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    before: StrategyDraftV2,
    draft: StrategyDraftV2,
    request: SetupTurnRequest,
) -> tuple[list[str], list[ProviderRequirementV2]]:
    """Every capability node checked against its registry contract, before compiling."""

    nodes = [
        node
        for node in (draft.condition_ast.walk() if draft.condition_ast else [])
        if node.node_type == ConditionNodeType.CONDITION
        and node.formula == FormulaKind.CAPABILITY
    ]
    if not nodes:
        return [], []
    previous = _existing_conditions(before)
    shortlist_required_node_ids = frozenset(
        node.node_id
        for node in nodes
        if previous.get(node.node_id) is None
        or previous[node.node_id].capability_key != node.capability_key
        or previous[node.node_id].capability_version != node.capability_version
    )
    authorizing: dict[str, str] = {
        node.node_id: node.source_fragment or ""
        for node in nodes
    }
    for operation in plan.operations:
        segment = segments.get(operation.authorizing_segment_id)
        if segment is None or operation.condition is None:
            continue
        for node in operation.condition.walk():
            authorizing[node.node_id] = segment.exact_source_text
    errors, _providers = capability_condition_errors(
        nodes,
        authorizing_text_by_node=authorizing,
        allowed_keys=request.allowed_capability_keys,
        source_turn_id=request.source_turn_id,
        changed_node_ids=shortlist_required_node_ids,
        previous_nodes_by_id=previous,
    )
    for node in nodes:
        text = authorizing.get(node.node_id)
        if text and previous.get(node.node_id) is None:
            errors.extend(grounded_operator_and_timeframe(node, authorizing_text=text))
    return errors, list(draft.static_provider_requirements)


# --------------------------------------------------------------------------------
# Building the one patch from authorised operations.
# --------------------------------------------------------------------------------


def _build_patch(
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    request: SetupTurnRequest,
) -> StrategyPatch | None:
    """Compose the authorised operations into exactly one canonical patch."""

    if not plan.operations and not plan.unsupported_segments:
        return None
    fields: dict[str, Any] = {}
    sharia_policy: Any | None = None
    add_conditions: list[ConditionNodeV2] = []
    updates: list[ConditionUpdateV2] = []
    removals: list[str] = []
    replace_groups: ConditionNodeV2 | None = None
    include: list[str] = []
    exclude: list[str] = []
    remove_include: list[str] = []
    remove_exclude: list[str] = []
    unsupported: list[UnsupportedRequirementV2] = []
    resolved_keys: list[str] = []
    removed_unsupported: list[str] = []
    unresolved_items: list[Any] = []
    reversion: ReversionV2 | None = None

    for operation in plan.operations:
        segment = segments[operation.authorizing_segment_id]
        if operation.kind == "set_fields" and operation.fields is not None:
            fields.update(
                {
                    key: value
                    for key, value in operation.fields.model_dump(exclude_none=True).items()
                }
            )
        elif operation.kind == "set_sharia_policy" and operation.sharia_policy is not None:
            sharia_policy = operation.sharia_policy
        elif operation.kind == "add_condition" and operation.condition is not None:
            add_conditions.append(operation.condition)
        elif operation.kind == "update_condition" and operation.condition is not None:
            updates.append(
                ConditionUpdateV2(
                    node_id=operation.target_condition_id or "",
                    replacement=operation.condition,
                )
            )
        elif operation.kind == "remove_condition" and operation.target_condition_id:
            removals.append(operation.target_condition_id)
        elif operation.kind == "replace_groups" and operation.condition is not None:
            replace_groups = operation.condition
        elif operation.kind == "add_inclusion" and operation.symbol:
            include.append(operation.symbol)
        elif operation.kind == "add_exclusion" and operation.symbol:
            exclude.append(operation.symbol)
        elif operation.kind == "remove_inclusion" and operation.symbol:
            remove_include.append(operation.symbol)
        elif operation.kind == "remove_exclusion" and operation.symbol:
            remove_exclude.append(operation.symbol)
        elif operation.kind == "add_unsupported" and operation.missing_contract:
            unsupported.append(
                UnsupportedRequirementV2(
                    # Same expression the reconciler matches on. Two copies of this
                    # format string would silently stop agreeing.
                    key=unsupported_key_for(operation),
                    source_turn_id=request.source_turn_id,
                    source_fragment=segment.exact_source_text,
                    missing_contract=operation.missing_contract,
                )
            )
        elif operation.kind == "resolve_unresolved_key" and operation.target_key:
            resolved_keys.append(operation.target_key)
        elif operation.kind == "add_unresolved" and operation.unresolved is not None:
            unresolved_items.append(
                _merge_unresolved_target(operation.unresolved, request.draft)
            )
        elif (
            operation.kind == "update_unresolved"
            and operation.unresolved is not None
            and operation.target_key
        ):
            resolved_keys.append(operation.target_key)
            unresolved_items.append(
                _merge_unresolved_target(operation.unresolved, request.draft)
            )
        elif operation.kind == "remove_unsupported_key" and operation.target_key:
            removed_unsupported.append(operation.target_key)
        elif operation.kind == "restore_snapshot" and operation.target_executable_version:
            reversion = ReversionV2(
                target_version=operation.target_executable_version,
            )

    if not any(
        (
            fields,
            sharia_policy is not None,
            add_conditions,
            updates,
            removals,
            replace_groups is not None,
            include,
            exclude,
            remove_include,
            remove_exclude,
            unsupported,
            resolved_keys,
            removed_unsupported,
            unresolved_items,
            reversion is not None,
        )
    ):
        return None

    return StrategyPatch(
        source_turn_id=request.source_turn_id,
        set_fields=DraftFieldPatch(**fields),
        set_sharia_policy=sharia_policy,
        add_conditions=add_conditions,
        update_conditions=updates,
        remove_conditions=removals,
        replace_groups=replace_groups,
        add_inclusions=include,
        add_exclusions=exclude,
        remove_inclusions=remove_include,
        remove_exclusions=remove_exclude,
        unsupported_requirements=unsupported,
        unresolved_references=unresolved_items,
        remove_unresolved_keys=resolved_keys,
        remove_unsupported_keys=removed_unsupported,
        reversion=reversion,
    )


def _merge_unresolved_target(item: Any, draft: StrategyDraftV2) -> Any:
    """Reuse one unresolved identity for one typed target.

    The lookup itself lives in `operation_target.merged_unresolved_key`, because
    reconciliation has to predict the same answer to attribute this operation's change to
    it. Two independent implementations of "which key will this land under" is exactly
    the kind of duplicate reader that drifts.
    """

    merged = merged_unresolved_key(item, draft)
    return item if merged == item.unresolved_id else item.model_copy(
        update={"unresolved_id": merged}
    )


# --------------------------------------------------------------------------------
# Approval lifecycle. A turn that changes nothing must not undo signed-off work.
# --------------------------------------------------------------------------------


def _has_valid_approval(draft: StrategyDraftV2) -> bool:
    """True when this exact draft version and hash are the approved ones.

    The binding is validated by the schema, so if it says approved it is bound to this
    version and this semantic hash — there is no separate trust decision to make.
    """
    return (
        draft.approval.approved
        and draft.approval.executable_version == draft.executable_version
        and draft.approval.executable_hash == draft.executable_hash
    )


def _approval_status(
    draft: StrategyDraftV2,
    *,
    strategy_mutated: bool,
    material: bool,
    approval_eligible: bool,
    approval_requested: bool,
) -> ApprovalStatus:
    """The one true approval fact for this turn.

    An approved draft that this turn did not materially change stays approved. Reading
    the status from the compiler alone reported `eligible` on a draft the user had
    already signed off, and the caller then reset the session to
    `ready_for_approval` — losing the approval on a turn that only asked a question.
    """
    if not strategy_mutated and _has_valid_approval(draft):
        return "approved"
    if strategy_mutated and material and approval_requested:
        return "invalidated_by_edit"
    if strategy_mutated and material:
        return "eligible" if approval_eligible else "not_eligible"
    return "eligible" if approval_eligible else "not_eligible"


def _final_chat_status(
    draft: StrategyDraftV2,
    *,
    approval_status: ApprovalStatus,
    approval_eligible: bool,
) -> str:
    """The status the session will carry, decided here so nothing can contradict it."""

    if approval_status == "approved":
        return "approved"
    if not approval_eligible:
        return "needs_clarification"
    return "ready_to_scan" if draft.mode == DraftMode.SCANNER else "ready_for_approval"


def _assert_lifecycle(
    *,
    before: StrategyDraftV2,
    after: StrategyDraftV2,
    material: bool,
    approval_status: ApprovalStatus,
    final_chat_status: str,
) -> None:
    """Deterministic assertions around the transition this turn just made.

    These are invariants, not defensive noise. Each one failed in a real way before it
    was written down.
    """
    if not material:
        if after.executable_hash != before.executable_hash:
            raise SetupTurnRejected(
                "LIFECYCLE_VIOLATION",
                "That turn changed the strategy without a material instruction.",
                details=("non_material_turn_changed_executable_hash",),
            )
        if before.approval.approved and approval_status != "approved":
            raise SetupTurnRejected(
                "LIFECYCLE_VIOLATION",
                "That turn would have dropped an approval without changing anything.",
                details=("non_material_turn_lost_approval",),
            )
        if before.approval.approved and final_chat_status != "approved":
            raise SetupTurnRejected(
                "LIFECYCLE_VIOLATION",
                "That turn would have left an approved setup unapproved.",
                details=("non_material_turn_changed_status",),
            )
    elif approval_status == "approved":
        raise SetupTurnRejected(
            "LIFECYCLE_VIOLATION",
            "A material change cannot keep the earlier approval.",
            details=("material_turn_kept_approval",),
        )


# --------------------------------------------------------------------------------
# Clarifications: an answer only closes a question if the target really resolved.
# --------------------------------------------------------------------------------


def _resolved_questions(
    plan: SetupAgentTurnPlan,
    before: StrategyDraftV2,
    after: StrategyDraftV2,
    conversation: SetupConversationContext,
) -> list[str]:
    """Which open questions this turn actually closed.

    A mutating question closes only when its declared target changed in the resulting
    canonical draft. Trusting ``resolves_question`` alone let a question disappear while
    the draft stayed blocked for exactly the reason the question existed.
    """
    contract = conversation.active_question
    closed: list[str] = []
    for answer in plan.clarification_answers:
        if not answer.resolves_question:
            continue
        if contract is None or contract.question_id != answer.question_id:
            # No live contract to satisfy. Only a non-mutating acknowledgement can close
            # something the server is not tracking, and that changes nothing anyway.
            if answer.question_id in {item.key for item in before.unresolved_fields} and (
                answer.question_id not in {item.key for item in after.unresolved_fields}
            ):
                closed.append(answer.question_id)
            continue
        if not contract.mutating:
            closed.append(answer.question_id)
            continue
        if _target_resolved(contract, before, after):
            closed.append(answer.question_id)
    return list(dict.fromkeys(closed))


def _verify_resolved_targets(
    plan: SetupAgentTurnPlan,
    before: StrategyDraftV2,
    after: StrategyDraftV2,
    *,
    server_owned_option: bool = False,
) -> None:
    """A resolve operation may remove a blocker only after its typed target changed."""

    pending = {item.key: item for item in before.unresolved_fields}
    failures: list[str] = []
    for operation in plan.operations:
        if operation.kind != "resolve_unresolved_key" or not operation.target_key:
            continue
        item = pending.get(operation.target_key)
        if item is None:
            continue
        if (
            server_owned_option
            and operation.authorizing_segment_id == "server_option"
            and item.key
            not in {pending_item.key for pending_item in after.unresolved_fields}
        ):
            continue
        contract = ClarificationContract(
            question_id=item.key,
            question=item.question,
            reason=item.reason,
            target_type=item.target_type,
            target_field=item.target_field,
            target_condition_id=item.target_condition_id,
            expected_answer_schema=json.dumps(
                item.expected_answer_schema,
                sort_keys=True,
                separators=(",", ":"),
            )[:200],
            mutating=True,
            allowed_options=item.allowed_options[:6],
        )
        if not _target_resolved(contract, before, after):
            failures.append(
                f"{operation.operation_id}:resolve_unresolved_key:target_unchanged"
            )
    if failures:
        raise SetupTurnRejected(
            "UNRESOLVED_TARGET_UNCHANGED",
            "That answer did not fill the field its clarification asked for.",
            details=tuple(failures),
        )


def _target_resolved(
    contract: ClarificationContract,
    before: StrategyDraftV2,
    after: StrategyDraftV2,
) -> bool:
    """Did the thing this question was about actually change?"""

    if contract.target_type in {"unsupported_requirement", "unsupported_resolution"}:
        keys = {item.key for item in after.unsupported_requirements}
        return contract.question_id not in keys
    if contract.target_type == "universe":
        return (
            after.universe.included_symbols != before.universe.included_symbols
            or after.universe.excluded_symbols != before.universe.excluded_symbols
            or after.sharia_policy != before.sharia_policy
        )
    if contract.target_type in {"draft_field", "market_scope"}:
        field_name = contract.target_field or ""
        for holder_before, holder_after in (
            (before, after),
            (before.market_scope, after.market_scope),
        ):
            if hasattr(holder_after, field_name):
                return getattr(holder_after, field_name) != getattr(holder_before, field_name)
        return False
    if contract.target_type in {"condition_field", "reference_definition"}:
        node_id = contract.target_condition_id or ""
        old = _existing_conditions(before).get(node_id)
        new = _existing_conditions(after).get(node_id)
        if new is None:
            # The rule the question was about is gone, which resolves it either way.
            return old is not None
        if old is None:
            return True
        field_name = (
            "reference_definition"
            if contract.target_type == "reference_definition"
            else contract.target_field or ""
        )
        return getattr(new, field_name, None) != getattr(
            old, field_name, None
        )
    if contract.target_type == "capability_parameter":
        node_id = contract.target_condition_id or ""
        old = _existing_conditions(before).get(node_id)
        new = _existing_conditions(after).get(node_id)
        if new is None:
            return old is not None
        key = contract.target_field or ""
        return new.capability_parameters.get(key) != (
            old.capability_parameters.get(key) if old is not None else None
        )
    if contract.target_type == "condition_creation":
        return len(_existing_conditions(after)) > len(_existing_conditions(before))
    if contract.target_type == "boolean_structure":
        return (
            _boolean_shape(after.condition_ast)
            if after.condition_ast is not None
            else ""
        ) != (
            _boolean_shape(before.condition_ast)
            if before.condition_ast is not None
            else ""
        )
    return False


def _allowed_clarifications(
    draft: StrategyDraftV2,
    conversation: SetupConversationContext,
    answered_now: list[str],
) -> list[ClarificationContract]:
    """The only questions the composer may ask, built from real open items.

    Server-generated, so the composer cannot invent an executable question the platform
    never agreed was needed, and cannot re-ask one already answered.
    """
    already = set(conversation.answered_question_ids) | set(answered_now)
    if conversation.clarifications_asked >= MAX_CLARIFICATIONS_PER_DRAFT:
        return []
    allowed: list[ClarificationContract] = []
    for item in draft.unresolved_fields:
        if not item.blocking or item.key in already:
            continue
        allowed.append(
            ClarificationContract(
                question_id=item.key,
                question=item.question,
                reason=item.reason,
                target_type=item.target_type,
                target_field=item.target_field,
                target_condition_id=item.target_condition_id,
                expected_answer_schema=json.dumps(
                    item.expected_answer_schema,
                    sort_keys=True,
                    separators=(",", ":"),
                )[:200],
                mutating=True,
                allowed_options=item.allowed_options[:6],
            )
        )
    for blocker in draft.unsupported_requirements:
        if not blocker.blocking or blocker.key in already:
            continue
        allowed.append(
            ClarificationContract(
                question_id=blocker.key,
                question=(
                    "Can you describe that rule using a price move, a candle pattern or "
                    "an indicator the platform already measures?"
                ),
                reason=blocker.missing_contract,
                target_type="unsupported_requirement",
                expected_answer_schema="a measurable rule, or drop the requirement",
                mutating=True,
            )
        )
    remaining = MAX_CLARIFICATIONS_PER_DRAFT - conversation.clarifications_asked
    return allowed[: max(0, min(remaining, 6))]


def validated_clarification(
    result: SetupTurnExecutionResult,
    question_id: str | None,
) -> ClarificationContract | None:
    """The contract the composer chose, or ``None`` when it chose nothing valid."""

    if not question_id:
        return None
    return next(
        (item for item in result.allowed_clarifications if item.question_id == question_id),
        None,
    )


# --------------------------------------------------------------------------------
# Evidence and read models.
# --------------------------------------------------------------------------------


def _applied_instructions(
    plan: SetupAgentTurnPlan,
    segments: dict[str, TurnSegment],
    reconciliation: TurnReconciliation,
) -> list[AppliedInstruction]:
    """One record per **net-effective** operation, described from the turn-level diff.

    Two things are deliberately not used here. The model's ``intent_summary`` says what
    it meant to do, not what happened. And an operation's own before/after step is an
    intermediate state: add-then-remove reported an addition that the same turn deleted,
    and put its condition id into the references the next turn resolves.
    """
    by_id = {item.operation_id: item for item in reconciliation.operations}
    applied: list[AppliedInstruction] = []
    for operation in plan.operations:
        segment = segments.get(operation.authorizing_segment_id)
        if segment is None:
            continue
        result = by_id.get(operation.operation_id)
        if result is None or not result.is_effective:
            continue
        applied.append(
            AppliedInstruction(
                operation_id=operation.operation_id,
                segment_id=operation.authorizing_segment_id,
                source_text=segment.exact_source_text,
                summary="; ".join(
                    change.describe() for change in result.net_changes
                )[:400]
                or "updated the setup",
                condition_ids=list(result.final_condition_ids)[:24],
                operation=operation.kind,
                changes=[change.to_dict() for change in result.net_changes],
            )
        )
    return applied


def _operation_result(
    operation: Any,
    *,
    before: StrategyDraftV2,
    after: StrategyDraftV2,
    changes: list[DraftChange],
    applied: bool,
) -> OperationExecutionResult:
    return OperationExecutionResult(
        operation_id=operation.operation_id,
        authorizing_segment_id=operation.authorizing_segment_id,
        operation_kind=operation.kind,
        applied=applied,
        rejected=False,
        before_executable_hash=before.executable_hash,
        after_executable_hash=after.executable_hash,
        workflow_revision_before=before.workflow_revision,
        workflow_revision_after=after.workflow_revision,
        changes=[
            {
                **item.to_dict(),
                "description": item.describe(),
            }
            for item in changes
        ],
        affected_condition_ids=list(
            dict.fromkeys(
                node_id for change in changes for node_id in change.condition_ids
            )
        ),
    )


def draft_read_model(draft: StrategyDraftV2, changes: list[DraftChange]) -> dict[str, Any]:
    """Facts about the current draft, for answering questions about it.

    Supplied so an explanation comes from the draft rather than from the model's memory
    of what it thinks it built.
    """
    conditions = [
        {
            "condition_id": node.node_id,
            "formula": node.formula.value if node.formula else None,
            "operator": node.operator.value if node.operator else None,
            "threshold": node.threshold,
            "unit": node.unit,
            "movement_direction": node.movement_direction.value,
            "strategy_bias": node.strategy_bias.value,
            "trigger_timeframe": node.trigger_timeframe,
            "context_timeframes": list(node.context_timeframes),
            "confirmation_timeframes": list(node.confirmation_timeframes),
            "reference_definition": node.reference_definition,
            "capability_key": node.capability_key,
            "your_words": node.source_fragment,
        }
        for node in (draft.condition_ast.walk() if draft.condition_ast else [])
        if node.node_type == ConditionNodeType.CONDITION
    ]
    return {
        "executable_version": draft.executable_version,
        "workflow_revision": draft.workflow_revision,
        "executable_hash": draft.executable_hash,
        "workflow_state_hash": draft.workflow_state_hash,
        "mode": draft.mode.value,
        "included_symbols": draft.universe.included_symbols[:50],
        "excluded_symbols": draft.universe.excluded_symbols[:50],
        "market_scope": draft.market_scope.model_dump(mode="json"),
        "conditions": conditions[:40],
        "changed_this_turn": [change.to_dict() for change in changes][:40],
        "still_needed": [item.question for item in draft.unresolved_fields if item.blocking],
        "cannot_express": [
            item.missing_contract for item in draft.unsupported_requirements if item.blocking
        ],
        "approved": draft.approval.approved,
    }


# --------------------------------------------------------------------------------
# Spans, references and the remaining reporting helpers.
# --------------------------------------------------------------------------------


def _locate_spans(plan: SetupAgentTurnPlan, message: str) -> SetupAgentTurnPlan:
    """Find every quoted span in the real message and fix its offsets.

    The model supplies the quote; the server supplies the position. Models cannot count
    characters, so failing a correct quote on its arithmetic rejected good turns.
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
    """Every span must be the user's own words, and two actions cannot share them."""

    problems: list[str] = []
    spans: list[tuple[int, int, str]] = []
    for segment in plan.segments:
        quoted = message[segment.start_offset : segment.end_offset]
        if quoted != segment.exact_source_text:
            problems.append(
                f"{segment.segment_id}: {segment.exact_source_text[:80]!r} is not in this message"
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


def _verify_condition_references(
    plan: SetupAgentTurnPlan,
    draft: StrategyDraftV2,
) -> SetupAgentTurnPlan:
    """Edits must name a rule that exists. Labels that do not are dropped, not fatal."""

    existing = set(_existing_conditions(draft))
    mutating = {
        operation.target_condition_id
        for operation in plan.operations
        if operation.kind in {"update_condition", "remove_condition"}
        and operation.target_condition_id is not None
    }
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


def _ignored_segments(plan: SetupAgentTurnPlan) -> list[IgnoredSegment]:
    """Conversation, questions and boundary refusals — recorded, never compiled."""

    reasons = {
        SegmentKind.SOCIAL_REPLY: "greeting or courtesy, answered in words only",
        SegmentKind.ACKNOWLEDGEMENT_NO_ACTION: "acknowledgement, no rule implied",
        SegmentKind.CONVERSATIONAL_CONTEXT: "background wording, not a measurable rule",
        SegmentKind.USER_QUESTION: "question, answered in words only",
        SegmentKind.EXPLANATION_REQUEST: "explanation request, answered in words only",
        SegmentKind.PRODUCT_QUESTION: "product question, answered in words only",
        SegmentKind.APPROVAL_INTENT: (
            "approval happens only through the Review and approve control"
        ),
        SegmentKind.UNSUPPORTED_REQUEST: (
            "outside what this product does, answered as a boundary and never compiled"
        ),
    }
    return [
        IgnoredSegment(
            segment_id=segment.segment_id,
            source_text=segment.exact_source_text,
            kind=segment.kind,
            reason=reasons[segment.kind],
        )
        for segment in plan.segments
        if segment.kind in reasons
    ]


def _status(
    *,
    state_mutated: bool,
    patch_present: bool,
    answered: bool,
    blocked: bool,
) -> ExecutionStatus:
    if state_mutated and blocked:
        return "blocked"
    if state_mutated or answered:
        return "applied"
    if patch_present:
        return "no_change"
    return "conversation_only"


def _safe_compile_message(exc: StrategyV2CompileError) -> str:
    """A compiler refusal in the trader's terms, with no internal detail."""

    return {
        "draft_blocked": "The draft still has an item that must be resolved before it can run.",
        "timeframe_missing": "One rule still needs the timeframe it should be measured on.",
        "conditions_missing": "The draft has no measurable rule yet.",
        "semantic_validation_failed": (
            "One rule does not yet hold together well enough to run."
        ),
    }.get(exc.code, "This draft could not be turned into runnable rules yet.")


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
    if any(item.status != "available" for item in draft.runtime_state.provider_status):
        actions.append("provider_unavailable")
    if compile_status == "not_attempted" and draft.condition_ast is None:
        actions.append("describe_one_measurable_rule")
    return actions[:6]


def _next_conversation(
    request: SetupTurnRequest,
    plan: SetupAgentTurnPlan,
    result: SetupTurnExecutionResult,
    answered: list[str],
    reconciliation: TurnReconciliation,
) -> SetupConversationContext:
    """Carry forward what the next turn needs to resolve ordinary references.

    Only *net-effective* condition ids become references. A rule added and then removed
    in the same turn used to land here, so "that one" could resolve to a rule the draft
    no longer holds.
    """

    context = request.conversation
    if answered and context.active_question_id in answered:
        context = context.cleared_question()
    # `final_condition_ids` already holds only ids the reconciler found in the final
    # draft, so a cancelled or overwritten operation contributes nothing.
    changed = list(reconciliation.final_condition_ids)
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
        and segment.kind in {SegmentKind.EXPLANATION_REQUEST, SegmentKind.USER_QUESTION}
    ]
    return context.model_copy(
        update={
            "last_explained_condition_ids": list(dict.fromkeys(explained))[:24],
            "last_assistant_summary": (
                assistant_summary[:1000] if assistant_summary else context.last_assistant_summary
            ),
        }
    )

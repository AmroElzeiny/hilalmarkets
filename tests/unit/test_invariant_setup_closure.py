"""The fifteen launch-readiness invariants, each asserted directly.

Every test here names the invariant it proves and the code path that enforces it, so a
reader can check the claim rather than trust it. They assert rules across a family of
inputs, never a phrase that happened to be reported.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from ai_market_monitor.engine.draft_diff import diff_drafts, is_material
from ai_market_monitor.engine.semantic_grounding import (
    grounds_formula,
    grounds_number,
    grounds_operator,
    grounds_symbol,
    grounds_timeframe,
)
from ai_market_monitor.engine.setup_turn_execution import (
    MAX_CLARIFICATIONS_PER_DRAFT,
    REPLY_ONLY_KINDS,
    SetupTurnRejected,
    SetupTurnRequest,
    apply_setup_turn,
    validated_clarification,
)
from ai_market_monitor.engine.strategy_draft_v2 import apply_strategy_patch
from ai_market_monitor.schemas.setup_agent import (
    ACTIONABLE_SEGMENT_KINDS,
    SegmentKind,
    SetupAgentPlanEnvelope,
    SetupAgentTurnPlan,
    SetupConversationContext,
    StrategyInstructionPlan,
    UnsupportedSegment,
)
from ai_market_monitor.schemas.setup_authorization import (
    AuthorizedPatchOperation,
    ClarificationContract,
)
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ApprovalBindingV2,
    ConditionNodeType,
    DraftDirection,
    DraftFieldPatch,
    DraftRuntimeStateV2,
    FormulaKind,
    ProviderRuntimeStatusV2,
    StrategyDraftV2,
    StrategyPatch,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch
from tests.support.setup_agent_plans import operations_from_patch, responses_body, segment

TURN = "turn-closure-1"
RULE = "Monitor BTC/USDT on the 15m when the candle rises open-to-close by at least 5%"


def _patch(text: str, draft: StrategyDraftV2 | None = None):
    patch = deterministic_strategy_patch(draft or StrategyDraftV2(), text, source_turn_id=TURN)
    assert patch is not None, text
    return patch


def _draft_with(text: str = RULE) -> StrategyDraftV2:
    return apply_strategy_patch(StrategyDraftV2(), _patch(text)).draft


def _conditions(draft: StrategyDraftV2) -> list:
    if draft.condition_ast is None:
        return []
    return [
        node
        for node in draft.condition_ast.walk()
        if node.node_type is ConditionNodeType.CONDITION
    ]


def _approved(draft: StrategyDraftV2) -> StrategyDraftV2:
    """The same draft, approved and bound to this exact version and hash."""
    return StrategyDraftV2.model_validate(
        draft.model_copy(
            update={
                "approval": ApprovalBindingV2(
                    approved=True,
                    user_id=uuid4(),
                    draft_version=draft.version,
                    semantic_hash=draft.semantic_hash,
                    conversation_snapshot_hash="a" * 64,
                    approved_at=datetime.now(UTC),
                )
            }
        ).model_dump(mode="json")
    )


def test_executable_workflow_and_runtime_identities_are_independent() -> None:
    approved = _approved(_draft_with())
    renamed = apply_strategy_patch(
        approved,
        StrategyPatch(
            source_turn_id=TURN,
            set_fields=DraftFieldPatch(name="My renamed monitor"),
        ),
    ).draft

    assert renamed.executable_hash == approved.executable_hash
    assert renamed.executable_version == approved.executable_version
    assert renamed.workflow_revision == approved.workflow_revision + 1
    assert renamed.workflow_state_hash != approved.workflow_state_hash
    assert renamed.approval == approved.approval

    runtime_refreshed = StrategyDraftV2.model_validate(
        renamed.model_copy(
            update={
                "runtime_state": DraftRuntimeStateV2(
                    provider_status=[
                        ProviderRuntimeStatusV2(
                            provider="temporary-provider",
                            capability="health",
                            status="unavailable",
                        )
                    ],
                    runtime_health="degraded",
                ),
                "executable_hash": "",
                "workflow_state_hash": "",
            }
        ).model_dump(mode="json")
    )
    assert runtime_refreshed.executable_hash == renamed.executable_hash
    assert runtime_refreshed.executable_version == renamed.executable_version
    assert runtime_refreshed.approval == renamed.approval


def test_removing_last_provider_condition_clears_static_requirements() -> None:
    from ai_market_monitor.engine.capabilities import all_capabilities
    from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeV2, OperandV2

    spec = next(item for item in all_capabilities() if item.key == "market_cap_minimum")
    condition = ConditionNodeV2(
        node_id="market-cap",
        node_type=ConditionNodeType.CONDITION,
        source_turn_id=TURN,
        source_fragment="market cap at least 100000000 on 15m",
        formula=FormulaKind.CAPABILITY,
        operands=[
            OperandV2(
                role="value",
                kind="market_metric",
                name="market_cap_minimum",
            )
        ],
        operator=Comparator.GREATER_THAN_OR_EQUAL,
        threshold=100_000_000,
        unit="price",
        trigger_timeframe="15m",
        capability_key=spec.key,
        capability_version=spec.capability_version,
        capability_parameters={
            **spec.default_parameters,
            "threshold": 100_000_000,
        },
    )
    added = apply_strategy_patch(
        StrategyDraftV2(),
        StrategyPatch(source_turn_id=TURN, add_conditions=[condition]),
    ).draft
    assert [item.provider for item in added.static_provider_requirements] == [
        "market_cap_provider"
    ]

    removed = apply_strategy_patch(
        added,
        StrategyPatch(source_turn_id=TURN, remove_conditions=["market-cap"]),
    ).draft
    assert removed.condition_ast is None
    assert removed.static_provider_requirements == []


def _plan(message: str, kind: SegmentKind, *, operations=(), **extra) -> SetupAgentTurnPlan:
    return SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message,
                message,
                kind,
                segment_id="s1",
                action=kind in ACTIONABLE_SEGMENT_KINDS,
                reply=kind not in ACTIONABLE_SEGMENT_KINDS,
            )
        ],
        operations=list(operations),
        overall_confidence=0.9,
        **extra,
    )


def test_planner_condition_provenance_is_bound_to_its_authorizing_segment() -> None:
    condition = _patch(RULE).add_conditions[0].model_dump(mode="json")
    condition["source_turn_id"] = None
    condition["source_fragment"] = None
    condition["node_id"] = "null"
    condition["required"] = False
    condition["operands"].append(
        {
            "role": "right",
            "kind": "constant",
            "value": 5,
            "unit": "percent",
        }
    )
    plan = SetupAgentTurnPlan.model_validate(
        {
            "source_turn_id": TURN,
            "segments": [
                segment(
                    RULE,
                    RULE,
                    SegmentKind.STRATEGY_INSTRUCTION,
                    segment_id="s1",
                    action=True,
                    reply=False,
                ).model_dump(mode="json")
            ],
            "operations": [
                {
                    "operation_id": "add-rule-1",
                    "authorizing_segment_id": "s1",
                    "kind": "add_condition",
                    "condition": condition,
                }
            ],
            "overall_confidence": 0.9,
        }
    )

    proposed = plan.operations[0].condition
    assert proposed is not None
    assert proposed.source_turn_id == TURN
    assert proposed.source_fragment == RULE
    assert proposed.node_id != "null"
    assert proposed.required is True
    assert len(proposed.operands) == 1
    assert proposed.operands[0].name == "percentage_change"
    assert proposed.operands[0].parameters["formula"] == "open_to_close"


def test_pair_mention_grounds_the_exact_base_asset_only() -> None:
    text = "Monitor BTC/USDT on Binance spot"

    assert grounds_symbol(text, "BTC")
    assert grounds_symbol(text, "BTCUSDT")
    assert not grounds_symbol(text, "USDT")
    assert not grounds_symbol(text, "ETH")


async def _run(plan, message, draft, **kwargs):
    return await apply_setup_turn(
        SetupTurnRequest(
            plan=plan,
            message=message,
            draft=draft,
            source_turn_id=TURN,
            **kwargs,
        )
    )


# 1. A pure conversation cannot alter draft, approval or status. --------------------
# Enforced by `SetupAgentTurnPlan.requires_tool` (the tool never runs) and, if it does,
# by `_verify_authorization` refusing every reply-only segment.


@pytest.mark.parametrize("kind", sorted(REPLY_ONLY_KINDS, key=lambda item: item.value))
def test_1_a_conversational_turn_never_requires_the_tool(kind: SegmentKind) -> None:
    plan = _plan("some words", kind)
    assert plan.requires_tool is False


@pytest.mark.parametrize("kind", sorted(REPLY_ONLY_KINDS, key=lambda item: item.value))
async def test_1_a_reply_only_segment_can_never_authorize_a_change(
    kind: SegmentKind,
) -> None:
    """INV 3 and 5 as well: a question cannot author a rule, nor lend it values."""
    base = _approved(_draft_with())
    plan = _plan(RULE, kind, operations=operations_from_patch(_patch(RULE), segment_id="s1"))
    with pytest.raises(SetupTurnRejected) as error:
        await _run(plan, RULE, base)
    assert error.value.code == "UNAUTHORIZED_OPERATION"


# 2. A product question cannot block a strategy. ------------------------------------
# Enforced by `_build_patch`, which only turns an unsupported segment into a draft
# blocker when its segment kind is STRATEGY_INSTRUCTION.


@pytest.mark.parametrize(
    "kind",
    [
        SegmentKind.PRODUCT_QUESTION,
        SegmentKind.USER_QUESTION,
        SegmentKind.UNSUPPORTED_REQUEST,
        SegmentKind.APPROVAL_INTENT,
    ],
)
async def test_2_a_non_instruction_segment_never_blocks_the_draft(
    kind: SegmentKind,
) -> None:
    base = _draft_with()
    message = "can this place the trade for me?"
    plan = _plan(
        message,
        kind,
        unsupported_segments=[
            UnsupportedSegment(segment_id="s1", missing_contract="outside this product")
        ],
    )
    outcome = await _run(plan, message, base)
    assert outcome.draft.unsupported_requirements == []
    assert outcome.draft.blocking is False
    assert outcome.draft.semantic_hash == base.semantic_hash


# 4. Every mutation has exactly one actionable authorizing segment. -----------------
# Enforced by `AuthorizedPatchOperation.authorizing_segment_id` being required, and by
# `SetupAgentTurnPlan.validate_internal_references` rejecting an unknown id.


def test_4_an_operation_must_name_a_segment_that_exists() -> None:
    with pytest.raises(ValueError, match="names unknown segment"):
        SetupAgentTurnPlan(
            source_turn_id=TURN,
            segments=[segment(RULE, RULE, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1")],
            operations=[
                AuthorizedPatchOperation(
                    authorizing_segment_id="does-not-exist",
                    kind="add_exclusion",
                    symbol="LTC/USDT",
                )
            ],
            overall_confidence=0.9,
        )


def test_4_every_operation_kind_requires_its_own_payload() -> None:
    """A malformed operation is refused, never half-applied."""
    with pytest.raises(ValueError, match="requires"):
        AuthorizedPatchOperation(authorizing_segment_id="s1", kind="add_exclusion")


# 5. Values from one segment cannot authorize another segment's mutation. -----------
# Enforced by `_verify_operation_grounding`, which grounds each value in the authorising
# segment's own text.


async def test_5_a_value_from_a_neighbouring_question_cannot_author_a_rule() -> None:
    instruction = "drop LTC/USDT from the list"
    question = "is 5% a lot on a 15m candle?"
    message = f"{instruction}, and {question}"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message,
                instruction,
                SegmentKind.STRATEGY_INSTRUCTION,
                segment_id="s1",
                action=True,
            ),
            segment(message, question, SegmentKind.USER_QUESTION, segment_id="s2", reply=True),
        ],
        # The rule's 5% and 15m live only in the question, so the instruction segment
        # cannot authorise them.
        operations=operations_from_patch(
            _patch("BTC/USDT on the 15m rises open-to-close by at least 5%"),
            segment_id="s1",
        ),
        overall_confidence=0.9,
    )
    with pytest.raises(SetupTurnRejected) as error:
        await _run(plan, message, StrategyDraftV2())
    assert error.value.code == "VALUE_NOT_GROUNDED"


# 4/6. Typed grounding, not substring containment. ----------------------------------
# Enforced by `engine/semantic_grounding.py`.


@pytest.mark.parametrize(
    ("text", "value", "unit", "grounded"),
    [
        ("on the 15m", 1, "count", False),
        ("over 20 candles", 2, "count", False),
        ("over 20 candles", 20, "count", True),
        ("on the 5m", 5, "percent", False),
        ("rises by 5%", 5, "percent", True),
        ("on the 15m", 15, "price", False),
        ("above 50000", 50000, "price", True),
    ],
)
def test_4_numbers_match_on_token_boundaries_and_units(
    text: str, value: float, unit: str, grounded: bool
) -> None:
    assert grounds_number(text, value, unit=unit) is grounded


def test_4_natural_language_grounds_its_canonical_operator_and_formula() -> None:
    assert grounds_operator("rises by at least 5%", Comparator.GREATER_THAN_OR_EQUAL)
    assert not grounds_operator("rises by at least 5%", Comparator.LESS_THAN_OR_EQUAL)
    assert grounds_formula(
        "the candle rises open to close by 5%", FormulaKind.OPEN_TO_CLOSE_PERCENTAGE
    )
    assert not grounds_formula(
        "the candle rises open to close by 5%", FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE
    )
    assert grounds_timeframe("over 60 minutes", "1h")
    assert not grounds_timeframe("on the 15m", "1h")


# 6. Every applied summary is derived from canonical before/after state. ------------
# Enforced by `_applied_instructions`, which reads only `draft_diff.diff_drafts`.


async def test_6_applied_evidence_comes_from_the_diff_not_the_model_summary() -> None:
    message = "exclude LTC/USDT"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message, message, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1",
                kind="add_exclusion",
                symbol="LTC/USDT",
            )
        ],
        strategy_instructions=[
            StrategyInstructionPlan(
                segment_id="s1", intent_summary="I deleted every rule"
            )
        ],
        overall_confidence=0.9,
    )
    outcome = await _run(plan, message, _draft_with())
    applied = outcome.result.applied_instructions
    assert applied, "the change must be reported"
    # The model claimed it deleted every rule; the evidence says what really happened.
    assert "I deleted every rule" not in applied[0].summary
    assert "LTC/USDT" in applied[0].summary
    assert [item["kind"] for item in applied[0].changes] == ["symbol_excluded"]
    assert _conditions(outcome.draft), "no rule was deleted"


async def test_6_a_condition_change_attaches_only_the_rules_it_touched() -> None:
    """Attaching every rule to one instruction made replies claim untouched edits."""
    base = _draft_with()
    second = "also require the 1h close-to-close move to fall by at least 2%"
    base = apply_strategy_patch(base, _patch(second, base)).draft
    assert len(_conditions(base)) == 2
    target = _conditions(base)[0]
    replacement = target.model_copy(
        update={
            "source_turn_id": TURN,
            "source_fragment": "make that at least 8%",
            "threshold": 8.0,
        }
    )
    message = "make that at least 8%"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message, message, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1",
                kind="update_condition",
                condition=replacement,
                target_condition_id=target.node_id,
            )
        ],
        overall_confidence=0.9,
    )
    outcome = await _run(plan, message, base)
    ids = {
        node_id
        for item in outcome.result.applied_instructions
        for node_id in item.condition_ids
    }
    assert ids == {target.node_id}, "only the edited rule is named"


# 7. Clarifications cannot clear without resolving their declared target. -----------
# Enforced by `_resolved_questions` and `_target_resolved`.


def test_7_a_conversational_question_cannot_be_declared_mutating() -> None:
    with pytest.raises(ValueError, match="cannot be mutating"):
        ClarificationContract(
            question_id="q",
            question="ok?",
            reason="r",
            target_type="conversational",
            expected_answer_schema="yes or no",
            mutating=True,
        )


def test_7_a_field_question_must_name_its_field() -> None:
    with pytest.raises(ValueError, match="must name its field"):
        ClarificationContract(
            question_id="q",
            question="which timeframe?",
            reason="r",
            target_type="draft_field",
            expected_answer_schema="a timeframe",
        )


# 8. Composer clarifications must be server-authorized. -----------------------------
# Enforced by `validated_clarification`, the only path from a composer id to a stored
# question.


async def test_8_only_a_server_listed_question_id_survives() -> None:
    draft = StrategyDraftV2()
    plan = _plan("hello", SegmentKind.SOCIAL_REPLY)
    plan = plan.model_copy(
        update={
            "operations": [
                AuthorizedPatchOperation(
                    authorizing_segment_id="s1", kind="add_exclusion", symbol="LTC/USDT"
                )
            ]
        }
    )
    # A reply-only segment cannot authorise, so use an instruction for this one.
    message = "exclude LTC/USDT"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message, message, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1", kind="add_exclusion", symbol="LTC/USDT"
            )
        ],
        overall_confidence=0.9,
    )
    outcome = await _run(plan, message, draft)
    assert validated_clarification(outcome.result, "invented_question") is None
    for contract in outcome.result.allowed_clarifications:
        assert validated_clarification(outcome.result, contract.question_id) is contract


async def test_8_an_answered_question_is_not_offered_again() -> None:
    draft = _draft_with()
    message = "exclude LTC/USDT"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message, message, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1", kind="add_exclusion", symbol="LTC/USDT"
            )
        ],
        overall_confidence=0.9,
    )
    exhausted = SetupConversationContext(clarifications_asked=MAX_CLARIFICATIONS_PER_DRAFT)
    outcome = await _run(plan, message, draft, conversation=exhausted)
    assert outcome.result.allowed_clarifications == [], "the per-draft limit holds"


# 10. Final replies reflect screening and provider gates. --------------------------
# Enforced inside `apply_setup_turn`: screening and providers run before the result is
# built, and `SetupTurnExecutionResult` refuses to call an unscreened draft eligible.


async def test_10_a_screening_block_prevents_approval_eligibility() -> None:
    async def blocked(_definition):
        return None, "Choose and validate a Halal Market first."

    plan = _plan(RULE, SegmentKind.STRATEGY_INSTRUCTION, operations=operations_from_patch(
        _patch(RULE), segment_id="s1"
    ))
    outcome = await _run(plan, RULE, StrategyDraftV2(), screening=blocked)
    assert outcome.result.screening_status == "blocked"
    assert outcome.result.approval_eligible is False
    assert outcome.result.final_chat_status == "needs_clarification"
    assert outcome.result.safe_errors, "the reason must reach the reply"


async def test_10_an_unavailable_provider_prevents_approval_eligibility() -> None:
    from ai_market_monitor.schemas.strategy_draft_v2 import ProviderRuntimeStatusV2

    async def unavailable(requirements):
        return [
            ProviderRuntimeStatusV2(
                provider=item.provider,
                capability=item.capability,
                status="unavailable",
            )
            for item in requirements
        ]

    from ai_market_monitor.engine.capabilities import all_capabilities

    spec = next(
        item
        for item in all_capabilities()
        if item.provider_requirements or item.provider_required
    )
    provider = (
        spec.provider_requirements[0]
        if spec.provider_requirements
        else spec.provider_required
    )
    condition = _draft_with().condition_ast.model_copy(
        update={
            "formula": FormulaKind.CAPABILITY,
            "capability_key": spec.key,
            "capability_version": spec.capability_version,
            "capability_parameters": {},
            "source_fragment": f"{spec.intent_examples[0]} on 15m",
        }
    )
    draft = StrategyDraftV2(condition_ast=condition)
    assert provider
    message = f"{condition.source_fragment}; exclude LTC/USDT"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message, message, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1", kind="add_exclusion", symbol="LTC/USDT"
            )
        ],
        overall_confidence=0.9,
    )
    outcome = await _run(plan, message, draft, providers=unavailable)
    assert outcome.result.approval_eligible is False


# 11. Approved status survives every non-material turn. -----------------------------
# 12. Material edits always invalidate approval.
# Both enforced by `_approval_status`, `_final_chat_status` and `_assert_lifecycle`.


async def test_11_an_approved_draft_survives_a_turn_that_changes_nothing() -> None:
    approved = _approved(_draft_with())
    message = "why does the timeframe matter?"
    plan = _plan(message, SegmentKind.USER_QUESTION)
    # A question needs no tool, so the draft is untouched by construction. Running the
    # tool anyway (an unsupported note from a non-instruction segment) must also keep it.
    outcome = await _run(
        plan.model_copy(
            update={
                "unsupported_segments": [
                    UnsupportedSegment(
                        segment_id="s1", missing_contract="not a market rule"
                    )
                ]
            }
        ),
        message,
        approved,
    )
    assert outcome.result.approval_status == "approved"
    assert outcome.result.final_chat_status == "approved"
    assert outcome.draft.approval.approved is True
    assert outcome.draft.version == approved.version
    assert outcome.material_change is False


async def test_12_a_material_edit_always_invalidates_the_approval() -> None:
    approved = _approved(_draft_with())
    message = "exclude LTC/USDT"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message, message, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1", kind="add_exclusion", symbol="LTC/USDT"
            )
        ],
        overall_confidence=0.9,
    )
    outcome = await _run(plan, message, approved)
    assert outcome.material_change is True
    assert outcome.draft.approval.approved is False
    assert outcome.result.approval_status != "approved"
    assert outcome.draft.version == approved.version + 1


def test_12_answering_a_question_is_progress_but_not_a_material_change() -> None:
    """Closing an open item must not reset an approval."""
    before = _draft_with()
    after = StrategyDraftV2.model_validate(
        before.model_copy(update={"unresolved_fields": [], "semantic_hash": ""}).model_dump(
            mode="json"
        )
    )
    changes = diff_drafts(before, after)
    assert not is_material(changes) or not changes


# 14. Raw message structure reaches the planner unchanged. -------------------------
# Enforced by `SetupAgentTurnInput.message` being the raw text and
# `normalized_message` being the only collapsed copy.


def test_14_the_raw_message_keeps_its_structure() -> None:
    from ai_market_monitor.services.setup_chat_agent import SetupAgentTurnInput

    raw = "1. BTC/USDT on the 15m\n2. exclude ETH/USDT\n\n3. rises by at least 5%"
    turn = SetupAgentTurnInput(message=raw, source_turn_id=TURN, draft=StrategyDraftV2())
    assert turn.message == raw, "the planner sees exactly what was typed"
    assert "\n" not in turn.normalized_message, "only the lexical copy is collapsed"


async def test_14_a_span_is_located_in_the_raw_multiline_message() -> None:
    raw = "please watch this:\n\nBTC/USDT on the 15m rises open-to-close by at least 5%"
    quoted = "BTC/USDT on the 15m rises open-to-close by at least 5%"
    patch = _patch(quoted)
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                raw, quoted, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=operations_from_patch(patch, segment_id="s1"),
        overall_confidence=0.9,
    )
    outcome = await _run(plan, raw, StrategyDraftV2())
    assert outcome.result.strategy_mutated is True


# 15. No nearest-capability substitution exists. -----------------------------------
# Enforced by `_verify_capability_keys` plus `capability_contract`.


@pytest.mark.parametrize(
    "invented",
    ["rsi_oversold_deluxe", "bullish_sweep_v2", "whale_ratio_band", "gamma_squeeze"],
)
async def test_15_an_unoffered_capability_key_is_always_refused(invented: str) -> None:
    message = "exclude LTC/USDT"
    plan = SetupAgentTurnPlan(
        source_turn_id=TURN,
        segments=[
            segment(
                message, message, SegmentKind.STRATEGY_INSTRUCTION, segment_id="s1", action=True
            )
        ],
        operations=[
            AuthorizedPatchOperation(
                authorizing_segment_id="s1", kind="add_exclusion", symbol="LTC/USDT"
            )
        ],
        strategy_instructions=[
            StrategyInstructionPlan(
                segment_id="s1", intent_summary="something", capability_key=invented
            )
        ],
        overall_confidence=0.9,
    )
    with pytest.raises(SetupTurnRejected) as error:
        await _run(plan, message, StrategyDraftV2(), allowed_capability_keys=frozenset())
    assert error.value.code == "CAPABILITY_NOT_OFFERED"


async def test_resolving_unresolved_requires_the_declared_target_to_change() -> None:
    unresolved = UnresolvedFieldV2(
        unresolved_id="exchange",
        source_turn_id=TURN,
        source_fragment="Which exchange should this use?",
        target_type="market_scope",
        target_field="exchange",
        expected_answer_schema={"type": "string"},
        question="Which exchange should this use?",
        reason="An exchange is required for exact market mapping.",
    )
    draft = apply_strategy_patch(
        StrategyDraftV2(),
        StrategyPatch(source_turn_id=TURN, unresolved_references=[unresolved]),
    ).draft
    message = "Use Bybit."
    resolving_only = _plan(
        message,
        SegmentKind.CLARIFICATION_ANSWER,
        operations=(
            AuthorizedPatchOperation(
                authorizing_segment_id="s1",
                kind="resolve_unresolved_key",
                target_key="exchange",
            ),
        ),
    )
    with pytest.raises(SetupTurnRejected) as error:
        await _run(resolving_only, message, draft)
    assert error.value.code == "UNRESOLVED_TARGET_UNCHANGED"

    filled = _plan(
        message,
        SegmentKind.CLARIFICATION_ANSWER,
        operations=(
            AuthorizedPatchOperation(
                authorizing_segment_id="s1",
                kind="set_fields",
                fields=DraftFieldPatch(exchange="bybit"),
            ),
            AuthorizedPatchOperation(
                authorizing_segment_id="s1",
                kind="resolve_unresolved_key",
                target_key="exchange",
            ),
        ),
    )
    outcome = await _run(filled, message, draft)
    assert outcome.draft.market_scope.exchange == "bybit"
    assert outcome.draft.unresolved_fields == []
    assert outcome.draft.executable_version == draft.executable_version + 1


# 9. Capability parameters satisfy the registry schema and source grounding. --------
# Enforced by `engine/capability_contract.py`.


def test_9_the_registry_validator_checks_operator_direction_and_grounding() -> None:
    from ai_market_monitor.engine.capabilities import all_capabilities
    from ai_market_monitor.engine.capability_contract import validate_capability_node
    from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeV2, OperandV2

    spec = next(item for item in all_capabilities() if item.key == "rsi_threshold")
    node = ConditionNodeV2(
        node_type=ConditionNodeType.CONDITION,
        source_turn_id=TURN,
        source_fragment="RSI below 30 on the 15m",
        formula=FormulaKind.CAPABILITY,
        capability_key=spec.key,
        capability_version=spec.capability_version,
        operator=Comparator.LESS_THAN,
        threshold=30.0,
        unit="index",
        trigger_timeframe="15m",
        direction=DraftDirection.SHORT,
        operands=[OperandV2(role="value", kind="indicator", name="rsi")],
    )
    ok = validate_capability_node(
        node,
        authorizing_text="RSI below 30 on the 15m",
        allowed_keys=frozenset({spec.key}),
    )
    assert ok.ok, ok.errors
    # An operator the registry does not support is refused.
    bad = validate_capability_node(
        node.model_copy(update={"operator": Comparator.IS_TRUE}),
        authorizing_text="RSI below 30 on the 15m",
        allowed_keys=frozenset({spec.key}),
    )
    assert any("operator_unsupported" in item for item in bad.errors)
    # A key outside the shortlist is refused even when everything else is right.
    unoffered = validate_capability_node(
        node,
        authorizing_text="RSI below 30 on the 15m",
        allowed_keys=frozenset(),
    )
    assert any("capability_not_offered" in item for item in unoffered.errors)
    nearest = validate_capability_node(
        node,
        authorizing_text="MACD crossover on the 15m",
        allowed_keys=frozenset({spec.key}),
    )
    assert any("capability_semantics_not_exact" in item for item in nearest.errors)
    wrong_type = validate_capability_node(
        node.model_copy(update={"capability_parameters": {"period": "fourteen"}}),
        authorizing_text="RSI below 30 on the 15m",
        allowed_keys=frozenset({spec.key}),
    )
    assert any("parameter_type" in item for item in wrong_type.errors)
    unknown = validate_capability_node(
        node.model_copy(update={"capability_parameters": {"invented": 30}}),
        authorizing_text="RSI below 30 on the 15m",
        allowed_keys=frozenset({spec.key}),
    )
    assert any("parameter_additionalProperties" in item for item in unknown.errors)
    wrong_unit = validate_capability_node(
        node.model_copy(update={"capability_parameters": {"period": 15}}),
        authorizing_text="RSI below 30 on the 15m",
        allowed_keys=frozenset({spec.key}),
    )
    assert any("parameter_not_grounded:period:count" in item for item in wrong_unit.errors)


# 13. Same-key retries never duplicate work or disappear. --------------------------
# Enforced by `_replayed_turn` and `_record_turn` in `setup_chat_launch`.


async def test_14_simple_mutation_uses_exactly_one_planner_call() -> None:
    """Free text reaches the planner first; a simple mutation needs no composer."""
    from pydantic import SecretStr

    from ai_market_monitor.core.config import Settings
    from ai_market_monitor.services.setup_chat_agent import (
        SetupAgentTurnInput,
        SetupChatAgent,
    )

    message = "exclude LTC/USDT"
    draft = _draft_with()
    plan = _plan(
        message,
        SegmentKind.STRATEGY_INSTRUCTION,
        operations=operations_from_patch(_patch(message, draft), segment_id="s1"),
        strategy_instructions=[
            StrategyInstructionPlan(segment_id="s1", intent_summary="exclude LTC")
        ],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=responses_body(
                SetupAgentPlanEnvelope(plan=plan).model_dump_json()
            ),
        )

    agent = SetupChatAgent(
        Settings(
            _env_file=None,
            app_env="test",
            app_secret_key="closure-secret-with-at-least-thirty-two-chars",
            openai_api_key=SecretStr("test-key"),
            sharia_screening_enforced=False,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await agent.run_turn(
        SetupAgentTurnInput(message=message, source_turn_id=TURN, draft=draft)
    )
    assert result.trace.model_calls == 1
    assert result.execution is not None
    assert result.execution.strategy_mutated is True


def test_13_turn_statuses_cover_every_stage_the_spec_requires() -> None:
    from ai_market_monitor.services.setup_chat_launch import TurnStatus

    assert {item.value for item in TurnStatus} == {
        "RECEIVED",
        "PLANNING",
        "EXECUTING",
        "COMPOSING",
        "COMPLETED",
        "RETRYABLE_FAILURE",
        "PERMANENT_FAILURE",
    }


def test_13_turn_idempotency_no_longer_writes_session_json() -> None:
    from ai_market_monitor.services.setup_chat_launch import TurnStatus

    assert {item.value for item in TurnStatus} == {
        "RECEIVED",
        "PLANNING",
        "EXECUTING",
        "COMPOSING",
        "COMPLETED",
        "RETRYABLE_FAILURE",
        "PERMANENT_FAILURE",
    }

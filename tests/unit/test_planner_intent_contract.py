"""Invariants for the compact, model-facing Setup Chat contract."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ai_market_monitor.engine.capability_shortlist import CapabilityShortlist, ShortlistCandidate
from ai_market_monitor.engine.planner_intent_compiler import (
    _OPERATION_KIND,
    IntentCompileError,
    SemanticIntentOutcome,
    _repair_value_is_grounded,
    apply_repair_deltas,
    compile_planner_intents,
    normalize_planner_envelope,
    normalize_planner_segment_boundaries,
)
from ai_market_monitor.engine.planner_references import (
    MethodologyReference,
    PlannerReferenceContext,
    WatchlistReference,
)
from ai_market_monitor.engine.setup_failure_taxonomy import SetupFailureClass
from ai_market_monitor.engine.setup_turn_execution import SetupTurnRequest, apply_setup_turn
from ai_market_monitor.schemas.planner_intent import (
    FORBIDDEN_SCHEMA_MODELS,
    FORBIDDEN_SERVER_FIELDS,
    PLANNER_SCHEMA_BYTE_BUDGET,
    PLANNER_SCHEMA_DEPTH_BUDGET,
    PlannerIntentEnvelope,
    PlannerRepairEnvelope,
    RepairReplacementValue,
    SemanticAction,
    SemanticIntentRepairDelta,
    compact_json_schema,
    schema_complexity,
)
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2
from ai_market_monitor.services.setup_chat_agent import (
    _classify_plan_failure,
)

METHOD_ID = "11111111-1111-4111-8111-111111111111"


def _methodology(
    reference: str = "methodology_1",
    *,
    identifier: str = "AAOIFI",
    family: str = "AAOIFI",
    method_id: str = METHOD_ID,
) -> MethodologyReference:
    return MethodologyReference(
        reference=reference,
        public_identifier=identifier,
        public_name=f"{identifier} Sharia Standard",
        family=family,
        aliases=(),
        methodology_id=method_id,
        methodology_version="1.0",
    )


def _envelope(message: str, payload: dict[str, object] | None) -> PlannerIntentEnvelope:
    return PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "segment_1",
                    "exact_source_text": message,
                    "segment_kind": ("STRATEGY_INSTRUCTION" if payload else "PRODUCT_QUESTION"),
                }
            ],
            "semantic_intents": (
                [{"segment_ref": "segment_1", "payload": payload}] if payload else []
            ),
            "clarification_answers": [],
            "questions_to_answer": [] if payload else ["segment_1"],
            "unsupported_intents": [],
            "approval_intent": None,
            "overall_confidence": 0.97,
        }
    )


def test_exact_wire_schema_stays_bounded_and_contains_no_canonical_models() -> None:
    schema = compact_json_schema(PlannerIntentEnvelope)
    measured = schema_complexity(schema)
    assert measured["minified_schema_bytes"] <= PLANNER_SCHEMA_BYTE_BUDGET
    assert measured["maximum_nesting_depth"] <= PLANNER_SCHEMA_DEPTH_BUDGET
    assert not (set(schema.get("$defs") or {}) & FORBIDDEN_SCHEMA_MODELS)
    # Pinned exactly, in order. A supported-but-incomplete request and a read-only
    # percentage scan are their own fields precisely so neither can be smuggled in as
    # an unsupported intent — the defect that made a missing timeframe read as an
    # unbuildable rule. Growing this list is a wire-contract change, so it is stated
    # here rather than asserted loosely.
    assert list(PlannerIntentEnvelope.model_fields) == [
        "segments",
        "semantic_intents",
        "clarification_answers",
        "questions_to_answer",
        "supported_incomplete_intents",
        "read_only_percentage_scans",
        "unsupported_intents",
        "approval_intent",
        "overall_confidence",
    ]
    semantic = (schema.get("$defs") or {})["SemanticIntent"]
    assert set(semantic["properties"]) == {"segment_ref", "payload"}
    assert all(
        forbidden not in json.dumps(schema)
        for forbidden in (
            "source_turn_id",
            "intent_id",
            "operation_id",
            "node_id",
            "unresolved_id",
            "start_offset",
            "end_offset",
            "registry_version",
            "methodology_version",
            "workflow_revision",
            "executable_version",
            '"hash"',
            "set_universe_policy",
            "semantic_target",
            '"values"',
        )
    )
    assert "value" not in semantic["properties"]
    property_names = {
        property_name
        for definition in (schema.get("$defs") or {}).values()
        for property_name in definition.get("properties", {})
    }
    assert not (property_names & FORBIDDEN_SERVER_FIELDS)
    parameter = (schema.get("$defs") or {})["CapabilityParameterIntent"]
    assert "value" not in parameter["properties"]
    assert "values" not in parameter["properties"]
    assert {
        "number_value",
        "string_value",
        "boolean_value",
        "number_items",
        "string_items",
        "boolean_items",
        "object_fields",
    } <= set(parameter["properties"])
    payload_definitions = {
        name: definition
        for name, definition in (schema.get("$defs") or {}).items()
        if name.endswith("Payload") and "action" in definition.get("properties", {})
    }
    assert payload_definitions
    assert all(
        "values" not in definition.get("properties", {})
        for definition in payload_definitions.values()
    )
    discriminators: list[str] = []
    for definition in payload_definitions.values():
        action_schema = definition["properties"]["action"]
        discriminators.extend(action_schema.get("enum") or [action_schema["const"]])
    assert len(discriminators) == len(set(discriminators))
    assert set(discriminators) == {item.value for item in SemanticAction}

    repair_schema = compact_json_schema(PlannerRepairEnvelope)
    assert not (set(repair_schema.get("$defs") or {}) & FORBIDDEN_SCHEMA_MODELS)
    assert all(forbidden not in json.dumps(repair_schema) for forbidden in FORBIDDEN_SERVER_FIELDS)


def test_strict_wire_empty_union_branches_do_not_invalidate_the_selected_typed_value() -> None:
    """Strict providers return every property, including unused arrays as ``[]``."""

    scalar = {
        "kind": "timeframe",
        "number_value": None,
        "integer_value": None,
        "string_value": "15m",
        "boolean_value": None,
        "number_items": [],
        "integer_items": [],
        "string_items": [],
        "boolean_items": [],
        "object_fields": [],
    }
    assert RepairReplacementValue.model_validate(scalar).semantic_value() == "15m"

    parameter = {
        "name": "period",
        "number_value": 14,
        "string_value": None,
        "boolean_value": None,
        "number_items": [],
        "string_items": [],
        "boolean_items": [],
        "object_fields": [],
    }
    envelope = _envelope(
        "Use registry test period 14",
        {
            "action": "add_condition",
            "condition": {
                "capability_key": "registry_test",
                "comparator": "is_true",
                "capability_parameters": [parameter],
            },
        },
    )
    parsed_parameter = envelope.semantic_intents[0].payload.condition.capability_parameters[0]
    assert parsed_parameter.semantic_value() == 14


def test_only_a_duplicated_connective_is_trimmed_from_overlapping_exact_segments() -> None:
    message = "Include BTCUSDT and exclude LTCUSDT."
    envelope = PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "s1",
                    "exact_source_text": "Include BTCUSDT and",
                    "segment_kind": "STRATEGY_INSTRUCTION",
                },
                {
                    "segment_ref": "s2",
                    "exact_source_text": "and exclude LTCUSDT.",
                    "segment_kind": "STRATEGY_INSTRUCTION",
                },
            ],
            "semantic_intents": [
                {
                    "segment_ref": "s1",
                    "payload": {"action": "include_symbol", "symbol": "BTCUSDT"},
                },
                {
                    "segment_ref": "s2",
                    "payload": {"action": "exclude_symbol", "symbol": "LTCUSDT"},
                },
            ],
            "clarification_answers": [],
            "questions_to_answer": [],
            "unsupported_intents": [],
            "approval_intent": None,
            "overall_confidence": 0.99,
        }
    )
    normalized = normalize_planner_segment_boundaries(envelope, message)
    assert [item.exact_source_text for item in normalized.segments] == [
        "Include BTCUSDT and",
        "exclude LTCUSDT.",
    ]
    compiled = compile_planner_intents(
        envelope,
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-overlap-connective",
    )
    assert [item.kind for item in compiled.plan.operations] == [
        "add_inclusion",
        "add_exclusion",
    ]


def test_generic_or_legacy_planner_payloads_are_rejected() -> None:
    message = "Use screened assets only"
    base = _envelope(message, {"action": "set_name", "name": "test"}).model_dump(mode="json")
    base["semantic_intents"][0]["payload"] = {
        "action": "set_universe_policy",
        "value": "eligible_market",
    }
    with pytest.raises(ValidationError):
        PlannerIntentEnvelope.model_validate(base)


@pytest.mark.parametrize(
    ("authored", "canonical"),
    (("60m", "1h"), ("24h", "1d"), ("daily", "1d"), ("four-hour", "4h")),
)
def test_model_facing_timeframe_aliases_normalize_without_losing_source(
    authored: str,
    canonical: str,
) -> None:
    message = f"Use {authored} as the trigger timeframe"
    envelope = _envelope(
        message,
        {
            "action": "add_condition",
            "condition": {
                "formula_key": "open_to_close_percentage",
                "movement_direction": "up",
                "comparator": "gte",
                "threshold": 2,
                "unit": "percent",
                "trigger_timeframe": authored,
            },
        },
    )
    condition = envelope.semantic_intents[0].payload.condition
    assert condition.trigger_timeframe == canonical
    assert envelope.segments[0].exact_source_text == message


def test_missing_trigger_timeframe_is_a_semantic_requirement_not_a_compiler_fault() -> None:
    """A planner omission must become one typed clarification before any operation exists."""

    message = "Require a bullish close-to-close move of at least 2%."
    envelope = _envelope(
        message,
        {
            "action": "add_condition",
            "condition": {
                "formula_key": "close_to_close_percentage",
                "movement_direction": "up",
                "comparator": "gte",
                "threshold": 2,
                "unit": "percent",
            },
        },
    )
    with pytest.raises(IntentCompileError) as failure:
        compile_planner_intents(
            envelope,
            draft=StrategyDraftV2(),
            message=message,
            source_turn_id="turn-missing-trigger",
        )
    assert failure.value.code == "INTENT_INCOMPLETE"
    assert failure.value.outcome == SemanticIntentOutcome.USER_INFORMATION_REQUIRED
    assert failure.value.target_path == "condition.trigger_timeframe"


async def test_multiple_omitted_adjacent_roles_are_not_inserted_by_the_compiler() -> None:
    """Several planner omissions cannot become deterministic trader semantics.

    They are also not an internal compiler fault. This assertion used to require
    ``COMPILER_INVARIANT_VIOLATION`` for two or more omissions, and that terminal class
    is what made an ordinary sentence unanswerable: no repair, no question, HTTP 422,
    and a user who could only send the same words again. Evaluator run
    20260803T000036Z, case ``precedence_grouping-013-1996163001``, shows eight
    identical refusals to one complete instruction.

    Every omission is named instead, each with the field the model left out, so one
    bounded correction can address all of them at once — and the compiler still
    inserts nothing.
    """

    scope = "Use 15m as context and 5m as the trigger timeframe."
    rule = "Require a bullish close-to-close move of at most 5%."
    message = f"{scope} {rule}"
    envelope = PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "scope",
                    "exact_source_text": scope,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                },
                {
                    "segment_ref": "rule",
                    "exact_source_text": rule,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                },
            ],
            "semantic_intents": [
                {
                    "segment_ref": "rule",
                    "payload": {
                        "action": "add_condition",
                        "condition": {
                            "source_quote": rule,
                            "formula_key": "close_to_close_percentage",
                            "movement_direction": "up",
                            "comparator": "lte",
                            "threshold": 5,
                            "unit": "percent",
                        },
                    },
                }
            ],
            "clarification_answers": [],
            "questions_to_answer": [],
            "unsupported_intents": [],
            "approval_intent": None,
            "overall_confidence": 0.97,
        }
    )
    with pytest.raises(IntentCompileError) as omitted:
        compile_planner_intents(
            envelope,
            draft=StrategyDraftV2(),
            message=message,
            source_turn_id="turn-adjacent-role-evidence",
        )
    assert omitted.value.code == "PLANNER_SEMANTIC_OMISSION"
    assert omitted.value.outcome is SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED
    # Both omitted roles are named, not just whichever was met first.
    assert set(omitted.value.target_paths) == {
        "condition.context_timeframes",
        "condition.trigger_timeframe",
    }
    # And nothing was written into the intent on the trader's behalf.
    condition = envelope.semantic_intents[0].payload.condition  # type: ignore[union-attr]
    assert condition.trigger_timeframe is None
    assert condition.context_timeframes == []


async def test_role_evidence_never_crosses_a_question_or_another_operation() -> None:
    """An unclaimed conversation span is never a fallback grounding source."""

    question = "Would 15m be enough context and 5m be the trigger timeframe?"
    rule = "Require a bullish close-to-close move of at most 5%."
    message = f"{question} {rule}"
    envelope = PlannerIntentEnvelope.model_validate(
        {
            "segments": [
                {
                    "segment_ref": "question",
                    "exact_source_text": question,
                    "segment_kind": "USER_QUESTION",
                },
                {
                    "segment_ref": "rule",
                    "exact_source_text": rule,
                    "segment_kind": "STRATEGY_INSTRUCTION",
                },
            ],
            "semantic_intents": [
                {
                    "segment_ref": "rule",
                    "payload": {
                        "action": "add_condition",
                        "condition": {
                            "source_quote": rule,
                            "formula_key": "close_to_close_percentage",
                            "movement_direction": "up",
                            "comparator": "lte",
                            "threshold": 5,
                            "unit": "percent",
                        },
                    },
                }
            ],
            "clarification_answers": [],
            "questions_to_answer": ["question"],
            "unsupported_intents": [],
            "approval_intent": None,
            "overall_confidence": 0.97,
        }
    )
    with pytest.raises(IntentCompileError) as refused:
        compile_planner_intents(
            envelope,
            draft=StrategyDraftV2(),
            message=message,
            source_turn_id="turn-question-role-evidence",
        )
    assert refused.value.code == "INTENT_INCOMPLETE"


def test_every_model_facing_action_has_one_canonical_operation_mapping() -> None:
    assert {action.value: kind for action, kind in _OPERATION_KIND.items()} == {
        "set_mode": "set_fields",
        "set_name": "set_fields",
        "set_exchange": "set_fields",
        "set_quote_asset": "set_fields",
        "set_market_type": "set_fields",
        "set_sharia_preferences": "set_sharia_policy",
        "include_symbol": "add_inclusion",
        "exclude_symbol": "add_exclusion",
        "remove_included_symbol": "remove_inclusion",
        "remove_excluded_symbol": "remove_exclusion",
        "add_condition": "add_condition",
        "update_condition": "update_condition",
        "remove_condition": "remove_condition",
        "replace_boolean_structure": "replace_groups",
        "restore_owned_version": "restore_snapshot",
    }
    assert set(_OPERATION_KIND) == set(SemanticAction)


def test_identical_semantic_intents_are_normalized_before_operation_creation() -> None:
    message = "Exclude DOGE/USDT"
    payload = {"action": "exclude_symbol", "symbol": "DOGE/USDT"}
    raw = _envelope(message, payload).model_dump(mode="json")
    raw["semantic_intents"].append(dict(raw["semantic_intents"][0]))
    envelope = PlannerIntentEnvelope.model_validate(raw)

    normalized = normalize_planner_envelope(envelope)
    assert len(normalized.semantic_intents) == 1
    compiled = compile_planner_intents(
        envelope,
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-deduplicate",
    )
    assert [item.kind for item in compiled.plan.operations] == ["add_exclusion"]


async def test_explicit_sharia_preference_resolves_through_governed_reference() -> None:
    message = "Use the AAOIFI methodology and screened assets only"
    references = PlannerReferenceContext(methodologies=(_methodology(),))
    envelope = _envelope(
        message,
        {
            "action": "set_sharia_preferences",
            "methodology_identifier": "AAOIFI",
            "screened_assets_only": True,
        },
    )
    before = StrategyDraftV2()
    compiled = compile_planner_intents(
        envelope,
        draft=before,
        message=message,
        source_turn_id="turn-sharia-1",
        references=references,
    )
    operation = compiled.plan.operations[0]
    assert operation.kind == "set_sharia_policy"
    assert str(operation.sharia_policy.methodology_id) == METHOD_ID
    assert operation.sharia_policy.methodology_version == "1.0"

    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=before,
            source_turn_id="turn-sharia-1",
            planner_references=references,
        )
    )
    assert outcome.draft.executable_hash != before.executable_hash
    assert outcome.draft.executable_version == before.executable_version + 1
    assert outcome.draft.approval.approved is False


def test_screened_market_and_approved_watchlist_are_distinct_preferences() -> None:
    watchlist = WatchlistReference(
        reference="watchlist_1",
        public_name="Core assets",
        aliases=("favorites",),
        watchlist_id="22222222-2222-4222-8222-222222222222",
        watchlist_version="wlv2:abc",
    )
    references = PlannerReferenceContext(watchlists=(watchlist,))
    screened = (
        compile_planner_intents(
            _envelope(
                "Use screened assets only",
                {"action": "set_sharia_preferences", "screened_assets_only": True},
            ),
            draft=StrategyDraftV2(
                sharia_policy={
                    "universe_mode": "explicit_assets",
                    "explicit_symbols": ["BTC/USDT"],
                }
            ),
            message="Use screened assets only",
            source_turn_id="turn-screened",
            references=references,
        )
        .plan.operations[0]
        .sharia_policy
    )
    approved = (
        compile_planner_intents(
            _envelope(
                "Use my favorites watchlist only",
                {"action": "set_sharia_preferences", "approved_watchlist_only": True},
            ),
            draft=StrategyDraftV2(),
            message="Use my favorites watchlist only",
            source_turn_id="turn-watchlist",
            references=references,
        )
        .plan.operations[0]
        .sharia_policy
    )
    assert screened.universe_mode.value == "eligible_market"
    assert screened.approved_watchlist_id is None
    assert approved.universe_mode.value == "approved_watchlist"
    assert str(approved.approved_watchlist_id) == watchlist.watchlist_id


@pytest.mark.parametrize(
    "preference",
    (
        {"screened_assets_only": False},
        {"approved_watchlist_only": False},
        {"screened_assets_only": False, "approved_watchlist_only": False},
        {"screened_assets_only": True, "approved_watchlist_only": True},
    ),
)
def test_incomplete_or_conflicting_sharia_universe_preferences_require_information(
    preference: dict[str, bool],
) -> None:
    message = "Do not use screened assets or an approved watchlist only"
    with pytest.raises(IntentCompileError) as blocked:
        compile_planner_intents(
            _envelope(message, {"action": "set_sharia_preferences", **preference}),
            draft=StrategyDraftV2(),
            message=message,
            source_turn_id="turn-sharia-negative",
        )
    assert blocked.value.outcome is SemanticIntentOutcome.USER_INFORMATION_REQUIRED


def test_negative_sharia_preference_is_valid_only_with_grounded_alternative() -> None:
    message = "Do not use an approved watchlist; use screened assets only"
    compiled = compile_planner_intents(
        _envelope(
            message,
            {
                "action": "set_sharia_preferences",
                "screened_assets_only": True,
                "approved_watchlist_only": False,
            },
        ),
        draft=StrategyDraftV2(
            sharia_policy={
                "universe_mode": "explicit_assets",
                "explicit_symbols": ["BTC/USDT"],
            }
        ),
        message=message,
        source_turn_id="turn-sharia-alternative",
    )
    assert len(compiled.plan.operations) == 1
    assert compiled.plan.operations[0].sharia_policy is not None
    assert compiled.plan.operations[0].sharia_policy.universe_mode.value == "eligible_market"


def test_exact_current_sharia_preference_emits_no_canonical_operation() -> None:
    message = "Use screened assets only"
    compiled = compile_planner_intents(
        _envelope(
            message,
            {"action": "set_sharia_preferences", "screened_assets_only": True},
        ),
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-sharia-current",
    )
    assert compiled.plan.operations == []
    assert "sharia_policy:exact_current_policy:no_operation" in compiled.derivations


def test_offered_watchlist_answer_resolves_without_exposing_its_identity() -> None:
    first = WatchlistReference(
        reference="watchlist_1",
        public_name="Core assets",
        aliases=("core",),
        watchlist_id="22222222-2222-4222-8222-222222222222",
        watchlist_version="wlv2:core",
    )
    second = WatchlistReference(
        reference="watchlist_2",
        public_name="Growth assets",
        aliases=("growth",),
        watchlist_id="33333333-3333-4333-8333-333333333333",
        watchlist_version="wlv2:growth",
    )
    references = PlannerReferenceContext(watchlists=(first, second))
    with pytest.raises(IntentCompileError) as initial:
        compile_planner_intents(
            _envelope(
                "Use my approved watchlist only",
                {"action": "set_sharia_preferences", "approved_watchlist_only": True},
            ),
            draft=StrategyDraftV2(),
            message="Use my approved watchlist only",
            source_turn_id="turn-watchlist-ambiguous",
            references=references,
        )
    assert initial.value.outcome is SemanticIntentOutcome.USER_INFORMATION_REQUIRED

    answer_envelope = _envelope(
        "Core assets",
        {"action": "set_sharia_preferences", "approved_watchlist_only": True},
    ).model_dump(mode="json")
    answer_envelope["segments"][0]["segment_kind"] = "CLARIFICATION_ANSWER"
    selected = compile_planner_intents(
        PlannerIntentEnvelope.model_validate(answer_envelope),
        draft=StrategyDraftV2(),
        message="Core assets",
        source_turn_id="turn-watchlist-answer",
        references=references,
    )
    policy = selected.plan.operations[0].sharia_policy
    assert policy is not None
    assert str(policy.approved_watchlist_id) == first.watchlist_id
    assert policy.approved_watchlist_version == first.watchlist_version


async def test_offered_watchlist_answer_passes_the_existing_grounding_gate() -> None:
    watchlist = WatchlistReference(
        reference="watchlist_1",
        public_name="Core assets",
        aliases=(),
        watchlist_id="22222222-2222-4222-8222-222222222222",
        watchlist_version="wlv2:core",
    )
    references = PlannerReferenceContext(watchlists=(watchlist,))
    message = "Core assets"
    raw = _envelope(
        message,
        {"action": "set_sharia_preferences", "approved_watchlist_only": True},
    ).model_dump(mode="json")
    raw["segments"][0]["segment_kind"] = "CLARIFICATION_ANSWER"
    compiled = compile_planner_intents(
        PlannerIntentEnvelope.model_validate(raw),
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-watchlist-grounding",
        references=references,
    )
    outcome = await apply_setup_turn(
        SetupTurnRequest(
            plan=compiled.plan,
            message=message,
            draft=StrategyDraftV2(),
            source_turn_id="turn-watchlist-grounding",
            planner_references=references,
        )
    )
    assert str(outcome.draft.sharia_policy.approved_watchlist_id) == watchlist.watchlist_id


def _parameter_shortlist() -> CapabilityShortlist:
    return CapabilityShortlist(
        candidates=(
            ShortlistCandidate(
                capability_key="registry_test",
                capability_version="1.0",
                label="Registry test",
                description="Registry test capability",
                supported_operators=("is_true",),
                parameter_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string", "x-semantic-unit": "symbol"},
                        },
                        "confirmation": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string", "enum": ["close"]},
                                "enabled": {"type": "boolean"},
                                "timeframe": {
                                    "type": "string",
                                    "x-semantic-unit": "timeframe",
                                },
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                direction_support=("neutral",),
                supported_timeframes=("15m",),
                requires_higher_timeframe=False,
                provider_requirements=(),
                availability="available",
                executable=True,
                negative_examples=(),
                intent_examples=("registry test",),
            ),
        )
    )


def test_compact_capability_parameters_preserve_typed_lists_and_objects() -> None:
    message = "Use registry test with BTC/USDT and ETH/USDT; confirm on close, enabled, on 1h"
    envelope = _envelope(
        message,
        {
            "action": "add_condition",
            "condition": {
                "capability_key": "registry_test",
                "comparator": "is_true",
                "trigger_timeframe": "1h",
                "capability_parameters": [
                    {"name": "symbols", "string_items": ["BTC/USDT", "ETH/USDT"]},
                    {
                        "name": "confirmation",
                        "object_fields": [
                            {"name": "field", "string_value": "close"},
                            {"name": "enabled", "boolean_value": True},
                            {"name": "timeframe", "string_value": "1h"},
                        ],
                    },
                ],
            },
        },
    )
    compiled = compile_planner_intents(
        envelope,
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-typed-parameters",
        shortlist=_parameter_shortlist(),
    )
    condition = compiled.plan.operations[0].condition
    assert condition is not None
    assert condition.capability_parameters == {
        "symbols": ["BTC/USDT", "ETH/USDT"],
        "confirmation": {"field": "close", "enabled": True, "timeframe": "1h"},
    }


def test_compact_capability_parameter_type_mismatch_stops_before_canonical_operation() -> None:
    message = "Use registry test with BTC/USDT"
    envelope = _envelope(
        message,
        {
            "action": "add_condition",
            "condition": {
                "capability_key": "registry_test",
                "comparator": "is_true",
                "trigger_timeframe": "1h",
                "capability_parameters": [
                    {"name": "symbols", "number_value": 14},
                ],
            },
        },
    )
    with pytest.raises(IntentCompileError) as refused:
        compile_planner_intents(
            envelope,
            draft=StrategyDraftV2(),
            message=message,
            source_turn_id="turn-wrong-parameter-type",
            shortlist=_parameter_shortlist(),
        )
    assert refused.value.outcome is SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED
    assert refused.value.target_path == "condition.capability_parameters.symbols"


def test_ambiguous_or_unknown_methodology_fails_closed() -> None:
    message = "Use the standard family methodology"
    envelope = _envelope(
        message,
        {
            "action": "set_sharia_preferences",
            "methodology_family": "standard",
        },
    )
    ambiguous = PlannerReferenceContext(
        methodologies=(
            _methodology(identifier="M1", family="standard"),
            _methodology(
                "methodology_2",
                identifier="M2",
                family="standard",
                method_id="33333333-3333-4333-8333-333333333333",
            ),
        )
    )
    with pytest.raises(IntentCompileError) as ambiguity:
        compile_planner_intents(
            envelope,
            draft=StrategyDraftV2(),
            message=message,
            source_turn_id="turn-ambiguous",
            references=ambiguous,
        )
    assert ambiguity.value.outcome == SemanticIntentOutcome.USER_INFORMATION_REQUIRED

    with pytest.raises(IntentCompileError) as unknown:
        compile_planner_intents(
            envelope,
            draft=StrategyDraftV2(),
            message=message,
            source_turn_id="turn-unknown",
            references=PlannerReferenceContext(),
        )
    assert unknown.value.outcome == SemanticIntentOutcome.NON_RECOVERABLE_FAILURE


def test_halal_question_is_conversation_and_cannot_create_policy_or_status() -> None:
    message = "Is DOGE halal?"
    compiled = compile_planner_intents(
        _envelope(message, None),
        draft=StrategyDraftV2(),
        message=message,
        source_turn_id="turn-question",
    )
    assert compiled.plan.operations == []
    assert compiled.plan.questions_to_answer == [message]


def test_repair_cannot_introduce_a_new_sharia_preference() -> None:
    message = "Use screened assets only"
    envelope = _envelope(
        message,
        {"action": "set_sharia_preferences", "screened_assets_only": True},
    )
    delta = SemanticIntentRepairDelta(
        intent_ref="intent_1",
        target_path="methodology_identifier",
        repair_kind="replace_with_grounded_value",
        replacement_value={"kind": "string", "string_value": "AAOIFI"},
        source_segment_ref="segment_1",
        validation_code="INTENT_VALUE_UNREADABLE",
    )
    repaired = apply_repair_deltas(
        envelope,
        [delta],
        message=message,
        validation_code="INTENT_VALUE_UNREADABLE",
        invalid_intent_ref="intent_1",
        invalid_target_path="screened_assets_only",
    )
    assert repaired.semantic_intents[0].payload.methodology_identifier is None


def test_only_one_exact_model_owned_canonical_failure_is_repairable() -> None:
    message = "Trigger on 15m when close-to-close rises at most 5 percent"
    envelope = _envelope(
        message,
        {
            "action": "add_condition",
            "condition": {
                "formula_key": "close_to_close_percentage",
                "movement_direction": "up",
                "comparator": "lte",
                "threshold": 5,
                "unit": "percent",
                "trigger_timeframe": "15m",
            },
        },
    )
    repairable = _classify_plan_failure(
        code="VALUE_NOT_GROUNDED",
        details=("op_1:condition:threshold:not_grounded",),
        envelope=envelope,
        message=message,
        operation_intent_refs={"op_1": "intent_1"},
        intent_segments={"intent_1": "segment_1"},
    )
    assert repairable.outcome == SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED
    assert repairable.intent_ref == "intent_1"
    assert repairable.target_path == "condition.threshold"

    # Two complaints about the same intent are one correction, not an internal fault.
    # Both fields are named so a single bounded correction can address both; requiring
    # exactly one path here is what sent an ordinary two-field refusal down the
    # terminal branch and left the trader with nothing to do but repeat themselves.
    multi_field = _classify_plan_failure(
        code="VALUE_NOT_GROUNDED",
        details=(
            "op_1:condition:threshold:not_grounded",
            "op_1:condition:operator:not_grounded",
        ),
        envelope=envelope,
        message=message,
        operation_intent_refs={"op_1": "intent_1"},
        intent_segments={"intent_1": "segment_1"},
    )
    assert multi_field.outcome == SemanticIntentOutcome.SEMANTIC_INTENT_REPAIR_REQUIRED
    assert multi_field.intent_ref == "intent_1"
    assert set(multi_field.paths) == {"condition.threshold", "condition.comparator"}

    # Two complaints about *different* intents still cannot be one correction: there is
    # no single verified span that authorises both.
    two_intents = _classify_plan_failure(
        code="VALUE_NOT_GROUNDED",
        details=(
            "op_1:condition:threshold:not_grounded",
            "op_2:condition:threshold:not_grounded",
        ),
        envelope=envelope,
        message=message,
        operation_intent_refs={"op_1": "intent_1", "op_2": "intent_2"},
        intent_segments={"intent_1": "segment_1", "intent_2": "segment_1"},
    )
    assert two_intents.outcome == SemanticIntentOutcome.NON_RECOVERABLE_FAILURE
    assert two_intents.intent_ref is None


def test_opaque_patch_failure_and_canonical_default_never_enter_repair() -> None:
    message = "Trigger on 15m when close-to-close rises at most 5 percent"
    envelope = _envelope(
        message,
        {
            "action": "add_condition",
            "condition": {
                "formula_key": "close_to_close_percentage",
                "movement_direction": "up",
                "comparator": "lte",
                "threshold": 5,
                "unit": "percent",
                "trigger_timeframe": "15m",
            },
        },
    )
    for code, details in (
        ("PATCH_REJECTED", ("op_1:internal validation exception",)),
        ("PATCH_REJECTED", ("op_1:add_condition:threshold:not_grounded",)),
        ("SPAN_NOT_GROUNDED", ("op_1:add_condition:threshold:not_grounded",)),
        ("VALUE_NOT_GROUNDED", ("op_1:condition:strategy_bias:not_grounded",)),
    ):
        classified = _classify_plan_failure(
            code=code,
            details=details,
            envelope=envelope,
            message=message,
            operation_intent_refs={"op_1": "intent_1"},
            intent_segments={"intent_1": "segment_1"},
        )
        # Not repairable, and not a compiler invariant either. A canonical gate refused
        # what the server built; the trader wrote nothing wrong. Calling that
        # COMPILER_INVARIANT_VIOLATION made the name meaningless and hid the real
        # invariants inside a pile of ordinary refusals.
        assert classified.outcome == SemanticIntentOutcome.NON_RECOVERABLE_FAILURE
        # And never as a compiler invariant. That name has to keep meaning "the server
        # built something invalid from a valid reading"; using it as the catch-all for
        # every unattributable refusal buried the real ones and made an ordinary
        # refusal terminal and unexplainable.
        assert classified.failure_class in {
            SetupFailureClass.CANONICAL_VALIDATION_FAILURE,
            SetupFailureClass.GROUNDING_MISMATCH,
        }
        assert classified.failure_class is not SetupFailureClass.COMPILER_INVARIANT_VIOLATION
        assert classified.intent_ref is None
        assert classified.target_path is None


@pytest.mark.parametrize(
    ("source", "path", "replacement"),
    (
        ("at most 5 percent", "condition.threshold", {"kind": "number", "number_value": 5}),
        ("look back 14 candles", "condition.lookback", {"kind": "integer", "integer_value": 14}),
        ("name it Alpha setup", "name", {"kind": "string", "string_value": "Alpha setup"}),
        ("at most", "condition.comparator", {"kind": "enum", "string_value": "lte"}),
        (
            "make the rule optional",
            "condition.required",
            {"kind": "boolean", "boolean_value": False},
        ),
        ("include BTC/USDT", "symbol", {"kind": "symbol", "string_value": "BTC/USDT"}),
        (
            "use 15m as context",
            "condition.context_timeframes",
            {"kind": "timeframe", "string_value": "15m"},
        ),
        (
            "use 15m as context and 1h as context",
            "condition.context_timeframes",
            {"kind": "string_list", "string_items": ["15m", "1h"]},
        ),
        (
            "use levels 20 and 30",
            "condition.capability_parameters.levels",
            {"kind": "number_list", "number_items": [20, 30]},
        ),
        (
            "use periods 14 and 20",
            "condition.capability_parameters.periods",
            {"kind": "integer_list", "integer_items": [14, 20]},
        ),
        (
            "set enabled true and strict false",
            "condition.capability_parameters.flags",
            {"kind": "boolean_list", "boolean_items": [True, False]},
        ),
        (
            "confirmation field close enabled true",
            "condition.capability_parameters.confirmation",
            {
                "kind": "shallow_object",
                "object_fields": [
                    {"name": "field", "string_value": "close"},
                    {"name": "enabled", "boolean_value": True},
                ],
            },
        ),
    ),
)
def test_every_supported_repair_replacement_type_is_source_grounded(
    source: str,
    path: str,
    replacement: dict[str, object],
) -> None:
    typed = RepairReplacementValue.model_validate(replacement)
    assert _repair_value_is_grounded(
        typed.semantic_value(),
        source,
        path=path,
        references=PlannerReferenceContext(),
        replacement_kind=typed.kind,
    )


def test_repair_timeframe_role_cannot_swap() -> None:
    typed = RepairReplacementValue(kind="timeframe", string_value="15m")
    assert not _repair_value_is_grounded(
        typed.semantic_value(),
        "use 15m as the trigger timeframe",
        path="condition.context_timeframes",
        references=PlannerReferenceContext(),
        replacement_kind=typed.kind,
    )

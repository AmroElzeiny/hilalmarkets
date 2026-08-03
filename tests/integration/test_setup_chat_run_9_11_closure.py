"""Every failure evaluator runs 9, 10 and 11 measured, driven through the real path.

The whole production turn runs here: the real planner payload, the real compact
envelope, the real semantic compiler, the real canonical validation and the real
execution. Only the network call is scripted, so what a real model *could* return is
supplied and everything the server does with it is genuine.

A test that starts from a prebuilt ``SetupAgentTurnPlan`` or a prebuilt
``AuthorizedPatchOperation`` proves nothing about this path — it skips the exact stage
where every one of these failures happened.

Baseline the runs measured (40 cases, 19 strict passes):

============================================  ===================================
case                                          what went wrong
============================================  ===================================
``timeframe_mapping-027-1886256349``          multi-omission -> terminal 422
``nested_boolean_logic-001/002/008/017/025``  stated grouping silently flattened
``operator_mapping-026-512624184``            ``at most 1%`` shipped as ``lt 1%``
``precedence_grouping-012/013/015/019/024``   8 identical 422s to one instruction
============================================  ===================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.setup_failure_taxonomy import SetupFailureClass
from ai_market_monitor.engine.validated_intent_snapshot import (
    GroundedRequirement,
    RepeatState,
)
from ai_market_monitor.schemas.strategy import Comparator
from ai_market_monitor.schemas.strategy_draft_v2 import ConditionNodeType, StrategyDraftV2
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentError,
    SetupAgentTurnInput,
    SetupChatAgent,
)

TURN_ID = "turn-closure-0001"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_secret_key="setup-agent-secret-with-at-least-32-characters",
        openai_api_key=SecretStr("test-key"),
        sharia_screening_enforced=False,
        setup_agent_max_estimated_cost_usd_per_turn=5,
    )


def _body(text: str) -> dict[str, Any]:
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 20, "output_tokens": 8},
    }


@dataclass
class Script:
    """What the model returns for each call this turn may make."""

    plan: dict[str, Any] = field(default_factory=dict)
    repair: dict[str, Any] | None = None
    topology_repair: dict[str, Any] | None = None
    reply: str = "Done."
    planner_calls: list[dict[str, Any]] = field(default_factory=list)
    repair_calls: list[dict[str, Any]] = field(default_factory=list)
    topology_calls: list[dict[str, Any]] = field(default_factory=list)
    schema_names: list[str] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            name = body["text"]["format"]["name"]
            self.schema_names.append(name)
            payload = json.loads(body["input"])
            if name == "hilalmarkets_setup_turn_intent":
                self.planner_calls.append(payload)
                return httpx.Response(200, json=_body(json.dumps(self.plan)))
            if name == "hilalmarkets_setup_intent_repair":
                self.repair_calls.append(payload)
                return httpx.Response(
                    200,
                    json=_body(json.dumps(self.repair or {"deltas": [], "cannot_repair": True})),
                )
            if name == "hilalmarkets_setup_boolean_topology_repair":
                self.topology_calls.append(payload)
                return httpx.Response(
                    200,
                    json=_body(
                        json.dumps(
                            self.topology_repair
                            or {
                                "existing_leaf_refs": ["l1"],
                                "groups": [],
                                "root_ref": "l1",
                                "cannot_repair": True,
                            }
                        )
                    ),
                )
            return httpx.Response(
                200,
                json=_body(json.dumps({"message": self.reply, "clarification_question_id": None})),
            )

        return httpx.MockTransport(handler)


async def _run(
    script: Script,
    message: str,
    *,
    draft: StrategyDraftV2 | None = None,
    repeats: RepeatState | None = None,
):
    agent = SetupChatAgent(_settings(), transport=script.transport())
    return await agent.run_turn(
        SetupAgentTurnInput(
            message=message,
            source_turn_id=TURN_ID,
            draft=draft or StrategyDraftV2(),
            repeats=repeats or RepeatState(),
        )
    )


def _segments(*pairs: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {"segment_ref": reference, "exact_source_text": text, "segment_kind": kind}
        for reference, text, kind in pairs
    ]


def _condition(
    quote: str,
    *,
    timeframe: str,
    threshold: float,
    comparator: str = "gte",
    direction: str = "up",
    context: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_quote": quote,
        "formula_key": "close_to_close_percentage",
        "movement_direction": direction,
        "comparator": comparator,
        "threshold": threshold,
        "unit": "percent",
        "trigger_timeframe": timeframe,
        "context_timeframes": context or [],
    }


def _leaves(draft: StrategyDraftV2) -> list[Any]:
    if draft.condition_ast is None:
        return []
    return [
        node
        for node in draft.condition_ast.walk()
        if node.node_type is ConditionNodeType.CONDITION
    ]


def _shape(node: Any) -> str:
    if node.node_type is ConditionNodeType.CONDITION:
        return "leaf"
    children = [_shape(child) for child in node.children]
    if node.node_type in {ConditionNodeType.AND, ConditionNodeType.OR} and len(children) == 1:
        return children[0]
    return f"{node.node_type.value}({','.join(sorted(children))})"


# ---------------------------------------------------------------------------------
# operator_mapping-026-512624184
# ---------------------------------------------------------------------------------


async def test_operator_mapping_026_ships_the_inclusive_ceiling_on_the_first_turn() -> None:
    """Five 422s, then ``lt 1%``. Now: one turn, ``lte 1%``, no correction call.

    The trader wrote an inclusive ceiling. A monitor built with ``lt`` stays silent on
    exactly the 1% move they asked to see, and the only reason it shipped is that
    nothing checked the model's comparator against the words it came from.
    """

    message = (
        "Build a Watchlist for ETHUSDT only and exclude SOLUSDT. Use 1h as context and "
        "15m as the trigger timeframe. Require a bearish close-to-close move of at most "
        "1%. Keep approval explicit."
    )
    rule = (
        "Require a bearish close-to-close move of at most "
        "1%."
    )
    script = Script(
        plan={
            "segments": _segments(
                (
                    "s1",
                    "Build a Watchlist for ETHUSDT only and exclude SOLUSDT.",
                    "STRATEGY_INSTRUCTION",
                ),
                (
                    "s2",
                    "Use 1h as context and 15m as the trigger timeframe.",
                    "STRATEGY_INSTRUCTION",
                ),
                ("s3", rule, "STRATEGY_INSTRUCTION"),
                ("s4", "Keep approval explicit.", "APPROVAL_INTENT"),
            ),
            "semantic_intents": [
                {"segment_ref": "s1", "payload": {"action": "include_symbol", "symbol": "ETHUSDT"}},
                {"segment_ref": "s1", "payload": {"action": "exclude_symbol", "symbol": "SOLUSDT"}},
                {
                    "segment_ref": "s3",
                    "payload": {
                        "action": "add_condition",
                        # The model's wrong answer, exactly as run 11 recorded it.
                        "condition": _condition(
                            rule,
                            timeframe="15m",
                            threshold=1.0,
                            comparator="lt",
                            direction="down",
                            context=["1h"],
                        ),
                    },
                },
            ],
            "approval_intent": {"segment_ref": "s4"},
            "overall_confidence": 0.99,
        }
    )
    result = await _run(script, message)
    leaves = _leaves(result.draft)
    assert len(leaves) == 1
    assert leaves[0].operator is Comparator.LESS_THAN_OR_EQUAL
    assert leaves[0].threshold == 1.0
    assert leaves[0].trigger_timeframe == "15m"
    assert leaves[0].context_timeframes == ["1h"]
    assert result.draft.universe.included_symbols == ["ETH/USDT"]
    assert result.draft.universe.excluded_symbols == ["SOL/USDT"]
    # Correcting a comparator the trader already wrote needs no provider call.
    assert script.repair_calls == []
    assert script.topology_calls == []
    # And the draft is a reviewed preview, never an approval.
    assert not result.draft.approval.approved


# ---------------------------------------------------------------------------------
# timeframe_mapping-027 and precedence_grouping-013/024: the multi-omission 422 loop
# ---------------------------------------------------------------------------------

_LOOP_MESSAGE = (
    "I want a simple watchlist for ETHUSDT, not BTCUSDT. Use the 1-minute chart for "
    "context and the 1-hour chart for the trigger, with a clear bullish move of at "
    "least 2.5% before I approve anything."
)
_LOOP_RULE = (
    "Use the 1-minute chart for context and the 1-hour chart for the trigger, with a "
    "clear bullish move of at least 2.5%"
)


def _loop_script(*, drop: set[str], repair: dict[str, Any] | None = None) -> Script:
    condition = _condition(_LOOP_RULE, timeframe="1h", threshold=2.5, context=["1m"])
    for field_name in drop:
        if field_name.endswith("_timeframes"):
            condition[field_name] = []
        else:
            condition.pop(field_name, None)
    return Script(
        plan={
            "segments": _segments(
                (
                    "s1",
                    "I want a simple watchlist for ETHUSDT, not BTCUSDT.",
                    "STRATEGY_INSTRUCTION",
                ),
                ("s2", _LOOP_RULE, "STRATEGY_INSTRUCTION"),
                ("s3", "before I approve anything.", "APPROVAL_INTENT"),
            ),
            "semantic_intents": [
                {"segment_ref": "s1", "payload": {"action": "include_symbol", "symbol": "ETHUSDT"}},
                {"segment_ref": "s1", "payload": {"action": "exclude_symbol", "symbol": "BTCUSDT"}},
                {
                    "segment_ref": "s2",
                    "payload": {"action": "add_condition", "condition": condition},
                },
            ],
            "approval_intent": {"segment_ref": "s3"},
            "overall_confidence": 0.98,
        },
        repair=repair,
    )


async def test_precedence_grouping_013_no_longer_refuses_a_complete_instruction() -> None:
    """The turn that produced eight identical 422s now applies on the first attempt."""

    result = await _run(_loop_script(drop=set()), _LOOP_MESSAGE)
    leaves = _leaves(result.draft)
    assert len(leaves) == 1
    assert leaves[0].trigger_timeframe == "1h"
    assert leaves[0].context_timeframes == ["1m"]
    assert leaves[0].operator is Comparator.GREATER_THAN_OR_EQUAL
    assert leaves[0].threshold == 2.5
    assert result.draft.universe.included_symbols == ["ETH/USDT"]
    assert result.draft.universe.excluded_symbols == ["BTC/USDT"]


@pytest.mark.parametrize(
    "drop",
    (
        {"movement_direction"},
        {"trigger_timeframe"},
        {"movement_direction", "trigger_timeframe"},
        {"movement_direction", "context_timeframes", "trigger_timeframe"},
    ),
    ids=lambda value: "+".join(sorted(value)),
)
async def test_any_number_of_planner_omissions_is_correctable_not_terminal(
    drop: set[str],
) -> None:
    """One omission or three, the recovery is the same and the draft is untouched.

    Two or more used to be ``COMPILER_INVARIANT_VIOLATION``: terminal, no correction,
    no question, and a message telling the trader nothing changed.
    """

    script = _loop_script(drop=drop)
    with pytest.raises(SetupAgentError) as captured:
        await _run(script, _LOOP_MESSAGE)
    error = captured.value
    assert error.failure_class is not SetupFailureClass.COMPILER_INVARIANT_VIOLATION
    assert error.failure_record is not None
    assert error.failure_record.repair_eligible
    # A correction was attempted, with every omitted field named in one envelope.
    assert len(script.repair_calls) == 1
    named = script.repair_calls[0]["validation"]["paths"]
    assert {path.removeprefix("condition.") for path in named} == drop


async def test_a_correction_that_fixes_every_named_field_recovers_the_turn() -> None:
    """One bounded envelope, several fields, each proved from the same verified span."""

    script = _loop_script(
        drop={"movement_direction", "trigger_timeframe"},
        repair={
            "deltas": [
                {
                    "intent_ref": "intent_3",
                    "target_path": "condition.movement_direction",
                    "repair_kind": "replace_with_grounded_value",
                    "replacement_value": {"kind": "enum", "string_value": "up"},
                    "source_segment_ref": "s2",
                    "validation_code": "PLANNER_SEMANTIC_OMISSION",
                },
                {
                    "intent_ref": "intent_3",
                    "target_path": "condition.trigger_timeframe",
                    "repair_kind": "replace_with_grounded_value",
                    "replacement_value": {"kind": "timeframe", "string_value": "1h"},
                    "source_segment_ref": "s2",
                    "validation_code": "PLANNER_SEMANTIC_OMISSION",
                },
            ],
            "cannot_repair": False,
        },
    )
    result = await _run(script, _LOOP_MESSAGE)
    leaves = _leaves(result.draft)
    assert len(leaves) == 1
    assert leaves[0].trigger_timeframe == "1h"
    assert leaves[0].movement_direction.value == "up"
    assert len(script.repair_calls) == 1


async def test_a_correction_that_changes_nothing_is_not_reported_as_a_success() -> None:
    script = _loop_script(
        drop={"trigger_timeframe"},
        repair={"deltas": [], "cannot_repair": True},
    )
    with pytest.raises(SetupAgentError) as captured:
        await _run(script, _LOOP_MESSAGE)
    assert captured.value.usage.get("_setup_repair_successes") is None
    assert len(script.repair_calls) == 1


async def test_a_repeated_instruction_is_answered_from_what_is_already_known() -> None:
    """The second time, the answer says what was kept and what is still missing.

    Repeating "I could not turn that into an exact change" is what produced eight
    identical turns. It also told the trader to rewrite a sentence that was correct.
    """

    script = _loop_script(drop={"trigger_timeframe"})
    repeats = RepeatState(
        same_intent_retry_count=1,
        same_failure_repeat_count=1,
        reusable_requirements=(
            GroundedRequirement("condition.threshold", "2.5", _LOOP_RULE),
            GroundedRequirement("condition.context_timeframes", "1m", _LOOP_RULE),
        ),
    )
    with pytest.raises(SetupAgentError) as captured:
        await _run(script, _LOOP_MESSAGE, repeats=repeats)
    text = str(captured.value)
    assert "do not need to send it again" in text
    assert "threshold" in text and "context_timeframes" in text
    assert "trigger_timeframe" in text


async def test_a_failure_already_corrected_once_never_pays_for_a_second_call() -> None:
    """The 0-of-18 recovery rate was 18 provider calls that could not have worked."""

    script = _loop_script(drop={"trigger_timeframe"})
    agent = SetupChatAgent(_settings(), transport=script.transport())
    turn = SetupAgentTurnInput(
        message=_LOOP_MESSAGE,
        source_turn_id=TURN_ID,
        draft=StrategyDraftV2(),
    )
    # Learn the fingerprint from a first attempt, then replay it as already-attempted.
    with pytest.raises(SetupAgentError):
        await agent.run_turn(turn)
    fingerprint = turn.telemetry.notes.get("turn_failure_fingerprint")
    assert isinstance(fingerprint, str) and fingerprint

    second = Script(plan=script.plan)
    with pytest.raises(SetupAgentError):
        await _run(
            second,
            _LOOP_MESSAGE,
            repeats=RepeatState(
                same_intent_retry_count=1,
                same_failure_repeat_count=1,
                attempted_fingerprints=(fingerprint,),
            ),
        )
    assert second.repair_calls == []


# ---------------------------------------------------------------------------------
# nested_boolean_logic-001/002/008/017/025 and precedence_grouping-012/015/019
# ---------------------------------------------------------------------------------

_BOOLEAN_MESSAGE = (
    "Alert when the 15m close-to-close move is bullish at least 2% AND "
    "(the 1h close-to-close move is bearish at least 1% OR the 4h close-to-close move "
    "is bullish at least 5%)."
)
_LEAF_A = "the 15m close-to-close move is bullish at least 2%"
_LEAF_B = "the 1h close-to-close move is bearish at least 1%"
_LEAF_C = "the 4h close-to-close move is bullish at least 5%"


def _boolean_structure(groups: list[dict[str, Any]], root: str) -> dict[str, Any]:
    return {
        "condition_leaves": [
            {
                "leaf_ref": "l1",
                "segment_ref": "s1",
                "condition": _condition(_LEAF_A, timeframe="15m", threshold=2.0),
            },
            {
                "leaf_ref": "l2",
                "segment_ref": "s1",
                "condition": _condition(
                    _LEAF_B, timeframe="1h", threshold=1.0, direction="down"
                ),
            },
            {
                "leaf_ref": "l3",
                "segment_ref": "s1",
                "condition": _condition(_LEAF_C, timeframe="4h", threshold=5.0),
            },
        ],
        "boolean_groups": groups,
        "root_ref": root,
    }


_CORRECT_GROUPS = [
    {
        "group_ref": "g1",
        "operator": "or",
        "child_refs": ["l2", "l3"],
        "source_quote": f"{_LEAF_B} OR {_LEAF_C}",
    },
    {
        "group_ref": "g2",
        "operator": "and",
        "child_refs": ["l1", "g1"],
        "source_quote": _BOOLEAN_MESSAGE,
    },
]


def _boolean_script(
    groups: list[dict[str, Any]],
    root: str,
    *,
    message: str = _BOOLEAN_MESSAGE,
    **extra: Any,
) -> Script:
    return Script(
        plan={
            "segments": _segments(("s1", message, "STRATEGY_INSTRUCTION")),
            "semantic_intents": [
                {
                    "segment_ref": "s1",
                    "payload": {
                        "action": "replace_boolean_structure",
                        "boolean_structure": _boolean_structure(groups, root),
                    },
                }
            ],
            "overall_confidence": 0.99,
        },
        **extra,
    )


async def test_a_stated_expression_compiles_to_the_exact_stated_tree() -> None:
    result = await _run(_boolean_script(_CORRECT_GROUPS, "g2"), _BOOLEAN_MESSAGE)
    assert result.draft.condition_ast is not None
    assert _shape(result.draft.condition_ast) == "and(leaf,or(leaf,leaf))"
    assert len(_leaves(result.draft)) == 3


async def test_a_flattened_expression_is_refused_and_the_draft_is_untouched() -> None:
    """``A AND (B OR C)`` returned as ``A AND B AND C`` is a different monitor."""

    script = _boolean_script(
        [
            {
                "group_ref": "g1",
                "operator": "and",
                "child_refs": ["l1", "l2", "l3"],
                "source_quote": _BOOLEAN_MESSAGE,
            }
        ],
        "g1",
    )
    with pytest.raises(SetupAgentError) as captured:
        await _run(script, _BOOLEAN_MESSAGE)
    assert captured.value.failure_class is SetupFailureClass.BOOLEAN_TOPOLOGY_MISSING
    # And a structure-only correction was attempted, not a full re-plan.
    assert len(script.topology_calls) == 1
    assert script.repair_calls == []


async def test_a_structure_only_correction_recovers_the_stated_shape() -> None:
    script = _boolean_script(
        [
            {
                "group_ref": "g1",
                "operator": "and",
                "child_refs": ["l1", "l2", "l3"],
                "source_quote": _BOOLEAN_MESSAGE,
            }
        ],
        "g1",
        topology_repair={
            "existing_leaf_refs": ["l1", "l2", "l3"],
            "groups": _CORRECT_GROUPS,
            "root_ref": "g2",
            "cannot_repair": False,
        },
    )
    result = await _run(script, _BOOLEAN_MESSAGE)
    assert result.draft.condition_ast is not None
    assert _shape(result.draft.condition_ast) == "and(leaf,or(leaf,leaf))"
    assert len(script.topology_calls) == 1


async def test_a_structure_correction_cannot_change_which_rules_exist() -> None:
    """The contract has no condition fields, and dropping a leaf is refused outright."""

    script = _boolean_script(
        [
            {
                "group_ref": "g1",
                "operator": "and",
                "child_refs": ["l1", "l2", "l3"],
                "source_quote": _BOOLEAN_MESSAGE,
            }
        ],
        "g1",
        topology_repair={
            "existing_leaf_refs": ["l1", "l2"],
            "groups": [
                {
                    "group_ref": "g9",
                    "operator": "and",
                    "child_refs": ["l1", "l2"],
                    "source_quote": _BOOLEAN_MESSAGE,
                }
            ],
            "root_ref": "g9",
            "cannot_repair": False,
        },
    )
    with pytest.raises(SetupAgentError):
        await _run(script, _BOOLEAN_MESSAGE)


async def test_a_watchlist_sentence_never_becomes_boolean_logic() -> None:
    """"ETHUSDT, not BTCUSDT" is an inclusion and an exclusion, never ``A AND NOT B``."""

    result = await _run(_loop_script(drop=set()), _LOOP_MESSAGE)
    assert result.draft.condition_ast is not None
    # One rule, joined by nothing: the registry's own root group and a single leaf.
    assert _shape(result.draft.condition_ast) == "leaf"
    assert result.draft.universe.included_symbols == ["ETH/USDT"]
    assert result.draft.universe.excluded_symbols == ["BTC/USDT"]


async def test_an_incomplete_draft_is_never_offered_for_approval() -> None:
    script = _loop_script(drop={"trigger_timeframe"})
    with pytest.raises(SetupAgentError):
        await _run(script, _LOOP_MESSAGE)


async def test_a_boolean_edit_invalidates_an_earlier_approval() -> None:
    """Approval is bound to the executable hash, and structure is part of it."""

    first = await _run(_boolean_script(_CORRECT_GROUPS, "g2"), _BOOLEAN_MESSAGE)
    all_or_message = f"Alert when {_LEAF_A} OR {_LEAF_B} OR {_LEAF_C}."
    all_or = await _run(
        _boolean_script(
            [
                {
                    "group_ref": "g1",
                    "operator": "or",
                    "child_refs": ["l1", "l2", "l3"],
                    "source_quote": all_or_message,
                }
            ],
            "g1",
            message=all_or_message,
        ),
        all_or_message,
    )
    # Same three rules, joined differently. If the hash matched, an approval given for
    # one arrangement would carry over to the other, and the trader would have approved
    # a monitor they never saw.
    assert first.draft.executable_hash != all_or.draft.executable_hash

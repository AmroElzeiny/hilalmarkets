from uuid import uuid4

import pytest

from ai_market_monitor.schemas.agent_control import GetMonitorStatusArgs
from ai_market_monitor.services.agent_policy import (
    AgentPolicyService,
    AgentPolicyViolation,
    AgentRuntimePolicyState,
    AgentServerContext,
    _custom_capability_consent_source,
    _has_explicit_custom_capability_consent,
    _looks_like_setup_language,
)


def _context(**updates) -> AgentServerContext:
    values = {
        "user_id": uuid4(),
        "chat_id": uuid4(),
        "request_text": "RSI below 30 on 15m",
        "chat_status": "interviewing",
        "setup_mode": "monitor",
        "has_draft": False,
        "draft_hash": None,
        "has_pending_clarification": False,
        "explicit_scan_request": False,
        "explicit_revision_request": False,
        "market_question": False,
        "monitor_question": False,
        "setup_language": True,
        "scan_entitled": False,
        "owned_monitor_ids": frozenset(),
    }
    values.update(updates)
    return AgentServerContext(**values)


def test_allowed_tools_are_built_from_authoritative_state() -> None:
    policy = AgentPolicyService()
    monitor_id = uuid4()
    context = _context(
        market_question=True,
        monitor_question=True,
        owned_monitor_ids=frozenset({monitor_id}),
    )
    runtime = AgentRuntimePolicyState()

    assert policy.allowed_tools(context, runtime) == (
        "inspect_current_draft",
        "get_market_snapshot",
        "get_monitor_status",
        "resolve_trading_capabilities",
    )

    runtime.candidate_capability_keys.add("rsi_below")
    runtime.resolution_complete = True
    assert policy.allowed_tools(context, runtime) == (
        "inspect_current_draft",
        "get_market_snapshot",
        "get_monitor_status",
        "resolve_trading_capabilities",
        "validate_capability_selection",
        "compile_strategy_draft",
    )


@pytest.mark.parametrize("tool_name", ["activate_monitor", "execute_python", "place_trade"])
def test_forbidden_tools_fail_closed(tool_name: str) -> None:
    with pytest.raises(AgentPolicyViolation) as caught:
        AgentPolicyService().validate_call(
            tool_name=tool_name,
            raw_arguments={},
            offered_tools=(tool_name,),
            context=_context(),
            runtime=AgentRuntimePolicyState(),
        )
    assert caught.value.code == "forbidden_tool"


def test_unknown_and_not_offered_tools_are_rejected() -> None:
    policy = AgentPolicyService()
    with pytest.raises(AgentPolicyViolation) as unknown:
        policy.validate_call(
            tool_name="invented_market_oracle",
            raw_arguments={},
            offered_tools=(),
            context=_context(),
            runtime=AgentRuntimePolicyState(),
        )
    assert unknown.value.code == "unknown_tool"

    with pytest.raises(AgentPolicyViolation) as not_offered:
        policy.validate_call(
            tool_name="compile_strategy_draft",
            raw_arguments={},
            offered_tools=("resolve_trading_capabilities",),
            context=_context(),
            runtime=AgentRuntimePolicyState(),
        )
    assert not_offered.value.code == "tool_not_offered"


def test_tool_arguments_reject_extras_and_type_coercion() -> None:
    policy = AgentPolicyService()
    with pytest.raises(AgentPolicyViolation) as extras:
        policy.validate_call(
            tool_name="resolve_trading_capabilities",
            raw_arguments={
                "fragments": ["RSI below 30"],
                "default_timeframe": None,
                "user_id": str(uuid4()),
            },
            offered_tools=("resolve_trading_capabilities",),
            context=_context(),
            runtime=AgentRuntimePolicyState(),
        )
    assert extras.value.code == "invalid_tool_arguments"

    with pytest.raises(AgentPolicyViolation) as coercion:
        policy.validate_call(
            tool_name="resolve_trading_capabilities",
            raw_arguments={"fragments": "RSI below 30", "default_timeframe": None},
            offered_tools=("resolve_trading_capabilities",),
            context=_context(),
            runtime=AgentRuntimePolicyState(),
        )
    assert coercion.value.code == "invalid_tool_arguments"

    with pytest.raises(AgentPolicyViolation) as unsupported_exchange:
        policy.validate_call(
            tool_name="get_market_snapshot",
            raw_arguments={"exchange": "kraken", "quote_currency": "USDT"},
            offered_tools=("get_market_snapshot",),
            context=_context(market_question=True),
            runtime=AgentRuntimePolicyState(),
        )
    assert unsupported_exchange.value.code == "invalid_tool_arguments"


def test_monitor_ownership_is_derived_server_side() -> None:
    owned = uuid4()
    foreign = uuid4()
    policy = AgentPolicyService()
    context = _context(
        monitor_question=True,
        owned_monitor_ids=frozenset({owned}),
    )

    decision = policy.validate_call(
        tool_name="get_monitor_status",
        raw_arguments={"monitor_id": str(owned)},
        offered_tools=("get_monitor_status",),
        context=context,
        runtime=AgentRuntimePolicyState(),
    )
    assert isinstance(decision.arguments, GetMonitorStatusArgs)
    assert decision.arguments.monitor_id == str(owned)

    with pytest.raises(AgentPolicyViolation) as caught:
        policy.validate_call(
            tool_name="get_monitor_status",
            raw_arguments={"monitor_id": str(foreign)},
            offered_tools=("get_monitor_status",),
            context=context,
            runtime=AgentRuntimePolicyState(),
        )
    assert caught.value.code == "monitor_not_owned"


def test_scan_requires_current_request_entitlement_and_hash() -> None:
    draft_hash = "a" * 64
    policy = AgentPolicyService()
    runtime = AgentRuntimePolicyState(compiled_hash=draft_hash)
    base = _context(
        setup_mode="scanner",
        explicit_scan_request=True,
        scan_entitled=True,
        draft_hash=draft_hash,
    )

    decision = policy.validate_call(
        tool_name="run_one_time_scan",
        raw_arguments={"expected_draft_hash": draft_hash},
        offered_tools=("run_one_time_scan",),
        context=base,
        runtime=runtime,
    )
    assert decision.classification == "confirmation_required"

    with pytest.raises(AgentPolicyViolation) as entitlement:
        policy.validate_call(
            tool_name="run_one_time_scan",
            raw_arguments={"expected_draft_hash": draft_hash},
            offered_tools=("run_one_time_scan",),
            context=_context(
                setup_mode="scanner",
                explicit_scan_request=True,
                scan_entitled=False,
                draft_hash=draft_hash,
            ),
            runtime=runtime,
        )
    assert entitlement.value.code == "scan_not_entitled"

    with pytest.raises(AgentPolicyViolation) as mismatch:
        policy.validate_call(
            tool_name="run_one_time_scan",
            raw_arguments={"expected_draft_hash": "b" * 64},
            offered_tools=("run_one_time_scan",),
            context=base,
            runtime=runtime,
        )
    assert mismatch.value.code == "draft_hash_mismatch"


def test_approved_chat_never_receives_draft_mutation_tools() -> None:
    policy = AgentPolicyService()
    runtime = AgentRuntimePolicyState(
        candidate_capability_keys={"rsi_below"},
        resolution_complete=True,
    )
    offered = policy.allowed_tools(
        _context(chat_status="approved", explicit_revision_request=True),
        runtime,
    )
    assert "validate_capability_selection" not in offered
    assert "compile_strategy_draft" not in offered


def test_custom_capability_requires_live_scope_consent_limit_and_exact_fragment() -> None:
    policy = AgentPolicyService()
    fragment = "moon-wobble closed candle on 15m"
    context = _context(
        request_text="Yes, build this custom mechanic",
        capability_extension_enabled=True,
        explicit_custom_capability_consent=True,
        custom_capability_source_fragments=frozenset({fragment}),
        custom_capability_requests_today=1,
        custom_capability_daily_limit=3,
    )
    runtime = AgentRuntimePolicyState()

    offered = policy.allowed_tools(context, runtime)
    assert "request_custom_capability" in offered
    decision = policy.validate_call(
        tool_name="request_custom_capability",
        raw_arguments={"source_fragment": fragment, "confirmed_by_user": True},
        offered_tools=offered,
        context=context,
        runtime=runtime,
    )
    assert decision.classification == "confirmation_required"

    with pytest.raises(AgentPolicyViolation) as mismatch:
        policy.validate_call(
            tool_name="request_custom_capability",
            raw_arguments={
                "source_fragment": "a different model-invented mechanic",
                "confirmed_by_user": True,
            },
            offered_tools=offered,
            context=context,
            runtime=runtime,
        )
    assert mismatch.value.code == "custom_capability_source_mismatch"

    exhausted = _context(
        capability_extension_enabled=True,
        explicit_custom_capability_consent=True,
        custom_capability_source_fragments=frozenset({fragment}),
        custom_capability_requests_today=3,
        custom_capability_daily_limit=3,
    )
    assert "request_custom_capability" not in policy.allowed_tools(exhausted, runtime)


def test_capability_selection_is_bound_to_its_own_fragment_not_the_whole_turn() -> None:
    """A multi-condition turn must not let a capability from one clause bind to another's text.

    ``candidate_capability_keys`` and ``candidate_source_fragments`` are each a
    flattened, whole-turn set. On their own they only prove a key and a fragment
    each appeared *somewhere* in the turn's resolution, not that the registry ever
    offered that key *for that wording* — so "RSI below 30 and volume 2x average"
    could let ``volume_ratio`` bind to the RSI clause's text and neither flattened
    check would notice.
    """
    policy = AgentPolicyService()
    runtime = AgentRuntimePolicyState(
        candidate_capability_keys={"rsi_below_30", "volume_ratio"},
        candidate_source_fragments={"rsi below 30", "volume 2x average"},
        candidate_capability_keys_by_fragment={
            "rsi below 30": frozenset({"rsi_below_30"}),
            "volume 2x average": frozenset({"volume_ratio"}),
        },
    )
    context = _context()

    def _call(capability_key: str, source_fragment: str):
        return policy.validate_call(
            tool_name="validate_capability_selection",
            raw_arguments={
                "capability_key": capability_key,
                "parameters": [],
                "timeframe": "15m",
                "direction": None,
                "required": True,
                "source_fragment": source_fragment,
                "comparator": None,
            },
            offered_tools=("validate_capability_selection",),
            context=context,
            runtime=runtime,
        )

    # The correct pairing for each clause clears this policy layer.
    assert _call("rsi_below_30", "RSI below 30").tool_name == "validate_capability_selection"
    assert _call("volume_ratio", "volume 2x average").tool_name == "validate_capability_selection"

    # A capability that only ever scored on the OTHER clause must not bind here,
    # even though both the key and this fragment separately appeared in the turn.
    with pytest.raises(AgentPolicyViolation) as cross_wired:
        _call("volume_ratio", "RSI below 30")
    assert cross_wired.value.code == "capability_not_shortlisted_for_fragment"


def test_multilingual_setup_and_custom_build_consent_are_recognized() -> None:
    assert _looks_like_setup_language(
        "\u0639\u0627\u064a\u0632 \u0623\u0631\u0627\u0642\u0628 \u0643\u0633\u0631 "
        "\u0627\u0644\u0645\u0642\u0627\u0648\u0645\u0629 \u0639\u0644\u0649 15m"
    )
    assert _looks_like_setup_language("3ayez ara2eb kasr el moqawma 15m")
    assert _has_explicit_custom_capability_consent("\u0646\u0639\u0645")
    assert _has_explicit_custom_capability_consent("ah ebniha")
    assert not _has_explicit_custom_capability_consent("Please explain this mechanic")


def test_short_custom_consent_is_bound_to_exact_active_build_question() -> None:
    fragment = "moon-wobble closed candle on 15m"
    key = "capability_meaning_moon_wobble_closed_candle_on_15m"
    resolution = {
        "fragments": [{"fragment": fragment, "status": "unknown"}],
        "unsupported_fragments": [fragment],
    }
    custom_pending = {
        "key": key,
        "clarification": {
            "key": key,
            "question": "Should I build and test that exact rule?",
            "reason": "It must pass deterministic certification.",
            "options": [
                {
                    "key": key,
                    "label": "Build and test this rule",
                    "value": "__build_mechanic__",
                    "action": "build_mechanic",
                }
            ],
        },
    }
    unrelated_pending = {
        "key": "rsi_timeframe",
        "clarification": {
            "key": "rsi_timeframe",
            "question": "Which timeframe should RSI use?",
            "reason": "The timeframe is required.",
            "options": [
                {
                    "key": "rsi_timeframe",
                    "label": "Use trigger timeframe",
                    "value": "Use the trigger timeframe",
                    "action": "answer",
                }
            ],
        },
    }

    assert (
        _custom_capability_consent_source(
            "yes",
            pending=custom_pending,
            capability_resolution=resolution,
        )
        == fragment
    )
    assert (
        _custom_capability_consent_source(
            "yes",
            pending=unrelated_pending,
            capability_resolution=resolution,
        )
        is None
    )
    assert (
        _custom_capability_consent_source(
            "yes",
            pending={},
            capability_resolution=resolution,
        )
        is None
    )

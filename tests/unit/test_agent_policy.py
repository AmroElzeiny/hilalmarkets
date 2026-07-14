from uuid import uuid4

import pytest

from ai_market_monitor.schemas.agent_control import GetMonitorStatusArgs
from ai_market_monitor.services.agent_policy import (
    AgentPolicyService,
    AgentPolicyViolation,
    AgentRuntimePolicyState,
    AgentServerContext,
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

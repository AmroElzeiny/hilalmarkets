from typing import Any
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AISetupChatSession, CapabilityExtension
from ai_market_monitor.schemas.agent_control import (
    CompileStrategyDraftArgs,
    GetMarketSnapshotArgs,
    GetRecentScannerResultArgs,
    RequestCustomCapabilityArgs,
    ResolveTradingCapabilitiesArgs,
    RunOneTimeScanArgs,
    ValidateCapabilitySelectionArgs,
)
from ai_market_monitor.services.agent_policy import AgentServerContext
from ai_market_monitor.services.agent_tools import (
    AgentToolRuntime,
    AgentToolService,
    redact_agent_arguments,
)


class ToolHarness:
    def __init__(self) -> None:
        self.compile_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.snapshot_calls = 0
        self.scan_calls = 0
        self.snapshot_status = "available"

    async def compile(self, _session, _chat, setup_text, bindings):
        self.compile_calls.append((setup_text, bindings))
        return {
            "canonical_hash": "a" * 64,
            "approval_eligible": True,
            "lint_warnings": [],
            "translation_sheet": {"summary": "RSI below 30 on 15m"},
        }

    async def snapshot(self, exchange, quote_currency):
        self.snapshot_calls += 1
        if self.snapshot_status != "available":
            return {
                "status": "unavailable",
                "provider_name": "TestProvider",
                "unavailable_reason": "ticker metadata unavailable",
            }
        return {
            "status": "available",
            "provider_name": "TestProvider",
            "exchange": exchange or "binance",
            "quote_currency": quote_currency,
            "captured_at": "2026-07-14T12:00:00Z",
            "symbols_checked": 3,
        }

    async def scan(self, _session, _chat, draft_hash):
        self.scan_calls += 1
        return {
            "draft_hash": draft_hash,
            "status": "succeeded",
            "symbols_scanned": 3,
            "evidence_refs": ["scan:test-result"],
        }


def _settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        openai_api_key=SecretStr("test-key"),
    )


def _runtime(
    *,
    request_text: str = "RSI below 30 on 15m",
    setup_mode: str = "monitor",
    context_json: dict[str, Any] | None = None,
) -> AgentToolRuntime:
    chat = AISetupChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Agent tools",
        status="interviewing",
        context_json=context_json or {"setup_fragments": []},
    )
    context = AgentServerContext(
        user_id=chat.user_id,
        chat_id=chat.id,
        request_text=request_text,
        chat_status=chat.status,
        setup_mode="scanner" if setup_mode == "scanner" else "monitor",
        has_draft=False,
        draft_hash=(context_json or {}).get("schema_hash"),
        has_pending_clarification=False,
        explicit_scan_request=setup_mode == "scanner",
        explicit_revision_request=False,
        market_question=False,
        monitor_question=False,
        setup_language=True,
        scan_entitled=setup_mode == "scanner",
    )
    return AgentToolRuntime(
        context=context,
        chat=chat,
        history=[],
        setup_fragments=list((context_json or {}).get("setup_fragments") or []),
    )


def _service(harness: ToolHarness) -> AgentToolService:
    return AgentToolService(
        _settings(),
        compile_draft=harness.compile,
        market_snapshot=harness.snapshot,
        run_scanner=harness.scan,
    )


async def test_resolution_uses_only_user_authored_fragments_and_registry_keys(test_context):
    harness = ToolHarness()
    service = _service(harness)
    runtime = _runtime()
    async with test_context["session_factory"]() as session:
        result = await service.execute(
            session,
            runtime,
            call_id="resolve-1",
            tool_name="resolve_trading_capabilities",
            arguments=ResolveTradingCapabilitiesArgs(
                fragments=["RSI below 30 on 15m"],
                default_timeframe="15m",
            ),
        )
    assert result.status == "success"
    assert result.data["candidate_keys"] == ["rsi_threshold"]
    assert runtime.policy_state.candidate_capability_keys == {"rsi_threshold"}
    assert runtime.policy_state.resolution_complete is True

    unauthorized = _runtime()
    async with test_context["session_factory"]() as session:
        rejected = await service.execute(
            session,
            unauthorized,
            call_id="resolve-2",
            tool_name="resolve_trading_capabilities",
            arguments=ResolveTradingCapabilitiesArgs(
                fragments=["MACD crosses above signal on 1h"],
                default_timeframe="1h",
            ),
        )
    assert rejected.status == "validation_error"
    assert unauthorized.setup_fragments == []


async def test_selection_rejects_invented_numeric_values_and_unknown_capabilities(test_context):
    harness = ToolHarness()
    service = _service(harness)
    runtime = _runtime(request_text="strong volume on 15m")
    async with test_context["session_factory"]() as session:
        invented = await service.execute(
            session,
            runtime,
            call_id="validate-1",
            tool_name="validate_capability_selection",
            arguments=ValidateCapabilitySelectionArgs(
                capability_key="volume_ratio",
                parameters=[{"name": "threshold", "value": 1.5}],
                timeframe="15m",
                direction=None,
                required=True,
                source_fragment="strong volume on 15m",
                comparator="gte",
            ),
        )
        unknown = await service.execute(
            session,
            _runtime(),
            call_id="validate-2",
            tool_name="validate_capability_selection",
            arguments=ValidateCapabilitySelectionArgs(
                capability_key="made_up_profitable_signal",
                parameters=[],
                timeframe="15m",
                direction=None,
                required=True,
                source_fragment="RSI below 30 on 15m",
                comparator=None,
            ),
        )
    assert invented.status == "validation_error"
    assert "threshold" in invented.warnings[0]
    assert unknown.status == "validation_error"
    assert "Unknown capability_key" in unknown.warnings[0]


async def test_selection_validates_registered_parameters_and_user_intent(test_context):
    harness = ToolHarness()
    service = _service(harness)
    runtime = _runtime()
    async with test_context["session_factory"]() as session:
        result = await service.execute(
            session,
            runtime,
            call_id="validate-3",
            tool_name="validate_capability_selection",
            arguments=ValidateCapabilitySelectionArgs(
                capability_key="rsi_threshold",
                parameters=[{"name": "threshold", "value": 30}],
                timeframe="15m",
                direction=None,
                required=True,
                source_fragment="RSI below 30 on 15m",
                comparator="lte",
            ),
        )
    assert result.status == "success"
    assert result.data["capability_key"] == "rsi_threshold"
    assert result.data["validated_parameters"] == {"threshold": 30}
    assert result.evidence_refs[0].startswith("capability:rsi_threshold:v")


async def test_selection_rejects_model_invented_direction_and_crossing(test_context):
    service = _service(ToolHarness())
    runtime = _runtime()
    async with test_context["session_factory"]() as session:
        direction = await service.execute(
            session,
            runtime,
            call_id="validate-direction",
            tool_name="validate_capability_selection",
            arguments=ValidateCapabilitySelectionArgs(
                capability_key="rsi_threshold",
                parameters=[{"name": "threshold", "value": 30}],
                timeframe="15m",
                direction="bullish",
                required=True,
                source_fragment="RSI below 30 on 15m",
                comparator="lte",
            ),
        )
        crossing = await service.execute(
            session,
            _runtime(),
            call_id="validate-crossing",
            tool_name="validate_capability_selection",
            arguments=ValidateCapabilitySelectionArgs(
                capability_key="rsi_threshold",
                parameters=[{"name": "threshold", "value": 30}],
                timeframe="15m",
                direction=None,
                required=True,
                source_fragment="RSI below 30 on 15m",
                comparator="crosses_below",
            ),
        )
    assert direction.status == "validation_error"
    assert "direction" in direction.warnings[0]
    assert crossing.status == "validation_error"
    assert "comparator" in crossing.warnings[0]


async def test_compile_receives_user_text_and_verified_bindings_only(test_context):
    harness = ToolHarness()
    service = _service(harness)
    runtime = _runtime(context_json={"setup_fragments": ["RSI below 30 on 15m"]})
    runtime.policy_state.resolution_complete = True
    runtime.validated_bindings = [
        {
            "capability_key": "rsi_threshold",
            "parameters": {"threshold": 30},
            "timeframe": "15m",
            "required": True,
            "source_fragment": "RSI below 30 on 15m",
        }
    ]
    async with test_context["session_factory"]() as session:
        result = await service.execute(
            session,
            runtime,
            call_id="compile-1",
            tool_name="compile_strategy_draft",
            arguments=CompileStrategyDraftArgs(),
        )
    assert result.status == "success"
    assert result.evidence_refs == [f"strategy-draft:{'a' * 64}"]
    assert harness.compile_calls == [
        ("RSI below 30 on 15m", runtime.validated_bindings)
    ]


async def test_market_snapshot_is_evidence_backed_or_explicitly_unavailable(test_context):
    harness = ToolHarness()
    service = _service(harness)
    runtime = _runtime(request_text="How is the market today?")
    async with test_context["session_factory"]() as session:
        available = await service.execute(
            session,
            runtime,
            call_id="snapshot-1",
            tool_name="get_market_snapshot",
            arguments=GetMarketSnapshotArgs(exchange=None, quote_currency=None),
        )
        harness.snapshot_status = "unavailable"
        unavailable = await service.execute(
            session,
            runtime,
            call_id="snapshot-2",
            tool_name="get_market_snapshot",
            arguments=GetMarketSnapshotArgs(exchange=None, quote_currency=None),
        )
    assert available.status == "success"
    assert available.data["quote_currency"] == "USDT"
    assert available.evidence_refs
    assert unavailable.status == "unavailable"
    assert unavailable.evidence_refs == []


async def test_custom_capability_tool_does_not_queue_provider_only_data(test_context):
    service = _service(ToolHarness())
    runtime = _runtime(request_text="Use whale wallets and liquidation heatmaps")
    async with test_context["session_factory"]() as session:
        result = await service.execute(
            session,
            runtime,
            call_id="custom-provider-only-1",
            tool_name="request_custom_capability",
            arguments=RequestCustomCapabilityArgs(
                source_fragment="Use whale wallets and liquidation heatmaps",
                confirmed_by_user=True,
            ),
        )
        queued = int(await session.scalar(select(func.count(CapabilityExtension.id))) or 0)

    assert result.status == "validation_error"
    assert result.data["queued"] is False
    assert result.data["dependency_category"] in {"on_chain_or_wallets", "derivatives"}
    assert queued == 0


async def test_scanner_reuses_same_draft_result_without_second_execution(test_context):
    harness = ToolHarness()
    draft_hash = "c" * 64
    existing = {
        "draft_hash": draft_hash,
        "status": "succeeded",
        "evidence_refs": ["scan:existing"],
    }
    runtime = _runtime(
        setup_mode="scanner",
        context_json={"schema_hash": draft_hash, "scanner_result": existing},
    )
    service = _service(harness)
    async with test_context["session_factory"]() as session:
        result = await service.execute(
            session,
            runtime,
            call_id="scan-1",
            tool_name="run_one_time_scan",
            arguments=RunOneTimeScanArgs(expected_draft_hash=draft_hash),
        )
    assert result.status == "success"
    assert result.data == existing
    assert harness.scan_calls == 0
    assert "already scanned" in result.warnings[0]


async def test_recent_scanner_result_preserves_authoritative_evidence_refs(test_context):
    runtime = _runtime(
        setup_mode="scanner",
        context_json={
            "scanner_result": {
                "status": "succeeded",
                "evidence_refs": ["scan:job:one", "scan:proof:two"],
                "results": [],
            }
        },
    )
    async with test_context["session_factory"]() as session:
        result = await _service(ToolHarness()).execute(
            session,
            runtime,
            call_id="recent-scan-1",
            tool_name="get_recent_scanner_result",
            arguments=GetRecentScannerResultArgs(),
        )

    assert result.status == "success"
    assert result.evidence_refs == ["scan:job:one", "scan:proof:two"]


def test_openai_tool_schemas_are_strict_and_redaction_does_not_store_prompt_text() -> None:
    harness = ToolHarness()
    service = _service(harness)
    runtime = _runtime()
    tools = service.openai_tools(
        (
            "resolve_trading_capabilities",
            "get_market_snapshot",
            "compile_strategy_draft",
        ),
        context=runtime.context,
    )

    def assert_strict(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties") or {})
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    for tool in tools:
        assert tool["strict"] is True
        assert_strict(tool["parameters"])

    redacted = redact_agent_arguments(
        "resolve_trading_capabilities",
        ResolveTradingCapabilitiesArgs(
            fragments=["RSI below 30 on 15m"],
            default_timeframe="15m",
        ),
    )
    assert "RSI below" not in str(redacted)
    assert redacted["arguments"]["fragments"][0]["characters"] == 19

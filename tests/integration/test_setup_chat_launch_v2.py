import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from ai_market_monitor.api.routers.dashboard_api import get_ai_setup_chat_service
from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AISetupChatMessage,
    AISetupChatSession,
    AIUsageEvent,
    SetupChatDraftSnapshot,
    SetupChatOperationalIssue,
    SetupChatTurn,
    User,
)
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
from ai_market_monitor.engine.strategy_draft_v2 import apply_strategy_patch
from ai_market_monitor.schemas.setup_agent import (
    SegmentKind,
    SetupAgentPlanEnvelope,
    SetupAgentTurnPlan,
    StrategyInstructionPlan,
    TurnSegment,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ConditionUpdateV2,
    ProviderRuntimeStatusV2,
    StrategyDraftV2,
    StrategyPatch,
)
from ai_market_monitor.services.ai_setup_chat import AISetupChatService, SetupChatError
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.setup_chat_agent import SetupAgentError, SetupChatAgent
from ai_market_monitor.services.setup_chat_launch import (
    SetupChatLaunchService,
    SetupLaunchError,
    load_strategy_draft_v2,
)
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch
from tests.integration.test_ai_setup_chat_api import _signup
from tests.support.setup_agent_plans import operations_from_patch, planner_envelope_json


class MarketProvider:
    def __init__(self):
        self.fetches: list[tuple[str, str]] = []

    async def list_symbols(self, exchange, quote_currencies):
        return ["BTC/USDT", "ETH/USDT"]

    async def fetch_ohlcv(self, exchange, symbol, timeframe, limit):
        self.fetches.append((symbol, timeframe))
        minutes = {"1m": 1, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(
            timeframe,
            15,
        )
        end = datetime.now(UTC) - timedelta(minutes=minutes)
        return [
            Candle(
                timestamp=end - timedelta(minutes=minutes * offset),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
            )
            for offset in range(limit - 1, -1, -1)
        ]


class StandInPlanner:
    """A model stand-in that segments the turn and reuses the deterministic parser.

    Free text now reaches the Setup Agent, so these tests drive the agent rather than
    the patch extractor. The one network call is faked: the real planner
    payload, the real `apply_setup_turn` checks and the real compiler all run.
    """

    def __init__(self, *, failure: Exception | None = None) -> None:
        self.plan_calls = 0
        self.reply_calls = 0
        self.failure = failure

    def _body(self, text: str) -> dict:
        return {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 12, "output_tokens": 6},
        }

    def _envelope(self, message: str, turn_id: str) -> SetupAgentPlanEnvelope:
        patch = deterministic_strategy_patch(StrategyDraftV2(), message, source_turn_id=turn_id)
        kind = SegmentKind.STRATEGY_INSTRUCTION if patch is not None else SegmentKind.SOCIAL_REPLY
        segment = TurnSegment(
            segment_id="s1",
            exact_source_text=message,
            start_offset=0,
            end_offset=len(message),
            kind=kind,
            action_required=patch is not None,
            reply_required=patch is None,
            confidence=0.95,
        )
        plan = SetupAgentTurnPlan(
            source_turn_id=turn_id,
            segments=[segment],
            operations=(operations_from_patch(patch, segment_id="s1") if patch is not None else []),
            strategy_instructions=(
                [StrategyInstructionPlan(segment_id="s1", intent_summary=message[:200])]
                if patch is not None
                else []
            ),
            overall_confidence=0.95,
        )
        return SetupAgentPlanEnvelope(
            plan=plan,
            direct_reply=None if patch is not None else "Happy to help — what should I watch?",
        )

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            payload = json.loads(body["input"])
            if body["text"]["format"]["name"] == "hilalmarkets_setup_turn_intent":
                self.plan_calls += 1
                if self.failure is not None:
                    raise self.failure
                envelope = self._envelope(payload["current_user_turn"], "server-owned-turn")
                return httpx.Response(200, json=self._body(planner_envelope_json(envelope)))
            self.reply_calls += 1
            return httpx.Response(
                200,
                json=self._body(
                    json.dumps(
                        {
                            "message": "Done — the AI Sheet is updated.",
                            "clarification_question_id": None,
                        }
                    )
                ),
            )

        return httpx.MockTransport(handler)


class PaidRejectedAgent:
    """Simulate a completed paid plan rejected by deterministic authorization."""

    async def run_turn(self, turn):
        turn.telemetry.record_model_call("planner_provider_wait")
        turn.telemetry.record_provider_call()
        turn.telemetry.notes.update(
            {
                "combined_estimated_cost_usd": 0.01,
                "combined_actual_cost_usd": 0.01,
                "planner_attempt_count": 1,
                "planner_repair_attempt_count": 0,
                "planner_repair_success_count": 0,
            }
        )
        raise SetupAgentError(
            "VALUE_NOT_GROUNDED",
            "The proposed threshold is not grounded in the user's wording.",
            stage="tool_validation",
            usage={
                "input_tokens": 1_200,
                "output_tokens": 300,
                "input_tokens_details": {"cached_tokens": 200},
                "output_tokens_details": {"reasoning_tokens": 120},
                "_traceedge_model": "gpt-5.4-mini",
                "_traceedge_reasoning_effort": "low",
                "_setup_service_tier": "fast",
            },
        )


def _agent(base: Settings, planner: StandInPlanner) -> SetupChatAgent:
    return SetupChatAgent(_launch_settings(base), transport=planner.transport())


def _launch_settings(base: Settings) -> Settings:
    return base.model_copy(
        update={
            "setup_chat_legacy_test_compat_enabled": False,
            "sharia_screening_enforced": False,
            "openai_api_key": SecretStr("unused-test-key"),
        }
    )


async def _user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name="Launch V2 Test")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_launch_pipeline_conversation_does_not_mutate_and_strategy_compiles(
    test_context,
):
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        initial = load_strategy_draft_v2(chat)

        await service.handle_message(
            session,
            chat,
            message="Hi, how are you?",
            client_message_id="launch-v2-greeting",
        )
        after_greeting = load_strategy_draft_v2(chat)
        assert after_greeting.semantic_hash == initial.semantic_hash
        assert after_greeting.version == initial.version
        # The greeting *does* reach the agent now — that is the point of the rebuild.
        # It simply changes nothing and runs no tool.
        assert planner.plan_calls == 1
        assert planner.reply_calls == 0, "conversation needs no second call"

        await service.handle_message(
            session,
            chat,
            message=(
                "Monitor BTC/USDT when the 15m candle rises open-to-close "
                "by at least 5%, excluding ETH/USDT"
            ),
            client_message_id="launch-v2-strategy",
        )
        draft = load_strategy_draft_v2(chat)

        assert planner.plan_calls == 2, "every ordinary free-text turn is planned first"
        assert chat.status == "ready_for_approval"
        assert chat.draft_schema_json is not None
        assert draft.universe.included_symbols == ["BTC/USDT"]
        assert draft.universe.excluded_symbols == ["ETH/USDT"]
        assert draft.approval_eligible


async def test_launch_pipeline_idempotent_retry_uses_no_second_extraction(test_context):
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        kwargs = {
            "message": ("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"),
            "client_message_id": "launch-v2-idempotent",
        }
        await service.handle_message(session, chat, **kwargs)
        first = load_strategy_draft_v2(chat)
        turn = await session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == "launch-v2-idempotent",
            )
        )
        assert turn is not None
        assert turn.status == "COMPLETED"
        assert turn.mutation_committed is True
        assert turn.reply_json and turn.reply_json["execution_result"]
        assert turn.telemetry_json is not None
        assert turn.telemetry_json["stage_counts"]["total_turn"] == 1
        assert turn.telemetry_json["stage_counts"]["persistence"] >= 1
        assistant_count = await session.scalar(
            select(func.count(AISetupChatMessage.id)).where(
                AISetupChatMessage.session_id == chat.id,
                AISetupChatMessage.role == "assistant",
            )
        )
        snapshot_count = await session.scalar(
            select(func.count(SetupChatDraftSnapshot.id)).where(
                SetupChatDraftSnapshot.chat_session_id == chat.id,
                SetupChatDraftSnapshot.user_id == user.id,
            )
        )
        measured_before_replay = dict((chat.context_json or {})["turn_runtime"]["measured"])
        # Operational gates block new work, not the exact read-only result already
        # committed for this idempotency key.
        service.settings.setup_chat_emergency_disabled = True
        await service.handle_message(session, chat, **kwargs)
        second = load_strategy_draft_v2(chat)

        assert planner.plan_calls == 1
        replay = chat._setup_replayed_turn
        assert replay["reply"] == turn.reply_json
        assert replay["execution"] == turn.execution_result_json
        assert second.version == first.version
        assert second.semantic_hash == first.semantic_hash
        assert (chat.context_json or {})["turn_runtime"]["measured"] == measured_before_replay
        assert turn.telemetry_json == measured_before_replay
        replay_runtime = (chat.context_json or {})["last_idempotent_replay"]
        assert replay_runtime["client_message_id"] == "launch-v2-idempotent"
        assert replay_runtime["cache_hit"] is True
        assert replay_runtime["duration_ms"] >= 0
        assert (
            await session.scalar(
                select(func.count(AISetupChatMessage.id)).where(
                    AISetupChatMessage.session_id == chat.id,
                    AISetupChatMessage.role == "assistant",
                )
            )
            == assistant_count
        )
        assert snapshot_count == 2, "both the before and public final version are restorable"


async def test_allowlisted_ui_option_uses_canonical_turn_without_ai(test_context):
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        before = load_strategy_draft_v2(chat)

        await service.handle_message(
            session,
            chat,
            message="",
            option_key="setup_mode",
            option_value="scanner",
            option_label="Scanner",
            client_message_id="launch-v2-ui-scanner",
        )

        after = load_strategy_draft_v2(chat)
        turn = await session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == "launch-v2-ui-scanner",
            )
        )
        assert planner.plan_calls == 0
        assert after.mode.value == "scanner"
        assert after.executable_version == before.executable_version + 1
        assert turn is not None and turn.status == "COMPLETED"
        execution = turn.reply_json["execution_result"]
        assert execution["operation_results"][0]["operation_kind"] == "set_fields"
        assert execution["operation_results"][0]["operation_id"].startswith("ui_setup_mode_")


@pytest.mark.parametrize("typed", ("Scanner", "scanner", "  Scanner  "))
async def test_typing_scanner_activates_the_same_mode_as_the_button(test_context, typed):
    """One mode change, one route.

    Typing the word used to be answered conversationally, which left ``draft.mode`` on
    Monitor. Governed scan execution reads ``draft.mode``, so the next market question
    then refused itself for being in the wrong mode — after the trader had chosen it.
    """

    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        before = load_strategy_draft_v2(chat)

        await service.handle_message(
            session,
            chat,
            message=typed,
            client_message_id=f"launch-v2-typed-{typed.strip()}-{len(typed)}",
        )

        after = load_strategy_draft_v2(chat)
        assert after.mode.value == "scanner"
        assert after.executable_version == before.executable_version + 1
        # And it cost no model call, exactly like the button.
        assert planner.plan_calls == 0


async def test_a_typed_mode_word_never_approves_or_builds_a_rule(test_context):
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)

        await service.handle_message(
            session, chat, message="Scanner", client_message_id="launch-v2-typed-safety"
        )

        after = load_strategy_draft_v2(chat)
        assert after.condition_ast is None
        assert not after.approval.approved


async def test_runtime_preflight_verifies_every_explicit_symbol_and_timeframe(test_context):
    provider = MarketProvider()
    owner = AISetupChatService(
        _launch_settings(test_context["settings"]),
        provider,
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    draft = StrategyDraftV2()
    patch = deterministic_strategy_patch(
        draft,
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%",
        source_turn_id="turn-preflight-base",
    )
    assert patch is not None
    draft = apply_strategy_patch(draft, patch).draft
    condition = draft.condition_ast
    assert condition is not None
    draft = apply_strategy_patch(
        draft,
        StrategyPatch(
            source_turn_id="turn-preflight-roles",
            add_inclusions=["ETH/USDT"],
            update_conditions=[
                ConditionUpdateV2(
                    node_id=condition.node_id,
                    replacement=condition.model_copy(
                        update={
                            "context_timeframes": ["4h"],
                            "confirmation_timeframes": ["1h"],
                        }
                    ),
                )
            ],
        ),
    ).draft
    definition = compile_strategy_draft_v2(draft)
    launch = SetupChatLaunchService(
        _launch_settings(test_context["settings"]),
        owner,
        agent=owner.launch_agent,
    )

    statuses = await launch._runtime_preflight()(definition)

    expected = {
        (symbol, timeframe)
        for symbol in ("BTC/USDT", "ETH/USDT")
        for timeframe in ("15m", "1h", "4h")
    }
    assert set(provider.fetches) == expected
    assert len(statuses) == len(expected)
    assert all(item.status == "available" for item in statuses)


async def test_runtime_preflight_rejects_stale_candles(test_context):
    class StaleProvider(MarketProvider):
        async def fetch_ohlcv(self, exchange, symbol, timeframe, limit):
            candles = await super().fetch_ohlcv(exchange, symbol, timeframe, limit)
            return [
                Candle(
                    timestamp=item.timestamp - timedelta(days=14),
                    open=item.open,
                    high=item.high,
                    low=item.low,
                    close=item.close,
                    volume=item.volume,
                    is_closed=item.is_closed,
                )
                for item in candles
            ]

    provider = StaleProvider()
    owner = AISetupChatService(
        _launch_settings(test_context["settings"]),
        provider,
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%",
        source_turn_id="turn-stale-preflight",
    )
    assert patch is not None
    definition = compile_strategy_draft_v2(apply_strategy_patch(StrategyDraftV2(), patch).draft)
    launch = SetupChatLaunchService(
        _launch_settings(test_context["settings"]),
        owner,
        agent=owner.launch_agent,
    )

    statuses = await launch._runtime_preflight()(definition)

    assert statuses
    assert all(item.status == "unavailable" for item in statuses)
    assert all("stale" in (item.safe_error or "").casefold() for item in statuses)


async def test_runtime_preflight_checks_available_pairs_even_when_one_market_is_missing(
    test_context,
):
    class PartialProvider(MarketProvider):
        async def list_symbols(self, exchange, quote_currencies):
            return ["BTC/USDT"]

    provider = PartialProvider()
    owner = AISetupChatService(
        _launch_settings(test_context["settings"]),
        provider,
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%",
        source_turn_id="turn-partial-preflight",
    )
    assert patch is not None
    draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
    draft = apply_strategy_patch(
        draft,
        StrategyPatch(
            source_turn_id="turn-partial-preflight-include",
            add_inclusions=["ETH/USDT"],
        ),
    ).draft
    definition = compile_strategy_draft_v2(draft)
    launch = SetupChatLaunchService(
        _launch_settings(test_context["settings"]),
        owner,
        agent=owner.launch_agent,
    )

    statuses = await launch._runtime_preflight()(definition)

    assert provider.fetches == [("BTC/USDT", "15m")]
    assert any(
        item.capability == "market:ETH/USDT" and item.status == "unavailable" for item in statuses
    )
    assert any(
        item.capability == "market:BTC/USDT:15m" and item.status == "available" for item in statuses
    )


async def test_approval_revalidation_rejects_stale_provider_evidence(test_context):
    user = await _user(test_context)
    settings = _launch_settings(test_context["settings"])
    owner = AISetupChatService(
        settings,
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    async with test_context["session_factory"]() as session:
        chat = await owner.create_session(session, user.id)
        patch = deterministic_strategy_patch(
            StrategyDraftV2(),
            "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%",
            source_turn_id="turn-stale-approval",
        )
        assert patch is not None
        draft = apply_strategy_patch(StrategyDraftV2(), patch).draft
        definition = compile_strategy_draft_v2(draft)
        launch = SetupChatLaunchService(settings, owner, agent=owner.launch_agent)

        async def stale_preflight(
            _definition: StrategyDefinition,
        ) -> list[ProviderRuntimeStatusV2]:
            return [
                ProviderRuntimeStatusV2(
                    provider="MarketProvider",
                    capability="market:BTC/USDT:15m",
                    status="available",
                    checked_at=datetime.now(UTC)
                    - timedelta(seconds=settings.setup_provider_preflight_ttl_seconds + 1),
                )
            ]

        launch._runtime_preflight = lambda: stale_preflight  # type: ignore[method-assign]
        with pytest.raises(SetupLaunchError) as error:
            await launch.revalidate_for_approval(
                session,
                chat,
                draft,
                expected_executable_version=draft.executable_version,
                expected_executable_hash=draft.executable_hash,
                expected_schema_hash=definition.canonical_hash(),
            )

        assert error.value.code == "PROVIDER_PREFLIGHT_STALE"


async def test_legacy_session_sharia_policy_migrates_to_one_v2_authority(test_context):
    user = await _user(test_context)
    settings = _launch_settings(test_context["settings"])
    owner = AISetupChatService(
        settings,
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    methodology_id = uuid4()
    async with test_context["session_factory"]() as session:
        chat = await owner.create_session(session, user.id)
        chat.context_json = {
            "setup_mode": "monitor",
            "screened_universe_mode": "explicit_assets",
            "screened_explicit_symbols": ["BTC/USDT"],
            "sharia_methodology_id": str(methodology_id),
            "sharia_methodology_version": "2026.07",
            "allowed_sharia_statuses": ["eligible"],
            "qualification_policy": "exclude",
            "disputed_asset_policy": "exclude",
            "compliance_change_behavior": "pause_asset",
        }

        draft = load_strategy_draft_v2(chat)
        context = dict(chat.context_json or {})

        assert draft.schema_version == "2.2"
        assert draft.executable_version == 2
        assert draft.sharia_policy.methodology_id == methodology_id
        assert draft.sharia_policy.methodology_version == "2026.07"
        assert draft.sharia_policy.explicit_symbols == ["BTC/USDT"]
        assert draft.approval.approved is False
        assert context["sharia_policy_authority"] == "strategy_draft_v2"
        assert "screened_universe_mode" not in context
        assert "sharia_methodology_id" not in context


async def test_invalid_legacy_sharia_values_migrate_fail_closed(test_context):
    user = await _user(test_context)
    owner = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    async with test_context["session_factory"]() as session:
        chat = await owner.create_session(session, user.id)
        chat.context_json = {
            "screened_universe_mode": "guess_everything",
            "allowed_sharia_statuses": ["eligible", "probably_fine"],
            "compliance_change_behavior": "keep_running_silently",
        }

        draft = load_strategy_draft_v2(chat)

        assert {item.unresolved_id for item in draft.unresolved_fields} == {
            "sharia.universe_mode",
            "sharia.allowed_statuses",
            "sharia.compliance_change_behavior",
        }
        assert draft.authoring_blocking is True
        assert draft.approval.approved is False


async def test_stale_planned_turn_cannot_overwrite_a_newer_draft(test_context):
    user = await _user(test_context)
    settings = _launch_settings(test_context["settings"])
    owner = AISetupChatService(
        settings,
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    launch = SetupChatLaunchService(settings, owner, agent=owner.launch_agent)
    async with test_context["session_factory"]() as session:
        chat = await owner.create_session(session, user.id)
        before = load_strategy_draft_v2(chat)
        turn = await launch._get_or_create_turn(
            session,
            chat,
            "concurrent-stale-turn",
        )
        patch = deterministic_strategy_patch(
            before,
            "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 4%",
            source_turn_id="newer-turn",
        )
        assert patch is not None
        newer = apply_strategy_patch(before, patch).draft
        context = dict(chat.context_json or {})
        context["strategy_draft_v2"] = newer.model_dump(mode="json")
        chat.context_json = context
        await session.commit()

        callback = launch._turn_stage_callback(
            session,
            chat,
            turn,
            message="exclude ETH/USDT",
            source_turn_id=str(uuid4()),
            expected_executable_hash=before.executable_hash,
            expected_workflow_state_hash=before.workflow_state_hash,
        )
        with pytest.raises(SetupLaunchError) as error:
            await callback(
                "EXECUTING",
                {"planner_model": "test-model", "plan": {}},
            )

        assert error.value.code == "SETUP_TURN_CONFLICT"
        assert load_strategy_draft_v2(chat).executable_hash == newer.executable_hash


async def test_committed_execution_recovers_without_reapplying_or_replanning(test_context):
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        request = {
            "message": ("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 4%"),
            "client_message_id": "launch-v2-recover-committed",
        }
        await service.handle_message(session, chat, **request)
        committed = load_strategy_draft_v2(chat)
        turn = await session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == request["client_message_id"],
            )
        )
        assert turn is not None and turn.execution_result_json
        assistant = await session.get(AISetupChatMessage, turn.assistant_message_id)
        assert assistant is not None
        await session.delete(assistant)
        turn.assistant_message_id = None
        turn.reply_json = None
        turn.status = "COMPOSING"
        turn.completed_at = None
        await session.commit()

        await service.handle_message(session, chat, **request)
        recovered = load_strategy_draft_v2(chat)

        assert planner.plan_calls == 1
        assert planner.reply_calls == 0
        assert recovered.executable_version == committed.executable_version
        assert recovered.executable_hash == committed.executable_hash
        assert turn.status == "COMPLETED"
        assert turn.reply_json and turn.reply_json["execution_result"]


async def test_repeated_identical_text_is_understood_again_but_changes_nothing(
    test_context,
):
    """The same words can mean different things at different points in a conversation.

    The old path skipped the model whenever a message's text hash repeated. In a
    context-aware agent that is wrong — `yes` twice answers two different questions —
    so the text cache is gone. A genuine retry is still free: it is caught earlier by
    ``client_message_id``. Repeating the words costs one planning call and, because the
    plan is already reflected, leaves the draft untouched. Both ordinary free-text
    messages still reach the AI planner first.
    """
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        message = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"
        await service.handle_message(
            session,
            chat,
            message=message,
            client_message_id="launch-v2-repeat-first",
        )
        first = load_strategy_draft_v2(chat)
        await service.handle_message(
            session,
            chat,
            message=message,
            client_message_id="launch-v2-repeat-second",
        )
        second = load_strategy_draft_v2(chat)

        assert planner.plan_calls == 2
        assert second.version == first.version, "an identical patch is not a new version"
        assert second.semantic_hash == first.semantic_hash


async def test_launch_pipeline_approval_binds_exact_v2_and_retries_idempotently(
    test_context,
):
    user = await _user(test_context)
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message=("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"),
            client_message_id="launch-v2-before-approval",
        )
        draft = load_strategy_draft_v2(chat)
        assert chat.draft_schema_json is not None
        schema_hash = StrategyDefinition.model_validate(chat.draft_schema_json).canonical_hash()

        await service.approve_draft(
            session,
            chat,
            expected_schema_hash=schema_hash,
            expected_executable_version=draft.executable_version,
            expected_executable_hash=draft.executable_hash,
        )
        await session.flush()

        approved = load_strategy_draft_v2(chat)
        assert chat.status == "approved"
        assert approved.approval.approved
        assert approved.approval.user_id == user.id
        assert approved.approval.draft_version == draft.version
        assert approved.approval.semantic_hash == draft.semantic_hash
        strategy_id = chat.approved_strategy_id
        version_id = chat.approved_strategy_version_id

        await service.approve_draft(
            session,
            chat,
            expected_schema_hash=schema_hash,
            expected_executable_version=draft.executable_version,
            expected_executable_hash=draft.executable_hash,
        )

        assert chat.approved_strategy_id == strategy_id
        assert chat.approved_strategy_version_id == version_id

        await service.handle_message(
            session,
            chat,
            message="Thanks, that is clear.",
            client_message_id="launch-v2-approved-conversation",
        )
        after_conversation = load_strategy_draft_v2(chat)
        assert chat.status == "approved"
        assert after_conversation.executable_hash == approved.executable_hash
        assert after_conversation.executable_version == approved.executable_version
        assert after_conversation.workflow_revision == approved.workflow_revision
        assert after_conversation.approval == approved.approval

        await service.handle_message(
            session,
            chat,
            message=("Also require the 1h candle to fall close-to-close by at most -2%"),
            client_message_id="launch-v2-material-edit",
        )
        changed = load_strategy_draft_v2(chat)

        assert changed.version == draft.version + 1
        assert not changed.approval.approved
        assert chat.status == "ready_for_approval"
        assert chat.approved_strategy_id is None
        assert chat.approved_strategy_version_id is None
        assert len((chat.context_json or {}).get("previous_approvals") or []) == 1


async def test_launch_pipeline_error_preserves_authoritative_draft(test_context):
    user = await _user(test_context)
    settings = _launch_settings(test_context["settings"])
    working = AISetupChatService(
        settings,
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    async with test_context["session_factory"]() as session:
        chat = await working.create_session(session, user.id)
        await working.handle_message(
            session,
            chat,
            message=("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"),
            client_message_id="launch-v2-before-error",
        )
        authoritative = load_strategy_draft_v2(chat)

        failing = AISetupChatService(
            settings,
            MarketProvider(),
            RuleBasedStrategyInterpreter(),
            launch_agent=_agent(
                test_context["settings"],
                StandInPlanner(failure=httpx.ReadTimeout("the provider timed out")),
            ),
        )
        try:
            await failing.handle_message(
                session,
                chat,
                message="Add RSI below 30 on 15m",
                client_message_id="launch-v2-error",
            )
        except SetupChatError as exc:
            assert exc.code == "TARGET_READ_TIMEOUT"
            assert exc.retryable
        else:
            raise AssertionError("provider failure must return a structured setup error")

        preserved = load_strategy_draft_v2(chat)
        assert preserved.version == authoritative.version
        assert preserved.semantic_hash == authoritative.semantic_hash
        assert preserved.draft_id == authoritative.draft_id
        failed_turn = await session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == "launch-v2-error",
            )
        )
        assert failed_turn is not None
        assert failed_turn.telemetry_json is not None
        assert failed_turn.telemetry_json["model_calls"] == 1
        assert failed_turn.telemetry_json["provider_calls"] == 1
        assert failed_turn.telemetry_json["stage_counts"]["total_turn"] == 1
        assert failed_turn.telemetry_json["stage_counts"]["persistence"] >= 1


async def test_paid_planner_usage_is_recorded_when_grounding_rejects_turn(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=PaidRejectedAgent(),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        authoritative = load_strategy_draft_v2(chat)

        with pytest.raises(SetupChatError) as failure:
            await service.handle_message(
                session,
                chat,
                message="Make it stricter",
                client_message_id="launch-v2-paid-grounding-rejection",
            )

        assert failure.value.code == "VALUE_NOT_GROUNDED"
        usage = await session.scalar(
            select(AIUsageEvent).where(AIUsageEvent.chat_session_id == chat.id)
        )
        assert usage is not None
        assert usage.operation == "setup_agent_turn"
        assert usage.input_tokens == 1_200
        assert usage.output_tokens == 300
        assert usage.pricing_source == "configured_from_openai_fast_pricing"
        assert usage.estimated_cost_usd > 0
        assert load_strategy_draft_v2(chat).executable_hash == authoritative.executable_hash
        measured = (chat.context_json or {})["turn_runtime"]["measured"]
        assert measured["stage_counts"]["total_turn"] == 1
        assert measured["total_ms"] >= 0
        assert (chat.context_json or {})["last_turn_failure"]["code"] == "VALUE_NOT_GROUNDED"
        failed_turn = await session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == "launch-v2-paid-grounding-rejection",
            )
        )
        assert failed_turn is not None
        assert failed_turn.telemetry_json == measured
        assert failed_turn.telemetry_json["notes"]["combined_actual_cost_usd"] > 0
        assert failed_turn.telemetry_json["stage_counts"]["persistence"] >= 1


async def test_compiler_invariant_failure_persists_complete_turn_telemetry(
    test_context, monkeypatch
):
    import ai_market_monitor.services.setup_chat_agent as agent_module
    from ai_market_monitor.engine.planner_intent_compiler import IntentCompileError

    def broken_compiler(*args, **kwargs):
        raise IntentCompileError(
            "COMPILER_INVARIANT_VIOLATION",
            "internal compiler contract failed",
        )

    monkeypatch.setattr(agent_module, "compile_planner_intents", broken_compiler)
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        chat_id = chat.id
        before = load_strategy_draft_v2(chat)
        with pytest.raises(SetupChatError) as failure:
            await service.handle_message(
                session,
                chat,
                message="Exclude ETH/USDT",
                client_message_id="launch-v2-compiler-invariant-telemetry",
            )
        assert failure.value.code == "COMPILER_INVARIANT_VIOLATION"
        measured = (chat.context_json or {})["turn_runtime"]["measured"]
        assert measured["notes"]["compiler_invariant_violation_count"] == 1
        assert measured["notes"]["model_facing_schema_bytes"] > 0
        assert measured["model_calls"] == 1
        assert measured["stage_counts"]["total_turn"] == 1
        assert load_strategy_draft_v2(chat).executable_hash == before.executable_hash
    async with test_context["session_factory"]() as verification_session:
        persisted = await verification_session.get(AISetupChatSession, chat_id)
        assert persisted is not None
        persisted_measurement = (persisted.context_json or {})["turn_runtime"]["measured"]
        assert persisted_measurement["notes"]["compiler_invariant_violation_count"] == 1
        assert persisted_measurement["model_calls"] == 1
        assert persisted_measurement["stage_counts"]["total_turn"] == 1
        persisted_turn = await verification_session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat_id,
                SetupChatTurn.client_message_id
                == "launch-v2-compiler-invariant-telemetry",
            )
        )
        assert persisted_turn is not None
        assert persisted_turn.telemetry_json == persisted_measurement
        assert persisted_turn.telemetry_json["stage_counts"]["persistence"] >= 1
        issue = await verification_session.scalar(
            select(SetupChatOperationalIssue).where(
                SetupChatOperationalIssue.chat_session_id == chat_id
            )
        )
        assert issue is not None
        assert issue.issue_kind == "compiler_invariant"
        assert issue.failure_class == "COMPILER_INVARIANT_VIOLATION"
        assert issue.status == "open"
        assert issue.support_reference


async def test_a_question_the_agent_answers_in_words_is_not_an_error(test_context):
    """An open question is answered, not compiled, and it is not a failed turn."""
    user = await _user(test_context)
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], planner),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        chat_id = chat.id
        initial = load_strategy_draft_v2(chat)

        result = await service.handle_message(
            session,
            chat,
            message="What is the difference between close-to-close and a swing high?",
            client_message_id="launch-v2-model-non-mutation",
        )
        current = load_strategy_draft_v2(result)

        assert current.semantic_hash == initial.semantic_hash
        assert current.version == initial.version
        # One turn is bounded at one planning call. Mutation wording is deterministic.
        assert (result.context_json or {})["turn_runtime"]["model_call_count"] <= 1
        measured = (result.context_json or {})["turn_runtime"]["measured"]
        assert measured["notes"]["model_facing_schema_bytes"] > 0
        assert measured["notes"]["model_facing_schema_depth"] > 0
        assert measured["notes"]["model_facing_definition_count"] > 0
        assert measured["notes"]["canonical_models_exposed_to_model"] == []
        assert measured["notes"]["semantic_intent_count"] == 0
        assert measured["notes"]["compiled_operation_count"] == 0
        assert measured["notes"]["semantic_to_operation_expansion_ratio"] == 0
        assert measured["notes"]["compiler_invariant_violation_count"] == 0
        assert measured["notes"]["combined_estimated_cost_usd"] > 0
        assert measured["notes"]["combined_actual_cost_usd"] >= 0
        assert measured["notes"]["planner_repair_attempt_count"] == 0
        assert measured["stage_counts"]["total_turn"] == 1
        assert planner.plan_calls == 1, "exactly one planning call"
    async with test_context["session_factory"]() as verification_session:
        persisted = await verification_session.get(AISetupChatSession, chat_id)
        assert persisted is not None
        persisted_measurement = (persisted.context_json or {})["turn_runtime"]["measured"]
        assert persisted_measurement["notes"]["model_facing_schema_bytes"] > 0
        assert persisted_measurement["notes"]["semantic_intent_count"] == 0
        assert persisted_measurement["notes"]["combined_estimated_cost_usd"] > 0
        assert persisted_measurement["stage_counts"]["total_turn"] == 1
        persisted_turn = await verification_session.scalar(
            select(SetupChatTurn).where(
                SetupChatTurn.chat_session_id == chat_id,
                SetupChatTurn.client_message_id == "launch-v2-model-non-mutation",
            )
        )
        assert persisted_turn is not None
        assert persisted_turn.telemetry_json == persisted_measurement
        assert persisted_turn.telemetry_json["stage_counts"]["persistence"] >= 1


def test_patch_application_is_one_patch_per_turn():
    draft = StrategyDraftV2()
    patch = deterministic_strategy_patch(
        draft,
        "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%",
        source_turn_id="turn-12345678",
    )
    assert patch is not None

    result = apply_strategy_patch(draft, patch)

    assert result.draft.version == draft.version + 1
    assert len(result.draft.source_provenance) == 1


async def test_launch_v2_http_contract_compiles_and_approves_exact_draft(test_context):
    await _signup(test_context, "launch-v2-http@example.com")
    service = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service

    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    assert created.status_code == 201
    chat_id = created.json()["id"]
    assert created.json()["draft_v2"]["schema_version"] == "2.2"

    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": ("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"),
            "client_message_id": "launch-v2-http-turn",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ready_for_approval"
    assert payload["can_approve"] is True
    assert payload["draft_v2"]["condition_ast"]["threshold"] == 3

    approval_payload = {
        "approved": True,
        "expected_schema_hash": payload["schema_hash"],
        "expected_executable_version": payload["draft_v2"]["executable_version"],
        "expected_executable_hash": payload["draft_v2"]["executable_hash"],
        "confirmed_low_confidence_rule_keys": [],
    }
    approved = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/approve",
        json=approval_payload,
    )
    assert approved.status_code == 200, approved.text
    approved_body = approved.json()
    assert approved_body["status"] == "approved"
    assert approved_body["draft_v2"]["approval"]["approved"] is True

    repeated = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/approve",
        json=approval_payload,
    )
    assert repeated.status_code == 200, repeated.text
    assert (
        repeated.json()["approved_strategy_version_id"]
        == (approved_body["approved_strategy_version_id"])
    )


async def test_launch_v2_http_error_keeps_authoritative_draft_identity(test_context):
    await _signup(test_context, "launch-v2-http-error@example.com")
    working = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: working
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]
    ready = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": ("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"),
            "client_message_id": "launch-v2-http-ready",
        },
    )
    before = ready.json()["draft_v2"]

    failing = AISetupChatService(
        _launch_settings(test_context["settings"]),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(
            test_context["settings"],
            StandInPlanner(failure=httpx.ReadTimeout("the provider timed out")),
        ),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: failing
    failed = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "Add RSI below 30 on 15m",
            "client_message_id": "launch-v2-http-failure",
        },
    )

    assert failed.status_code == 503
    body = failed.json()
    assert body["error"]["error_code"] == "TARGET_READ_TIMEOUT"
    # A provider read timeout is the provider's stage, not the step that happened to
    # be running when it struck. This line asserted "extract" until August 2026, which
    # is the product telling a customer it could not read rules they had written
    # correctly, when the truth was that the model never answered. What this test is
    # for - the draft identity surviving an HTTP failure - is unchanged below.
    assert body["error"]["stage"] == "provider"
    assert body["error"]["draft_id"] == before["draft_id"]
    assert body["error"]["executable_version"] == before["executable_version"]
    assert body["error"]["executable_hash"] == before["executable_hash"]
    assert body["draft_v2"]["executable_hash"] == before["executable_hash"]

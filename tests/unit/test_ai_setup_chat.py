import json
from datetime import UTC, datetime, timedelta
from time import monotonic

import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import Strategy, Subscription, User
from ai_market_monitor.db.models.enums import SubscriptionStatus
from ai_market_monitor.engine.strategy_state import StrategyDraftState, patches_for_turn
from ai_market_monitor.schemas.ai_setup_chat import (
    SetupChatClarification,
    SetupChatInterviewResult,
    SetupChatOption,
    SetupChatTurnClassification,
)
from ai_market_monitor.schemas.strategy import (
    ConditionRule,
    InterpretationIssue,
    InterpretationPreview,
    StrategyDefinition,
)
from ai_market_monitor.services.ai_setup_chat import (
    AISetupChatService,
    OpenAISetupChatInterviewer,
    SetupChatError,
    _requests_favorites_universe,
    lint_strategy,
    translation_sheet,
)
from ai_market_monitor.services.entitlements import PlanCatalogService
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from tests.factories import candle_sets, load_strategy


def test_favorites_language_selects_the_saved_screened_universe():
    assert _requests_favorites_universe("Analyze only my favorite coins")
    assert _requests_favorites_universe("Use my favourites")
    assert not _requests_favorites_universe("Analyze all screened coins")


class ReadyInterviewer:
    async def respond(self, **_) -> SetupChatInterviewResult:
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message=(
                "I translated your idea into measurable monitoring rules. Review every rule "
                "and assumption before approval."
            ),
            ready_to_compile=True,
            setup_summary=(
                "Binance USDT spot pairs on 15m. Candle closes above the previous 20-candle "
                "high and volume is at least 1.5x the 20-candle average."
            ),
            suggestions=["Optionally add a higher-timeframe trend filter."],
        )


class RecordingInterviewer(ReadyInterviewer):
    def __init__(self) -> None:
        self.calls = []

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.calls.append(kwargs)
        return await super().respond(**kwargs)


class MultiQuestionInterviewer(ReadyInterviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.calls += 1
        if self.calls > 1:
            return await super().respond(**kwargs)
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="I need two short details.",
            ready_to_compile=False,
            clarifications=[
                SetupChatClarification(
                    key="timeframe_choice",
                    question="Which trigger timeframe?",
                    reason="The timeframe controls each evaluation candle.",
                    options=[SetupChatOption(key="timeframe_choice", label="15m", value="15m")],
                ),
                SetupChatClarification(
                    key="universe_choice",
                    question="Which spot universe?",
                    reason="The universe defines which symbols are scanned.",
                    options=[],
                ),
            ],
        )


class ExcessiveConditionQuestionsInterviewer(ReadyInterviewer):
    async def respond(self, **_) -> SetupChatInterviewResult:
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="I need several RSI details.",
            ready_to_compile=False,
            clarifications=[
                SetupChatClarification(
                    key="rsi_candle_count",
                    question="Which RSI period should I use?",
                    reason="The period changes the RSI calculation.",
                    options=[
                        SetupChatOption(
                            key="rsi_candle_count",
                            label="14 candles",
                            value="Use RSI period 14",
                        )
                    ],
                ),
                SetupChatClarification(
                    key="rsi_threshold",
                    question="Which RSI threshold should match?",
                    reason="The threshold changes which coins match.",
                    options=[
                        SetupChatOption(
                            key="rsi_threshold",
                            label="Below 30",
                            value="Require RSI below 30",
                        )
                    ],
                ),
                SetupChatClarification(
                    key="rsi_price_source",
                    question="Which RSI price source should I use?",
                    reason="The source changes the RSI calculation.",
                    options=[
                        SetupChatOption(
                            key="rsi_price_source",
                            label="Close",
                            value="Use closing prices",
                        )
                    ],
                ),
            ],
        )


class RephrasedQuestionInterviewer(ReadyInterviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.calls += 1
        if self.calls == 1:
            return SetupChatInterviewResult(
                intent="setup",
                assistant_message="I need one detail.",
                ready_to_compile=False,
                clarifications=[
                    SetupChatClarification(
                        key="timeframe_choice",
                        question="Which trigger timeframe should I use?",
                        reason="The timeframe controls each evaluation candle.",
                        options=[SetupChatOption(key="timeframe_choice", label="15m", value="15m")],
                    )
                ],
            )
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="I have the timeframe.",
            ready_to_compile=False,
            clarifications=[
                SetupChatClarification(
                    key="timeframe_recheck",
                    question="What timeframe should this monitor use?",
                    reason="This is the same timeframe question in different wording.",
                )
            ],
        )


class ExplanationChoiceInterviewer(ReadyInterviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.calls += 1
        if self.calls > 1:
            return await super().respond(**kwargs)
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="I need your FVG definition.",
            ready_to_compile=False,
            clarifications=[
                SetupChatClarification(
                    key="fvg_definition",
                    question=(
                        "What exact definition should HilalMarkets use to detect an FVG for "
                        "this monitor?"
                    ),
                    reason="FVG can refer to several measurable gap states.",
                    options=[
                        SetupChatOption(
                            key="fvg_definition",
                            label="New bullish FVG",
                            value="Require bullish fair value gap formation",
                            description="A three-candle bullish imbalance has just formed.",
                        ),
                        SetupChatOption(
                            key="fvg_definition",
                            label="Open bullish FVG",
                            value="Require a bullish FVG that remains open",
                            description="A prior bullish gap remains unfilled.",
                        ),
                        SetupChatOption(
                            key="fvg_definition",
                            label="I don't know - explain the candidates",
                            value="__explain_options__",
                            description="Compare the definitions before I choose.",
                            action="explain",
                        ),
                    ],
                )
            ],
        )


class ContextAwareInterviewer(ReadyInterviewer):
    def __init__(self, routes: list[SetupChatTurnClassification]) -> None:
        self.routes = list(routes)
        self.route_calls = []
        self.respond_calls = 0

    async def classify_turn(self, **kwargs) -> SetupChatTurnClassification:
        self.route_calls.append(kwargs)
        return self.routes.pop(0)

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.respond_calls += 1
        return await super().respond(**kwargs)


class ContextAwareExplanationInterviewer(ExplanationChoiceInterviewer):
    def __init__(self, routes: list[SetupChatTurnClassification]) -> None:
        super().__init__()
        self.routes = list(routes)
        self.route_calls = []

    async def classify_turn(self, **kwargs) -> SetupChatTurnClassification:
        self.route_calls.append(kwargs)
        return self.routes.pop(0)


class UniverseOptionInterviewer(ReadyInterviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.calls += 1
        if self.calls > 1:
            return await super().respond(**kwargs)
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="Choose the spot universe.",
            ready_to_compile=False,
            clarifications=[
                SetupChatClarification(
                    key="universe_choice",
                    question="Which market universe should HilalMarkets scan?",
                    reason="This limits the eligible spot symbols.",
                    options=[
                        SetupChatOption(
                            key="universe_choice",
                            label="All supported spot pairs",
                            value="all_supported_spot_pairs",
                            description="Scan every supported spot pair for the quote asset.",
                        )
                    ],
                )
            ],
        )


class QuantityQuestionInterviewer(ReadyInterviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.calls += 1
        if self.calls > 1:
            return await super().respond(**kwargs)
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="I need one numeric detail.",
            ready_to_compile=False,
            clarifications=[
                SetupChatClarification(
                    key="persistence_candles",
                    question="How many closed candles should the condition persist?",
                    reason="Persistence controls how long the rule must remain true.",
                    options=[],
                )
            ],
        )


class ToleranceOptionInterviewer(ReadyInterviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, **kwargs) -> SetupChatInterviewResult:
        self.calls += 1
        if self.calls > 1:
            return await super().respond(**kwargs)
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="I need the sweep tolerance.",
            ready_to_compile=False,
            clarifications=[
                SetupChatClarification(
                    key="tolerance_percent",
                    question="What tolerance should count as swept (previous daily low)?",
                    reason="The answer defines how exact the level touch must be.",
                    options=[
                        SetupChatOption(
                            key="tol_0",
                            label="Exact only (0%)",
                            value="0",
                        ),
                        SetupChatOption(
                            key="tol_01",
                            label="Within 0.1%",
                            value="0.1",
                        ),
                    ],
                )
            ],
        )


class FixedInterpreter:
    async def interpret(self, _) -> InterpretationPreview:
        definition = load_strategy().model_copy(deep=True)
        first = _first_rule(definition.conditions)
        first.confidence = 0.55
        first.ai_interpreted = True
        return InterpretationPreview(
            strategy=definition,
            assumptions=["Binance USDT spot is used."],
            interpreter="test-ai-compiler",
        )


class CountingInterpreter(FixedInterpreter):
    def __init__(self) -> None:
        self.calls = 0

    async def interpret(self, guided) -> InterpretationPreview:
        self.calls += 1
        return await super().interpret(guided)


class ChangingInterpreter(FixedInterpreter):
    async def interpret(self, guided) -> InterpretationPreview:
        preview = await super().interpret(guided)
        if "apply:" in guided.setup_text.casefold():
            rule = _first_rule(preview.strategy.conditions)
            rule.label = f"{rule.label} with candle-close confirmation"
            rule.source_fragment = "Apply: Add candle-close confirmation"
        return preview


class BlockingInterpreter(FixedInterpreter):
    async def interpret(self, guided) -> InterpretationPreview:
        preview = await super().interpret(guided)
        preview.ambiguities = [
            InterpretationIssue(
                code="missing_threshold",
                message="The trigger still needs a measurable threshold.",
                field="conditions",
                blocking=True,
            )
        ]
        return preview


class SnapshotProvider:
    async def list_symbols(self, exchange, quote_currencies):
        assert exchange == "binance"
        assert quote_currencies == ["USDT"]
        return ["SOL/USDT", "BTC/USDT", "ETH/USDT"]

    async def fetch_universe_metadata(self, exchange, symbols, **_):
        return {
            "SOL/USDT": {"percentage_24h": 7.2},
            "BTC/USDT": {"percentage_24h": 1.1},
            "ETH/USDT": {"percentage_24h": -2.4},
        }


class ScannerProvider(SnapshotProvider):
    async def fetch_ohlcv(self, exchange, symbol, timeframe, limit):
        return candle_sets(volume_multiplier=1.6)[timeframe][-limit:]


async def test_unchanged_compiler_input_uses_cached_preview_and_records_the_hit(
    test_context,
):
    user = await _user(test_context)
    interpreter = CountingInterpreter()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), interpreter, interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        context = dict(chat.context_json or {})
        context["turn_runtime"] = {"attach": True, "cache_hits": 0, "stages": []}
        chat.context_json = context

        first = await service._interpret_setup(
            session,
            chat,
            "RSI below 30 on 15m Binance USDT spot pairs.",
            operation="compile_draft",
        )
        second = await service._interpret_setup(
            session,
            chat,
            "RSI below 30 on 15m Binance USDT spot pairs.",
            operation="compile_draft",
        )

        runtime = chat.context_json["turn_runtime"]
        assert first is second
        assert interpreter.calls == 1
        assert runtime["cache_hits"] == 1
        assert [item["cache_hit"] for item in runtime["stages"]] == [False, True]


async def test_complete_formula_uses_no_model_and_meets_ordinary_turn_budget(test_context):
    user = await _user(test_context)
    interpreter = CountingInterpreter()
    interviewer = RecordingInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), interpreter, interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        context = dict(chat.context_json or {})
        context.update(
            {
                "setup_mode": "monitor",
                "confirmed_monitor_name": "ETH decline",
            }
        )
        chat.context_json = context
        chat.title = "ETH decline"

        started = monotonic()
        await service.handle_message(
            session,
            chat,
            message=(
                "Use ETHUSDT only and keep LTCUSDT out. Use 1h context and a "
                "4h trigger. Short when close-to-close falls at least 0.5%."
            ),
        )
        elapsed = monotonic() - started

        assert chat.status == "ready_for_approval"
        assert interpreter.calls == 0
        assert interviewer.calls == []
        assert elapsed < 12
        assert chat.context_json["turn_runtime"]["deterministic_interpretations"] == 1
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        assert definition.universe.include_symbols == ["ETH/USDT"]
        assert definition.universe.exclude_symbols == ["LTC/USDT"]
        assert definition.base_timeframe == "4h"
        assert definition.supporting_timeframes == ["1h"]


async def test_canonical_formula_stays_deterministic_with_noisy_companion_mechanic(
    test_context,
):
    user = await _user(test_context)
    interpreter = CountingInterpreter()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), interpreter, interviewer=RecordingInterviewer()
    )
    text = (
        "Use BTCUSDT only with 1h context and a 1d trigger. Require a bullish "
        "close-to-close move of at least 5%. Also detect the unregistered lunar "
        "rotation pattern."
    )
    state = StrategyDraftState().apply(patches_for_turn(text, StrategyDraftState()))
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        context = dict(chat.context_json or {})
        context["strategy_state"] = state.to_dict()
        chat.context_json = context

        preview = await service._interpret_setup(
            session,
            chat,
            text,
            operation="compile_draft",
        )

        assert interpreter.calls == 0
        assert preview.strategy.base_timeframe == "1d"
        assert preview.strategy.supporting_timeframes == ["1h"]
        assert any(item.blocking for item in preview.unsupported_conditions)


async def test_approval_policy_prompt_builds_then_approves_exact_draft_without_models(
    test_context,
):
    user = await _user(test_context)
    interpreter = CountingInterpreter()
    interviewer = RecordingInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), interpreter, interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message=(
                "Alright - quick setup. I want a watchlist only for ETHUSDT "
                "(explicitly NOT LTCUSDT). Use 1h context to decide, but the trigger "
                "is on 4h. Trigger condition: bearish move >= 0.5% (short, gte 0.5). "
                "Approval should apply only to the exact reviewed version and hash; "
                "do not carry it over if the draft changes. Confirm the logic and "
                "then I'll review/approve the exact version."
            ),
        )

        assert chat.status == "ready_for_approval"
        assert chat.draft_schema_json is not None
        assert interpreter.calls == 0
        assert interviewer.calls == []

        expected_hash = StrategyDefinition.model_validate(
            chat.draft_schema_json
        ).canonical_hash()

        await service.handle_message(session, chat, message="I approve")
        assert chat.status == "approved"
        assert chat.context_json["approved_schema_hash"] == expected_hash
        approved_strategy_id = chat.approved_strategy_id

        await service.handle_message(session, chat, message="I approve")
        assert chat.status == "approved"
        assert chat.approved_strategy_id == approved_strategy_id
        assert interpreter.calls == 0
        assert interviewer.calls == []


async def test_material_edit_after_approval_creates_a_new_unapproved_draft(test_context):
    user = await _user(test_context)
    interpreter = CountingInterpreter()
    interviewer = RecordingInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), interpreter, interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message=(
                "ETHUSDT only; use 1h context and trigger on 4h; "
                "short when close-to-close falls at least 0.5%."
            ),
        )
        await service.handle_message(session, chat, message="I approve")
        first_strategy_id = chat.approved_strategy_id
        first_version_id = chat.approved_strategy_version_id
        first_hash = chat.context_json["approved_schema_hash"]

        await service.handle_message(
            session,
            chat,
            message="Change the bearish close-to-close move threshold to 1% on 4h.",
        )

        assert chat.status == "ready_for_approval"
        assert chat.approved_at is None
        assert chat.approved_strategy_id is None
        assert chat.approved_strategy_version_id is None
        assert chat.context_json["strategy_state"]["approval_state"] == "AWAITING_APPROVAL"
        assert chat.context_json["schema_hash"] != first_hash
        assert chat.context_json["previous_approvals"][-1]["strategy_id"] == str(
            first_strategy_id
        )
        assert chat.context_json["previous_approvals"][-1]["strategy_version_id"] == str(
            first_version_id
        )

        await service.handle_message(
            session,
            chat,
            message="Reuse my previous approval for this edited draft.",
        )

        assert chat.status == "ready_for_approval"
        assert chat.approved_strategy_id is None
        assert chat.approved_strategy_version_id is None
        assert (await service.messages(session, chat.id))[-1].message_type == (
            "approval_required"
        )
        assert interpreter.calls == 0
        assert interviewer.calls == []

        await service.handle_message(session, chat, message="I approve this exact version")

        assert chat.status == "approved"
        assert chat.approved_strategy_id not in {None, first_strategy_id}
        assert chat.approved_strategy_version_id not in {None, first_version_id}
        assert interpreter.calls == 0
        assert interviewer.calls == []


class UnavailableSnapshotProvider:
    async def list_symbols(self, exchange, quote_currencies):
        raise ConnectionError("exchange unavailable")


def _first_rule(node) -> ConditionRule:
    if isinstance(node, ConditionRule):
        return node
    return _first_rule(node.children[0])


def _rules(node) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    return [rule for child in node.children for rule in _rules(child)]


def _settings(*, key: bool = True) -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        openai_api_key=SecretStr("test-key") if key else None,
        market_breadth_max_symbols=100,
        setup_chat_legacy_test_compat_enabled=True,
    )


async def _user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name="Setup Chat Test")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_vague_prompt_clarifies_then_compiles_and_persists(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Find bullish breakouts with strong volume on 15m Binance spot.",
        )
        assert chat.status == "needs_clarification"
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "clarification"
        assert latest.payload["clarifications"][0]["key"] == "breakout"
        assert latest.payload["clarifications"][0]["options"][-1] == {
            "key": "breakout",
            "label": "Other (type in chat)",
            "value": "__other__",
            "description": "Answer this question in your own words.",
            "action": "other",
        }

        await service.handle_message(
            session,
            chat,
            message="",
            option_key="breakout",
            option_value="Candle closes above the previous 20-candle high",
        )
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.payload["clarifications"][0]["key"] == "strong_volume"

        await service.handle_message(
            session,
            chat,
            message="",
            option_key="strong_volume",
            option_value="Volume is at least 1.5x the 20-candle average",
        )
        await session.commit()
        assert chat.status == "ready_for_approval"
        assert chat.draft_schema_json
        assert chat.translation_sheet["approval_required"] is True
        assert any(item["requires_confirmation"] for item in chat.rule_confidence)
        intent_state = chat.context_json["intent_state"]
        assert intent_state["version"] == 1
        assert intent_state["required_conditions"]
        assert intent_state["timeframes"]
        assert chat.context_json["setup_fragment_records"]

    async with test_context["session_factory"]() as session:
        resumed = await service.latest_open_session(session, user.id)
        assert resumed is not None
        assert resumed.id == chat.id
        assert len(await service.messages(session, resumed.id)) == 8


async def test_questions_already_answered_in_prompt_are_not_repeated(test_context):
    user = await _user(test_context)
    interviewer = MultiQuestionInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message="RSI below 30 on Binance spot pairs.")
        first = (await service.messages(session, chat.id))[-1]
        assert first.content.startswith("Question 1 of 1")

        await service.handle_message(
            session,
            chat,
            message="",
            option_key="timeframe_choice",
            option_value="15m",
            option_label="15m",
        )
        assert chat.status == "ready_for_approval"
        assert interviewer.calls == 2


async def test_chat_asks_no_more_than_two_questions_per_condition(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(),
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ExcessiveConditionQuestionsInterviewer(),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Find coins with RSI below 30 on 15m.",
        )
        first = (await service.messages(session, chat.id))[-1]
        first_option = first.payload["clarifications"][0]["options"][0]
        await service.handle_message(
            session,
            chat,
            message="",
            option_key=first_option["key"],
            option_value=first_option["value"],
            option_label=first_option["label"],
        )
        second = (await service.messages(session, chat.id))[-1]
        second_option = second.payload["clarifications"][0]["options"][0]
        await service.handle_message(
            session,
            chat,
            message="",
            option_key=second_option["key"],
            option_value=second_option["value"],
            option_label=second_option["label"],
        )

        questions = [
            item
            for item in await service.messages(session, chat.id)
            if item.message_type == "clarification"
        ]
        assert len(questions) == 2
        assert chat.context_json["clarification_question_counts"]["condition:rsi"] == 2
        assert chat.status == "ready_for_approval"


async def test_rephrased_question_is_not_asked_again_and_checkpoint_is_not_repeated(test_context):
    user = await _user(test_context)
    interviewer = RephrasedQuestionInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message="RSI below 30 on Binance spot pairs.")
        initial_messages = await service.messages(session, chat.id)
        checkpoints = [item for item in initial_messages if item.message_type == "process_state"]
        assert len(checkpoints) == 1
        assert "Clarification checkpoint" in checkpoints[0].content

        await service.handle_message(
            session,
            chat,
            message="",
            option_key="timeframe_choice",
            option_value="15m",
            option_label="15m",
        )

        messages = await service.messages(session, chat.id)
        assert chat.status == "ready_for_approval"
        assert interviewer.calls == 2
        assert sum(item.message_type == "process_state" for item in messages) == 1
        assert not any(
            "What timeframe should this monitor use?" in item.content for item in messages
        )


async def test_active_clarification_key_prevents_repeated_question_loop(test_context):
    user = await _user(test_context)
    interviewer = MultiQuestionInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message="RSI below 30 on Binance spot pairs.")

        # A rendered option must resolve the active question even if an old client sends an
        # incorrect key. Otherwise the same question is emitted forever.
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="stale_option_key",
            option_value="15m",
            option_label="15m",
        )
        second = (await service.messages(session, chat.id))[-1]
        assert second.message_type == "translation"
        assert chat.context_json["resolved_ambiguities"]["timeframe_choice"] == "15m"
        assert chat.status == "ready_for_approval"
        assert interviewer.calls == 2


@pytest.mark.parametrize("use_option", [True, False])
async def test_explanation_request_keeps_technical_question_open_without_becoming_a_rule(
    test_context,
    use_option,
):
    user = await _user(test_context)
    interviewer = ExplanationChoiceInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Detect a bullish fair value gap on 15m Binance spot pairs.",
        )
        before = list(chat.context_json["setup_fragments"])
        clarification = (await service.messages(session, chat.id))[-1].payload["clarifications"][0]
        help_option = next(item for item in clarification["options"] if item["action"] == "explain")

        if use_option:
            await service.handle_message(
                session,
                chat,
                message="",
                option_key=help_option["key"],
                option_value=help_option["value"],
                option_label=help_option["label"],
                client_message_id="explain-fvg-option-001",
            )
        else:
            await service.handle_message(
                session,
                chat,
                message="I don't know - explain the candidates",
                client_message_id="explain-fvg-text-001",
            )

        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "clarification_help"
        assert latest.payload["awaiting_answer"] is True
        assert len(latest.payload["explanations"]) == 2
        assert latest.payload["clarifications"][0]["key"] == "fvg_definition"
        assert chat.status == "needs_clarification"
        assert chat.context_json["setup_fragments"] == before
        assert "fvg_definition" not in chat.context_json.get("resolved_ambiguities", {})
        assert interviewer.calls == 1
        assert "What do you mean by 'explain'" not in latest.content

        answer = clarification["options"][0]
        await service.handle_message(
            session,
            chat,
            message="",
            option_key=answer["key"],
            option_value=answer["value"],
            option_label=answer["label"],
        )
        assert interviewer.calls == 2
        assert chat.status == "ready_for_approval"


async def test_capability_question_is_answered_without_becoming_setup_input(test_context):
    user = await _user(test_context)
    prompt = 'Do you have in the system identified "FVG" (fair value gap)?'
    interviewer = ContextAwareInterviewer(
        [
            SetupChatTurnClassification(
                # Even a bad model route cannot turn an obvious product question into a rule.
                intent="setup_instruction",
                assistant_message=(
                    "Yes. HilalMarkets has registered bullish and bearish fair-value-gap "
                    "mechanics. I have not added either one to your setup."
                ),
                technical_fragments=[prompt],
                segments=[{"text": prompt, "category": "technical_instruction"}],
                preserve_pending_question=False,
                confidence=0.98,
            )
        ]
    )
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message=prompt)

        latest = (await service.messages(session, chat.id))[-1]
        candidate_keys = {
            item["capability_key"]
            for item in interviewer.route_calls[0]["capability_context"]["candidates"]
        }
        assert latest.message_type == "product_answer"
        assert "not added" in latest.content
        assert chat.context_json["setup_fragments"] == []
        assert chat.original_idea is None
        assert interviewer.respond_calls == 0
        assert {"bullish_fair_value_gap", "bearish_fair_value_gap"}.intersection(candidate_keys)


async def test_question_about_ai_options_preserves_active_question_and_context(test_context):
    user = await _user(test_context)
    setup = "Detect a bullish fair value gap on 15m Binance spot pairs."
    question = "What is the difference between the choices you gave me?"
    interviewer = ContextAwareExplanationInterviewer(
        [
            SetupChatTurnClassification(
                intent="setup_instruction",
                assistant_message="",
                technical_fragments=[setup],
                segments=[{"text": setup, "category": "technical_instruction"}],
                preserve_pending_question=False,
                confidence=0.99,
            ),
            SetupChatTurnClassification(
                # The deterministic boundary protects option-help even if the model calls it
                # an answer. The active question must remain open.
                intent="clarification_answer",
                assistant_message=(
                    "A new FVG forms on the current three-candle pattern; an open FVG can "
                    "come from an earlier pattern and must still be unfilled."
                ),
                technical_fragments=[question],
                clarification_answer=question,
                segments=[{"text": question, "category": "clarification_answer"}],
                preserve_pending_question=False,
                confidence=0.99,
            ),
        ]
    )
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message=setup)
        before = list(chat.context_json["setup_fragments"])
        active = dict(chat.context_json["awaiting_clarification"])

        await service.handle_message(session, chat, message=question)

        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "clarification_help"
        assert latest.payload["awaiting_answer"] is True
        assert latest.payload["clarifications"][0]["key"] == "fvg_definition"
        assert chat.context_json["awaiting_clarification"] == active
        assert chat.context_json["setup_fragments"] == before
        assert interviewer.calls == 1
        assert all(
            "value" not in option
            for option in interviewer.route_calls[1]["active_clarification"]["options"]
        )


async def test_internal_universe_option_value_never_becomes_a_capability(test_context):
    user = await _user(test_context)
    interviewer = UniverseOptionInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message="RSI below 30 on 15m.")
        clarification = (await service.messages(session, chat.id))[-1].payload["clarifications"][0]
        option = clarification["options"][0]

        await service.handle_message(
            session,
            chat,
            message="",
            option_key=option["key"],
            option_value=option["value"],
            option_label=option["label"],
        )

        fragments = chat.context_json["setup_fragments"]
        assert "all_supported_spot_pairs" not in fragments
        assert "Clarification answer for universe_choice: All supported spot pairs" in fragments
        assert chat.status == "ready_for_approval"
        assert not any(
            "all_supported_spot_pairs" in item.content
            for item in await service.messages(session, chat.id)
            if item.role == "assistant"
        )


async def test_numeric_option_stays_bound_to_the_question_instead_of_becoming_a_mechanic(
    test_context,
):
    user = await _user(test_context)
    interviewer = ToleranceOptionInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Find Binance USDT spot pairs that swept PDL on 1d.",
        )
        clarification = (await service.messages(session, chat.id))[-1].payload[
            "clarifications"
        ][0]
        exact = clarification["options"][0]

        await service.handle_message(
            session,
            chat,
            message="",
            option_key=exact["key"],
            option_value=exact["value"],
            option_label=exact["label"],
        )

        assert chat.status == "ready_for_approval"
        assert "0" not in chat.context_json["setup_fragments"]
        assert (
            "Clarification answer for tolerance_percent: Exact only (0%)"
            in chat.context_json["setup_fragments"]
        )
        assert chat.context_json["resolved_ambiguities"]["tolerance_percent"] == "0"
        assert not any(
            "How should HilalMarkets measure '0'" in item.content
            for item in await service.messages(session, chat.id)
        )


async def test_daily_sweep_side_answer_preserves_daily_reference_period(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(),
        SnapshotProvider(),
        RuleBasedStrategyInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Alert when the current daily candle sweeps the previous daily candle.",
        )
        clarification = (await service.messages(session, chat.id))[-1].payload[
            "clarifications"
        ][0]
        previous_low = clarification["options"][0]
        await service.handle_message(
            session,
            chat,
            message="",
            option_key=previous_low["key"],
            option_value=previous_low["value"],
            option_label=previous_low["label"],
        )

        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        rule = _first_rule(definition.conditions)
        assert rule.capability_key == "previous_daily_low_sweep"
        assert rule.left.name == "daily_low_swept"
        assert "Sweep the previous daily low" in chat.context_json["setup_fragments"]


async def test_misspelled_head_and_shoulders_flow_compiles_without_generic_questions(
    test_context,
):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(),
        SnapshotProvider(),
        RuleBasedStrategyInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    prompt = (
        "I want to monitor every forming head & sholders on halal coins then once the "
        "neckline is broken, alert me on the 1m chart"
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message=prompt)

        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        rules = _rules(definition.conditions)
        assert {rule.capability_key for rule in rules} >= {
            "head_and_shoulders_formed",
            "head_and_shoulders_neckline_break",
        }
        assert all(
            rule.timeframe == "1m"
            for rule in rules
            if rule.capability_key
            in {"head_and_shoulders_formed", "head_and_shoulders_neckline_break"}
        )
        assert not any(
            "How should HilalMarkets measure" in item.content
            for item in await service.messages(session, chat.id)
        )
        assert not any(
            item.get("code") == "prompt_fragment_unclassified"
            for item in chat.lint_warnings
        )


async def test_turn_router_receives_deduplicated_conversation_history(test_context):
    user = await _user(test_context)
    prompt = "Do you support FVG?"
    routes = [
        SetupChatTurnClassification(
            intent="product_question",
            assistant_message="Yes, registered FVG mechanics are available.",
            technical_fragments=[],
            segments=[{"text": prompt, "category": "product_question"}],
            preserve_pending_question=True,
            confidence=0.98,
        )
        for _ in range(3)
    ]
    interviewer = ContextAwareInterviewer(routes)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message=prompt)
        await service.handle_message(session, chat, message=prompt)
        await service.handle_message(session, chat, message=prompt)

        third_history = interviewer.route_calls[2]["history"]
        assert sum(item["content"] == prompt for item in third_history) == 1
        assert (
            sum(
                item["content"] == "Yes, registered FVG mechanics are available."
                for item in third_history
            )
            == 1
        )


@pytest.mark.parametrize("ordinary_word", ["then", "about"])
async def test_ordinary_single_word_does_not_become_an_unknown_mechanic(
    test_context,
    ordinary_word,
):
    user = await _user(test_context)
    interviewer = ExplanationChoiceInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Detect a bullish fair value gap on 15m Binance spot pairs.",
        )
        before = list(chat.context_json["setup_fragments"])
        await service.handle_message(session, chat, message=ordinary_word)
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "clarification"
        assert latest.content.startswith("I still need this detail:")
        assert chat.context_json["setup_fragments"] == before
        assert f"What do you mean by '{ordinary_word}'" not in latest.content


async def test_spelled_out_quantity_is_understood_in_technical_question_context(test_context):
    user = await _user(test_context)
    interviewer = QuantityQuestionInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="RSI below 30 on 15m Binance spot pairs.",
        )
        await service.handle_message(session, chat, message="two")
        assert interviewer.calls == 2
        assert chat.status == "ready_for_approval"
        assert any(
            item == "Clarification answer for persistence_candles: two"
            for item in chat.context_json["setup_fragments"]
        )
        assert not any(
            "What do you mean by 'two'" in item.content
            for item in await service.messages(session, chat.id)
        )


async def test_scanner_mode_uses_current_match_conditions_without_a_primary_trigger(
    test_context,
):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        welcome = (await service.messages(session, chat.id))[-1]
        assert {item["value"] for item in welcome.payload["start_modes"]} == {
            "scanner",
            "monitor",
        }
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="setup_mode",
            option_value="scanner",
            option_label="Scanner",
        )
        assert chat.context_json["setup_mode"] == "scanner"
        await service.handle_message(
            session,
            chat,
            message="RSI below 30 on 15m Binance USDT spot pairs.",
        )
        assert chat.status == "ready_to_scan"

        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        assert definition.trigger_mode.value == "intrabar"
        assert {
            item["role"] for item in chat.translation_sheet["conditions"] if item["required"]
        } == {"current_match_condition"}
        field_labels = {item["label"] for item in chat.translation_sheet["fields"]}
        assert "Current-match conditions" in field_labels
        assert "Primary trigger" not in field_labels
        assert "current condition" in chat.translation_sheet["summary_paragraph"]
        for rule in _rules(definition.conditions):
            rule.required = False
        chat.draft_schema_json = definition.model_dump(mode="json")
        with pytest.raises(SetupChatError, match="condition that coins must match") as exc_info:
            await service.run_scanner(session, chat, user_id=user.id)
        assert exc_info.value.code == "scanner_condition_required"


async def test_scanner_preserves_an_explicit_closed_candle_requirement(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="setup_mode",
            option_value="scanner",
            option_label="Scanner",
        )
        await service.handle_message(
            session,
            chat,
            message="Find RSI below 30 on 15m after the candle closes.",
        )

        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        assert definition.trigger_mode.value == "candle_close"
        timing = next(
            item["value"]
            for item in chat.translation_sheet["fields"]
            if item["label"] == "Evaluation timing"
        )
        assert timing == "Latest closed candle"


async def test_monitor_mode_prompts_for_trigger_instead_of_claiming_ready(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="setup_mode",
            option_value="monitor",
            option_label="Monitor",
        )

        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "mode_selected"
        assert "describe the market event" in latest.content.casefold()
        assert "monitor is ready" not in latest.content.casefold()
        assert chat.status == "interviewing"


async def test_scanner_runs_the_shared_evaluator_without_creating_a_monitor(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), ScannerProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        plan = await PlanCatalogService(session).get_or_sync("trader")
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=plan.id,
                status=SubscriptionStatus.ACTIVE,
                provider="test",
                provider_customer_id=f"cus_{user.id}",
                provider_subscription_id=f"sub_{user.id}_scanner",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.flush()
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="setup_mode",
            option_value="scanner",
            option_label="Scanner",
        )
        await service.handle_message(
            session,
            chat,
            message="RSI below 30 on 15m Binance USDT spot pairs.",
        )
        assert chat.status == "ready_to_scan"

        await service.run_scanner(session, chat, user_id=user.id)

        latest = (await service.messages(session, chat.id))[-1]
        result = latest.payload["scanner_result"]
        assert latest.message_type == "scanner_result"
        assert result["symbols_scanned"] > 0
        assert result["results"]
        assert result["confirmed_count"] + result["forming_count"] + result["failed_count"] == len(
            result["results"]
        )
        assert result["disclaimer"] == (
            "Scanner results are market research, not buy or sell advice."
        )
        assert chat.approved_strategy_id is None
        assert chat.approved_strategy_version_id is None


async def test_out_of_topic_and_greeting_are_scoped_without_openai(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(key=False), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message="Hi, how are you?")
        greeting = (await service.messages(session, chat.id))[-1]
        assert greeting.message_type == "greeting"
        assert "crypto spot setup" in greeting.content

        await service.handle_message(session, chat, message="Give me a cupcake recipe")
        refusal = (await service.messages(session, chat.id))[-1]
        assert refusal.message_type == "scope_refusal"
        assert "crypto spot monitoring" in refusal.content


async def test_market_snapshot_uses_provider_values(test_context):
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    snapshot = await service.market_snapshot()
    assert snapshot.status == "available"
    assert snapshot.symbols_checked == 3
    assert snapshot.top_movers[0].symbol == "SOL/USDT"
    assert snapshot.top_movers[0].percentage_24h == 7.2
    assert snapshot.provider_name == "SnapshotProvider"
    assert snapshot.btc_status.percentage_24h == 1.1
    assert snapshot.eth_status.percentage_24h == -2.4
    assert snapshot.advancing == 2
    assert snapshot.declining == 1
    assert snapshot.dispersion_24h is not None
    assert snapshot.captured_at.tzinfo is not None
    assert "not financial advice" in snapshot.message


async def test_market_snapshot_does_not_fabricate_when_provider_is_unavailable():
    service = AISetupChatService(
        _settings(),
        UnavailableSnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    snapshot = await service.market_snapshot()
    assert snapshot.status == "unavailable"
    assert snapshot.top_movers == []
    assert snapshot.unavailable_reason == "the eligible symbol universe could not be loaded"
    assert "No values were invented" in snapshot.message


async def test_client_message_id_is_idempotent_and_history_has_no_current_duplicate(
    test_context,
):
    user = await _user(test_context)
    interviewer = RecordingInterviewer()
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=interviewer
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        kwargs = {
            "message": "RSI below 30 on 15m Binance USDT spot pairs.",
            "client_message_id": "client-message-123",
        }
        await service.handle_message(session, chat, **kwargs)
        first_count = len(await service.messages(session, chat.id))
        await service.handle_message(session, chat, **kwargs)
        assert len(await service.messages(session, chat.id)) == first_count
        assert interviewer.calls[0]["current_message"] == kwargs["message"]
        assert all(item["content"] != kwargs["message"] for item in interviewer.calls[0]["history"])


async def test_compiler_preserves_required_filters_and_confirmations(
    test_context,
):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message=(
                "RSI below 30 with volume at least 1.5x average and price above EMA 200 "
                "on 15m Binance spot."
            ),
        )
        rules = chat.translation_sheet["conditions"]
        compiled = StrategyDefinition.model_validate(chat.draft_schema_json)
        expected_required = sum(rule.required for rule in _rules(compiled.conditions))
        assert expected_required > 1
        assert sum(1 for item in rules if item["required"]) == expected_required
        assert sum(item["role"] == "primary_trigger" for item in rules) == 1
        assert any(item["role"] in {"required_filter", "required_confirmation"} for item in rules)
        assert chat.translation_sheet["logic_operator"] in {"and", "or"}
        assert chat.status == "ready_for_approval"


async def test_apply_suggestion_recompiles_and_invalidates_old_hash(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), ChangingInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message="RSI below 30 on 15m Binance USDT spot pairs."
        )
        old_hash = chat.context_json["schema_hash"]
        await service.handle_message(
            session,
            chat,
            message="Apply: Add candle-close confirmation",
            client_message_id="suggestion-client-message",
        )
        assert chat.context_json["schema_hash"] != old_hash
        assert "candle-close confirmation" in chat.translation_sheet["conditions"][0]["name"]


async def test_monitor_mode_requires_confirmed_name_before_approval(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Monitor",
            option_key="setup_mode",
            option_value="monitor",
            option_label="Monitor",
        )
        await service.handle_message(
            session, chat, message="RSI below 30 on 15m Binance USDT spot pairs."
        )
        assert chat.status == "needs_clarification"
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "monitor_name_required"
        clarification = latest.payload["clarifications"][0]
        assert clarification["question"] == "What would you like to name this monitor?"
        assert len(clarification["options"]) == 3
        old_hash = StrategyDefinition.model_validate(chat.draft_schema_json).canonical_hash()
        chosen = clarification["options"][0]
        await service.handle_message(
            session,
            chat,
            message=chosen["label"],
            option_key="monitor_name",
            option_value=chosen["value"],
            option_label=chosen["label"],
            client_message_id="monitor-name-choice-001",
        )
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        assert chat.status == "ready_for_approval"
        assert definition.name == chosen["value"]
        assert chat.translation_sheet["monitor_name"] == chosen["value"]
        assert definition.canonical_hash() != old_hash
        assert (await service.messages(session, chat.id))[-2].client_message_id == (
            "monitor-name-choice-001"
        )


async def test_monitor_name_validation_and_duplicate_are_friendly(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        session.add(Strategy(user_id=user.id, name="RSI Watch"))
        await session.flush()
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="Monitor",
            option_key="setup_mode",
            option_value="monitor",
        )
        await service.handle_message(
            session, chat, message="RSI below 30 on 15m Binance USDT spot pairs."
        )
        await service.handle_message(session, chat, message="a")
        assert chat.status == "needs_clarification"
        assert (await service.messages(session, chat.id))[-1].message_type == (
            "monitor_name_invalid"
        )
        await service.handle_message(session, chat, message="RSI Watch")
        assert chat.status == "needs_clarification"
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "monitor_name_duplicate"
        assert latest.payload["clarifications"][0]["options"]


async def test_ai_cannot_claim_ready_when_deterministic_lint_blocks(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), BlockingInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message="RSI below 30 on 15m Binance USDT spot pairs."
        )
        latest = (await service.messages(session, chat.id))[-1]
        assert chat.status == "needs_clarification"
        assert "Open the Translation Sheet" in latest.content
        assert latest.payload["can_approve"] is False
        assert latest.payload["refusal_reasons"] == [
            {
                "code": "missing_threshold",
                "title": "One detail needs review",
                "message": "The trigger still needs a measurable threshold.",
                "next_step": (
                    "Answer or revise this detail in the chat, then review the Translation "
                    "Sheet again."
                ),
                "category": "Review",
                "severity": "critical",
                "blocking": True,
                "label": "Fix before approval",
            }
        ]


async def test_chat_compiles_pdl_sweep_as_an_executable_primary_trigger(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(),
        SnapshotProvider(),
        RuleBasedStrategyInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message="Bring coins which sweeped the PDL through."
        )
        assert chat.status == "ready_for_approval"
        rule = _first_rule(StrategyDefinition.model_validate(chat.draft_schema_json).conditions)
        assert rule.left.name == "daily_low_swept"


async def test_weekly_sweep_flow_asks_only_for_side_then_compiles(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(),
        SnapshotProvider(),
        RuleBasedStrategyInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message=(
                "to check whether the current candle in this week sweeped "
                "the previous weekly candle"
            ),
        )
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "clarification"
        clarification = latest.payload["clarifications"][0]
        assert clarification["key"] == "reference_sweep_side"
        assert [item["label"] for item in clarification["options"]] == [
            "Previous low",
            "Previous high",
            "Other (type in chat)",
        ]
        low = clarification["options"][0]
        await service.handle_message(
            session,
            chat,
            message="",
            option_key=low["key"],
            option_value=low["value"],
            option_label=low["label"],
        )
        assert chat.status == "ready_for_approval"
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        rule = next(
            item
            for item in _rules(definition.conditions)
            if item.capability_key == "reference_period_sweep"
        )
        assert rule.left.parameters["reference_period"] == "week"
        assert rule.left.parameters["side"] == "low"
        accumulated = " ".join(chat.context_json["setup_fragments"]).casefold()
        assert "capability meaning" not in accumulated
        assert "definition:" not in accumulated


async def test_other_option_keeps_question_open_and_typed_answer_is_used(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(),
        SnapshotProvider(),
        RuleBasedStrategyInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session,
            chat,
            message="I want the current candle to sweep the previous weekly candle",
        )
        await service.handle_message(
            session,
            chat,
            message="",
            option_key="reference_sweep_side",
            option_value="__other__",
            option_label="Other (type in chat)",
        )
        assert "awaiting_clarification" in chat.context_json
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "custom_answer_requested"

        await service.handle_message(session, chat, message="Use the previous weekly low")
        assert chat.status == "ready_for_approval"
        rule = next(
            item
            for item in _rules(StrategyDefinition.model_validate(chat.draft_schema_json).conditions)
            if item.capability_key == "reference_period_sweep"
        )
        assert rule.left.parameters["side"] == "low"


@pytest.mark.parametrize(
    "prompt",
    [
        "Give me a cupcake recipe",
        "Write a Python web scraper for me",
        "What is the capital of Finland?",
        "Should I buy SOL now?",
        "Which coin will pump next?",
        "Give me leverage advice",
        "Connect my Binance API key",
    ],
)
async def test_off_topic_and_unsafe_requests_are_refused(test_context, prompt):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(key=False), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message=prompt)
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "scope_refusal"
        assert "monitor" in latest.content.casefold()


@pytest.mark.parametrize(
    ("prompt", "code"),
    [
        ("Monitor order book imbalance", "order_book_imbalance"),
        ("Monitor CVD", "cumulative_volume_delta"),
        ("Use a liquidation heatmap", "liquidation_heatmap"),
        ("Track whale wallets", "whale_wallets"),
        ("Use the fear and greed index", "fear_and_greed"),
        ("Use news sentiment", "news_sentiment"),
    ],
)
async def test_unconfigured_provider_concepts_are_blocked(test_context, prompt, code):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(key=False), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message=prompt)
        latest = (await service.messages(session, chat.id))[-1]
        assert latest.message_type == "unsupported"
        assert chat.unsupported_conditions[0]["code"] == code
        assert chat.status == "needs_clarification"


async def test_beginner_mode_explains_common_jargon(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    prompt = (
        "Use RVOL and HTF for a breakout retest confirmation with invalidation after candle close."
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(session, chat, message=prompt)
        latest = (await service.messages(session, chat.id))[-1]
        terms = {item["term"] for item in latest.payload["jargon"]}
        assert terms == {
            "RVOL",
            "HTF",
            "Breakout",
            "Retest",
            "Confirmation",
            "Invalidation",
            "Candle Close",
        }


async def test_missing_openai_key_fails_clearly_for_setup_compilation(test_context):
    user = await _user(test_context)
    service = AISetupChatService(
        _settings(key=False), SnapshotProvider(), FixedInterpreter(), interviewer=ReadyInterviewer()
    )
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(SetupChatError, match="OPENAI_API_KEY") as exc_info:
            await service.handle_message(
                session,
                chat,
                message="RSI below 30 on 15m Binance USDT spot pairs.",
            )
        assert exc_info.value.code == "openai_not_configured"
        assert exc_info.value.status_code == 503


async def test_openai_interviewer_rejects_invalid_json_schema_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "not-json"}],
                    }
                ]
            },
        )

    client = OpenAISetupChatInterviewer(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(SetupChatError) as exc_info:
        await client.respond(history=[], current_message="RSI below 30", accumulated_setup="")
    assert exc_info.value.code == "ai_interview_failed"
    assert exc_info.value.status_code == 502


async def test_openai_turn_classifier_uses_context_and_strict_schema():
    prompt = "Do you support FVG?"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["text"]["format"]["name"] == "traceedge_setup_turn_router"
        turn_input = json.loads(payload["input"])
        assert turn_input["current_message"] == prompt
        assert turn_input["conversation"][-1]["content"] == "Which FVG definition?"
        assert turn_input["active_clarification"]["key"] == "fvg_definition"
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "intent": "product_question",
                                        "assistant_message": (
                                            "Yes. HilalMarkets has registered FVG mechanics."
                                        ),
                                        "technical_fragments": [],
                                        "clarification_answer": None,
                                        "segments": [
                                            {"text": prompt, "category": "product_question"}
                                        ],
                                        "preserve_pending_question": True,
                                        "confidence": 0.99,
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 25, "output_tokens": 15},
            },
        )

    client = OpenAISetupChatInterviewer(_settings(), transport=httpx.MockTransport(handler))
    result = await client.classify_turn(
        history=[{"role": "assistant", "content": "Which FVG definition?"}],
        current_message=prompt,
        accumulated_setup="Detect an FVG",
        active_clarification={
            "key": "fvg_definition",
            "question": "Which FVG definition?",
            "options": [{"label": "New FVG"}],
        },
        capability_context={"candidates": []},
    )
    assert result.intent == "product_question"
    assert result.technical_fragments == []
    assert client.last_usage["input_tokens"] == 25


async def test_openai_interviewer_uses_configured_complex_route_for_mixed_logic():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "configured-complex"
        assert payload["reasoning"] == {"effort": "medium"}
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "intent": "setup_instruction",
                                        "assistant_message": "",
                                        "technical_fragments": [
                                            "RSI below 30 and volume above 1.5x or EMA above 200"
                                        ],
                                        "clarification_answer": None,
                                        "segments": [
                                            {
                                                "text": (
                                                    "RSI below 30 and volume above 1.5x or "
                                                    "EMA above 200"
                                                ),
                                                "category": "technical_instruction",
                                            }
                                        ],
                                        "preserve_pending_question": False,
                                        "confidence": 0.9,
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )

    settings = _settings()
    settings.ai_setup_simple_model = "configured-simple"
    settings.ai_setup_complex_model = "configured-complex"
    settings.ai_setup_complex_reasoning_effort = "medium"
    client = OpenAISetupChatInterviewer(settings, transport=httpx.MockTransport(handler))
    result = await client.classify_turn(
        history=[],
        current_message="RSI below 30 and volume above 1.5x or EMA above 200",
        accumulated_setup="",
    )

    assert result.intent == "setup_instruction"
    assert client.last_usage["_traceedge_route_tier"] == "complex"


def test_translation_sheet_exposes_beginner_clause_coverage_and_blocks_loss():
    definition = load_strategy().model_copy(deep=True)
    preview = InterpretationPreview(
        strategy=definition,
        interpreter="test",
        raw_metadata={
            "prompt_coverage_report": {
                "mapping_table": [
                    {
                        "fragment": "RSI below 30",
                        "bucket": "executable_condition",
                        "condition_id": _first_rule(definition.conditions).key,
                    },
                    {"fragment": "avoid noisy periods", "bucket": "unclassified"},
                ]
            }
        },
    )

    sheet = translation_sheet("RSI below 30 and avoid noisy periods", definition, preview)
    warnings = lint_strategy(definition, preview)

    assert sheet["clause_coverage"][0]["status"] == "covered"
    assert sheet["clause_coverage"][1]["status"] == "needs_clarification"
    assert sheet["clause_coverage"][1]["blocking"] is True
    assert any(item["code"] == "meaningful_clause_uncovered" for item in warnings)


def test_strategy_lint_detects_contradictory_thresholds():
    definition = load_strategy().model_copy(deep=True)
    first = _first_rule(definition.conditions)
    opposite = first.model_copy(deep=True)
    opposite.key = "contradictory_threshold"
    opposite.comparator = type(first.comparator)("lt")
    opposite.right = first.right.model_copy(update={"value": 50}) if first.right else None
    first.comparator = type(first.comparator)("gt")
    if first.right:
        first.right.value = 60
    definition.conditions.children.append(opposite)
    preview = InterpretationPreview(strategy=definition, interpreter="test")
    warnings = lint_strategy(definition, preview)
    assert any(item["code"] == "contradictory_thresholds" for item in warnings)

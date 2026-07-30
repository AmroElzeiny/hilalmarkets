import json

import httpx
from pydantic import SecretStr

from ai_market_monitor.api.routers.dashboard_api import get_ai_setup_chat_service
from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import User
from ai_market_monitor.engine.strategy_draft_v2 import apply_strategy_patch
from ai_market_monitor.schemas.setup_agent import (
    SegmentKind,
    SetupAgentPlanEnvelope,
    SetupAgentTurnPlan,
    StrategyInstructionPlan,
    TurnSegment,
)
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2
from ai_market_monitor.services.ai_setup_chat import AISetupChatService, SetupChatError
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.setup_chat_agent import SetupChatAgent
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from ai_market_monitor.services.strategy_patch_extractor import deterministic_strategy_patch
from tests.integration.test_ai_setup_chat_api import _signup
from tests.support.setup_agent_plans import operations_from_patch


class MarketProvider:
    async def list_symbols(self, exchange, quote_currencies):
        return ["BTC/USDT", "ETH/USDT"]


class StandInPlanner:
    """A model stand-in that segments the turn and reuses the deterministic parser.

    Free text now reaches the Setup Agent, so these tests drive the agent rather than
    the patch extractor. Only the two network calls are faked: the real planner
    payload, the real `apply_setup_turn` checks and the real compiler all run.
    """

    def __init__(self, *, failure: Exception | None = None) -> None:
        self.plan_calls = 0
        self.reply_calls = 0
        self.failure = failure

    def _body(self, text: str) -> dict:
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}
            ],
            "usage": {"input_tokens": 12, "output_tokens": 6},
        }

    def _envelope(self, message: str, turn_id: str) -> SetupAgentPlanEnvelope:
        patch = deterministic_strategy_patch(
            StrategyDraftV2(), message, source_turn_id=turn_id
        )
        kind = (
            SegmentKind.STRATEGY_INSTRUCTION if patch is not None else SegmentKind.SOCIAL_REPLY
        )
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
            operations=(
                operations_from_patch(patch, segment_id="s1") if patch is not None else []
            ),
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
            if body["text"]["format"]["name"] == "hilalmarkets_setup_turn_plan":
                self.plan_calls += 1
                if self.failure is not None:
                    raise self.failure
                envelope = self._envelope(
                    payload["current_user_turn"], payload["source_turn_id"]
                )
                return httpx.Response(200, json=self._body(envelope.model_dump_json()))
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

        assert planner.plan_calls == 2
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
            "message": (
                "Monitor BTC/USDT when the 15m candle rises open-to-close "
                "by at least 3%"
            ),
            "client_message_id": "launch-v2-idempotent",
        }
        await service.handle_message(session, chat, **kwargs)
        first = load_strategy_draft_v2(chat)
        await service.handle_message(session, chat, **kwargs)
        second = load_strategy_draft_v2(chat)

        assert planner.plan_calls == 1
        assert second.version == first.version
        assert second.semantic_hash == first.semantic_hash


async def test_repeated_identical_text_is_understood_again_but_changes_nothing(
    test_context,
):
    """The same words can mean different things at different points in a conversation.

    The old path skipped the model whenever a message's text hash repeated. In a
    context-aware agent that is wrong — `yes` twice answers two different questions —
    so the text cache is gone. A genuine retry is still free: it is caught earlier by
    ``client_message_id``. Repeating the words costs one planning call and, because the
    patch is already reflected, leaves the draft untouched.
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
        message = (
            "Monitor BTC/USDT when the 15m candle rises open-to-close "
            "by at least 3%"
        )
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

        assert planner.plan_calls == 2, "the agent re-reads the turn in its new context"
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
            message=(
                "Monitor BTC/USDT when the 15m candle rises open-to-close "
                "by at least 3%"
            ),
            client_message_id="launch-v2-before-approval",
        )
        draft = load_strategy_draft_v2(chat)
        assert chat.draft_schema_json is not None
        schema_hash = StrategyDefinition.model_validate(
            chat.draft_schema_json
        ).canonical_hash()

        await service.approve_draft(
            session,
            chat,
            expected_schema_hash=schema_hash,
            expected_draft_version=draft.version,
            expected_semantic_hash=draft.semantic_hash,
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
            expected_draft_version=draft.version,
            expected_semantic_hash=draft.semantic_hash,
        )

        assert chat.approved_strategy_id == strategy_id
        assert chat.approved_strategy_version_id == version_id

        await service.handle_message(
            session,
            chat,
            message=(
                "Also require the 1h candle to fall close-to-close "
                "by at most -2%"
            ),
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
            message=(
                "Monitor BTC/USDT when the 15m candle rises open-to-close "
                "by at least 3%"
            ),
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
        initial = load_strategy_draft_v2(chat)

        result = await service.handle_message(
            session,
            chat,
            message=(
                "Should the move use close-to-close or a swing high? "
                "Choose one for me."
            ),
            client_message_id="launch-v2-model-non-mutation",
        )
        current = load_strategy_draft_v2(result)

        assert current.semantic_hash == initial.semantic_hash
        assert current.version == initial.version
        # One turn is bounded at one planning call plus one composing call, whichever
        # route it takes. Never a loop.
        assert (result.context_json or {})["turn_runtime"]["model_call_count"] <= 2
        assert planner.plan_calls == 1, "exactly one planning call"


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

    created = await test_context["client"].post(
        "/api/v1/dashboard/setup-chat/sessions"
    )
    assert created.status_code == 201
    chat_id = created.json()["id"]
    assert created.json()["draft_v2"]["schema_version"] == "2.0"

    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": (
                "Monitor BTC/USDT when the 15m candle rises open-to-close "
                "by at least 3%"
            ),
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
        "expected_draft_version": payload["draft_v2"]["version"],
        "expected_semantic_hash": payload["draft_v2"]["semantic_hash"],
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
    assert repeated.json()["approved_strategy_version_id"] == (
        approved_body["approved_strategy_version_id"]
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
    created = await test_context["client"].post(
        "/api/v1/dashboard/setup-chat/sessions"
    )
    chat_id = created.json()["id"]
    ready = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": (
                "Monitor BTC/USDT when the 15m candle rises open-to-close "
                "by at least 3%"
            ),
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
    assert body["error"]["stage"] == "extract"
    assert body["error"]["draft_id"] == before["draft_id"]
    assert body["error"]["draft_version"] == before["version"]
    assert body["error"]["semantic_hash"] == before["semantic_hash"]
    assert body["draft_v2"]["semantic_hash"] == before["semantic_hash"]

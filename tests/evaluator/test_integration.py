from pydantic import SecretStr

from ai_market_monitor.api.routers.dashboard_api import get_ai_setup_chat_service
from ai_market_monitor.schemas.ai_setup_chat import SetupChatInterviewResult
from ai_market_monitor.schemas.strategy import InterpretationPreview
from ai_market_monitor.services.ai_setup_chat import AISetupChatService
from hm_chatbot_eval.config import Settings as EvaluatorSettings
from hm_chatbot_eval.targets.backend import HilalMarketsBackendTarget
from tests.factories import load_strategy


class ReadyInterviewer:
    async def respond(self, **_) -> SetupChatInterviewResult:
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="The validated rule sheet is ready for review.",
            ready_to_compile=True,
            setup_summary="Monitor the supplied measurable setup.",
        )


class FixedInterpreter:
    async def interpret(self, _) -> InterpretationPreview:
        return InterpretationPreview(
            strategy=load_strategy(),
            assumptions=[],
            interpreter="evaluator-integration-compiler",
        )


class SnapshotProvider:
    async def list_symbols(self, exchange, quote_currencies):
        return ["SOL/USDT", "LINK/USDT"]


async def _signup(test_context, email: str) -> None:
    client = test_context["client"]
    response = await client.post(
        "/signup",
        data={
            "email": email,
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


async def test_real_backend_adapter_uses_owned_session_compile_contract(test_context):
    await _signup(test_context, "evaluator-adapter@example.com")
    test_context["settings"].openai_api_key = SecretStr("test-key")
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    settings = EvaluatorSettings(
        _env_file=None,
        target_backend_base_url="http://testserver",
    )
    target = HilalMarketsBackendTarget(
        settings,
        client=test_context["client"],
    )
    await target.start("integration-001", {"name": "current"})
    reply = await target.send(
        "RSI below 30 on 15m Binance USDT spot pairs.",
        scenario_id="integration-001",
    )
    assert reply.status_code == 200
    assert reply.error is None
    assert reply.conversation_id
    assert reply.structured is not None
    assert reply.structured["strategy"]["universe"]["market_type"] == "spot"
    assert reply.structured["canonical_hash"] == reply.structured["approval"]["schema_hash"]
    assert reply.structured["approval"]["eligible"] is True
    assert reply.structured["canvas"]["nodes"]
    approved = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{target.conversation_id}/approve",
        json={
            "approved": True,
            "expected_schema_hash": reply.structured["canonical_hash"],
            "confirmed_low_confidence_rule_keys": [],
        },
    )
    assert approved.status_code == 200, approved.text
    approved_contract = approved.json()["evaluation_contract"]
    assert approved_contract["approval"]["approved"] is True
    assert approved_contract["approval"]["strategy_version_number"] == 1
    assert (
        approved_contract["approval"]["immutable_version_hash"]
        == approved_contract["canonical_hash"]
    )
    await target.close()


async def test_evaluator_headers_fail_closed_when_test_control_is_disabled(test_context):
    await _signup(test_context, "evaluator-disabled@example.com")
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{created.json()['id']}/messages",
        headers={"X-HM-Eval-Fault": "timeout_once"},
        json={
            "message": "Monitor BTC on 15m",
            "client_message_id": "eval-disabled-message",
        },
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    # The classifier token stays first and unchanged, so the evaluator still records
    # this as EVALUATOR_FAULT_CONTROL_UNAVAILABLE and stops before spending. The
    # reason follows it: a bare token gave the operator nothing to act on, and a run
    # was lost to a target started with the wrong APP_ENV while both evaluator flags
    # were already true.
    assert detail.startswith("evaluator_control_unavailable")
    assert "AI_SETUP_EVALUATOR_ENABLED" in detail or "APP_ENV" in detail


async def test_authenticated_builder_exposes_only_targeted_evaluator_selectors(test_context):
    await _signup(test_context, "evaluator-selectors@example.com")
    response = await test_context["client"].get("/dashboard/strategies/new")
    assert response.status_code == 200
    assert 'data-evaluator-target="authenticated-ai-setup-chat"' in response.text
    assert 'data-testid="new-ai-setup-chat"' in response.text
    assert 'data-testid="ai-setup-structured-preview"' in response.text
    assert 'data-testid="ai-setup-approval"' in response.text
    assert 'data-evaluator-target="public-support-chat"' not in response.text

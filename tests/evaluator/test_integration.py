import json

from ai_market_monitor.api.routers.dashboard_api import get_ai_setup_chat_service
from ai_market_monitor.services.ai_setup_chat import AISetupChatService
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from hm_chatbot_eval.config import Settings as EvaluatorSettings
from hm_chatbot_eval.targets.backend import HilalMarketsBackendTarget
from tests.integration.test_setup_chat_launch_v2 import (
    MarketProvider,
    StandInPlanner,
    _agent,
    _launch_settings,
)


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
    launch_settings = _launch_settings(test_context["settings"])
    service = AISetupChatService(
        launch_settings,
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(test_context["settings"], StandInPlanner()),
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
        ("Monitor BTC/USDT when the 15m candle rises open-to-close by at least 3%"),
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
    assert reply.raw is not None
    draft_v2 = reply.raw["draft_v2"]
    approved = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{target.conversation_id}/approve",
        json={
            "approved": True,
            "expected_schema_hash": reply.structured["canonical_hash"],
            "expected_executable_version": draft_v2["executable_version"],
            "expected_executable_hash": draft_v2["executable_hash"],
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


async def test_consumed_one_shot_shape_fault_marks_the_recovered_response(test_context):
    """The marker survives the single shape recovery allowed by production."""

    await _signup(test_context, "evaluator-consumed@example.com")
    settings = test_context["settings"]
    settings.ai_setup_evaluator_enabled = True
    settings.ai_setup_evaluator_faults_enabled = True
    planner = StandInPlanner()
    service = AISetupChatService(
        _launch_settings(settings),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(settings, planner),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{created.json()['id']}/messages",
        headers={"X-HM-Eval-Fault": "empty_once"},
        json={
            "message": "Monitor BTC/USDT when the 15m candle rises by at least 3%",
            "client_message_id": "eval-consumed-message",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-HM-Eval-Fault-Applied"] == "empty_once"
    assert planner.plan_calls == 1, "the injected first call was followed by one recovery only"


async def _fault_marker_for(test_context, email: str, message: str) -> str | None:
    """Send ``message`` with a one-shot fault injected and report the marker."""

    await _signup(test_context, email)
    settings = test_context["settings"]
    settings.ai_setup_evaluator_enabled = True
    settings.ai_setup_evaluator_faults_enabled = True
    service = AISetupChatService(
        _launch_settings(settings),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(settings, StandInPlanner()),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    response = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{created.json()['id']}/messages",
        headers={"X-HM-Eval-Fault": "empty_once"},
        json={"message": message, "client_message_id": "oi4-008-probe"},
    )
    assert response.status_code < 500, response.text
    return response.headers.get("X-HM-Eval-Fault-Applied")


async def test_oi4_008_the_readiness_probe_message_does_reach_the_model_boundary(test_context):
    """OI4-008, half one: fault injection was never broken.

    The exact message the readiness gate sends, with the exact fault it probes
    with, reaches the model boundary and comes back carrying the marker. So a
    missing marker in a real run cannot be blamed on the probe message.
    """

    from hm_chatbot_eval.runner import EvaluationRunner

    marker = await _fault_marker_for(
        test_context,
        "oi4-008-readiness@example.com",
        EvaluationRunner._readiness_message(),
    )
    assert marker == "empty_once", (
        "the readiness probe message did not reach the model boundary"
    )


def test_oi4_008_a_recovered_turn_still_proves_the_fault_was_applied():
    """OI4-008, half two: the gate demanded the product fail in order to pass.

    This is the assertion that failed before the fix. ``empty_once`` is the fault
    the readiness gate probes with, and the product is built to survive exactly
    one bad response shape — so the turn answers 200 with the marker attached.
    The gate required a 4xx or 5xx, threw the proof away, and reported fault
    control as unavailable on a target where it worked.
    """

    from hm_chatbot_eval.runner import EvaluationRunner
    from hm_chatbot_eval.targets.base import TargetReply

    recovered = TargetReply(
        latency_ms=0.0,
        text="Here is your draft.",
        status_code=200,
        raw={"_evaluator_fault_applied": "empty_once"},
    )
    assert EvaluationRunner._expected_evaluator_fault_response(
        recovered, expected_fault="empty_once"
    ), "a recovered turn carrying the marker is proof the fault reached the model"


def test_oi4_008_an_unmarked_reply_is_never_accepted_as_proof():
    """The guard the fix must not weaken.

    Without the marker there is nothing to distinguish a target that applied the
    fault from one that ignored the header, so neither a normal reply nor an
    error may be accepted.
    """

    from hm_chatbot_eval.runner import EvaluationRunner
    from hm_chatbot_eval.targets.base import TargetReply

    for reply in (
        TargetReply(latency_ms=0.0, text="Here is your draft.", status_code=200, raw={}),
        TargetReply(
            latency_ms=0.0,
            text="",
            status_code=502,
            raw={"error": {"error_code": "TARGET_EMPTY_RESPONSE"}},
        ),
        TargetReply(
            latency_ms=0.0,
            text="",
            status_code=502,
            raw={
                "_evaluator_fault_applied": "timeout_once",
                "error": {"error_code": "TARGET_EMPTY_RESPONSE"},
            },
        ),
    ):
        assert not EvaluationRunner._expected_evaluator_fault_response(
            reply, expected_fault="empty_once"
        ), "an unmarked or mismatched reply was accepted as proof of a fault"


#: Wording that would tell a customer their request was refused for a religious,
#: screening or rule-building reason. None of it may appear because a provider
#: went down. Deliberately a superset of the product's own vocabulary: a defender
#: that only checks the words it already uses can only confirm what it knows.
_MISATTRIBUTION_WORDS = (
    "shariah",
    "sharia",
    "halal",
    "haram",
    "islamic",
    "compliance",
    "screening",
    "screened",
    "not eligible",
    "compile",
    "compiler",
    "compilation",
    "capability",
    "unsupported",
    "invalid rule",
)


def _what_the_customer_is_told(body: dict) -> str:
    """The words a person actually reads: the failure banner and the reply.

    Deliberately not the whole response. The draft is echoed back unchanged on
    every turn and carries structural field names such as ``sharia_policy``;
    matching those would fail on the product working correctly, and a check that
    cries wolf gets deleted rather than fixed.
    """

    error = body.get("error") or {}
    spoken = [
        str(message.get("content") or "")
        for message in (body.get("messages") or [])
        if str(message.get("role") or "") != "user"
    ]
    return json.dumps(
        {"error": error, "spoken": spoken}, ensure_ascii=False
    ).casefold()


async def _turn_with_fault(test_context, email: str, fault: str):
    await _signup(test_context, email)
    settings = test_context["settings"]
    settings.ai_setup_evaluator_enabled = True
    settings.ai_setup_evaluator_faults_enabled = True
    service = AISetupChatService(
        _launch_settings(settings),
        MarketProvider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=_agent(settings, StandInPlanner()),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    return await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{created.json()['id']}/messages",
        headers={"X-HM-Eval-Fault": fault},
        json={
            "message": "Monitor BTC/USDT when the 15m candle rises by at least 3%",
            "client_message_id": "fault-attribution",
        },
    )


async def test_a_provider_outage_is_never_shown_as_a_shariah_or_compiler_failure(test_context):
    """Phase 5 invariant, now proved by injecting the outage rather than assuming it.

    An AI or provider outage is the product's problem. Telling a customer their
    setup failed screening, or failed to compile, teaches them something false
    about their own rules and about the Shariah process — the one thing this
    product cannot afford to be careless with.
    """

    response = await _turn_with_fault(
        test_context, "fault-timeout@example.com", "timeout_once"
    )
    assert response.headers.get("X-HM-Eval-Fault-Applied") == "timeout_once", (
        "the fault never reached the model boundary, so this proves nothing"
    )
    body = response.json()
    error = body.get("error") or {}
    assert error.get("stage") == "provider", (
        f"an injected provider timeout was reported as stage {error.get('stage')!r}"
    )
    assert error.get("retryable") is True
    rendered = _what_the_customer_is_told(body)
    for word in _MISATTRIBUTION_WORDS:
        assert word not in rendered, (
            f"a provider outage rendered the word {word!r} to the customer"
        )


async def test_a_rate_limited_ai_call_is_never_shown_as_a_screening_failure(test_context):
    """The same invariant for the other outage a customer actually meets."""

    response = await _turn_with_fault(test_context, "fault-429@example.com", "429_once")
    assert response.headers.get("X-HM-Eval-Fault-Applied") == "429_once"
    body = response.json()
    error = body.get("error") or {}
    assert error.get("stage") == "provider", (
        f"an injected rate limit was reported as stage {error.get('stage')!r}"
    )
    rendered = _what_the_customer_is_told(body)
    for word in _MISATTRIBUTION_WORDS:
        assert word not in rendered, (
            f"a rate-limited AI call rendered the word {word!r} to the customer"
        )


async def test_authenticated_builder_exposes_only_targeted_evaluator_selectors(test_context):
    await _signup(test_context, "evaluator-selectors@example.com")
    response = await test_context["client"].get("/dashboard/strategies/new")
    assert response.status_code == 200
    assert 'data-evaluator-target="authenticated-ai-setup-chat"' in response.text
    assert 'data-testid="new-ai-setup-chat"' in response.text
    assert 'data-testid="ai-setup-structured-preview"' in response.text
    assert 'data-testid="ai-setup-approval"' in response.text
    assert 'data-evaluator-target="public-support-chat"' not in response.text



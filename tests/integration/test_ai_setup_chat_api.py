from pydantic import SecretStr
from sqlalchemy import select

from ai_market_monitor.api.routers.dashboard_api import get_ai_setup_chat_service
from ai_market_monitor.db.models import CapabilityExtension
from ai_market_monitor.schemas.ai_setup_chat import SetupChatInterviewResult
from ai_market_monitor.schemas.strategy import InterpretationPreview
from ai_market_monitor.services.ai_setup_chat import AISetupChatService
from tests.factories import load_strategy


class ReadyInterviewer:
    async def respond(self, **_) -> SetupChatInterviewResult:
        return SetupChatInterviewResult(
            intent="setup",
            assistant_message="Your deterministic rule sheet is ready for review.",
            ready_to_compile=True,
            setup_summary="RSI is below 30 on 15m Binance USDT spot pairs.",
        )


class FixedInterpreter:
    async def interpret(self, _) -> InterpretationPreview:
        return InterpretationPreview(
            strategy=load_strategy(),
            assumptions=[],
            interpreter="api-test-compiler",
        )


class SnapshotProvider:
    async def list_symbols(self, exchange, quote_currencies):
        return ["SOL/USDT", "BTC/USDT"]

    async def fetch_universe_metadata(self, exchange, symbols, **_):
        return {
            "SOL/USDT": {"percentage_24h": 4.2},
            "BTC/USDT": {"percentage_24h": -1.0},
        }


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


async def test_legacy_compat_setup_chat_api_creates_resumes_and_compiles(test_context):
    await _signup(test_context, "ai-chat-api@example.com")
    test_context["settings"].openai_api_key = SecretStr("test-key")
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service

    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    assert created.status_code == 201
    chat_id = created.json()["id"]
    assert created.json()["messages"][0]["message_type"] == "welcome"
    assert {item["value"] for item in created.json()["messages"][0]["payload"]["start_modes"]} == {
        "scanner",
        "monitor",
    }

    replied = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "RSI below 30 on 15m Binance USDT spot pairs.",
            "client_message_id": "api-client-message-001",
        },
    )
    assert replied.status_code == 200, replied.text
    payload = replied.json()
    assert payload["status"] == "ready_for_approval"
    assert payload["can_approve"] is True
    assert payload["draft_strategy"]["universe"]["market_type"] == "spot"
    user_messages = [item for item in payload["messages"] if item["role"] == "user"]
    assert user_messages[-1]["client_message_id"] == "api-client-message-001"

    duplicate = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "RSI below 30 on 15m Binance USDT spot pairs.",
            "client_message_id": "api-client-message-001",
        },
    )
    assert duplicate.status_code == 200
    assert len(duplicate.json()["messages"]) == len(payload["messages"])

    resumed = await test_context["client"].get("/api/v1/dashboard/setup-chat/sessions/current")
    assert resumed.status_code == 200
    assert resumed.json()["id"] == chat_id


async def test_authenticated_chat_approval_compiles_once_and_is_idempotent(test_context):
    await _signup(test_context, "ai-chat-message-approval@example.com")
    test_context["settings"].openai_api_key = SecretStr("test-key")
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service

    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]
    drafted = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "RSI below 30 on 15m Binance USDT spot pairs.",
            "client_message_id": "approval-message-001",
        },
    )
    assert drafted.status_code == 200, drafted.text
    draft_payload = drafted.json()
    assert draft_payload["status"] == "ready_for_approval"
    draft_hash = draft_payload["schema_hash"]

    approved = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "I approve",
            "client_message_id": "approval-message-002",
        },
    )
    assert approved.status_code == 200, approved.text
    approved_payload = approved.json()
    assert approved_payload["status"] == "approved"
    assert approved_payload["can_approve"] is False
    assert approved_payload["schema_hash"] == draft_hash
    assert approved_payload["approved_strategy_id"]
    assert approved_payload["approved_strategy_version_id"]
    assert approved_payload["evaluation_contract"]["approval"]["approved"] is True
    assert approved_payload["evaluation_contract"]["approval"]["lifecycle_state"] == "compiled"

    message_count = len(approved_payload["messages"])
    strategy_id = approved_payload["approved_strategy_id"]
    version_id = approved_payload["approved_strategy_version_id"]
    repeated = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "mowafe2",
            "client_message_id": "approval-message-003",
        },
    )
    assert repeated.status_code == 200, repeated.text
    repeated_payload = repeated.json()
    assert repeated_payload["status"] == "approved"
    assert repeated_payload["approved_strategy_id"] == strategy_id
    assert repeated_payload["approved_strategy_version_id"] == version_id
    assert len(repeated_payload["messages"]) == message_count


async def test_setup_chat_scanner_mode_compiles_a_temporary_rule_set(test_context):
    await _signup(test_context, "ai-chat-scanner@example.com")
    test_context["settings"].openai_api_key = SecretStr("test-key")
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]
    selected = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "",
            "option_key": "setup_mode",
            "option_value": "scanner",
            "option_label": "Scanner",
            "client_message_id": "scanner-mode-api-001",
        },
    )
    assert selected.status_code == 200
    assert selected.json()["setup_mode"] == "scanner"
    compiled = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "RSI below 30 on 15m Binance USDT spot pairs.",
            "client_message_id": "scanner-mode-api-002",
        },
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["status"] == "ready_to_scan"
    assert compiled.json()["can_scan"] is True
    assert compiled.json()["can_approve"] is False


async def test_setup_chat_api_refuses_unrelated_and_serves_market_snapshot(test_context):
    await _signup(test_context, "ai-chat-scope@example.com")
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]

    unrelated = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "Give me a cupcake recipe",
            "client_message_id": "unrelated-request-001",
        },
    )
    assert unrelated.status_code == 200
    assert unrelated.json()["messages"][-1]["message_type"] == "scope_refusal"

    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    snapshot = await test_context["client"].get("/api/v1/dashboard/setup-chat/market-snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "available"
    assert snapshot.json()["top_movers"][0]["symbol"] == "SOL/USDT"
    assert snapshot.json()["provider_name"] == "SnapshotProvider"
    assert snapshot.json()["captured_at"]


async def test_setup_chat_html_is_mobile_ready_and_does_not_expose_api_key(test_context):
    await _signup(test_context, "ai-chat-html@example.com")
    test_context["settings"].openai_api_key = SecretStr("must-never-appear-in-html")
    response = await test_context["client"].get("/dashboard/strategies/new")
    assert response.status_code == 200
    assert 'data-testid="ai-setup-chat"' in response.text
    assert 'data-testid="setup-entry-screen"' not in response.text
    assert 'data-ai-open-canvas' in response.text
    assert 'href="/dashboard/scan-now"' not in response.text
    assert "ai-chat-hero" not in response.text
    assert 'class="ai-chat-composer"' in response.text
    assert "data-ai-chat-input" in response.text
    assert "ai-setup-chat.css" in response.text
    assert "ai-setup-chat.js" in response.text
    assert "must-never-appear-in-html" not in response.text


async def test_legacy_quick_scan_page_redirects_to_chat_scanner(test_context):
    await _signup(test_context, "ai-chat-scanner-redirect@example.com")
    response = await test_context["client"].get(
        "/dashboard/scan-now", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/strategies/new?mode=scanner"


async def test_unknown_fragment_can_enter_certified_mechanic_queue(test_context):
    await _signup(test_context, "ai-chat-extension@example.com")
    test_context["settings"].openai_api_key = SecretStr("test-key")
    test_context["settings"].capability_extension_enabled = True
    service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]
    unresolved = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "Find moon-wobble candles on 15m",
            "client_message_id": "extension-request-1",
        },
    )
    assert unresolved.status_code == 200, unresolved.text
    clarification = unresolved.json()["messages"][-1]["payload"]["clarifications"][0]
    create_option = next(
        option for option in clarification["options"] if option["value"] == "__build_mechanic__"
    )
    queued = await test_context["client"].post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages",
        json={
            "message": "",
            "option_key": create_option["key"],
            "option_value": create_option["value"],
            "option_label": create_option["label"],
            "client_message_id": "extension-request-2",
        },
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "building_mechanic"
    assert any(
        item["message_type"] == "mechanic_build_status"
        for item in queued.json()["messages"]
    )
    async with test_context["session_factory"]() as session:
        extension = await session.scalar(select(CapabilityExtension))
        assert extension is not None
        assert extension.status == "queued"
        assert "moon-wobble" in extension.source_prompt


# ---------------------------------------------------------------------------
# The Guided Builder, driven over real HTTP.
#
# The service-level suite proves the behaviour; these prove the boundary — that the
# routes exist, that the request schema refuses a change with no target, and that the
# response carries everything the page needs to draw itself.
# ---------------------------------------------------------------------------


async def test_builder_contract_endpoint_is_authoritative_and_complete(test_context):
    await _signup(test_context, "builder-contract-api@example.com")
    response = await test_context["client"].get(
        "/api/v1/dashboard/setup-chat/builder-contract"
    )
    assert response.status_code == 200
    payload = response.json()
    for key in ("mechanics", "starters", "modes", "universes", "logic", "lifecycle_states"):
        assert payload[key], f"the contract sent no {key}"
    for mechanic in payload["mechanics"]:
        assert mechanic["label"] and mechanic["explanation"], mechanic["key"]
        if not mechanic["available"]:
            assert mechanic["unavailable_reason"], mechanic["key"]
        for parameter in mechanic["parameters"]:
            assert parameter["label"], f"{mechanic['key']}.{parameter['name']}"
            if parameter["kind"] == "choice":
                assert parameter["choices"], f"{mechanic['key']}.{parameter['name']}"
    assert payload["ai_availability"]["builder"] is True


async def test_builder_action_endpoint_builds_a_rule_and_returns_the_new_state(test_context):
    await _signup(test_context, "builder-action-api@example.com")
    client = test_context["client"]
    created = await client.post("/api/v1/dashboard/setup-chat/sessions")
    assert created.status_code == 201
    chat_id = created.json()["id"]

    moded = await client.post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/builder-actions",
        json={
            "action": "select_mode",
            "client_message_id": "cm-api-builder-mode",
            "value": "monitor",
        },
    )
    assert moded.status_code == 200, moded.text
    assert moded.json()["builder"]["mode"] == "monitor"

    added = await client.post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/builder-actions",
        json={
            "action": "add_condition",
            "client_message_id": "cm-api-builder-rule",
            "mechanic_key": "open_to_close_percentage",
            "values": {
                "direction": "up",
                "comparator": "gte",
                "threshold": 5,
                "timeframe": "1h",
            },
        },
    )
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["builder"]["conditions"], "the response carries no rule to draw"
    assert body["builder"]["conditions"][0]["editable"] is True
    assert body["lifecycle"]["label"], "the response carries no lifecycle to show"


async def test_a_builder_action_with_no_target_is_refused_at_the_boundary(test_context):
    """Refused by the request schema, so a malformed change never reaches the draft."""

    await _signup(test_context, "builder-invalid-api@example.com")
    client = test_context["client"]
    created = await client.post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/builder-actions",
        json={"action": "remove_condition", "client_message_id": "cm-api-no-target"},
    )
    assert response.status_code == 422


async def test_a_builder_action_without_an_idempotency_key_is_refused(test_context):
    """A double-clicked button must act once, so the key is not optional."""

    await _signup(test_context, "builder-nokey-api@example.com")
    client = test_context["client"]
    created = await client.post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/builder-actions",
        json={"action": "select_mode", "value": "monitor"},
    )
    assert response.status_code == 422


async def test_a_builder_action_cannot_touch_another_persons_setup(test_context):
    """Ownership is checked before anything else. A session id alone is not enough."""

    client = test_context["client"]
    await _signup(test_context, "builder-owner-api@example.com")
    created = await client.post("/api/v1/dashboard/setup-chat/sessions")
    chat_id = created.json()["id"]

    await client.post("/logout", follow_redirects=False)
    await _signup(test_context, "builder-intruder-api@example.com")
    response = await client.post(
        f"/api/v1/dashboard/setup-chat/sessions/{chat_id}/builder-actions",
        json={
            "action": "select_mode",
            "client_message_id": "cm-api-not-mine",
            "value": "scanner",
        },
    )
    assert response.status_code in {403, 404}
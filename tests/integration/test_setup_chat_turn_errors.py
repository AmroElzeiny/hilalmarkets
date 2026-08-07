"""A failed turn must never be a bare HTTP 500 with no assistant message.

Run 20260725T122105Z recorded 24 turns as ``HTTP 500 with no assistant message``
across 14 of 42 cases. The route caught only ``SetupChatError`` and
``AISetupEvaluatorControlError``, so a pydantic ``ValidationError`` or a provider
timeout escaped as an empty 500 and was then scored as a chatbot answer.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from ai_market_monitor.api.routers.dashboard_api import get_ai_setup_chat_service
from ai_market_monitor.schemas.ai_setup_chat import (
    SETUP_CHAT_MESSAGE_MAX_LENGTH,
    SETUP_CHAT_SOURCE_EXCERPT_MAX_LENGTH,
    SetupChatClarification,
    SetupChatMessageRequest,
)
from ai_market_monitor.services.ai_setup_chat import (
    AISetupChatService,
    SetupChatError,
    _fallback_turn_classification,
    setup_chat_error_envelope,
)
from tests.integration.test_ai_setup_chat_api import (
    FixedInterpreter,
    ReadyInterviewer,
    SnapshotProvider,
    _signup,
)

MESSAGE_PATH = "/api/v1/dashboard/setup-chat/sessions/{chat_id}/messages"


def _oversized_validation_error() -> ValidationError:
    """The real shape of a fast 500: a clarification longer than its 500-char bound."""
    try:
        SetupChatClarification(key="k", question="x" * 900, reason="r", options=[])
    except ValidationError as exc:
        return exc
    raise AssertionError("clarification bound no longer enforced")


class _FailingService(AISetupChatService):
    """Raises the way the production service did on the failing turns."""

    failure: BaseException = RuntimeError("interpreter exploded")

    async def handle_message(self, *args, **kwargs):
        raise type(self).failure


class _ConflictService(AISetupChatService):
    async def handle_message(self, *args, **kwargs):
        raise SetupChatError(
            "setup_changed",
            "The translated rules changed. Review the latest translation before approval.",
            status_code=409,
        )


async def _start_chat(test_context, email: str, service) -> str:
    await _signup(test_context, email)
    test_context["settings"].openai_api_key = SecretStr("test-key")
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = lambda: service
    created = await test_context["client"].post("/api/v1/dashboard/setup-chat/sessions")
    assert created.status_code == 201
    return created.json()["id"]


@pytest.mark.parametrize(
    ("failure", "error_code", "stage", "retryable"),
    [
        (RuntimeError("boom"), "STRATEGY_COMPILE_FAILED", "interpret", False),
        (ValueError("cannot compile"), "STRATEGY_COMPILE_FAILED", "compile", False),
        (TimeoutError("provider slow"), "TARGET_TOTAL_TIMEOUT", "provider", True),
    ],
)
def test_envelope_classifies_each_failure_kind(failure, error_code, stage, retryable) -> None:
    envelope = setup_chat_error_envelope(failure)
    assert envelope.error_code == error_code
    assert envelope.stage == stage
    assert envelope.retryable is retryable
    assert envelope.request_id


def test_validation_error_is_a_serialize_stage_failure_with_the_field() -> None:
    envelope = setup_chat_error_envelope(_oversized_validation_error())
    assert envelope.error_code == "TARGET_SCHEMA_VALIDATION"
    assert envelope.stage == "serialize"
    assert envelope.retryable is False
    assert envelope.field == "question"


def test_maximum_valid_message_uses_a_bounded_non_authoritative_segment() -> None:
    message = "Keep this explanation concise. " * 180
    message = message[:SETUP_CHAT_MESSAGE_MAX_LENGTH]
    request = SetupChatMessageRequest(
        message=message,
        client_message_id="max-length-message-1",
    )
    classification = _fallback_turn_classification(
        request.message,
        active_clarification=None,
    )
    assert len(classification.segments) == 1
    assert len(classification.segments[0].text) <= SETUP_CHAT_SOURCE_EXCERPT_MAX_LENGTH
    assert request.message == message


def test_envelope_never_leaks_the_original_exception_text() -> None:
    secret = "postgres://user:hunter2@db.internal:5432/prod"
    envelope = setup_chat_error_envelope(RuntimeError(secret))
    rendered = envelope.model_dump_json()
    assert "hunter2" not in rendered
    assert "db.internal" not in rendered


async def test_failed_turn_returns_envelope_and_an_assistant_message(test_context):
    service = _FailingService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    _FailingService.failure = RuntimeError("interpreter exploded")
    chat_id = await _start_chat(test_context, "turn-error-500@example.com", service)

    response = await test_context["client"].post(
        MESSAGE_PATH.format(chat_id=chat_id),
        json={"message": "RSI below 30 on 15m", "client_message_id": "turn-error-001"},
    )

    assert response.status_code == 500
    body = response.json()

    envelope = body["error"]
    assert envelope["error_code"] == "STRATEGY_COMPILE_FAILED"
    assert envelope["stage"] == "interpret"
    assert envelope["retryable"] is False
    assert envelope["request_id"]

    assistant = [m for m in body["messages"] if m["role"] == "assistant"]
    assert assistant, "the turn must not end silently"
    last = assistant[-1]
    assert last["message_type"] == "turn_error"
    assert envelope["request_id"] in last["content"]
    assert "interpreter exploded" not in last["content"]
    assert last["payload"]["can_approve"] is False


async def test_failed_turn_does_not_advance_the_draft_or_allow_approval(test_context):
    service = _FailingService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    _FailingService.failure = ValueError("compiler rejected the setup")
    chat_id = await _start_chat(test_context, "turn-error-state@example.com", service)

    response = await test_context["client"].post(
        MESSAGE_PATH.format(chat_id=chat_id),
        json={"message": "something impossible", "client_message_id": "turn-error-002"},
    )
    assert response.status_code == 500
    body = response.json()

    assert body["error"]["stage"] == "compile"
    assert body["can_approve"] is False
    assert body["draft_strategy"] is None
    assert body["evaluation_contract"] is None
    assert body["status"] == "interviewing"


async def test_provider_timeout_is_reported_as_retryable(test_context):
    service = _FailingService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    _FailingService.failure = TimeoutError("model call exceeded budget")
    chat_id = await _start_chat(test_context, "turn-error-timeout@example.com", service)

    response = await test_context["client"].post(
        MESSAGE_PATH.format(chat_id=chat_id),
        json={"message": "watch BTCUSDT", "client_message_id": "turn-error-003"},
    )
    assert response.status_code == 503
    body = response.json()

    assert body["error"]["error_code"] == "TARGET_TOTAL_TIMEOUT"
    assert body["error"]["stage"] == "provider"
    assert body["error"]["retryable"] is True
    assert [m for m in body["messages"] if m["role"] == "assistant"]


async def test_failed_later_turn_preserves_the_last_valid_draft(test_context):
    ready_service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    chat_id = await _start_chat(
        test_context,
        "turn-error-preserves-draft@example.com",
        ready_service,
    )
    drafted = await test_context["client"].post(
        MESSAGE_PATH.format(chat_id=chat_id),
        json={
            "message": "RSI below 30 on 15m Binance USDT spot pairs.",
            "client_message_id": "turn-error-preserve-001",
        },
    )
    assert drafted.status_code == 200, drafted.text
    before = drafted.json()
    assert before["status"] == "ready_for_approval"
    assert before["draft_strategy"] is not None

    failing_service = _FailingService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    _FailingService.failure = RuntimeError("provider response could not be interpreted")
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = (
        lambda: failing_service
    )
    failed = await test_context["client"].post(
        MESSAGE_PATH.format(chat_id=chat_id),
        json={
            "message": "Keep that draft and explain it more simply.",
            "client_message_id": "turn-error-preserve-002",
        },
    )

    assert failed.status_code == 500
    after = failed.json()
    assert after["status"] == "ready_for_approval"
    assert after["schema_hash"] == before["schema_hash"]
    assert after["draft_strategy"] == before["draft_strategy"]
    assert after["can_approve"] is True
    assert after["error"]["error_code"] == "STRATEGY_COMPILE_FAILED"


async def test_structured_conflict_preserves_the_authoritative_draft(test_context):
    ready_service = AISetupChatService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    chat_id = await _start_chat(
        test_context,
        "turn-conflict-preserves-draft@example.com",
        ready_service,
    )
    drafted = await test_context["client"].post(
        MESSAGE_PATH.format(chat_id=chat_id),
        json={
            "message": "RSI below 30 on 15m Binance USDT spot pairs.",
            "client_message_id": "turn-conflict-001",
        },
    )
    before = drafted.json()
    conflict_service = _ConflictService(
        test_context["settings"],
        SnapshotProvider(),
        FixedInterpreter(),
        interviewer=ReadyInterviewer(),
    )
    test_context["app"].dependency_overrides[get_ai_setup_chat_service] = (
        lambda: conflict_service
    )

    conflict = await test_context["client"].post(
        MESSAGE_PATH.format(chat_id=chat_id),
        json={
            "message": "I approve",
            "client_message_id": "turn-conflict-002",
        },
    )

    assert conflict.status_code == 409
    after = conflict.json()
    assert after["error"]["error_code"] == "setup_changed"
    assert after["error"]["retryable"] is False
    assert after["status"] == "ready_for_approval"
    assert after["schema_hash"] == before["schema_hash"]
    assert after["draft_strategy"] == before["draft_strategy"]
    assert after["evaluation_contract"] is not None
    assert after["messages"][-1]["message_type"] == "turn_error"
    assert "Review the latest translation" in after["messages"][-1]["content"]

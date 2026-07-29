"""Shared failure taxonomy for the target chatbot and the evaluator.

Run 20260723T152343Z recorded two turns as
``RemoteProtocolError: Server disconnected without sending a response.`` with an
empty assistant text, and scored them as chatbot quality failures. It also treated
an evaluator HTTP 429 as a single undifferentiated infrastructure outage.
"""

from __future__ import annotations

import asyncio
import json
import random

import httpx
import pytest

from hm_chatbot_eval.failures import (
    NON_RETRYABLE,
    RETRYABLE,
    RETRYABLE_STATUS_CODES,
    ExecutionState,
    FailureClass,
    FailureRecord,
    backoff_delay,
    classify_exception,
    classify_http_status,
    is_retryable,
    parse_retry_after,
    pause_state_for,
    rate_limit_headers,
    sanitize_excerpt,
)


def test_every_required_failure_class_exists() -> None:
    required = {
        "TARGET_DNS_RESOLUTION_FAILURE",
        "TARGET_CONNECT_TIMEOUT",
        "TARGET_READ_TIMEOUT",
        "TARGET_TOTAL_TIMEOUT",
        "TARGET_HTTP_429",
        "TARGET_HTTP_5XX",
        "TARGET_EMPTY_RESPONSE",
        "TARGET_INVALID_JSON",
        "TARGET_SCHEMA_VALIDATION",
        "TARGET_PARTIAL_STREAM",
        "TARGET_COMPILE_TIMEOUT",
        "TARGET_NO_ASSISTANT_MESSAGE",
        "UI_NAVIGATION_TIMEOUT",
        "UI_RESPONSE_TIMEOUT",
        "UI_AUTH_EXPIRED",
        "EVALUATOR_CONNECT_TIMEOUT",
        "EVALUATOR_READ_TIMEOUT",
        "EVALUATOR_HTTP_429_RATE_LIMIT",
        "EVALUATOR_HTTP_429_FLEX_CAPACITY",
        "EVALUATOR_HTTP_429_QUOTA",
        "EVALUATOR_HTTP_5XX",
        "EVALUATOR_AUTH_FAILURE",
        "EVALUATOR_FAULT_CONTROL_UNAVAILABLE",
        # A truncated reply from the grading model is not a chatbot fault. Without a
        # class of its own it was recorded as TARGET_INVALID_JSON and the wrong side
        # got investigated.
        "EVALUATOR_INVALID_JSON",
        "BUDGET_LIMIT",
        "USER_CANCELLED",
        "UNKNOWN_INFRASTRUCTURE_FAILURE",
    }
    assert required == {member.value for member in FailureClass}


def test_every_class_is_classified_exactly_once_for_retry() -> None:
    assert RETRYABLE.isdisjoint(NON_RETRYABLE)
    assert set(FailureClass) == RETRYABLE | NON_RETRYABLE


@pytest.mark.parametrize(
    ("exc", "role", "expected"),
    [
        (
            httpx.ConnectTimeout("connect timed out"),
            "target",
            FailureClass.TARGET_CONNECT_TIMEOUT,
        ),
        (
            httpx.ConnectError("getaddrinfo failed"),
            "target",
            FailureClass.TARGET_DNS_RESOLUTION_FAILURE,
        ),
        (httpx.ReadTimeout("read timed out"), "target", FailureClass.TARGET_READ_TIMEOUT),
        (
            httpx.ConnectTimeout("connect timed out"),
            "judge",
            FailureClass.EVALUATOR_CONNECT_TIMEOUT,
        ),
        (httpx.ReadTimeout("read timed out"), "judge", FailureClass.EVALUATOR_READ_TIMEOUT),
        (httpx.PoolTimeout("pool"), "target", FailureClass.TARGET_TOTAL_TIMEOUT),
        (httpx.ReadTimeout("slow"), "ui", FailureClass.UI_RESPONSE_TIMEOUT),
        (
            json.JSONDecodeError("Expecting value", "", 0),
            "target",
            FailureClass.TARGET_INVALID_JSON,
        ),
        (asyncio.CancelledError(), "target", FailureClass.USER_CANCELLED),
        (KeyboardInterrupt(), "judge", FailureClass.USER_CANCELLED),
    ],
)
def test_exceptions_map_to_their_owning_role(exc, role, expected) -> None:
    assert classify_exception(exc, role=role) is expected


def test_server_disconnect_is_a_partial_stream_not_a_chatbot_answer() -> None:
    """The exact failure recorded twice in run 20260723T152343Z."""
    exc = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    failure = classify_exception(exc, role="target")

    assert failure is FailureClass.TARGET_PARTIAL_STREAM
    assert is_retryable(failure) is True
    assert (
        FailureRecord(
            failure_class=failure, role="target", stage="send", retryable=True
        ).is_quality_signal
        is False
    )


def test_compile_stage_timeout_is_distinct_from_a_chat_timeout() -> None:
    exc = httpx.PoolTimeout("timed out")
    assert classify_exception(exc, role="target", stage="compile") is (
        FailureClass.TARGET_COMPILE_TIMEOUT
    )
    assert classify_exception(exc, role="target", stage="chat") is (
        FailureClass.TARGET_TOTAL_TIMEOUT
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "Rate limit reached for gpt-4o in organization org-x on tokens per min (TPM)",
            FailureClass.EVALUATOR_HTTP_429_RATE_LIMIT,
        ),
        (
            "Service tier flex has no capacity available right now",
            FailureClass.EVALUATOR_HTTP_429_FLEX_CAPACITY,
        ),
        (
            "You exceeded your current quota, please check your plan and billing details",
            FailureClass.EVALUATOR_HTTP_429_QUOTA,
        ),
    ],
)
def test_evaluator_429_is_split_into_three_distinct_conditions(body, expected) -> None:
    assert classify_http_status(429, role="judge", body=body) is expected


def test_quota_429_never_retries_but_rate_limit_429_does() -> None:
    """Quota needs a human; a rate limit is retried and only then pauses the run."""
    quota = classify_http_status(429, role="judge", body="insufficient_quota")
    rate = classify_http_status(429, role="judge", body="rate limit reached")

    assert is_retryable(quota) is False
    assert pause_state_for(quota) is ExecutionState.PAUSED_QUOTA
    assert is_retryable(rate) is True
    assert pause_state_for(rate) is ExecutionState.PAUSED_RATE_LIMIT


def test_flex_capacity_pauses_without_a_retry_loop() -> None:
    flex = classify_http_status(429, role="judge", body="flex capacity unavailable")
    assert pause_state_for(flex) is ExecutionState.PAUSED_FLEX_CAPACITY


def test_evaluator_auth_failure_pauses_and_does_not_retry() -> None:
    failure = classify_http_status(401, role="judge", body="invalid api key")
    assert failure is FailureClass.EVALUATOR_AUTH_FAILURE
    assert is_retryable(failure) is False
    assert pause_state_for(failure) is ExecutionState.PAUSED_AUTH


def test_target_429_and_5xx_are_target_side_not_evaluator_side() -> None:
    assert classify_http_status(429, role="target") is FailureClass.TARGET_HTTP_429
    assert classify_http_status(500, role="target") is FailureClass.TARGET_HTTP_5XX
    assert classify_http_status(503, role="judge") is FailureClass.EVALUATOR_HTTP_5XX


def test_ui_auth_expiry_is_classified_separately_from_a_bad_answer() -> None:
    failure = classify_http_status(401, role="ui", body="")
    assert failure is FailureClass.UI_AUTH_EXPIRED
    assert is_retryable(failure) is False
    assert pause_state_for(failure) is ExecutionState.PAUSED_AUTH


def test_evaluator_fault_control_denial_is_configuration_not_expired_auth() -> None:
    failure = classify_http_status(
        403,
        role="target",
        body='{"detail":"evaluator_control_unavailable"}',
    )
    assert failure is FailureClass.EVALUATOR_FAULT_CONTROL_UNAVAILABLE
    assert is_retryable(failure) is False
    assert pause_state_for(failure) is ExecutionState.FAILED_CONFIGURATION


def test_required_transient_status_codes_are_retryable() -> None:
    assert {408, 409, 429, 500, 502, 503, 504} == RETRYABLE_STATUS_CODES


def test_retry_after_header_wins_over_backoff() -> None:
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("2.5") == 2.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert backoff_delay(1, retry_after=12.0) == 12.0
    assert backoff_delay(9, retry_after=999.0, max_seconds=60.0) == 60.0


def test_backoff_is_exponential_with_jitter_and_bounded() -> None:
    rng = random.Random(7)
    for attempt in range(1, 9):
        delay = backoff_delay(attempt, base_seconds=1.0, max_seconds=60.0, rng=rng)
        assert 0.0 <= delay <= 60.0
    wide = [backoff_delay(6, base_seconds=1.0, rng=random.Random(s)) for s in range(20)]
    assert len(set(wide)) > 1, "full jitter must not collapse to one value"


def test_failure_record_carries_the_required_telemetry() -> None:
    record = FailureRecord(
        failure_class=FailureClass.EVALUATOR_HTTP_429_RATE_LIMIT,
        role="judge",
        stage="judge",
        retryable=True,
        case_id="operator_mapping-001",
        turn_id="a3",
        model="gpt-4o",
        service_tier="flex",
        attempt=2,
        elapsed_ms=1320.5,
        http_status=429,
        error_type="rate_limit_error",
        error_code="rate_limit_exceeded",
        error_message="Rate limit reached",
        request_id="req_123",
        retry_after_seconds=8.0,
        rate_limit_headers={"x-ratelimit-remaining-tokens": "0"},
        response_excerpt="Rate limit reached",
    )
    payload = record.to_dict()

    for key in (
        "role",
        "stage",
        "case_id",
        "turn_id",
        "model",
        "service_tier",
        "attempt",
        "elapsed_ms",
        "http_status",
        "error_type",
        "error_code",
        "error_message",
        "request_id",
        "retry_after_seconds",
        "rate_limit_headers",
        "retryable",
        "response_excerpt",
    ):
        assert key in payload, key
    assert payload["failure_class"] == "EVALUATOR_HTTP_429_RATE_LIMIT"


def test_rate_limit_headers_are_captured_and_others_ignored() -> None:
    headers = httpx.Headers(
        {
            "x-request-id": "req_abc",
            "retry-after": "5",
            "x-ratelimit-remaining-tokens": "0",
            "content-type": "application/json",
            "set-cookie": "session=secret",
        }
    )
    captured = rate_limit_headers(headers)
    assert captured["x-request-id"] == "req_abc"
    assert captured["retry-after"] == "5"
    assert captured["x-ratelimit-remaining-tokens"] == "0"
    assert "set-cookie" not in captured
    assert "content-type" not in captured


def test_response_excerpts_are_sanitized_and_bounded() -> None:
    excerpt = sanitize_excerpt("Authorization: Bearer abcdef123456 and key sk-abcdef123456")
    assert "abcdef123456" not in excerpt
    assert "[REDACTED]" in excerpt
    assert len(sanitize_excerpt("x" * 5000)) == 600


def test_budget_and_cancellation_are_run_level_and_never_retried() -> None:
    assert pause_state_for(FailureClass.BUDGET_LIMIT) is ExecutionState.STOPPED_BUDGET
    assert pause_state_for(FailureClass.USER_CANCELLED) is ExecutionState.CANCELLED
    assert is_retryable(FailureClass.BUDGET_LIMIT) is False
    assert is_retryable(FailureClass.USER_CANCELLED) is False

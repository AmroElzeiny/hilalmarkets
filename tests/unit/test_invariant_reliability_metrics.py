"""What the reliability layer counts, and what it must never be able to record.

Metrics are the part of an incident you still have afterwards, which is exactly why they
are where secrets leak: a label added in a hurry, a "just log the payload for now" that
outlives the incident, a prompt in a dashboard nobody has a retention policy for.

The filter is asserted as a rule over a family of names rather than on the two fields
somebody remembered, because the next leak will be a field that does not exist yet.
"""

from __future__ import annotations

import httpx
import pytest

from ai_market_monitor.services.provider_reliability import (
    CircuitBreaker,
    PoolLimits,
    ProviderCallError,
    ProviderHttpPool,
    RetryPolicy,
    call_with_reliability,
)
from ai_market_monitor.services.reliability_metrics import (
    FORBIDDEN_FIELD_PARTS,
    ReliabilityMetrics,
    is_safe_field,
    safe_metric_fields,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Nothing sensitive can reach a metric
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("part", sorted(FORBIDDEN_FIELD_PARTS))
def test_every_forbidden_word_is_rejected_wherever_it_appears_in_a_name(part) -> None:
    """Substring matching, because ``openai_api_key`` and ``api_key_hint`` are both secrets."""

    assert is_safe_field(part) is False
    assert is_safe_field(f"provider_{part}") is False
    assert is_safe_field(f"{part}_hint") is False
    assert is_safe_field(part.upper()) is False


@pytest.mark.parametrize(
    "name",
    [
        "provider",
        "operation",
        "attempt",
        "status",
        "failure_class",
        "latency_ms",
        "model",
        "disposition",
        "provider_request_id",
        "circuit_state",
    ],
)
def test_the_fields_an_operator_actually_needs_are_kept(name) -> None:
    assert is_safe_field(name) is True


def test_a_sensitive_field_is_dropped_rather_than_masked() -> None:
    """A mask still says the field was there and how long it was, and still travels."""

    cleaned = safe_metric_fields(
        {
            "provider": "openai",
            "api_key": "sk-live-secret",
            "authorization": "Bearer sk-live",
            "prompt": "the user's private strategy",
            "messages": [{"role": "user"}],
            "email": "person@example.com",
            "strategy_text": "buy when...",
            "latency_ms": 12,
        }
    )

    assert cleaned == {"provider": "openai", "latency_ms": 12}
    assert "sk-live-secret" not in str(cleaned)


async def test_an_attempt_log_from_a_real_call_contains_no_secret() -> None:
    """Checked on the record the call path actually produces, not a hand-built one."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-request-id": "req-9"})

    pool = ProviderHttpPool(
        limits=PoolLimits(connect_timeout_seconds=0.01),
        transport=httpx.MockTransport(handler),
    )
    try:
        outcome = await call_with_reliability(
            pool=pool,
            breaker=CircuitBreaker(),
            policy=RetryPolicy(max_attempts=1),
            provider="openai",
            operation="plan",
            base_url="https://provider.test",
            send=lambda client: client.post(
                "/v1/responses",
                json={"messages": [{"role": "user", "content": "secret strategy"}]},
                headers={"Authorization": "Bearer sk-live-secret"},
            ),
            deadline_seconds=5.0,
        )
        payload = outcome.attempts[-1].to_log()
        flat = str(payload)
        assert "sk-live-secret" not in flat
        assert "secret strategy" not in flat
        assert "Authorization" not in flat
        assert payload["provider_request_id"] == "req-9"
    finally:
        await pool.aclose()


# ---------------------------------------------------------------------------
# The counters are real
# ---------------------------------------------------------------------------


def test_a_retry_is_counted_separately_from_a_failure() -> None:
    metrics = ReliabilityMetrics()

    metrics.record_attempt(
        provider="openai",
        operation="plan",
        failure_class="transient",
        disposition="retrying",
        latency_ms=40,
        status=503,
    )

    assert metrics.provider_retries["openai:transient"] == 1
    assert metrics.provider_failures["openai:transient"] == 1
    assert metrics.provider_failures["openai:http_5xx"] == 1
    assert metrics.provider_attempts["openai:plan"] == 1
    assert metrics.latency_ms_total["openai:plan"] == 40


def test_a_successful_attempt_records_no_failure() -> None:
    metrics = ReliabilityMetrics()

    metrics.record_attempt(
        provider="openai",
        operation="plan",
        failure_class="ok",
        disposition="succeeded",
        latency_ms=10,
        status=200,
    )

    assert metrics.provider_retries == {}
    assert "openai:ok" not in metrics.provider_failures


def test_status_codes_are_bucketed_rather_than_counted_one_by_one() -> None:
    """A counter per distinct status is cardinality nobody reads."""

    metrics = ReliabilityMetrics()
    for status in (500, 502, 503, 504):
        metrics.record_attempt(
            provider="openai",
            operation="plan",
            failure_class="transient",
            disposition="gave_up",
            latency_ms=1,
            status=status,
        )

    assert metrics.provider_failures["openai:http_5xx"] == 4
    assert not any(key.endswith(":http_500") for key in metrics.provider_failures)


def test_cost_is_counted_in_micro_dollars_so_a_month_does_not_lose_pennies() -> None:
    metrics = ReliabilityMetrics()

    for _ in range(3):
        metrics.record_usage(
            feature="planner",
            model="gpt-x",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.000_1,
        )

    assert metrics.cost_micros["planner:gpt-x"] == 300
    assert metrics.tokens["gpt-x:input"] == 300
    assert metrics.tokens["gpt-x:output"] == 150


def test_a_negative_cost_or_token_count_is_floored_at_zero() -> None:
    metrics = ReliabilityMetrics()

    metrics.record_usage(
        feature="planner", model="m", input_tokens=-5, output_tokens=-5, cost_usd=-1.0
    )

    assert metrics.cost_micros["planner:m"] == 0
    assert metrics.tokens["m:input"] == 0


def test_a_redis_fallback_is_counted_because_a_silent_one_runs_for_weeks() -> None:
    metrics = ReliabilityMetrics()

    metrics.record_redis_fallback(component="circuit_breaker", reason="connection_refused")

    assert metrics.redis_fallbacks["circuit_breaker:connection_refused"] == 1


def test_budget_refusals_are_counted_by_scope_and_code() -> None:
    metrics = ReliabilityMetrics()

    metrics.record_budget_refusal(scope="user_daily", code="USER_DAILY_BUDGET_EXCEEDED")
    metrics.record_budget_refusal(scope="global_daily", code="GLOBAL_DAILY_BUDGET_EXCEEDED")
    metrics.record_budget_refusal(scope="user_daily", code="USER_DAILY_BUDGET_EXCEEDED")

    assert metrics.budget_refusals["user_daily:USER_DAILY_BUDGET_EXCEEDED"] == 2
    assert metrics.budget_refusals["global_daily:GLOBAL_DAILY_BUDGET_EXCEEDED"] == 1


def test_reservation_lifecycle_events_are_counted() -> None:
    metrics = ReliabilityMetrics()

    for event in ("reserved", "settled", "released", "expired", "replayed"):
        metrics.record_reservation(event=event)

    assert set(metrics.reservations) == {
        "reserved",
        "settled",
        "released",
        "expired",
        "replayed",
    }


def test_a_feature_decision_is_counted_with_its_reason_and_version() -> None:
    """Without the version an incident cannot be replayed against the same rollout."""

    metrics = ReliabilityMetrics()

    metrics.record_feature_decision(
        feature="planner", enabled=True, reason="percentage", version="rollout-7"
    )

    assert metrics.feature_decisions["planner:on:percentage:rollout-7"] == 1


def test_backpressure_records_the_high_water_mark() -> None:
    metrics = ReliabilityMetrics()

    metrics.record_backpressure(provider="openai", waiting=3)
    metrics.record_backpressure(provider="openai", waiting=9)
    metrics.record_backpressure(provider="openai", waiting=1)

    assert metrics.backpressure["openai"] == 9


def test_the_snapshot_exposes_every_counter_family() -> None:
    snapshot = ReliabilityMetrics().snapshot()

    assert set(snapshot) == {
        "provider_calls",
        "provider_attempts",
        "provider_retries",
        "provider_failures",
        "auth_failures",
        "circuit_transitions",
        "latency_ms_total",
        "tokens",
        "cost_micros",
        "budget_refusals",
        "reservations",
        "redis_fallbacks",
        "feature_decisions",
        "backpressure",
    }


# ---------------------------------------------------------------------------
# The counters move when the real call path runs
# ---------------------------------------------------------------------------


async def test_a_real_retry_sequence_moves_the_shared_counters() -> None:
    """A counter nobody increments is a dashboard that is always green."""

    from ai_market_monitor.services.reliability_metrics import METRICS

    before_attempts = METRICS.provider_attempts["countertest:probe"]
    before_retries = METRICS.provider_retries["countertest:transient"]

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    pool = ProviderHttpPool(
        limits=PoolLimits(connect_timeout_seconds=0.01),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProviderCallError):
            await call_with_reliability(
                pool=pool,
                breaker=CircuitBreaker(),
                policy=RetryPolicy(
                    max_attempts=3,
                    base_delay_seconds=0.001,
                    max_delay_seconds=0.002,
                    jitter=False,
                ),
                provider="countertest",
                operation="probe",
                base_url="https://provider.test",
                send=lambda client: client.get("/x"),
                deadline_seconds=10.0,
            )
    finally:
        await pool.aclose()

    assert METRICS.provider_attempts["countertest:probe"] - before_attempts == 3
    assert METRICS.provider_retries["countertest:transient"] - before_retries == 2
    assert METRICS.provider_calls["countertest:probe:gave_up"] >= 1

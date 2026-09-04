"""The rules a provider call must obey, asserted as a matrix rather than by example.

Every provider call used to build its own ``httpx.AsyncClient``: a fresh pool per call,
no bound on sockets, and no shared knowledge of an outage. Retry behaviour was whatever
each call site happened to write, which is how a 401 gets retried into a rate-limit ban
and a committed mutation gets sent twice.

The dangerous cases here are the ones nobody writes a test for on purpose:

* 401/403 retried — turns a wrong key into an escalating failure;
* Retry-After ignored — turns a rate limit into a ban;
* backoff that outlives the turn — spends the provider's budget for a result nobody waits
  for;
* a committed mutation retried — charges the customer twice.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ai_market_monitor.services.provider_reliability import (
    METRICS,
    AttemptRecord,
    CircuitBreaker,
    CircuitState,
    FailureClass,
    PoolLimits,
    ProviderCallError,
    ProviderHttpPool,
    RetryPolicy,
    call_with_reliability,
    classify_exception,
    classify_status,
    parse_retry_after,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


#: No jitter and no real waiting: these tests assert the decision, not the clock.
FAST = RetryPolicy(max_attempts=3, base_delay_seconds=0.001, max_delay_seconds=0.002, jitter=False)


def _pool(handler) -> ProviderHttpPool:
    return ProviderHttpPool(
        limits=PoolLimits(connect_timeout_seconds=0.01, max_concurrency=8),
        transport=httpx.MockTransport(handler),
    )


async def _call(pool, breaker, *, policy=FAST, deadline=5.0, **kwargs):
    return await call_with_reliability(
        pool=pool,
        breaker=breaker,
        policy=policy,
        provider="testprovider",
        operation="probe",
        base_url="https://provider.test",
        send=lambda client: client.get("/thing"),
        deadline_seconds=deadline,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The classification table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, FailureClass.OK),
        (204, FailureClass.OK),
        (400, FailureClass.PERMANENT),
        (401, FailureClass.AUTH),
        (403, FailureClass.AUTH),
        (404, FailureClass.PERMANENT),
        (408, FailureClass.TRANSIENT),
        (409, FailureClass.TRANSIENT),
        (422, FailureClass.PERMANENT),
        (429, FailureClass.RATE_LIMITED),
        (500, FailureClass.TRANSIENT),
        (502, FailureClass.TRANSIENT),
        (503, FailureClass.TRANSIENT),
        (504, FailureClass.TRANSIENT),
    ],
)
def test_every_status_class_is_decided_by_one_table(status, expected) -> None:
    assert classify_status(status) is expected


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout("slow"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("dns"),
        httpx.RemoteProtocolError("bad frame"),
        httpx.NetworkError("reset"),
    ],
)
def test_transport_failures_are_all_retryable(error) -> None:
    assert classify_exception(error) is FailureClass.TRANSIENT


@pytest.mark.parametrize(
    ("failure", "retryable"),
    [
        (FailureClass.OK, False),
        (FailureClass.AUTH, False),
        (FailureClass.PERMANENT, False),
        (FailureClass.INVALID_RESPONSE, False),
        (FailureClass.RATE_LIMITED, True),
        (FailureClass.TRANSIENT, True),
    ],
)
def test_the_retry_matrix_is_explicit_for_every_failure_class(failure, retryable) -> None:
    assert FAST.is_retryable(failure, mutation_committed=False) is retryable


@pytest.mark.parametrize("failure", list(FailureClass))
def test_a_committed_mutation_is_never_retried_whatever_went_wrong(failure) -> None:
    """A retry after the far side has committed is a second change, not a second try."""

    assert FAST.is_retryable(failure, mutation_committed=True) is False


# ---------------------------------------------------------------------------
# Auth is never retried, through the real call path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_an_auth_failure_is_attempted_exactly_once(status) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    pool = _pool(handler)
    breaker = CircuitBreaker()
    try:
        with pytest.raises(ProviderCallError) as raised:
            await _call(pool, breaker)
        assert calls == 1
        assert raised.value.failure_class is FailureClass.AUTH
        assert raised.value.attempts[-1].disposition == "not_retryable"
        # And a wrong key must not look like an outage: the circuit stays closed so an
        # operator sees the real problem instead of a provider that "went down".
        assert await breaker.state_for("testprovider") is CircuitState.CLOSED
    finally:
        await pool.aclose()


async def test_a_permanent_client_error_is_attempted_exactly_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422)

    pool = _pool(handler)
    try:
        with pytest.raises(ProviderCallError):
            await _call(pool, CircuitBreaker())
        assert calls == 1
    finally:
        await pool.aclose()


async def test_a_committed_mutation_is_not_retried_through_the_call_path() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    pool = _pool(handler)
    try:
        with pytest.raises(ProviderCallError):
            await _call(pool, CircuitBreaker(), mutation_committed=True)
        assert calls == 1
    finally:
        await pool.aclose()


# ---------------------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------------------


def test_retry_after_is_read_in_both_documented_forms() -> None:
    assert parse_retry_after("12") == pytest.approx(12.0)
    assert parse_retry_after(None) is None
    assert parse_retry_after("not a number") is None

    now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    later = (now + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(later, now=now) == pytest.approx(30.0, abs=1.0)
    # A date already in the past means "now", never a negative sleep.
    past = (now - timedelta(seconds=60)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(past, now=now) == 0.0


def test_retry_after_overrides_our_own_backoff() -> None:
    """The provider is the only party that knows when its limit resets."""

    policy = RetryPolicy(base_delay_seconds=0.5, max_delay_seconds=1.0, jitter=False)
    delay = policy.delay_for(1, failure=FailureClass.RATE_LIMITED, retry_after=3.0)
    assert delay == pytest.approx(3.0)
    # Without a header it falls back to our bounded backoff.
    assert policy.delay_for(1, failure=FailureClass.RATE_LIMITED, retry_after=None) <= 1.0


def test_backoff_grows_and_stays_bounded() -> None:
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=4.0, jitter=False)
    delays = [
        policy.delay_for(attempt, failure=FailureClass.TRANSIENT, retry_after=None)
        for attempt in range(1, 6)
    ]
    assert delays == [1.0, 2.0, 4.0, 4.0, 4.0]


async def test_a_rate_limited_call_honours_retry_after_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    pool = _pool(handler)
    try:
        outcome = await _call(pool, CircuitBreaker())
        assert calls == 2
        assert outcome.response is not None
        assert outcome.attempts[0].failure_class is FailureClass.RATE_LIMITED
        assert outcome.attempts[0].retry_after_seconds == 0.0
        assert outcome.attempts[-1].disposition == "succeeded"
    finally:
        await pool.aclose()


# ---------------------------------------------------------------------------
# The deadline bounds everything
# ---------------------------------------------------------------------------


async def test_no_retry_starts_when_it_cannot_finish_inside_the_turn() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    pool = _pool(handler)
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=10.0, jitter=False)
    try:
        with pytest.raises(ProviderCallError) as raised:
            await _call(pool, CircuitBreaker(), policy=policy, deadline=0.05)
        # One attempt happened; the 10-second backoff could never fit in 50ms.
        assert calls == 1
        assert raised.value.attempts[-1].disposition == "deadline"
    finally:
        await pool.aclose()


async def test_an_exhausted_deadline_stops_before_the_first_attempt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    pool = _pool(handler)
    try:
        with pytest.raises(ProviderCallError):
            await _call(pool, CircuitBreaker(), deadline=0.0)
        assert calls == 0
    finally:
        await pool.aclose()


# ---------------------------------------------------------------------------
# The circuit breaker
# ---------------------------------------------------------------------------


async def test_the_circuit_opens_after_the_threshold_and_then_refuses_calls() -> None:
    breaker = CircuitBreaker(failure_threshold=3, recovery_seconds=60.0)

    for _ in range(3):
        await breaker.record_failure("p")
    assert await breaker.state_for("p") is CircuitState.OPEN
    assert await breaker.allow("p") is False


async def test_half_open_admits_exactly_one_probe() -> None:
    """The backlog must not all go through the moment the timer expires."""

    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=0.0)
    await breaker.record_failure("p")
    assert await breaker.state_for("p") is CircuitState.HALF_OPEN

    assert await breaker.allow("p") is True
    assert await breaker.allow("p") is False


async def test_a_failed_probe_reopens_the_circuit_for_a_full_window() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
    await breaker.record_failure("p")
    breaker._entries["p"].state = CircuitState.HALF_OPEN  # noqa: SLF001
    breaker._entries["p"].probe_in_flight = True  # noqa: SLF001

    await breaker.record_failure("p")

    assert await breaker.state_for("p") is CircuitState.OPEN
    assert await breaker.allow("p") is False


async def test_a_successful_probe_closes_the_circuit() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=0.0)
    await breaker.record_failure("p")
    assert await breaker.allow("p") is True

    await breaker.record_success("p")

    assert await breaker.state_for("p") is CircuitState.CLOSED
    assert await breaker.allow("p") is True


async def test_one_failing_provider_never_blocks_another() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
    await breaker.record_failure("broken")

    assert await breaker.allow("broken") is False
    assert await breaker.allow("healthy") is True


async def test_a_caller_may_name_the_thing_that_can_be_down() -> None:
    """``circuit_key`` separates *what failed* from *what the metric is called*.

    A provider name is the right bucket only when it names one upstream. It is the wrong
    bucket for a caller whose "provider" is the open web: ``official_source`` reaches a
    different company on every call, so counting them together let a few dead domains
    open the circuit for every live site the same sweep needed to read.
    """

    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
    pool = _pool(lambda request: httpx.Response(503))

    with pytest.raises(ProviderCallError):
        await _call(pool, breaker, circuit_key="official_source:dead.example")

    assert await breaker.allow("official_source:dead.example") is False
    assert await breaker.allow("official_source:github.com") is True
    # The undivided name must not have been touched, or every other caller under it
    # would inherit one dead domain's failures.
    assert await breaker.allow("testprovider") is True


async def test_the_metric_label_stays_coarse_when_the_circuit_key_is_fine() -> None:
    """Per-host breaker keys must not become per-host counters.

    The point of the split is that the breaker gets to be precise without the metrics
    growing one series per website the product has ever fetched.
    """

    breaker = CircuitBreaker(failure_threshold=99, recovery_seconds=60.0)
    pool = _pool(lambda request: httpx.Response(200))
    METRICS.provider_calls.clear()

    await _call(pool, breaker, circuit_key="official_source:example.com")

    labels = set(METRICS.provider_calls)
    assert labels == {"testprovider:probe:succeeded"}, (
        f"a host leaked into a metric label: {labels}"
    )


async def test_a_refused_call_says_it_was_never_sent() -> None:
    """The one failure that is ours, and callers must be able to see that it is.

    A request the breaker refused reached nobody, so it proves nothing about the far
    side. A caller that writes down *why* an address could not be used has to tell that
    apart from an answer, or it records our own outage as the other site's behaviour.
    """

    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
    pool = _pool(lambda request: httpx.Response(503))

    with pytest.raises(ProviderCallError) as first:
        await _call(pool, breaker)
    assert first.value.circuit_open is False, (
        "a call that really was sent and really did fail must not claim it was skipped"
    )

    with pytest.raises(ProviderCallError) as second:
        await _call(pool, breaker)
    assert second.value.circuit_open is True


async def test_an_unreachable_shared_store_degrades_to_closed_not_open() -> None:
    """Refusing every call because the bookkeeping is unreachable is a self-inflicted
    outage. The per-attempt timeouts still bound the damage."""

    class BrokenStore:
        async def publish(self, provider, state):
            raise RuntimeError("redis is gone")

    breaker = CircuitBreaker(failure_threshold=5, store=BrokenStore())

    await breaker.record_failure("p")
    await breaker.record_success("p")

    assert await breaker.state_for("p") is CircuitState.CLOSED
    assert await breaker.allow("p") is True


async def test_an_open_circuit_refuses_without_touching_the_provider() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    pool = _pool(handler)
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
    await breaker.record_failure("testprovider")
    try:
        with pytest.raises(ProviderCallError) as raised:
            await _call(pool, breaker)
        assert calls == 0
        assert raised.value.attempts[-1].disposition == "circuit_open"
    finally:
        await pool.aclose()


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------


async def test_one_client_is_reused_for_a_base_url() -> None:
    """A client per call is a connection pool per call, which is no pooling at all."""

    pool = _pool(lambda request: httpx.Response(200))
    try:
        first = await pool.client("https://a.test")
        second = await pool.client("https://a.test")
        third = await pool.client("https://b.test")

        assert first is second
        assert first is not third
    finally:
        await pool.aclose()


async def test_closing_the_pool_twice_is_safe() -> None:
    """Shutdown paths call this more than once. It must not raise the second time."""

    pool = _pool(lambda request: httpx.Response(200))
    await pool.aclose()
    await pool.aclose()

    with pytest.raises(RuntimeError):
        await pool.client("https://a.test")


async def test_provider_concurrency_is_bounded_across_calls() -> None:
    """One slow upstream must not be able to consume the whole worker."""

    peak = 0
    live = 0
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal peak, live
        live += 1
        peak = max(peak, live)
        await gate.wait()
        live -= 1
        return httpx.Response(200)

    pool = ProviderHttpPool(
        limits=PoolLimits(max_concurrency=3, connect_timeout_seconds=0.01),
        transport=httpx.MockTransport(handler),
    )
    try:
        tasks = [
            asyncio.create_task(_call(pool, CircuitBreaker(), deadline=30.0))
            for _ in range(9)
        ]
        await asyncio.sleep(0.05)
        assert pool.in_flight <= 3
        assert peak <= 3
        gate.set()
        await asyncio.gather(*tasks)
        assert peak <= 3
    finally:
        gate.set()
        await pool.aclose()


# ---------------------------------------------------------------------------
# What is recorded, and what must never be
# ---------------------------------------------------------------------------


async def test_every_attempt_is_recorded_with_its_class_and_timing() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, headers={"x-request-id": "req-abc"})

    pool = _pool(handler)
    try:
        outcome = await _call(pool, CircuitBreaker(), deadline=30.0)
        assert outcome.attempt_count == 3
        assert [item.attempt for item in outcome.attempts] == [1, 2, 3]
        assert [item.disposition for item in outcome.attempts] == [
            "retrying",
            "retrying",
            "succeeded",
        ]
        assert outcome.attempts[-1].request_id == "req-abc"
        assert all(item.latency_ms >= 0 for item in outcome.attempts)
    finally:
        await pool.aclose()


def test_an_attempt_log_carries_no_secret_and_no_request_body() -> None:
    record = AttemptRecord(
        provider="openai",
        operation="plan",
        attempt=1,
        status=429,
        failure_class=FailureClass.RATE_LIMITED,
        latency_ms=120,
        request_id="req-1",
        model="gpt-x",
        retry_after_seconds=2.0,
        disposition="retrying",
    )

    payload = record.to_log()
    flattened = str(payload).casefold()
    for forbidden in ("authorization", "api_key", "bearer", "prompt", "messages", "body"):
        assert forbidden not in flattened, forbidden
    # And the things an operator actually needs are all there.
    assert payload["provider_request_id"] == "req-1"
    assert payload["failure_class"] == "rate_limited"
    assert payload["latency_ms"] == 120

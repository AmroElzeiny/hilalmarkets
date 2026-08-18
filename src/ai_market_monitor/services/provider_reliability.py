"""One pooled HTTP client, one retry matrix, one circuit breaker for every provider call.

Before this module each provider call built its own ``httpx.AsyncClient`` inside an
``async with``. That is a fresh connection pool per call: no keep-alive reuse, no bound on
how many sockets the process may open, and no shared view of whether a provider is
already failing. Under load the process opened as many connections as it had concurrent
calls, and each caller re-learned an outage the others had already discovered.

Three separate things live here because they are three separate decisions:

``ProviderHttpPool``
    Owns the clients. One per base URL, created once, closed once on shutdown, with
    bounded connections, bounded keep-alive and explicit connect/read/write/pool
    timeouts. A global semaphore bounds concurrency across every provider so one slow
    upstream cannot consume the whole worker.

``RetryPolicy``
    Decides whether an attempt may be retried at all, and how long to wait. The matrix is
    a table, not scattered ``if`` statements, because the dangerous cases are the ones
    nobody thinks to write: retrying a 401 turns a configuration mistake into a rate-limit
    ban, and retrying a committed mutation charges a customer twice.

``CircuitBreaker``
    Stops calling a provider that is already down, and lets exactly one probe through to
    find out when it is back.

**Deadline is the outer bound on everything.** A retry is only permitted if the backoff
*and* the next attempt's timeout both fit inside what is left of the turn. Sleeping past
the deadline and then issuing a request that cannot finish wastes the provider's rate
limit and returns nothing.

Nothing here logs a prompt, an API key, an ``Authorization`` header or a request body.
The attempt record carries status, class, timing and the provider's own request id — the
things needed to explain an outage, none of the things that must never be stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Any

import httpx
import structlog

from ai_market_monitor.services.reliability_metrics import METRICS, safe_metric_fields

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# What went wrong, in one vocabulary
# ---------------------------------------------------------------------------


class FailureClass(StrEnum):
    """Why an attempt failed, in the terms the retry decision is actually made on."""

    #: The request was understood and answered. Not a failure.
    OK = "ok"
    #: Bad or missing credentials, or no permission. Never a transient condition.
    AUTH = "auth"
    #: Too many requests. Retryable, but only on the provider's own schedule.
    RATE_LIMITED = "rate_limited"
    #: The upstream broke or timed out. Retryable with backoff.
    TRANSIENT = "transient"
    #: The upstream answered, but not with something we can use. Retrying the transport
    #: cannot change a malformed body — the same bytes come back.
    INVALID_RESPONSE = "invalid_response"
    #: The request itself is wrong (400, 404, 422). Retrying sends the same wrong thing.
    PERMANENT = "permanent"


#: Status codes that mean "your credentials are wrong", never "try again".
_AUTH_STATUSES = frozenset({401, 403})
#: Status codes worth another attempt: request timeout, conflict, and every server error.
_TRANSIENT_STATUSES = frozenset({408, 409, 500, 502, 503, 504, 507, 509, 599})


def classify_status(status: int) -> FailureClass:
    """Turn an HTTP status into the one word the retry decision needs."""

    if 200 <= status < 300:
        return FailureClass.OK
    if status in _AUTH_STATUSES:
        return FailureClass.AUTH
    if status == 429:
        return FailureClass.RATE_LIMITED
    if status in _TRANSIENT_STATUSES or status >= 500:
        return FailureClass.TRANSIENT
    return FailureClass.PERMANENT


def classify_exception(error: BaseException) -> FailureClass:
    """Transport failures. Connect, DNS, protocol and timeout are all worth retrying."""

    if isinstance(error, httpx.TimeoutException | httpx.ConnectError | httpx.NetworkError):
        return FailureClass.TRANSIENT
    if isinstance(error, httpx.ProtocolError | httpx.RemoteProtocolError):
        return FailureClass.TRANSIENT
    if isinstance(error, httpx.HTTPError):
        return FailureClass.TRANSIENT
    return FailureClass.PERMANENT


# ---------------------------------------------------------------------------
# One attempt, recorded
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Everything worth knowing about one call, and nothing that must not be stored.

    Deliberately carries no headers, no body and no prompt. An outage is explained by
    *when*, *what class* and *which upstream request id* — never by replaying what was
    sent.
    """

    provider: str
    operation: str
    attempt: int
    status: int | None
    failure_class: FailureClass
    latency_ms: int
    #: The provider's own identifier for this request, when it gives one. The single most
    #: useful thing to quote in a support ticket.
    request_id: str | None = None
    model: str | None = None
    service_tier: str | None = None
    retry_after_seconds: float | None = None
    #: "succeeded", "retrying", "gave_up", "not_retryable", "deadline", "circuit_open".
    disposition: str = "unknown"

    def to_log(self) -> dict[str, Any]:
        # Passed through the same filter every other metric uses, so a field added here
        # later cannot become the one place a secret escapes.
        return safe_metric_fields(
            {
                "provider": self.provider,
                "operation": self.operation,
                "attempt": self.attempt,
                "status": self.status,
                "failure_class": str(self.failure_class),
                "latency_ms": self.latency_ms,
                "provider_request_id": self.request_id,
                "model": self.model,
                "service_tier": self.service_tier,
                "retry_after_seconds": self.retry_after_seconds,
                "disposition": self.disposition,
            }
        )

    def count(self) -> None:
        METRICS.record_attempt(
            provider=self.provider,
            operation=self.operation,
            failure_class=str(self.failure_class),
            disposition=self.disposition,
            latency_ms=self.latency_ms,
            status=self.status,
        )


class ProviderCallError(RuntimeError):
    """A provider call that will not be retried, carrying why."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: FailureClass,
        attempts: list[AttemptRecord],
        status: int | None = None,
        response: httpx.Response | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.attempts = attempts
        self.status = status
        #: The transport exception from the last attempt, when the failure was a transport
        #: one. Kept because ``ConnectTimeout``, ``ReadTimeout`` and a DNS failure are three
        #: different things to tell a person, and a caller that already distinguishes them
        #: must not lose that just because the call is now retried.
        self.cause = cause
        #: The last response the provider actually sent, when it sent one. Carried so a
        #: caller that wants to read an error body — a 404 that means "not listed", a 422
        #: that names the bad field — can do so without re-issuing the request. ``None``
        #: when every attempt failed at the transport, because then there is no body.
        self.response = response


# ---------------------------------------------------------------------------
# The retry matrix
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many attempts, how long between them, and what may never be retried."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    #: Full jitter. Without it every worker that saw the same outage retries in the same
    #: millisecond and the recovering provider is knocked over again.
    jitter: bool = True

    def is_retryable(self, failure: FailureClass, *, mutation_committed: bool) -> bool:
        """Whether another attempt is allowed at all.

        ``mutation_committed`` is the override that matters most: once a call has changed
        state on the far side, a retry is a second change, not a second try. A timeout
        after the provider committed looks exactly like a timeout before it did, so the
        only safe answer is not to retry.
        """

        if mutation_committed:
            return False
        return failure in {FailureClass.RATE_LIMITED, FailureClass.TRANSIENT}

    def delay_for(
        self,
        attempt: int,
        *,
        failure: FailureClass,
        retry_after: float | None,
    ) -> float:
        """How long to wait before attempt ``attempt + 1``.

        ``Retry-After`` wins whenever the provider sent one. It is the only party that
        knows when its own limit resets, and ignoring it in favour of our own backoff is
        how a rate limit becomes a ban.
        """

        if failure is FailureClass.RATE_LIMITED and retry_after is not None:
            return max(0.0, min(retry_after, self.max_delay_seconds * 4))
        delay = min(self.base_delay_seconds * (2 ** max(0, attempt - 1)), self.max_delay_seconds)
        if self.jitter:
            return random.uniform(0.0, delay)  # noqa: S311 - backoff spread, not crypto
        return delay


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait, from either form of the header. ``None`` when unusable."""

    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return max(0.0, (when - reference).total_seconds())


# ---------------------------------------------------------------------------
# The circuit breaker
# ---------------------------------------------------------------------------


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _CircuitEntry:
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


class CircuitBreaker:
    """CLOSED в†’ OPEN в†’ HALF_OPEN в†’ CLOSED, with exactly one probe at a time.

    The state is keyed by provider so one failing upstream never stops another. When the
    shared store is unavailable the breaker **degrades to closed** rather than open:
    refusing every call because the bookkeeping is unreachable turns a monitoring outage
    into a product outage, and the per-attempt timeouts still bound the damage.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        store: CircuitStateStore | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._entries: dict[str, _CircuitEntry] = {}
        self._lock = asyncio.Lock()
        self._store = store

    async def state_for(self, provider: str) -> CircuitState:
        async with self._lock:
            return self._resolve(provider).state

    async def _ask_store(self, call: str, *args: Any, **kwargs: Any) -> Any:
        """Ask the shared store something, and treat any trouble as "no opinion".

        Every failure mode collapses to ``None``: no store, a store that does not
        implement this call, a store that raises, a store that times out. "No opinion"
        then falls through to the process-local breaker. The one answer this must never
        produce is "refuse", because that would turn a bookkeeping outage into a product
        outage.
        """

        if self._store is None:
            return None
        method = getattr(self._store, call, None)
        if method is None:
            return None
        try:
            return await method(*args, **kwargs)
        except Exception:  # noqa: BLE001 - coordination is never allowed to break a call
            METRICS.record_redis_fallback(component="circuit_breaker", reason=call)
            return None

    def _resolve(self, provider: str) -> _CircuitEntry:
        entry = self._entries.setdefault(provider, _CircuitEntry())
        if (
            entry.state is CircuitState.OPEN
            and entry.opened_at is not None
            and monotonic() - entry.opened_at >= self.recovery_seconds
        ):
            entry.state = CircuitState.HALF_OPEN
            entry.probe_in_flight = False
        return entry

    async def allow(self, provider: str) -> bool:
        """Whether a call may go out right now.

        In HALF_OPEN exactly one probe is admitted. Letting the whole backlog through the
        moment the timer expires is how a provider that has just come back is immediately
        knocked over again.
        """

        # The shared answer comes first, so a provider one worker has already found to be
        # down is not rediscovered once per worker. ``None`` means the store had no
        # opinion, and then the process-local breaker decides.
        shared = await self._ask_store(
            "allow",
            provider,
            failure_threshold=self.failure_threshold,
            recovery_seconds=self.recovery_seconds,
        )
        if shared is not None:
            return bool(shared)

        async with self._lock:
            entry = self._resolve(provider)
            if entry.state is CircuitState.CLOSED:
                return True
            if entry.state is CircuitState.OPEN:
                return False
            if entry.probe_in_flight:
                return False
            entry.probe_in_flight = True
            return True

    async def record_success(self, provider: str) -> None:
        async with self._lock:
            entry = self._resolve(provider)
            entry.state = CircuitState.CLOSED
            entry.failures = 0
            entry.opened_at = None
            entry.probe_in_flight = False
        METRICS.record_circuit(provider=provider, state=str(CircuitState.CLOSED))
        # Local state is updated whether or not the store answers, so the fallback path
        # is already warm the moment coordination goes away.
        await self._ask_store("record_success", provider)
        await self._publish(provider, CircuitState.CLOSED)

    async def record_failure(self, provider: str) -> None:
        async with self._lock:
            entry = self._resolve(provider)
            if entry.state is CircuitState.HALF_OPEN:
                # The probe failed, so the provider is still down. Straight back to open
                # for another full recovery window rather than trickling probes at it.
                entry.state = CircuitState.OPEN
                entry.opened_at = monotonic()
                entry.probe_in_flight = False
                state = entry.state
            else:
                entry.failures += 1
                if entry.failures >= self.failure_threshold:
                    entry.state = CircuitState.OPEN
                    entry.opened_at = monotonic()
                state = entry.state
        METRICS.record_circuit(provider=provider, state=str(state))
        await self._ask_store(
            "record_failure",
            provider,
            failure_threshold=self.failure_threshold,
            recovery_seconds=self.recovery_seconds,
        )
        await self._publish(provider, state)

    async def _publish(self, provider: str, state: CircuitState) -> None:
        if self._store is None:
            return
        try:
            await self._store.publish(provider, state)
        except Exception:  # noqa: BLE001 - telemetry must never break the call path
            logger.warning("circuit_state_publish_failed", provider=provider)


class CircuitStateStore:
    """Where circuit state is shared between workers.

    An interface rather than a concrete Redis client so the breaker keeps working — in
    per-worker mode — when no shared store is configured. Every implementation must treat
    unavailability as "no opinion", never as "open".

    ``allow`` deliberately has three answers. ``True`` and ``False`` are the shared
    decision. ``None`` means the store could not answer, and the breaker then uses its own
    process-local state. A two-valued interface would have to fold "unknown" into one of
    them, and both choices are wrong: "no" turns a bookkeeping outage into a product
    outage, "yes" hides a real outage from every worker that could have known about it.

    The default methods are the "no opinion" implementation, so a partial store is a
    reduced store rather than a crash.
    """

    async def allow(
        self,
        provider: str,
        *,
        failure_threshold: int,
        recovery_seconds: float,
    ) -> bool | None:
        return None

    async def record_failure(
        self,
        provider: str,
        *,
        failure_threshold: int,
        recovery_seconds: float,
    ) -> None:
        return None

    async def record_success(self, provider: str) -> None:
        return None

    async def publish(self, provider: str, state: CircuitState) -> None:
        return None


#: How long one coordination question may take. Measured on this machine, an unreachable
#: Redis took 2.7 seconds to say so, on every provider call. The diagnostic cost far more
#: than the outage it watched for, so the question is capped.
REDIS_CIRCUIT_TIMEOUT_SECONDS = 0.25
#: After a miss, stop asking for this long. One turn then pays the cap once instead of
#: once per call.
REDIS_CIRCUIT_COOLDOWN_SECONDS = 30.0


#: Admit the call, and move OPEN to HALF_OPEN when the recovery window has passed. Written
#: as one script because "read the state, decide, write the new state" from the
#: application would let two workers both see OPEN-and-expired and both send a probe.
_ALLOW_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'closed' then
  return 1
end
if state == 'half_open' then
  return 0
end
local opened_at = tonumber(redis.call('HGET', KEYS[1], 'opened_at') or '0')
if (tonumber(ARGV[1]) - opened_at) < tonumber(ARGV[2]) then
  return 0
end
redis.call('HSET', KEYS[1], 'state', 'half_open')
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""

_FAILURE_SCRIPT = """
local failures = redis.call('HINCRBY', KEYS[1], 'failures', 1)
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'half_open' or failures >= tonumber(ARGV[1]) then
  redis.call('HSET', KEYS[1], 'state', 'open', 'opened_at', ARGV[2])
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return failures
"""


class RedisCircuitStateStore(CircuitStateStore):
    """Circuit state shared between workers, coordinated through Redis.

    Redis **coordinates** here; it is never the authority on whether a call is allowed to
    happen. Every path out of this class returns ``None`` when Redis cannot answer, and
    the breaker then falls back to its own process-local state. That is the whole safety
    argument: losing Redis costs cross-worker knowledge, never correctness, and never the
    product.

    Two bounds keep the diagnostic cheaper than the outage. Each question is capped, and a
    miss is remembered for a cooldown so a turn pays the cap once rather than once per
    call.
    """

    def __init__(
        self,
        client: Any,
        *,
        namespace: str = "hm:provider:circuit",
        timeout_seconds: float = REDIS_CIRCUIT_TIMEOUT_SECONDS,
        cooldown_seconds: float = REDIS_CIRCUIT_COOLDOWN_SECONDS,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._timeout_seconds = timeout_seconds
        self._cooldown_seconds = cooldown_seconds
        self._unavailable_until = 0.0

    def key_for(self, provider: str) -> str:
        # Hashed so a provider name that happens to contain a colon, a space or a URL
        # cannot reshape the key space.
        digest = hashlib.sha256(provider.encode("utf-8")).hexdigest()[:24]
        return f"{self._namespace}:{digest}"

    async def _ask(self, operation: Callable[[Any], Awaitable[Any]], *, name: str) -> Any:
        if self._client is None or monotonic() < self._unavailable_until:
            return None
        try:
            return await asyncio.wait_for(
                operation(self._client), timeout=self._timeout_seconds
            )
        except Exception:  # noqa: BLE001 - any Redis trouble is "no opinion", never "no"
            self._unavailable_until = monotonic() + self._cooldown_seconds
            METRICS.record_redis_fallback(component="circuit_breaker", reason=name)
            return None

    async def allow(
        self,
        provider: str,
        *,
        failure_threshold: int,
        recovery_seconds: float,
    ) -> bool | None:
        answer = await self._ask(
            lambda client: client.eval(
                _ALLOW_SCRIPT,
                1,
                self.key_for(provider),
                str(time.time()),
                str(recovery_seconds),
                str(max(recovery_seconds * 3, 300)),
            ),
            name="allow",
        )
        return None if answer is None else bool(int(answer))

    async def record_failure(
        self,
        provider: str,
        *,
        failure_threshold: int,
        recovery_seconds: float,
    ) -> None:
        await self._ask(
            lambda client: client.eval(
                _FAILURE_SCRIPT,
                1,
                self.key_for(provider),
                str(failure_threshold),
                str(time.time()),
                str(max(recovery_seconds * 3, 300)),
            ),
            name="record_failure",
        )

    async def record_success(self, provider: str) -> None:
        # The provider answered, so the shared state is simply forgotten. Deleting is
        # safer than writing "closed": a delete cannot leave a stale opened_at behind.
        await self._ask(
            lambda client: client.delete(self.key_for(provider)),
            name="record_success",
        )


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PoolLimits:
    """Bounds on sockets and on time. Every one of them is finite on purpose."""

    max_connections: int = 40
    max_keepalive_connections: int = 20
    keepalive_expiry_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    write_timeout_seconds: float = 10.0
    #: How long a caller may wait for a free connection. Without it, a saturated pool
    #: makes every caller wait forever instead of failing in a way the turn can report.
    pool_timeout_seconds: float = 5.0
    #: The ceiling on provider calls in flight across the whole process.
    max_concurrency: int = 24


class ProviderHttpPool:
    """Application-scoped, pooled, bounded HTTP for every provider call.

    One client per base URL, kept for the life of the process. Closed once, on shutdown,
    so sockets are not leaked between reloads.
    """

    def __init__(
        self,
        *,
        limits: PoolLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.limits = limits or PoolLimits()
        self._transport = transport
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.limits.max_concurrency)
        self._closed = False

    async def client(self, base_url: str) -> httpx.AsyncClient:
        async with self._lock:
            if self._closed:
                raise RuntimeError("provider pool is closed")
            existing = self._clients.get(base_url)
            if existing is not None:
                return existing
            created = httpx.AsyncClient(
                base_url=base_url,
                transport=self._transport,
                limits=httpx.Limits(
                    max_connections=self.limits.max_connections,
                    max_keepalive_connections=self.limits.max_keepalive_connections,
                    keepalive_expiry=self.limits.keepalive_expiry_seconds,
                ),
                timeout=httpx.Timeout(
                    connect=self.limits.connect_timeout_seconds,
                    read=self.limits.read_timeout_seconds,
                    write=self.limits.write_timeout_seconds,
                    pool=self.limits.pool_timeout_seconds,
                ),
            )
            self._clients[base_url] = created
            return created

    async def aclose(self) -> None:
        """Close every client once. Safe to call twice; shutdown paths often do."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.warning("provider_client_close_failed")

    @property
    def in_flight(self) -> int:
        """How many calls are currently holding a concurrency slot."""

        return self.limits.max_concurrency - self._semaphore._value  # noqa: SLF001

    def slot(self) -> asyncio.Semaphore:
        return self._semaphore


# ---------------------------------------------------------------------------
# One guarded call
# ---------------------------------------------------------------------------


@dataclass
class CallOutcome:
    """The result of a guarded call, with every attempt it took to get there."""

    response: httpx.Response | None
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)


async def call_with_reliability(
    *,
    pool: ProviderHttpPool,
    breaker: CircuitBreaker,
    policy: RetryPolicy,
    provider: str,
    operation: str,
    base_url: str,
    send: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]],
    deadline_seconds: float,
    mutation_committed: bool = False,
    model: str | None = None,
    on_auth_failure: Callable[[str, str, int | None], Awaitable[None]] | None = None,
) -> CallOutcome:
    """Make one provider call, with pooling, bounded retries and the circuit breaker.

    ``deadline_seconds`` is what remains of the whole turn. No attempt starts, and no
    backoff is slept, unless both fit inside it — a retry that cannot finish before the
    turn is abandoned spends the provider's rate limit for nothing.

    ``mutation_committed`` must be True whenever the call has already changed state on the
    far side. It disables retries entirely, because a retry would be a second change.
    """

    outcome = CallOutcome(response=None)
    started = monotonic()

    def remaining() -> float:
        return deadline_seconds - (monotonic() - started)

    if not await breaker.allow(provider):
        outcome.attempts.append(
            AttemptRecord(
                provider=provider,
                operation=operation,
                attempt=0,
                status=None,
                failure_class=FailureClass.TRANSIENT,
                latency_ms=0,
                disposition="circuit_open",
                model=model,
            )
        )
        outcome.attempts[-1].count()
        METRICS.record_call(provider=provider, operation=operation, outcome="circuit_open")
        logger.warning("provider_circuit_open", **outcome.attempts[-1].to_log())
        raise ProviderCallError(
            f"{provider} is not answering right now.",
            failure_class=FailureClass.TRANSIENT,
            attempts=outcome.attempts,
        )

    client = await pool.client(base_url)
    last_failure = FailureClass.TRANSIENT
    last_status: int | None = None
    #: Kept so a caller that wants to read the provider's own error body can do so without
    #: sending the request a second time.
    last_response: httpx.Response | None = None
    last_error: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        if remaining() <= pool.limits.connect_timeout_seconds:
            outcome.attempts.append(
                AttemptRecord(
                    provider=provider,
                    operation=operation,
                    attempt=attempt,
                    status=None,
                    failure_class=last_failure,
                    latency_ms=0,
                    disposition="deadline",
                    model=model,
                )
            )
            break

        attempt_started = monotonic()
        response: httpx.Response | None = None
        status: int | None = None
        request_id: str | None = None
        retry_after: float | None = None
        service_tier: str | None = None
        try:
            async with pool.slot():
                response = await send(client)
            status = response.status_code
            request_id = response.headers.get("x-request-id") or response.headers.get(
                "openai-request-id"
            )
            service_tier = response.headers.get("openai-processing-ms")
            failure = classify_status(status)
            if failure is FailureClass.RATE_LIMITED:
                retry_after = parse_retry_after(response.headers.get("retry-after"))
        except Exception as error:  # noqa: BLE001 - classified, then re-raised or retried
            failure = classify_exception(error)
            response = None
            last_error = error

        latency_ms = int((monotonic() - attempt_started) * 1000)
        last_failure = failure
        last_status = status
        if response is not None:
            last_response = response

        if failure is FailureClass.OK:
            record = AttemptRecord(
                provider=provider,
                operation=operation,
                attempt=attempt,
                status=status,
                failure_class=failure,
                latency_ms=latency_ms,
                request_id=request_id,
                model=model,
                service_tier=service_tier,
                disposition="succeeded",
            )
            outcome.attempts.append(record)
            record.count()
            METRICS.record_call(provider=provider, operation=operation, outcome="succeeded")
            logger.info("provider_call", **record.to_log())
            await breaker.record_success(provider)
            outcome.response = response
            return outcome

        retryable = policy.is_retryable(failure, mutation_committed=mutation_committed)
        has_attempts_left = attempt < policy.max_attempts
        delay = (
            policy.delay_for(attempt, failure=failure, retry_after=retry_after)
            if retryable and has_attempts_left
            else 0.0
        )
        # Both the wait and the next attempt must fit. Checking only the wait leaves a
        # request that starts inside the deadline and cannot possibly finish.
        fits = delay + pool.limits.connect_timeout_seconds < remaining()
        will_retry = retryable and has_attempts_left and fits

        record = AttemptRecord(
            provider=provider,
            operation=operation,
            attempt=attempt,
            status=status,
            failure_class=failure,
            latency_ms=latency_ms,
            request_id=request_id,
            model=model,
            service_tier=service_tier,
            retry_after_seconds=retry_after,
            disposition=(
                "retrying"
                if will_retry
                else "not_retryable"
                if not retryable
                else "deadline"
                if not fits
                else "gave_up"
            ),
        )
        outcome.attempts.append(record)
        record.count()
        logger.warning("provider_call_failed", **record.to_log())

        if failure in {FailureClass.TRANSIENT, FailureClass.RATE_LIMITED}:
            await breaker.record_failure(provider)
        elif failure is FailureClass.AUTH:
            # Not a provider outage: our configuration is wrong. Opening the circuit
            # would hide a mistake only an operator can fix.
            logger.error(
                "provider_auth_failure",
                provider=provider,
                operation=operation,
                status=status,
                hint="check the configured API key and its permissions",
            )
            METRICS.record_auth_failure(provider=provider, operation=operation)
            if on_auth_failure is not None:
                # A log line nobody is watching is not an alert. A bad key stops the
                # feature completely and stays broken until somebody is told, so this is
                # the one failure class that reaches a person. It must never be able to
                # break the call it is reporting on.
                try:
                    await on_auth_failure(provider, operation, status)
                except Exception:  # noqa: BLE001 - a diagnostic must never become the failure
                    logger.warning("provider_auth_alert_failed", provider=provider)

        if not will_retry:
            break
        await asyncio.sleep(delay)

    METRICS.record_call(provider=provider, operation=operation, outcome="gave_up")
    raise ProviderCallError(
        f"{provider} did not complete the request.",
        failure_class=last_failure,
        attempts=outcome.attempts,
        status=last_status,
        response=last_response,
        cause=last_error,
    )

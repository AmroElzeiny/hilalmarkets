"""The process-wide reliability objects, and the one door every provider call uses.

The pool, the breaker and the retry policy are *process* state: a pool created per call
is not a pool, and a breaker created per call has never seen a failure. They are built
lazily on first use so importing this module costs nothing, and closed once from the
application's shutdown hook.

Everything is read from :class:`Settings`, so the bounds are operational knobs rather
than numbers compiled into the call sites.

:func:`provider_request` is the door. Before it existed every outbound call wrote its own
``async with httpx.AsyncClient(...)``: a new connection pool per call, no shared view of
whether the upstream was already failing, no bound on sockets, and a different retry
story — usually none — in each of them. One door means one place where pooling, timeouts,
the retry matrix, the circuit breaker and the credential alert are decided, and a new
call site cannot forget any of them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

import httpx
import structlog

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.provider_reliability import (
    CallOutcome,
    CircuitBreaker,
    CircuitStateStore,
    FailureClass,
    PoolLimits,
    ProviderCallError,
    ProviderHttpPool,
    RedisCircuitStateStore,
    RetryPolicy,
    call_with_reliability,
)

logger = structlog.get_logger(__name__)

_pool: ProviderHttpPool | None = None
_breaker: CircuitBreaker | None = None
_lock = asyncio.Lock()

#: One operator message per provider per this many seconds. A wrong API key fails on every
#: call, and an alert that repeats on every call is an alert nobody reads by lunchtime.
_AUTH_ALERT_COOLDOWN_SECONDS = 900.0
_auth_alerted_at: dict[str, float] = {}


def pool_limits_from(settings: Settings) -> PoolLimits:
    return PoolLimits(
        max_connections=settings.provider_pool_max_connections,
        max_keepalive_connections=settings.provider_pool_max_keepalive,
        keepalive_expiry_seconds=settings.provider_pool_keepalive_seconds,
        connect_timeout_seconds=settings.provider_connect_timeout_seconds,
        read_timeout_seconds=settings.provider_read_timeout_seconds,
        write_timeout_seconds=settings.provider_write_timeout_seconds,
        pool_timeout_seconds=settings.provider_pool_timeout_seconds,
        max_concurrency=settings.provider_max_concurrency,
    )


def retry_policy_from(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.provider_retry_max_attempts,
        base_delay_seconds=settings.provider_retry_base_delay_seconds,
        max_delay_seconds=settings.provider_retry_max_delay_seconds,
    )


def circuit_store_for(settings: Settings) -> CircuitStateStore | None:
    """The shared circuit store, when one is configured and wanted.

    Returns ``None`` under tests so the breaker is purely local and deterministic: a test
    that depended on a Redis being up would be testing the machine, not the code.
    """

    if settings.app_env == "test" or not settings.provider_circuit_share_state:
        return None
    try:
        from redis.asyncio import Redis

        return RedisCircuitStateStore(Redis.from_url(settings.redis_url))
    except Exception:  # noqa: BLE001 - no shared store is a reduced mode, not a failure
        logger.warning("circuit_store_unavailable")
        return None


async def provider_pool(settings: Settings) -> ProviderHttpPool:
    """The one pool for this process."""

    global _pool
    async with _lock:
        if _pool is None:
            _pool = ProviderHttpPool(limits=pool_limits_from(settings))
        return _pool


async def provider_breaker(settings: Settings) -> CircuitBreaker:
    """The one breaker for this process.

    Shared so that a failure one caller discovers is a failure every caller already
    knows about. A breaker per caller re-learns the same outage once per code path.
    """

    global _breaker
    async with _lock:
        if _breaker is None:
            _breaker = CircuitBreaker(
                failure_threshold=settings.provider_circuit_failure_threshold,
                recovery_seconds=settings.provider_circuit_recovery_seconds,
                store=circuit_store_for(settings),
            )
        return _breaker


async def shutdown_provider_runtime() -> None:
    """Close the pool and forget the breaker. Safe to call more than once."""

    global _pool, _breaker
    async with _lock:
        if _pool is not None:
            await _pool.aclose()
        _pool = None
        _breaker = None
    _auth_alerted_at.clear()


async def release_provider_runtime_for_loop() -> None:
    """Drop every loop-bound object so the next event loop builds its own.

    Celery's prefork worker runs each task inside its own ``asyncio.run``. The pool's
    ``AsyncClient`` objects, the breaker's lock and Redis client, and this module's own
    ``_lock`` all bind to the loop that first uses them. Surviving into the next task
    means every later call raises "Event loop is closed" or "bound to a different event
    loop" — one working task per worker process, then nothing. Telegram polling showed it
    first only because it runs every five seconds; the scanner's market-data and OpenAI
    calls go through the same door and fail the same way.

    ``shutdown_provider_runtime`` cannot serve here, and replacing it would be wrong: it
    takes ``_lock`` to be safe against concurrent callers, and ``_lock`` is the very object
    that is stale. This path therefore takes no lock. That is correct rather than lax — a
    prefork worker runs one task at a time, and the loop this runs in is being destroyed.

    ``_auth_alerted_at`` is deliberately left alone. It is a plain dict, not bound to any
    loop, and clearing it every task would turn a fifteen-minute alert cooldown into an
    operator message per call — the exact noise that cooldown exists to prevent.
    """

    global _pool, _breaker, _lock
    pool, _pool = _pool, None
    breaker, _breaker = _breaker, None
    _lock = asyncio.Lock()

    if pool is not None:
        try:
            await pool.aclose()
        except Exception:  # noqa: BLE001 - a dying loop must never fail the task
            logger.warning("provider_pool_release_failed")

    store = getattr(breaker, "_store", None)
    store_close = getattr(store, "aclose", None)
    if store_close is not None:
        try:
            await store_close()
        except Exception:  # noqa: BLE001 - same
            logger.warning("provider_breaker_release_failed")


def _auth_alert(settings: Settings) -> Callable[[str, str, int | None], Awaitable[None]]:
    """Tell an operator that a credential was refused, at most once per cooldown."""

    async def alert(provider: str, operation: str, status: int | None) -> None:
        now = monotonic()
        last = _auth_alerted_at.get(provider)
        if last is not None and now - last < _AUTH_ALERT_COOLDOWN_SECONDS:
            return
        _auth_alerted_at[provider] = now
        from ai_market_monitor.services.admin_notifications import AdminNotificationService

        # Provider, operation and status only. The key itself is never named, quoted or
        # hinted at — an alert that leaks the secret it is complaining about is worse than
        # no alert.
        await AdminNotificationService(settings).send(
            f"Provider credentials refused: {provider} ({operation}) returned "
            f"{status if status is not None else 'an auth error'}. "
            "The feature stays off until the key is fixed."
        )

    return alert


async def provider_call(
    settings: Settings,
    method: str,
    url: str,
    *,
    provider: str,
    operation: str,
    base_url: str | None = None,
    timeout: float | httpx.Timeout | None = None,
    deadline_seconds: float | None = None,
    retry: bool = True,
    mutation_committed: bool = False,
    model: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **request_kwargs: Any,
) -> CallOutcome:
    """The full result of one guarded call, including every attempt it took.

    Use this when the caller reports *how many* attempts a call needed — a retry count in
    a research record, an evidence ledger. Most callers want :func:`provider_request`,
    which is this with the response unwrapped.
    """

    origin = base_url or _origin_of(url)
    policy = retry_policy_from(settings) if retry else RetryPolicy(max_attempts=1)
    breaker = await provider_breaker(settings)

    limits = pool_limits_from(settings)
    if timeout is not None:
        # A caller that names its own timeout means it, so the deadline has to be at
        # least that long or the guard would cancel the call the caller just asked for.
        limits = _with_read_timeout(limits, timeout)

    if transport is None:
        pool = await provider_pool(settings)
        owned = False
    else:
        pool = ProviderHttpPool(limits=limits, transport=transport)
        owned = True

    budget = deadline_seconds
    if budget is None:
        attempt_seconds = _seconds_of(timeout, limits.read_timeout_seconds)
        budget = attempt_seconds * policy.max_attempts + policy.max_delay_seconds

    async def send(client: httpx.AsyncClient) -> httpx.Response:
        return await client.request(method, url, timeout=timeout, **request_kwargs)

    try:
        return await call_with_reliability(
            pool=pool,
            breaker=breaker,
            policy=policy,
            provider=provider,
            operation=operation,
            base_url=origin,
            send=send,
            deadline_seconds=budget,
            mutation_committed=mutation_committed,
            model=model,
            on_auth_failure=_auth_alert(settings),
        )
    finally:
        if owned:
            await pool.aclose()


async def provider_request(
    settings: Settings,
    method: str,
    url: str,
    *,
    provider: str,
    operation: str,
    base_url: str | None = None,
    timeout: float | httpx.Timeout | None = None,
    deadline_seconds: float | None = None,
    retry: bool = True,
    mutation_committed: bool = False,
    raise_for_failure: bool = False,
    unwrap_transport_errors: bool = True,
    model: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **request_kwargs: Any,
) -> httpx.Response:
    """Make one outbound provider call through the shared pool.

    ``mutation_committed`` must be True whenever the far side has already changed state —
    a payment taken, a message delivered. It disables retries entirely, because a retry
    of a committed change is a second change, not a second try. It is the most important
    argument here and it is deliberately explicit at every call site rather than guessed
    from the HTTP method: plenty of POSTs are safe to repeat and a few GETs are not.

    ``raise_for_failure`` defaults to False so the returned object is the provider's own
    response, error status and all, and existing callers keep their own status handling.
    Passing True turns an exhausted call into :class:`ProviderCallError` instead.

    ``unwrap_transport_errors`` defaults to True, so a call that never got a response
    raises the original ``httpx`` exception. The reliability layer folds every transport
    failure into one class in order to decide about retrying, but a caller telling a
    person what happened still needs the difference: "the name did not resolve",
    "connected then timed out" and "the stream was cut" are three different problems with
    three different fixes. Folding them together at the boundary would have quietly turned
    every one of them into the same unhelpful message.

    ``retry`` is True by default, but every call that *generates* a paid model answer
    passes ``retry=False``. Those paths already promise the product one paid attempt per
    turn — the extractor enforces it as ``MODEL_CALL_LIMIT``, the Setup agent counts model
    calls, the budget reserves for one. Retrying inside them would spend two or three
    answers against a reservation for one and quietly break a bound the product states out
    loud. They still get the pool, the breaker and the credential alert; what they do not
    get is a second attempt. Retrying belongs to the calls that only *read* — robots
    files, evidence documents, price lookups, embeddings — where a repeat costs nothing
    but a little time.

    ``transport`` exists for tests. Production never passes one, so production always uses
    the shared pool; a supplied transport gets its own short-lived pool so an injected
    stub cannot be cached into the process-wide client and leak into another test.
    """

    try:
        outcome = await provider_call(
            settings,
            method,
            url,
            provider=provider,
            operation=operation,
            base_url=base_url,
            timeout=timeout,
            deadline_seconds=deadline_seconds,
            retry=retry,
            mutation_committed=mutation_committed,
            model=model,
            transport=transport,
            **request_kwargs,
        )
    except ProviderCallError as error:
        if error.response is not None and not raise_for_failure:
            # The provider did answer, just not with success. Handing the response back
            # lets the caller keep the error handling it already has, instead of every
            # call site learning a second exception vocabulary.
            return error.response
        if unwrap_transport_errors and error.cause is not None:
            raise error.cause from error
        raise

    if outcome.response is None:  # pragma: no cover - call_with_reliability raises instead
        raise ProviderCallError(
            f"{provider} returned no response.",
            failure_class=FailureClass.TRANSIENT,
            attempts=outcome.attempts,
        )
    return outcome.response


def _origin_of(url: str) -> str:
    """The scheme and host of a URL, which is what a connection pool is keyed by.

    Pooling per full URL would open a new pool per path and reuse nothing.
    """

    parsed = httpx.URL(url)
    if not parsed.host:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.host}{port}"


def _seconds_of(timeout: float | httpx.Timeout | None, fallback: float) -> float:
    if timeout is None:
        return fallback
    if isinstance(timeout, httpx.Timeout):
        return float(timeout.read or timeout.connect or fallback)
    return float(timeout)


def _with_read_timeout(limits: PoolLimits, timeout: float | httpx.Timeout) -> PoolLimits:
    seconds = _seconds_of(timeout, limits.read_timeout_seconds)
    return PoolLimits(
        max_connections=limits.max_connections,
        max_keepalive_connections=limits.max_keepalive_connections,
        keepalive_expiry_seconds=limits.keepalive_expiry_seconds,
        # The connect bound is also the deadline guard's minimum useful attempt, so a
        # caller asking for a very short timeout must not be blocked by a longer one.
        connect_timeout_seconds=min(limits.connect_timeout_seconds, max(seconds, 0.001)),
        read_timeout_seconds=seconds,
        write_timeout_seconds=limits.write_timeout_seconds,
        pool_timeout_seconds=limits.pool_timeout_seconds,
        max_concurrency=limits.max_concurrency,
    )

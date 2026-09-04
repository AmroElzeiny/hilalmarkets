"""A 401 or 403 is only a credential problem for a caller that sent a credential.

The rule is asserted across the whole family — every provider name the codebase uses,
every auth-ish status — not on the one call that was reported. The reported instance was
``official_source (fetch_evidence) returned 403``, sent to an operator's phone as
"Provider credentials refused ... The feature stays off until the key is fixed."
``official_source`` fetches a different company's public website on every call and holds
no key at all, so there was nothing to fix and nothing had stopped.

Three things are pinned here:

* the table covers every ``provider=`` literal in ``src``, so a new caller cannot be
  added without saying whether it authenticates;
* an unauthenticated 401/403 is never ``AUTH`` — it does not alert, does not retry, and
  does not count towards opening that host's circuit;
* an authenticated 401/403 still is, because silencing a real expired key is the
  expensive way to be wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from ai_market_monitor.services.provider_credentials import (
    PROVIDER_CREDENTIALS,
    UNAUTHENTICATED_PROVIDERS,
    authenticates,
    credential_setting,
)
from ai_market_monitor.services.provider_reliability import (
    CircuitBreaker,
    CircuitState,
    FailureClass,
    PoolLimits,
    ProviderCallError,
    ProviderHttpPool,
    RetryPolicy,
    call_with_reliability,
    classify_status,
)

pytestmark = pytest.mark.anyio

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"

#: The two answers a far side gives when it will not serve this caller.
AUTH_STATUSES = (401, 403)

FAST = RetryPolicy(
    max_attempts=3, base_delay_seconds=0.001, max_delay_seconds=0.002, jitter=False
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _pool(handler) -> ProviderHttpPool:
    return ProviderHttpPool(
        limits=PoolLimits(connect_timeout_seconds=0.01, max_concurrency=4),
        transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------------------
# The table covers everything, so it cannot drift
# ---------------------------------------------------------------------------


def _declared_provider_names() -> set[str]:
    """Every ``provider="..."`` literal written anywhere under ``src``."""

    found: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        found.update(
            re.findall(r'provider=["\']([a-z0-9_]+)["\']', path.read_text(encoding="utf-8"))
        )
    return found


def test_every_provider_name_in_the_codebase_declares_its_credential() -> None:
    """A caller added without declaring itself fails here, not silently in production."""

    undeclared = sorted(_declared_provider_names() - set(PROVIDER_CREDENTIALS))
    assert not undeclared, (
        "These provider names make calls but do not say whether they send a credential. "
        "Add each to PROVIDER_CREDENTIALS in services/provider_credentials.py — with the "
        f"setting that holds its key, or None if it sends none: {undeclared}"
    )


def test_the_open_web_fetchers_are_declared_as_holding_no_key() -> None:
    """The three callers whose "provider" is somebody else's public website."""

    for provider in ("official_source", "sc_malaysia", "fasset"):
        assert credential_setting(provider) is None
        assert not authenticates(provider)
        assert provider in UNAUTHENTICATED_PROVIDERS


def test_an_unknown_provider_still_raises_the_alert() -> None:
    """Being wrong towards a spare alert is recoverable; being wrong the other way is not."""

    assert authenticates("a_provider_nobody_declared")


# ---------------------------------------------------------------------------
# The classification, across every provider and every auth status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", sorted(PROVIDER_CREDENTIALS))
@pytest.mark.parametrize("status", AUTH_STATUSES)
def test_auth_status_is_a_credential_problem_only_when_a_credential_was_sent(
    provider: str, status: int
) -> None:
    failure = classify_status(status, authenticated=authenticates(provider))
    if credential_setting(provider) is None:
        assert failure is FailureClass.PERMANENT, (
            f"{provider} sends no credential, so HTTP {status} from it cannot mean a key "
            "is wrong."
        )
    else:
        assert failure is FailureClass.AUTH


@pytest.mark.parametrize("status", AUTH_STATUSES)
def test_an_unauthenticated_refusal_is_not_retried(status: int) -> None:
    """PERMANENT, not TRANSIENT: the same anonymous request gets the same refusal."""

    failure = classify_status(status, authenticated=False)
    assert not FAST.is_retryable(failure, mutation_committed=False)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, FailureClass.OK),
        (400, FailureClass.PERMANENT),
        (404, FailureClass.PERMANENT),
        (429, FailureClass.RATE_LIMITED),
        (500, FailureClass.TRANSIENT),
        (503, FailureClass.TRANSIENT),
    ],
)
def test_no_other_status_changes_meaning_when_nothing_was_sent(
    status: int, expected: FailureClass
) -> None:
    """Only 401 and 403 are read differently. Everything else is the same number."""

    assert classify_status(status, authenticated=False) is expected
    assert classify_status(status, authenticated=True) is expected


def test_the_default_keeps_the_old_reading() -> None:
    """A caller that does not pass the flag is still treated as holding a key."""

    for status in AUTH_STATUSES:
        assert classify_status(status) is FailureClass.AUTH


# ---------------------------------------------------------------------------
# End to end: no alert, and no circuit opened, for the open web
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", AUTH_STATUSES)
async def test_the_open_web_never_reaches_the_credential_alert(status: int) -> None:
    """The reported bug, asserted directly: a website's 403 tells nobody about a key."""

    alerted: list[tuple[str, str, int | None]] = []

    async def on_auth_failure(provider: str, operation: str, code: int | None) -> None:
        alerted.append((provider, operation, code))

    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60.0)
    pool = _pool(lambda request: httpx.Response(status))
    with pytest.raises(ProviderCallError) as raised:
        await call_with_reliability(
            pool=pool,
            breaker=breaker,
            policy=FAST,
            provider="official_source",
            operation="fetch_evidence",
            base_url="https://someproject.test",
            send=lambda client: client.get("/blog"),
            deadline_seconds=5.0,
            circuit_key="official_source:someproject.test",
            on_auth_failure=on_auth_failure,
        )

    assert alerted == [], "a site refusing an anonymous visit is not a credential failure"
    assert raised.value.failure_class is FailureClass.PERMANENT
    assert len(raised.value.attempts) == 1, "an anonymous refusal must not be retried"


@pytest.mark.parametrize("status", AUTH_STATUSES)
async def test_a_refused_website_does_not_open_the_circuit_for_that_host(
    status: int,
) -> None:
    """PERMANENT does not count towards the breaker, so one filtered site stays isolated."""

    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60.0)
    pool = _pool(lambda request: httpx.Response(status))
    for _ in range(4):
        with pytest.raises(ProviderCallError):
            await call_with_reliability(
                pool=pool,
                breaker=breaker,
                policy=FAST,
                provider="official_source",
                operation="fetch_evidence",
                base_url="https://someproject.test",
                send=lambda client: client.get("/blog"),
                deadline_seconds=5.0,
                circuit_key="official_source:someproject.test",
            )
    assert await breaker.state_for("official_source:someproject.test") is CircuitState.CLOSED


@pytest.mark.parametrize("status", AUTH_STATUSES)
async def test_a_real_credential_failure_still_alerts(status: int) -> None:
    """The half that must keep working: an expired OpenAI key still reaches a person."""

    alerted: list[tuple[str, str, int | None]] = []

    async def on_auth_failure(provider: str, operation: str, code: int | None) -> None:
        alerted.append((provider, operation, code))

    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60.0)
    pool = _pool(lambda request: httpx.Response(status))
    with pytest.raises(ProviderCallError):
        await call_with_reliability(
            pool=pool,
            breaker=breaker,
            policy=FAST,
            provider="openai",
            operation="responses",
            base_url="https://api.openai.test",
            send=lambda client: client.post("/v1/responses"),
            deadline_seconds=5.0,
            on_auth_failure=on_auth_failure,
        )

    assert alerted == [("openai", "responses", status)]

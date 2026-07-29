"""One shared failure taxonomy for the target chatbot and the evaluator.

Run 20260723T152343Z scored two ``RemoteProtocolError: Server disconnected`` turns
as chatbot quality failures, and reported ``infrastructure_unavailable`` with the
message "before a quality case could run" after nine cases had already completed.
Both mistakes come from having no vocabulary that separates *the chatbot answered
badly* from *the transport, the evaluator, or the account got in the way*.

Every classification here is deterministic. Nothing in this module decides whether a
chatbot answer was good; it only decides who failed and whether retrying can help.
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class FailureClass(StrEnum):
    """Who failed, at which stage, and in what way."""

    # The chatbot under test.
    TARGET_DNS_RESOLUTION_FAILURE = "TARGET_DNS_RESOLUTION_FAILURE"
    TARGET_CONNECT_TIMEOUT = "TARGET_CONNECT_TIMEOUT"
    TARGET_READ_TIMEOUT = "TARGET_READ_TIMEOUT"
    TARGET_TOTAL_TIMEOUT = "TARGET_TOTAL_TIMEOUT"
    TARGET_HTTP_429 = "TARGET_HTTP_429"
    TARGET_HTTP_5XX = "TARGET_HTTP_5XX"
    TARGET_EMPTY_RESPONSE = "TARGET_EMPTY_RESPONSE"
    TARGET_INVALID_JSON = "TARGET_INVALID_JSON"
    TARGET_SCHEMA_VALIDATION = "TARGET_SCHEMA_VALIDATION"
    TARGET_PARTIAL_STREAM = "TARGET_PARTIAL_STREAM"
    TARGET_COMPILE_TIMEOUT = "TARGET_COMPILE_TIMEOUT"
    TARGET_NO_ASSISTANT_MESSAGE = "TARGET_NO_ASSISTANT_MESSAGE"

    # The browser-driven UI harness.
    UI_NAVIGATION_TIMEOUT = "UI_NAVIGATION_TIMEOUT"
    UI_RESPONSE_TIMEOUT = "UI_RESPONSE_TIMEOUT"
    UI_AUTH_EXPIRED = "UI_AUTH_EXPIRED"

    # The evaluator's own model provider.
    EVALUATOR_CONNECT_TIMEOUT = "EVALUATOR_CONNECT_TIMEOUT"
    EVALUATOR_READ_TIMEOUT = "EVALUATOR_READ_TIMEOUT"
    EVALUATOR_HTTP_429_RATE_LIMIT = "EVALUATOR_HTTP_429_RATE_LIMIT"
    EVALUATOR_HTTP_429_FLEX_CAPACITY = "EVALUATOR_HTTP_429_FLEX_CAPACITY"
    EVALUATOR_HTTP_429_QUOTA = "EVALUATOR_HTTP_429_QUOTA"
    EVALUATOR_HTTP_5XX = "EVALUATOR_HTTP_5XX"
    EVALUATOR_AUTH_FAILURE = "EVALUATOR_AUTH_FAILURE"
    EVALUATOR_FAULT_CONTROL_UNAVAILABLE = "EVALUATOR_FAULT_CONTROL_UNAVAILABLE"
    #: The evaluator's own model returned a body that would not parse. Distinct from
    #: `TARGET_INVALID_JSON`: without the distinction a truncated *grader* reply was
    #: recorded as a chatbot fault, and the wrong side got investigated.
    EVALUATOR_INVALID_JSON = "EVALUATOR_INVALID_JSON"

    # Run-level outcomes.
    BUDGET_LIMIT = "BUDGET_LIMIT"
    USER_CANCELLED = "USER_CANCELLED"
    UNKNOWN_INFRASTRUCTURE_FAILURE = "UNKNOWN_INFRASTRUCTURE_FAILURE"


class ExecutionState(StrEnum):
    """Terminal state of a run, derived only from persisted run state."""

    COMPLETED = "COMPLETED"
    PAUSED_RATE_LIMIT = "PAUSED_RATE_LIMIT"
    PAUSED_FLEX_CAPACITY = "PAUSED_FLEX_CAPACITY"
    PAUSED_QUOTA = "PAUSED_QUOTA"
    PAUSED_AUTH = "PAUSED_AUTH"
    PAUSED_TARGET_UNAVAILABLE = "PAUSED_TARGET_UNAVAILABLE"
    STOPPED_BUDGET = "STOPPED_BUDGET"
    FAILED_CONFIGURATION = "FAILED_CONFIGURATION"
    CANCELLED = "CANCELLED"


Role = str  # "target" | "simulated_trader" | "judge" | "ui" | "backend"

#: Transient conditions worth another attempt. Everything else is a standing
#: condition that repeated requests cannot resolve.
RETRYABLE: frozenset[FailureClass] = frozenset(
    {
        FailureClass.TARGET_CONNECT_TIMEOUT,
        FailureClass.TARGET_DNS_RESOLUTION_FAILURE,
        FailureClass.TARGET_READ_TIMEOUT,
        FailureClass.TARGET_TOTAL_TIMEOUT,
        FailureClass.TARGET_HTTP_429,
        FailureClass.TARGET_HTTP_5XX,
        FailureClass.TARGET_EMPTY_RESPONSE,
        FailureClass.TARGET_PARTIAL_STREAM,
        FailureClass.TARGET_COMPILE_TIMEOUT,
        FailureClass.UI_NAVIGATION_TIMEOUT,
        FailureClass.UI_RESPONSE_TIMEOUT,
        FailureClass.EVALUATOR_CONNECT_TIMEOUT,
        FailureClass.EVALUATOR_READ_TIMEOUT,
        FailureClass.EVALUATOR_HTTP_429_RATE_LIMIT,
        FailureClass.EVALUATOR_HTTP_429_FLEX_CAPACITY,
        FailureClass.EVALUATOR_HTTP_5XX,
    }
)

#: Standing conditions. Retrying these burns budget and never succeeds: bad
#: credentials, denied permission, exhausted quota, invalid model or schema.
NON_RETRYABLE: frozenset[FailureClass] = frozenset(
    {
        FailureClass.EVALUATOR_HTTP_429_QUOTA,
        FailureClass.EVALUATOR_AUTH_FAILURE,
        FailureClass.UI_AUTH_EXPIRED,
        FailureClass.TARGET_INVALID_JSON,
        FailureClass.TARGET_SCHEMA_VALIDATION,
        FailureClass.EVALUATOR_FAULT_CONTROL_UNAVAILABLE,
        # Already retried inside `bounded_retry`; by the time the run sees it the
        # attempts are spent, so retrying again at run level would double the wait.
        FailureClass.EVALUATOR_INVALID_JSON,
        FailureClass.TARGET_NO_ASSISTANT_MESSAGE,
        FailureClass.BUDGET_LIMIT,
        FailureClass.USER_CANCELLED,
        FailureClass.UNKNOWN_INFRASTRUCTURE_FAILURE,
    }
)

#: HTTP statuses that justify another attempt regardless of which side returned them.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504})

_PAUSE_STATE_BY_CLASS: dict[FailureClass, ExecutionState] = {
    FailureClass.EVALUATOR_HTTP_429_RATE_LIMIT: ExecutionState.PAUSED_RATE_LIMIT,
    FailureClass.EVALUATOR_HTTP_429_FLEX_CAPACITY: ExecutionState.PAUSED_FLEX_CAPACITY,
    FailureClass.EVALUATOR_HTTP_429_QUOTA: ExecutionState.PAUSED_QUOTA,
    FailureClass.EVALUATOR_AUTH_FAILURE: ExecutionState.PAUSED_AUTH,
    FailureClass.UI_AUTH_EXPIRED: ExecutionState.PAUSED_AUTH,
    FailureClass.EVALUATOR_FAULT_CONTROL_UNAVAILABLE: ExecutionState.FAILED_CONFIGURATION,
    FailureClass.TARGET_DNS_RESOLUTION_FAILURE: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_CONNECT_TIMEOUT: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_READ_TIMEOUT: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_TOTAL_TIMEOUT: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_HTTP_429: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_HTTP_5XX: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_EMPTY_RESPONSE: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_PARTIAL_STREAM: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.TARGET_COMPILE_TIMEOUT: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.UI_NAVIGATION_TIMEOUT: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.UI_RESPONSE_TIMEOUT: ExecutionState.PAUSED_TARGET_UNAVAILABLE,
    FailureClass.BUDGET_LIMIT: ExecutionState.STOPPED_BUDGET,
    FailureClass.USER_CANCELLED: ExecutionState.CANCELLED,
}

_QUOTA_MARKERS = (
    "insufficient_quota",
    "exceeded your current quota",
    "billing_hard_limit_reached",
    "account is not active",
    "check your plan and billing",
)
_FLEX_MARKERS = (
    "flex",
    "service_tier",
    "capacity",
    "no capacity",
    "temporarily unable to serve",
    "resource_exhausted",
)
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit_exceeded",
    "requests per min",
    "tokens per min",
    "tpm",
    "rpm",
)

_RATE_LIMIT_HEADERS = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "x-request-id",
)


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """Everything needed to tell a chatbot defect from an infrastructure event."""

    failure_class: FailureClass
    role: Role
    stage: str
    retryable: bool
    case_id: str | None = None
    turn_id: str | None = None
    model: str | None = None
    service_tier: str | None = None
    attempt: int = 1
    elapsed_ms: float | None = None
    http_status: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    retry_after_seconds: float | None = None
    rate_limit_headers: dict[str, str] = field(default_factory=dict)
    response_excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failure_class"] = str(self.failure_class)
        return payload

    @property
    def is_quality_signal(self) -> bool:
        """False for every entry here: infrastructure never scores chatbot quality."""
        return False


def classify_http_status(
    status: int,
    *,
    role: Role,
    body: str = "",
) -> FailureClass:
    """Classify a non-2xx response from either side."""
    evaluator = role in {"simulated_trader", "judge"}
    lowered = (body or "").casefold()
    if status in {401, 403}:
        if evaluator:
            return FailureClass.EVALUATOR_AUTH_FAILURE
        if "evaluator_control_unavailable" in lowered:
            return FailureClass.EVALUATOR_FAULT_CONTROL_UNAVAILABLE
        # An authenticated target that stops accepting the session has an expired
        # login, which is a harness problem rather than a chatbot answer.
        return FailureClass.UI_AUTH_EXPIRED
    if status == 429:
        if not evaluator:
            return FailureClass.TARGET_HTTP_429
        # A 429 can mean three very different things. Only one of them is worth
        # retrying on a short timer; quota needs a human, flex needs a longer pause.
        if any(marker in lowered for marker in _QUOTA_MARKERS):
            return FailureClass.EVALUATOR_HTTP_429_QUOTA
        if any(marker in lowered for marker in _FLEX_MARKERS):
            return FailureClass.EVALUATOR_HTTP_429_FLEX_CAPACITY
        return FailureClass.EVALUATOR_HTTP_429_RATE_LIMIT
    if status >= 500:
        return FailureClass.EVALUATOR_HTTP_5XX if evaluator else FailureClass.TARGET_HTTP_5XX
    return FailureClass.UNKNOWN_INFRASTRUCTURE_FAILURE


def classify_exception(exc: BaseException, *, role: Role, stage: str = "") -> FailureClass:
    """Classify a transport-level or runtime failure."""
    name = type(exc).__name__
    text = f"{name}: {exc}".casefold()
    evaluator = role in {"simulated_trader", "judge"}

    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
        return FailureClass.USER_CANCELLED
    # The browser harness owns its own timeout vocabulary: a slow page is a UI
    # problem, not evidence that the chatbot produced a bad answer.
    if role == "ui" and "timeout" in text:
        return (
            FailureClass.UI_NAVIGATION_TIMEOUT
            if "navigat" in text or stage == "navigate"
            else FailureClass.UI_RESPONSE_TIMEOUT
        )
    if role == "ui" and (
        "client_message_id" in text or "assistant message id" in text
    ):
        return FailureClass.UI_RESPONSE_TIMEOUT
    if any(
        marker in text
        for marker in (
            "getaddrinfo failed",
            "name or service not known",
            "nodename nor servname provided",
            "temporary failure in name resolution",
            "name resolution",
            "dns",
        )
    ):
        return (
            FailureClass.EVALUATOR_CONNECT_TIMEOUT
            if evaluator
            else FailureClass.TARGET_DNS_RESOLUTION_FAILURE
        )
    if "connecttimeout" in text or "connect timeout" in text:
        return (
            FailureClass.EVALUATOR_CONNECT_TIMEOUT
            if evaluator
            else FailureClass.TARGET_CONNECT_TIMEOUT
        )
    if "readtimeout" in text or "read timeout" in text:
        return (
            FailureClass.EVALUATOR_READ_TIMEOUT if evaluator else FailureClass.TARGET_READ_TIMEOUT
        )
    if "pooltimeout" in text or "writetimeout" in text or "timeout" in text:
        if role == "ui":
            return FailureClass.UI_RESPONSE_TIMEOUT
        if stage == "compile":
            return FailureClass.TARGET_COMPILE_TIMEOUT
        return (
            FailureClass.EVALUATOR_READ_TIMEOUT if evaluator else FailureClass.TARGET_TOTAL_TIMEOUT
        )
    # `Server disconnected without sending a response` is a truncated exchange, not a
    # bad answer. Run 20260723T152343Z scored two of these as chatbot failures.
    if "remoteprotocolerror" in text or "disconnected without sending" in text:
        return FailureClass.TARGET_PARTIAL_STREAM
    if "incomplete" in text or "peer closed" in text or "chunked" in text:
        return FailureClass.TARGET_PARTIAL_STREAM
    if "jsondecodeerror" in text or "invalid json" in text or "expecting value" in text:
        return FailureClass.TARGET_INVALID_JSON
    if "connecterror" in text or "connectionreset" in text or "connection refused" in text:
        return (
            FailureClass.EVALUATOR_CONNECT_TIMEOUT
            if evaluator
            else FailureClass.TARGET_CONNECT_TIMEOUT
        )
    return FailureClass.UNKNOWN_INFRASTRUCTURE_FAILURE


def is_retryable(failure_class: FailureClass) -> bool:
    return failure_class in RETRYABLE


def pause_state_for(failure_class: FailureClass) -> ExecutionState | None:
    """Return the run state this failure forces once retries are exhausted.

    A retryable class can still map to a pause state: a rate-limit 429 is retried
    first, and only ends the run as ``PAUSED_RATE_LIMIT`` if the retries run out.
    ``None`` means the run simply continues with the case marked failed.
    """
    return _PAUSE_STATE_BY_CLASS.get(failure_class)


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header expressed in seconds."""
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    return None


def backoff_delay(
    attempt: int,
    *,
    retry_after: float | None = None,
    base_seconds: float = 1.0,
    max_seconds: float = 60.0,
    rng: random.Random | None = None,
) -> float:
    """``Retry-After`` wins; otherwise exponential backoff with full jitter."""
    if retry_after is not None and retry_after >= 0:
        return min(float(retry_after), max_seconds)
    generator = rng or random
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return generator.uniform(0.0, ceiling)


def rate_limit_headers(headers: Any) -> dict[str, str]:
    """Extract the rate-limit and request-id headers worth recording."""
    if not headers:
        return {}
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {
        str(key).casefold(): str(value)
        for key, value in items
        if str(key).casefold() in _RATE_LIMIT_HEADERS
    }


def sanitize_excerpt(body: Any, *, limit: int = 600) -> str | None:
    """Return a short, secret-free excerpt of a response body."""
    if body is None:
        return None
    text = body if isinstance(body, str) else str(body)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9._\-]{8,}", "[REDACTED]", text)
    text = " ".join(text.split())
    return text[:limit] or None

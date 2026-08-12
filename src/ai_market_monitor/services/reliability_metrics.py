"""Counters for the reliability layer, and one gate that keeps secrets out of them.

Metrics are the part of an incident you still have afterwards, so the temptation is to
record everything. That is exactly how an API key ends up in a dashboard label and a
customer's prompt ends up in a log aggregator that nobody has a retention policy for.

Two rules, enforced rather than remembered:

* **Labels are bounded.** Every dimension is a small, known set — provider, feature,
  failure class, circuit state. A label whose values come from user input (an email, a
  strategy name, a request body) turns a counter into an unbounded cardinality explosion
  and a data leak at the same time.
* **Nothing sensitive is accepted at all.** :func:`safe_metric_fields` drops any field
  whose name looks like a secret or like private content, and it is the only way values
  reach the log. Filtering at the boundary means a new call site cannot forget to filter.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


#: Field names that must never reach a metric or a log line, whatever the caller intended.
#: Substring matching on purpose: ``openai_api_key`` and ``api_key_hint`` are both secrets.
FORBIDDEN_FIELD_PARTS: frozenset[str] = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "credential",
        "bearer",
        "cookie",
        "prompt",
        "messages",
        "body",
        "payload",
        "content",
        "email",
        "strategy_text",
        "watch_plan_text",
        "raw",
    }
)


def is_safe_field(name: str) -> bool:
    lowered = name.casefold()
    return not any(part in lowered for part in FORBIDDEN_FIELD_PARTS)


def safe_metric_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that must not be recorded, before it reaches a log or a counter.

    Dropped rather than masked. A masked value still tells a reader that the field was
    present and how long it was, and it still has to travel through the logging pipeline
    to get there.
    """

    return {name: value for name, value in fields.items() if is_safe_field(name)}


@dataclass
class ReliabilityMetrics:
    """In-process counters for the reliability layer.

    Deliberately simple: this is the shape an exporter reads, not a metrics backend. It
    exists so the invariants can be *tested* — "a 401 is never retried" is a claim about
    counters, and a claim nobody counts is a claim nobody can check.
    """

    provider_calls: Counter[str] = field(default_factory=Counter)
    provider_attempts: Counter[str] = field(default_factory=Counter)
    provider_retries: Counter[str] = field(default_factory=Counter)
    provider_failures: Counter[str] = field(default_factory=Counter)
    auth_failures: Counter[str] = field(default_factory=Counter)
    circuit_transitions: Counter[str] = field(default_factory=Counter)
    latency_ms_total: Counter[str] = field(default_factory=Counter)
    tokens: Counter[str] = field(default_factory=Counter)
    cost_micros: Counter[str] = field(default_factory=Counter)
    budget_refusals: Counter[str] = field(default_factory=Counter)
    reservations: Counter[str] = field(default_factory=Counter)
    redis_fallbacks: Counter[str] = field(default_factory=Counter)
    feature_decisions: Counter[str] = field(default_factory=Counter)
    backpressure: Counter[str] = field(default_factory=Counter)

    # -- provider --------------------------------------------------------

    def record_attempt(
        self,
        *,
        provider: str,
        operation: str,
        failure_class: str,
        disposition: str,
        latency_ms: int,
        status: int | None = None,
    ) -> None:
        self.provider_attempts[f"{provider}:{operation}"] += 1
        self.latency_ms_total[f"{provider}:{operation}"] += max(0, latency_ms)
        if disposition == "retrying":
            self.provider_retries[f"{provider}:{failure_class}"] += 1
        if failure_class != "ok":
            self.provider_failures[f"{provider}:{failure_class}"] += 1
        if status is not None:
            # Bucketed, not exact: 4xx/5xx is the dimension anybody alerts on, and a
            # counter per distinct status is cardinality nobody reads.
            self.provider_failures[f"{provider}:http_{status // 100}xx"] += 1
        logger.debug(
            "provider_attempt_metric",
            **safe_metric_fields(
                {
                    "provider": provider,
                    "operation": operation,
                    "failure_class": failure_class,
                    "disposition": disposition,
                    "latency_ms": latency_ms,
                    "status": status,
                }
            ),
        )

    def record_call(self, *, provider: str, operation: str, outcome: str) -> None:
        self.provider_calls[f"{provider}:{operation}:{outcome}"] += 1

    def record_circuit(self, *, provider: str, state: str) -> None:
        self.circuit_transitions[f"{provider}:{state}"] += 1

    def record_auth_failure(self, *, provider: str, operation: str) -> None:
        """A credential was refused. Counted separately from every other failure.

        Mixed in with timeouts and 500s it disappears: an outage is noisy and a wrong key
        is a steady trickle. On its own counter it is unmistakable, and it is the one
        failure class that no amount of waiting will fix.
        """

        self.auth_failures[f"{provider}:{operation}"] += 1

    def record_backpressure(self, *, provider: str, waiting: int) -> None:
        self.backpressure[provider] = max(self.backpressure[provider], waiting)

    # -- cost ------------------------------------------------------------

    def record_usage(
        self,
        *,
        feature: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        self.tokens[f"{model}:input"] += max(0, input_tokens)
        self.tokens[f"{model}:output"] += max(0, output_tokens)
        # Stored as micro-dollars because a Counter of floats loses pennies to rounding
        # over a month, and the whole point is that the total is trustworthy.
        self.cost_micros[f"{feature}:{model}"] += int(round(max(0.0, cost_usd) * 1_000_000))

    def record_budget_refusal(self, *, scope: str, code: str) -> None:
        self.budget_refusals[f"{scope}:{code}"] += 1

    def record_reservation(self, *, event: str) -> None:
        """``reserved``, ``settled``, ``released``, ``expired``, ``replayed``."""

        self.reservations[event] += 1

    def record_redis_fallback(self, *, component: str, reason: str) -> None:
        """Redis went away and something used its safe path instead.

        Counted because a silent fallback is how a coordination outage runs for a week
        before anybody notices the behaviour changed.
        """

        self.redis_fallbacks[f"{component}:{reason}"] += 1
        logger.warning("redis_fallback", component=component, reason=reason)

    # -- rollout ---------------------------------------------------------

    def record_feature_decision(
        self, *, feature: str, enabled: bool, reason: str, version: str
    ) -> None:
        state = "on" if enabled else "off"
        self.feature_decisions[f"{feature}:{state}:{reason}:{version}"] += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Everything counted so far, for an exporter or a test to read."""

        return {
            "provider_calls": dict(self.provider_calls),
            "provider_attempts": dict(self.provider_attempts),
            "provider_retries": dict(self.provider_retries),
            "provider_failures": dict(self.provider_failures),
            "auth_failures": dict(self.auth_failures),
            "circuit_transitions": dict(self.circuit_transitions),
            "latency_ms_total": dict(self.latency_ms_total),
            "tokens": dict(self.tokens),
            "cost_micros": dict(self.cost_micros),
            "budget_refusals": dict(self.budget_refusals),
            "reservations": dict(self.reservations),
            "redis_fallbacks": dict(self.redis_fallbacks),
            "feature_decisions": dict(self.feature_decisions),
            "backpressure": dict(self.backpressure),
        }


#: The process-wide metrics. One instance so every surface counts into the same place.
METRICS = ReliabilityMetrics()

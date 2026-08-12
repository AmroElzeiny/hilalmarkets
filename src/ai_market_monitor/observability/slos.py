"""Service-level objectives, defined once and versioned with the code that meets them.

An objective that cannot be measured is a wish. Every objective here therefore names
its indicator as a structure over :data:`ai_market_monitor.observability.metrics.METRICS`
rather than as a sentence, and
``tests/unit/test_observability_slos.py::test_every_indicator_is_computable`` fails the
build if an objective names a metric nothing emits. Adding an objective means adding
the metric that answers it, or not adding the objective.

The set is deliberately small. Each one answers a question a person on call actually
asks — is the API answering, are alerts arriving, is the queue draining — and each
one states who owns it and whether breaching it should stop a launch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from ai_market_monitor.observability.metrics import METRICS, MetricsRecorder

__all__ = [
    "SLO_DEFINITION_VERSION",
    "SLOS",
    "GaugeMaxIndicator",
    "Indicator",
    "QuantileIndicator",
    "RatioIndicator",
    "ServiceLevelObjective",
    "SLOEvaluation",
    "evaluate_slo",
    "evaluate_all_slos",
    "launch_blocking_slos",
    "slo_by_name",
]

#: Bumped whenever an objective, its threshold or its indicator changes.
#:
#: Alert rules bind to this version. A dashboard showing "99.5% availability" against
#: last quarter's threshold and this quarter's data is a wrong answer that looks
#: right, so the version travels with every evaluation.
SLO_DEFINITION_VERSION: Final[str] = "2026-08-12.1"

Severity = Literal["page", "ticket"]
Comparison = Literal["gte", "lte"]


@dataclass(frozen=True, slots=True)
class RatioIndicator:
    """Good events divided by total events, both drawn from counters."""

    good_metric: str
    total_metric: str
    good_labels: Mapping[str, str] = field(default_factory=dict)
    total_labels: Mapping[str, str] = field(default_factory=dict)

    def metric_names(self) -> tuple[str, ...]:
        return (self.good_metric, self.total_metric)

    def measure(self, recorder: MetricsRecorder) -> float | None:
        total = recorder.total(self.total_metric, **dict(self.total_labels))
        if total <= 0:
            return None
        good = recorder.total(self.good_metric, **dict(self.good_labels))
        return good / total


@dataclass(frozen=True, slots=True)
class QuantileIndicator:
    """A quantile of one histogram, in that histogram's own unit."""

    metric: str
    quantile: float
    labels: Mapping[str, str] = field(default_factory=dict)

    def metric_names(self) -> tuple[str, ...]:
        return (self.metric,)

    def measure(self, recorder: MetricsRecorder) -> float | None:
        return recorder.quantile(self.metric, self.quantile, **dict(self.labels))


@dataclass(frozen=True, slots=True)
class GaugeMaxIndicator:
    """The worst current reading across every series of one gauge.

    Worst, not average. One dead worker among four is an outage for the schedules
    that worker owned, and an average would report it as a quarter of a problem.
    """

    metric: str
    labels: Mapping[str, str] = field(default_factory=dict)

    def metric_names(self) -> tuple[str, ...]:
        return (self.metric,)

    def measure(self, recorder: MetricsRecorder) -> float | None:
        wanted = dict(self.labels)
        values = [
            sample.value
            for sample in recorder.snapshot()
            if sample.name == self.metric and wanted.items() <= sample.label_map.items()
        ]
        if not values:
            return None
        return max(values)


Indicator = RatioIndicator | QuantileIndicator | GaugeMaxIndicator


@dataclass(frozen=True, slots=True)
class ServiceLevelObjective:
    """One measurable promise, with an owner and a stated consequence."""

    name: str
    description: str
    service: str
    indicator: Indicator
    objective: float
    comparison: Comparison
    unit: str
    window_minutes: int
    #: The share of the window that may breach before the objective itself is missed.
    #: Stated rather than derived so a latency objective, where it is not simply
    #: ``1 - objective``, cannot be read as if it were.
    error_budget: float
    owner: str
    severity_on_breach: Severity
    launch_blocking: bool
    runbook_anchor: str

    def metric_names(self) -> tuple[str, ...]:
        return self.indicator.metric_names()

    def is_met(self, measured: float) -> bool:
        if self.comparison == "gte":
            return measured >= self.objective
        return measured <= self.objective


@dataclass(frozen=True, slots=True)
class SLOEvaluation:
    """What one objective reads right now.

    ``measured is None`` means *no data*, and that is reported as its own state
    rather than as a pass. An objective with no traffic behind it has not been met;
    it has not been tested, and a dashboard that shows those the same way is how an
    outage in a quiet subsystem stays invisible.
    """

    slo: ServiceLevelObjective
    measured: float | None
    definition_version: str = SLO_DEFINITION_VERSION

    @property
    def state(self) -> Literal["met", "breached", "no_data"]:
        if self.measured is None:
            return "no_data"
        return "met" if self.slo.is_met(self.measured) else "breached"

    @property
    def breached(self) -> bool:
        return self.state == "breached"


SLOS: Final[tuple[ServiceLevelObjective, ...]] = (
    ServiceLevelObjective(
        name="api_availability",
        description=(
            "Share of HTTP requests answered without a server error. A 4xx is the "
            "caller being told no, which is the API working."
        ),
        service="api",
        indicator=RatioIndicator(
            good_metric="http_requests_total",
            total_metric="http_requests_total",
            good_labels={"status_class": "2xx"},
        ),
        objective=0.995,
        comparison="gte",
        unit="ratio",
        window_minutes=60,
        error_budget=0.005,
        owner="platform",
        severity_on_breach="page",
        launch_blocking=True,
        runbook_anchor="#api-availability",
    ),
    ServiceLevelObjective(
        name="api_latency_p95",
        description="Ninety-fifth percentile time to answer an HTTP request.",
        service="api",
        indicator=QuantileIndicator(metric="http_request_duration_ms", quantile=0.95),
        objective=1_000.0,
        comparison="lte",
        unit="milliseconds",
        window_minutes=60,
        error_budget=0.05,
        owner="platform",
        severity_on_breach="ticket",
        launch_blocking=False,
        runbook_anchor="#api-latency",
    ),
    ServiceLevelObjective(
        name="setup_chat_turn_success",
        description=(
            "Share of Setup Chat turns that completed. A refusal counts as a failure "
            "of the turn only when the product could not answer at all."
        ),
        service="setup_chat",
        indicator=RatioIndicator(
            good_metric="ai_turns_total",
            total_metric="ai_turns_total",
            good_labels={"feature": "setup_chat", "outcome": "success"},
            total_labels={"feature": "setup_chat"},
        ),
        objective=0.98,
        comparison="gte",
        unit="ratio",
        window_minutes=60,
        error_budget=0.02,
        owner="setup_chat",
        severity_on_breach="page",
        launch_blocking=True,
        runbook_anchor="#setup-chat-turn-failures",
    ),
    ServiceLevelObjective(
        name="setup_chat_latency_p95",
        description="Ninety-fifth percentile Setup Chat turn time at the request boundary.",
        service="setup_chat",
        indicator=QuantileIndicator(
            metric="ai_turn_duration_ms",
            quantile=0.95,
            labels={"feature": "setup_chat"},
        ),
        objective=12_000.0,
        comparison="lte",
        unit="milliseconds",
        window_minutes=60,
        error_budget=0.05,
        owner="setup_chat",
        severity_on_breach="ticket",
        launch_blocking=False,
        runbook_anchor="#setup-chat-latency",
    ),
    ServiceLevelObjective(
        name="ai_provider_success",
        description="Share of outbound AI provider calls that returned a usable answer.",
        service="provider",
        indicator=RatioIndicator(
            good_metric="provider_calls_total",
            total_metric="provider_calls_total",
            good_labels={"provider": "openai", "outcome": "success"},
            total_labels={"provider": "openai"},
        ),
        objective=0.97,
        comparison="gte",
        unit="ratio",
        window_minutes=30,
        error_budget=0.03,
        owner="platform",
        severity_on_breach="page",
        launch_blocking=True,
        runbook_anchor="#ai-provider-degraded",
    ),
    ServiceLevelObjective(
        name="scheduled_scan_completion",
        description="Share of due scan jobs that finished before their next due time.",
        service="scanner",
        indicator=RatioIndicator(
            good_metric="scan_jobs_completed_in_window_total",
            total_metric="scan_jobs_due_total",
        ),
        objective=0.99,
        comparison="gte",
        unit="ratio",
        window_minutes=180,
        error_budget=0.01,
        owner="scanner",
        severity_on_breach="page",
        launch_blocking=True,
        runbook_anchor="#scans-delayed",
    ),
    ServiceLevelObjective(
        name="market_data_freshness",
        description="Age of the newest candle held for any monitored market.",
        service="market_data",
        indicator=GaugeMaxIndicator(metric="market_data_age_seconds"),
        objective=300.0,
        comparison="lte",
        unit="seconds",
        window_minutes=15,
        error_budget=0.02,
        owner="scanner",
        severity_on_breach="page",
        launch_blocking=True,
        runbook_anchor="#market-data-stale",
    ),
    ServiceLevelObjective(
        name="alert_delivery_success",
        description="Share of alert delivery attempts accepted by the channel.",
        service="alert_delivery",
        indicator=RatioIndicator(
            good_metric="alert_delivery_attempts_total",
            total_metric="alert_delivery_attempts_total",
            good_labels={"delivery_result": "delivered"},
        ),
        objective=0.99,
        comparison="gte",
        unit="ratio",
        window_minutes=60,
        error_budget=0.01,
        owner="delivery",
        severity_on_breach="page",
        launch_blocking=True,
        runbook_anchor="#alert-delivery-failing",
    ),
    ServiceLevelObjective(
        name="email_outbox_drain_p95",
        description="Ninety-fifth percentile time from queueing an email to accepted delivery.",
        service="email_delivery",
        indicator=QuantileIndicator(metric="email_outbox_drain_seconds", quantile=0.95),
        objective=900.0,
        comparison="lte",
        unit="seconds",
        window_minutes=180,
        error_budget=0.05,
        owner="delivery",
        severity_on_breach="ticket",
        launch_blocking=False,
        runbook_anchor="#email-outbox-backed-up",
    ),
    ServiceLevelObjective(
        name="worker_liveness",
        description=(
            "Age of the oldest worker or scheduler heartbeat. A scheduler that stops "
            "beating stops every scan, and nothing else reports it."
        ),
        service="worker",
        indicator=GaugeMaxIndicator(metric="worker_heartbeat_age_seconds"),
        objective=180.0,
        comparison="lte",
        unit="seconds",
        window_minutes=15,
        error_budget=0.01,
        owner="platform",
        severity_on_breach="page",
        launch_blocking=True,
        runbook_anchor="#worker-or-scheduler-down",
    ),
    ServiceLevelObjective(
        name="review_case_sla",
        description=(
            "Age of the oldest overdue Shariah review case. Overdue review is a "
            "governance breach, not a backlog."
        ),
        service="governance",
        indicator=GaugeMaxIndicator(
            metric="review_case_age_hours",
            labels={"review_stage": "overdue"},
        ),
        objective=48.0,
        comparison="lte",
        unit="hours",
        window_minutes=1_440,
        error_budget=0.05,
        owner="governance",
        severity_on_breach="ticket",
        launch_blocking=True,
        runbook_anchor="#review-case-overdue",
    ),
)


def slo_by_name(name: str) -> ServiceLevelObjective:
    for slo in SLOS:
        if slo.name == name:
            return slo
    raise KeyError(f"Unknown service-level objective {name!r}")


def launch_blocking_slos() -> tuple[ServiceLevelObjective, ...]:
    return tuple(slo for slo in SLOS if slo.launch_blocking)


def evaluate_slo(slo: ServiceLevelObjective, recorder: MetricsRecorder) -> SLOEvaluation:
    return SLOEvaluation(slo=slo, measured=slo.indicator.measure(recorder))


def evaluate_all_slos(recorder: MetricsRecorder) -> tuple[SLOEvaluation, ...]:
    return tuple(evaluate_slo(slo, recorder) for slo in SLOS)


def undeclared_metric_names() -> tuple[str, ...]:
    """Metric names an objective needs that :data:`METRICS` does not declare.

    Exists so the invariant is one expression rather than a loop rewritten in each
    place that checks it: the unit test and the release gate both read this.
    """

    return tuple(
        sorted(
            {
                metric
                for slo in SLOS
                for metric in slo.metric_names()
                if metric not in METRICS
            }
        )
    )

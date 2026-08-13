"""The one place the application records an operational measurement.

Before this module each surface kept its own idea of what was worth counting:
``ReliabilityService.record_metric`` wrote rows for the scanner, Setup Chat kept
per-turn telemetry, the bounded agent kept another set, and nothing tied any of
them to a stated objective. Three parallel recorders meant three different label
spellings for the same provider and no way to answer "is the product healthy"
from any single one of them.

So there is one registry of metric names here, and one recorder that writes them.
A metric that is not declared in :data:`METRICS` cannot be recorded, and a metric
that is declared states exactly which labels it carries. That is what makes the
service-level objectives in :mod:`ai_market_monitor.observability.slos` provable
rather than aspirational: every objective names a metric in this registry, and a
test fails if it names one that nothing emits.

Recording is deliberately cheap and in-process. It never opens a transaction, never
touches strategy, Passport, entitlement or approval state, and never blocks the
request that produced it.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final, Literal

from ai_market_monitor.observability.labels import (
    assert_no_sensitive_content,
    validate_labels,
)

__all__ = [
    "HISTOGRAM_BUCKETS",
    "METRICS",
    "MetricDelta",
    "MetricRetentionPolicy",
    "MetricSample",
    "MetricSpec",
    "MetricsRecorder",
    "UnknownMetricError",
    "get_metrics_recorder",
    "reset_metrics_recorder",
]

MetricKind = Literal["counter", "gauge", "histogram"]


class UnknownMetricError(KeyError):
    """A metric name that is not declared in :data:`METRICS`."""


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """What one metric measures, and exactly which labels it carries.

    ``labels`` is the complete set, not a minimum. A recorder call that omits one or
    adds one is refused: a metric whose label set varies between call sites cannot be
    aggregated, and the disagreement usually means two callers think they are
    measuring the same thing when they are not.
    """

    name: str
    kind: MetricKind
    unit: str
    component: str
    description: str
    labels: tuple[str, ...] = ()


def _spec(
    name: str,
    kind: MetricKind,
    unit: str,
    component: str,
    description: str,
    *labels: str,
) -> tuple[str, MetricSpec]:
    return name, MetricSpec(
        name=name,
        kind=kind,
        unit=unit,
        component=component,
        description=description,
        labels=labels,
    )


#: Every operational measurement the product emits.
#:
#: Grouped by the question each group answers. A new metric belongs here next to the
#: others, where its labels can be compared with the ones already in use, rather than
#: in a helper beside the code that happens to record it.
METRICS: Final[Mapping[str, MetricSpec]] = dict(
    (
        # -- Is the API answering, and how fast. ------------------------------
        _spec(
            "http_requests_total",
            "counter",
            "requests",
            "api",
            "HTTP requests answered, by route template and response class.",
            "route",
            "method",
            "status_class",
        ),
        _spec(
            "http_request_duration_ms",
            "histogram",
            "milliseconds",
            "api",
            "Wall-clock time to answer an HTTP request.",
            "route",
            "method",
        ),
        # -- Are the upstream providers answering. ----------------------------
        _spec(
            "provider_calls_total",
            "counter",
            "calls",
            "provider",
            "Outbound provider calls, by provider and how the call ended.",
            "provider",
            "operation",
            "outcome",
        ),
        _spec(
            "provider_call_duration_ms",
            "histogram",
            "milliseconds",
            "provider",
            "Wall-clock time of one outbound provider call.",
            "provider",
            "operation",
        ),
        _spec(
            "provider_retries_total",
            "counter",
            "attempts",
            "provider",
            "Retried provider attempts beyond the first.",
            "provider",
            "operation",
        ),
        _spec(
            "provider_circuit_state",
            "gauge",
            "state",
            "provider",
            "Circuit breaker position per provider; 1 marks the current state.",
            "provider",
            "circuit_state",
        ),
        # -- What the AI turns cost, and whether they land. --------------------
        _spec(
            "ai_turns_total",
            "counter",
            "turns",
            "ai",
            "Completed AI turns by product feature, model and outcome.",
            "feature",
            "model",
            "outcome",
        ),
        _spec(
            "ai_turn_duration_ms",
            "histogram",
            "milliseconds",
            "ai",
            "Wall-clock time of one AI turn, measured at the request boundary.",
            "feature",
            "model",
        ),
        _spec(
            "ai_turn_tokens_total",
            "counter",
            "tokens",
            "ai",
            "Tokens consumed by AI turns, by feature and model.",
            "feature",
            "model",
        ),
        _spec(
            "ai_turn_cost_usd",
            "counter",
            "usd",
            "ai",
            "Estimated and actual AI spend, kept apart by cost_kind.",
            "feature",
            "model",
            "cost_kind",
        ),
        # -- Are scheduled scans running on time. ------------------------------
        _spec(
            "scan_jobs_total",
            "counter",
            "jobs",
            "scanner",
            "Scan jobs by lifecycle phase: claimed, run, failed, recovered, abandoned.",
            "job_phase",
        ),
        _spec(
            "scan_job_duration_ms",
            "histogram",
            "milliseconds",
            "scanner",
            "Wall-clock time of one scan job.",
            "exchange",
        ),
        _spec(
            "scan_jobs_due_total",
            "counter",
            "jobs",
            "scanner",
            "Scan jobs that became due in the window.",
            "exchange",
        ),
        _spec(
            "scan_jobs_completed_in_window_total",
            "counter",
            "jobs",
            "scanner",
            "Scan jobs that finished before their next due time.",
            "exchange",
        ),
        # -- Is the work queue draining. ---------------------------------------
        _spec(
            "queue_depth",
            "gauge",
            "messages",
            "worker",
            "Messages waiting on a Celery queue.",
            "queue",
        ),
        _spec(
            "celery_task_failures_total",
            "counter",
            "tasks",
            "worker",
            "Celery tasks that ended in an unhandled failure.",
            "task",
        ),
        _spec(
            "worker_heartbeat_age_seconds",
            "gauge",
            "seconds",
            "worker",
            "Age of the most recent heartbeat from a worker or scheduler.",
            "component",
        ),
        # -- Are alerts reaching people. ---------------------------------------
        _spec(
            "alert_delivery_attempts_total",
            "counter",
            "attempts",
            "delivery",
            "Alert delivery attempts by channel and result.",
            "channel",
            "delivery_result",
        ),
        _spec(
            "alert_delivery_latency_ms",
            "histogram",
            "milliseconds",
            "delivery",
            "Time from alert creation to accepted delivery.",
            "channel",
        ),
        # -- Is the screened universe refusing work, and why. -------------------
        _spec(
            "screening_refusals_total",
            "counter",
            "refusals",
            "screening",
            "Fail-closed refusals from screened-universe resolution, by reason.",
            "refusal_reason",
        ),
        _spec(
            "passport_publications_total",
            "counter",
            "publications",
            "governance",
            "Evidence Passport publications completed.",
            "outcome",
        ),
        _spec(
            "review_case_age_hours",
            "gauge",
            "hours",
            "governance",
            "Age of the oldest review case at each stage.",
            "review_stage",
        ),
        # -- Is outbound email draining. ---------------------------------------
        _spec(
            "email_outbox_depth",
            "gauge",
            "messages",
            "delivery",
            "Undelivered rows waiting in an email outbox.",
            "queue",
        ),
        _spec(
            "email_outbox_abandoned_claims_total",
            "counter",
            "claims",
            "delivery",
            "Outbox rows whose delivery claim expired and had to be recovered.",
            "queue",
        ),
        _spec(
            "email_outbox_drain_seconds",
            "histogram",
            "seconds",
            "delivery",
            "Time from enqueue to accepted delivery for one outbox row.",
            "queue",
        ),
        # -- Are the stateful dependencies up. ----------------------------------
        _spec(
            "dependency_health",
            "gauge",
            "state",
            "infrastructure",
            "Health of a stateful dependency; 1 marks the current state.",
            "component",
            "health",
        ),
        _spec(
            "market_data_age_seconds",
            "gauge",
            "seconds",
            "market_data",
            "Age of the newest candle held for a market and timeframe.",
            "exchange",
            "timeframe",
        ),
    )
)


@dataclass(frozen=True, slots=True)
class MetricSample:
    """One metric's current value for one label combination."""

    name: str
    labels: tuple[tuple[str, str], ...]
    value: float
    count: int
    kind: MetricKind
    unit: str
    component: str

    @property
    def label_map(self) -> dict[str, str]:
        return dict(self.labels)


#: Bucket edges for every histogram, in the metric's own unit.
#:
#: Fixed rather than per-metric on purpose. A latency objective needs a quantile, and
#: a quantile needs the distribution, not the mean — an average request time stays
#: healthy while the slowest twentieth of users time out. One shared ladder spanning
#: a millisecond to ten minutes covers request latency, delivery latency and outbox
#: drain time without asking each caller to invent its own edges.
_HISTOGRAM_BUCKETS: Final[tuple[float, ...]] = (
    1.0,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1_000.0,
    2_500.0,
    5_000.0,
    10_000.0,
    30_000.0,
    60_000.0,
    300_000.0,
    600_000.0,
)

#: The same ladder, published so the durable store can write bucket counts that mean
#: the same thing after a restart. A stored histogram whose edges no longer match the
#: reader's is not a slightly wrong quantile; it is a number about nothing.
HISTOGRAM_BUCKETS: Final[tuple[float, ...]] = _HISTOGRAM_BUCKETS


@dataclass(frozen=True, slots=True)
class MetricRetentionPolicy:
    """How long stored measurements live, and when they stop being per-process.

    Lives here rather than beside the store that uses it so that the settings object
    can hold one without importing the database layer.
    """

    window_seconds: int
    rollup_after_hours: int
    retention_hours: int

    def validate(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("OBSERVABILITY_WINDOW_SECONDS must be at least 1.")
        if self.rollup_after_hours <= 0:
            raise ValueError("OBSERVABILITY_ROLLUP_AFTER_HOURS must be at least 1.")
        if self.retention_hours <= self.rollup_after_hours:
            raise ValueError(
                "OBSERVABILITY_RETENTION_HOURS must be longer than "
                "OBSERVABILITY_ROLLUP_AFTER_HOURS, otherwise rows are deleted before "
                "they are ever folded together."
            )


@dataclass(frozen=True, slots=True)
class MetricDelta:
    """What one series gained since this process last wrote it down.

    A *delta*, not a total, because several processes record the same metric and none
    of them can see the others. Each writes only its own movement, and the reader adds
    them up. Nothing is ever read, changed and written back, so two processes flushing
    at the same moment cannot lose each other's counts.

    ``snapshot_*`` is the exact reading the delta was taken against. It travels with
    the delta so that the flush can mark precisely that much as written, even though
    more observations arrived while the write was in flight.
    """

    name: str
    kind: MetricKind
    labels: tuple[tuple[str, str], ...]
    #: Counter and histogram: how much was added. Gauge: the current reading.
    value: float
    observations: int
    buckets: tuple[int, ...] | None
    snapshot_total: float
    snapshot_count: int
    snapshot_buckets: tuple[int, ...] | None

    @property
    def label_map(self) -> dict[str, str]:
        return dict(self.labels)


@dataclass
class _Series:
    total: float = 0.0
    count: int = 0
    last: float = 0.0
    #: Counts per bucket edge, plus an overflow slot for anything above the last edge.
    #: Only populated for histograms; a counter or gauge has no distribution to keep.
    buckets: list[int] = field(default_factory=lambda: [0] * (len(_HISTOGRAM_BUCKETS) + 1))

    def observe_bucket(self, value: float) -> None:
        for index, edge in enumerate(_HISTOGRAM_BUCKETS):
            if value <= edge:
                self.buckets[index] += 1
                return
        self.buckets[-1] += 1


@dataclass
class MetricsRecorder:
    """Holds current values for every declared metric, in this process.

    Deliberately not a metrics *backend*. It keeps the numbers a scrape or a
    service-level objective needs and nothing else, so that recording stays free of
    I/O and cannot fail the request that produced it. Exporting to a long-term store
    is a separate step that reads :meth:`snapshot`.
    """

    _series: dict[str, dict[tuple[tuple[str, str], ...], _Series]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    _seen_label_values: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)
    #: The last reading successfully written to the durable store, per series. What is
    #: in memory minus what is here is what still has to be written down.
    _flushed: dict[str, dict[tuple[tuple[str, str], ...], tuple[float, int, tuple[int, ...]]]] = (
        field(default_factory=lambda: defaultdict(dict))
    )

    def record(self, name: str, value: float = 1.0, /, **labels: str) -> None:
        """Record one observation, or raise naming what is wrong with it.

        Raising is intended. A metric with the wrong labels is worse than a missing
        one, because it looks answerable: a dashboard built on it shows a number, and
        the number is about something other than what it claims.
        """

        spec = METRICS.get(name)
        if spec is None:
            raise UnknownMetricError(
                f"Unknown metric {name!r}. Declare it in observability.metrics.METRICS "
                "so an objective can be written against it."
            )
        supplied = set(labels)
        expected = set(spec.labels)
        if supplied != expected:
            missing = sorted(expected - supplied)
            unexpected = sorted(supplied - expected)
            raise ValueError(
                f"Metric {name} expects labels {sorted(expected)}; "
                f"missing={missing} unexpected={unexpected}."
            )
        assert_no_sensitive_content(labels, field=f"metric.{name}.labels")
        with self._lock:
            validated = validate_labels(labels, seen_values=self._seen_label_values)
            for label_name, label_value in validated.items():
                self._seen_label_values[label_name].add(label_value)
            key = tuple(sorted(validated.items()))
            series = self._series[name].setdefault(key, _Series())
            if spec.kind == "gauge":
                series.total = float(value)
                series.last = float(value)
                series.count += 1
            else:
                series.total += float(value)
                series.last = float(value)
                series.count += 1
                if spec.kind == "histogram":
                    series.observe_bucket(float(value))

    @contextmanager
    def time_ms(self, name: str, /, **labels: str) -> Iterator[None]:
        """Record the wall-clock duration of the enclosed block, in milliseconds.

        The duration is recorded whether the block succeeded or raised. A latency
        objective that silently drops failed requests reports the latency of the
        healthy path only, which is exactly the number that stays green during an
        outage.
        """

        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.record(name, elapsed_ms, **labels)

    def snapshot(self) -> tuple[MetricSample, ...]:
        """Every current value, ready for an exporter or an objective to read."""

        with self._lock:
            samples: list[MetricSample] = []
            for name, series_by_labels in self._series.items():
                spec = METRICS[name]
                for key, series in series_by_labels.items():
                    samples.append(
                        MetricSample(
                            name=name,
                            labels=key,
                            value=series.total,
                            count=series.count,
                            kind=spec.kind,
                            unit=spec.unit,
                            component=spec.component,
                        )
                    )
        return tuple(samples)

    def value(self, name: str, /, **labels: str) -> float:
        """Current value for one exact label combination, or ``0.0``."""

        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            return self._series.get(name, {}).get(key, _Series()).total

    def total(self, name: str, /, **labels: str) -> float:
        """Sum across every series for ``name`` that matches the given labels.

        Objectives are written as ratios over a whole family — every provider, every
        route — so the common read is a partial match, not an exact one.
        """

        wanted = {k: str(v) for k, v in labels.items()}
        with self._lock:
            series_by_labels = self._series.get(name, {})
            return sum(
                series.total
                for key, series in series_by_labels.items()
                if wanted.items() <= dict(key).items()
            )

    def observation_count(self, name: str, /, **labels: str) -> int:
        """How many observations landed in the matching series."""

        wanted = {k: str(v) for k, v in labels.items()}
        with self._lock:
            series_by_labels = self._series.get(name, {})
            return sum(
                series.count
                for key, series in series_by_labels.items()
                if wanted.items() <= dict(key).items()
            )

    def quantile(self, name: str, ratio: float, /, **labels: str) -> float | None:
        """Upper bound of the requested quantile, or ``None`` with nothing recorded.

        Bucketed, so the answer is the top edge of the bucket the quantile falls in
        rather than an interpolated point. That is the honest reading: it never
        reports a latency better than one actually observed, which is the direction a
        latency objective must err in. An observation above the last edge reports
        ``inf``, because "slower than ten minutes" is all the buckets know.
        """

        spec = METRICS.get(name)
        if spec is None:
            raise UnknownMetricError(f"Unknown metric {name!r}.")
        if spec.kind != "histogram":
            raise ValueError(f"Metric {name} is a {spec.kind}; only histograms have quantiles.")
        if not 0.0 < ratio <= 1.0:
            raise ValueError("A quantile ratio must be above 0 and at most 1.")
        wanted = {k: str(v) for k, v in labels.items()}
        with self._lock:
            series_by_labels = self._series.get(name, {})
            matching = [
                series
                for key, series in series_by_labels.items()
                if wanted.items() <= dict(key).items()
            ]
            if not matching:
                return None
            merged = [0] * (len(_HISTOGRAM_BUCKETS) + 1)
            for series in matching:
                for index, count in enumerate(series.buckets):
                    merged[index] += count
            total = sum(merged)
            if total == 0:
                return None
            target = ratio * total
            cumulative = 0
            for index, count in enumerate(merged):
                cumulative += count
                if cumulative >= target:
                    if index < len(_HISTOGRAM_BUCKETS):
                        return _HISTOGRAM_BUCKETS[index]
                    return float("inf")
        return float("inf")

    def pending_deltas(self) -> tuple[MetricDelta, ...]:
        """Everything recorded since the last confirmed write, ready to be stored.

        Never clears anything. The in-memory reading stays whole, so a failed write is
        simply retried on the next pass with the same numbers plus whatever arrived in
        between — a lost database connection costs latency, not measurements.
        """

        deltas: list[MetricDelta] = []
        with self._lock:
            for name, series_by_labels in self._series.items():
                spec = METRICS[name]
                flushed = self._flushed[name]
                for key, series in series_by_labels.items():
                    seen_total, seen_count, seen_buckets = flushed.get(
                        key, (0.0, 0, (0,) * (len(_HISTOGRAM_BUCKETS) + 1))
                    )
                    current_buckets = tuple(series.buckets)
                    if spec.kind == "gauge":
                        # A gauge is a reading, not an accumulation. Its delta is the
                        # reading itself; adding gauges across processes would report
                        # four healthy workers as one very stale one.
                        if series.total == seen_total and series.count == seen_count:
                            continue
                        value = series.total
                        observations = series.count - seen_count
                        buckets = None
                    else:
                        value = series.total - seen_total
                        observations = series.count - seen_count
                        if observations <= 0 and value == 0.0:
                            continue
                        buckets = (
                            tuple(
                                current - previous
                                for current, previous in zip(
                                    current_buckets, seen_buckets, strict=True
                                )
                            )
                            if spec.kind == "histogram"
                            else None
                        )
                    deltas.append(
                        MetricDelta(
                            name=name,
                            kind=spec.kind,
                            labels=key,
                            value=value,
                            observations=observations,
                            buckets=buckets,
                            snapshot_total=series.total,
                            snapshot_count=series.count,
                            snapshot_buckets=current_buckets,
                        )
                    )
        return tuple(deltas)

    def mark_flushed(self, deltas: tuple[MetricDelta, ...]) -> None:
        """Record that exactly these readings reached the store.

        Called only after the write is committed, and only for the snapshot the delta
        was taken against — not for the current reading, which has moved on. Marking
        the current reading instead would silently discard every observation that
        landed while the write was in flight.
        """

        with self._lock:
            for delta in deltas:
                self._flushed[delta.name][delta.labels] = (
                    delta.snapshot_total,
                    delta.snapshot_count,
                    delta.snapshot_buckets or (0,) * (len(_HISTOGRAM_BUCKETS) + 1),
                )

    def merge_stored(
        self,
        name: str,
        labels: Mapping[str, str],
        *,
        value: float,
        observations: int,
        buckets: tuple[int, ...] | None = None,
    ) -> None:
        """Add one stored reading back in, for a recorder built from the database.

        Deliberately skips label validation. These values were validated when they
        were recorded; re-validating on the way out would count each stored label
        value against the cardinality ceiling a second time and refuse a legitimate
        history for looking too varied.
        """

        spec = METRICS.get(name)
        if spec is None:
            raise UnknownMetricError(f"Unknown metric {name!r}.")
        key = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        with self._lock:
            series = self._series[name].setdefault(key, _Series())
            if spec.kind == "gauge":
                series.total = float(value)
            else:
                series.total += float(value)
            series.last = float(value)
            series.count += int(observations)
            if spec.kind == "histogram" and buckets is not None:
                for index, count in enumerate(buckets[: len(series.buckets)]):
                    series.buckets[index] += int(count)

    def reset(self) -> None:
        with self._lock:
            self._series.clear()
            self._seen_label_values.clear()
            self._flushed.clear()


_RECORDER: MetricsRecorder | None = None
_RECORDER_LOCK: Final[threading.Lock] = threading.Lock()


def get_metrics_recorder() -> MetricsRecorder:
    """The process-wide recorder. One per process, created on first use."""

    global _RECORDER
    if _RECORDER is None:
        with _RECORDER_LOCK:
            if _RECORDER is None:
                _RECORDER = MetricsRecorder()
    return _RECORDER


def reset_metrics_recorder() -> MetricsRecorder:
    """Drop every recorded series. For tests, which must not inherit each other's."""

    recorder = get_metrics_recorder()
    recorder.reset()
    return recorder

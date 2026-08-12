"""Operational truth for HilalMarkets: what is measured, promised, and paged on.

One package rather than a helper beside each surface, because the previous
arrangement — a recorder in the scanner, another in Setup Chat, a third in the
bounded agent — could not answer "is the product healthy" from any single one of
them, and spelled the same provider three different ways.

Read in this order:

``labels``   the one vocabulary for metric labels, and the redaction rule
``metrics``  the registry of every measurement, and the recorder that writes it
``slos``     the objectives, each naming a metric from that registry
``alerts``   the rules that page or ticket when an objective breaks
``issues``   the durable, deduplicated queue of operational problems

Everything here is read-only with respect to the product. It never touches
strategy, Passport, entitlement or approval state.
"""

from ai_market_monitor.observability.labels import (
    MetricLabelError,
    SensitiveValueError,
    assert_no_sensitive_content,
    validate_labels,
)
from ai_market_monitor.observability.metrics import (
    METRICS,
    MetricSpec,
    MetricsRecorder,
    UnknownMetricError,
    get_metrics_recorder,
    reset_metrics_recorder,
)

__all__ = [
    "METRICS",
    "MetricLabelError",
    "MetricSpec",
    "MetricsRecorder",
    "SensitiveValueError",
    "UnknownMetricError",
    "assert_no_sensitive_content",
    "get_metrics_recorder",
    "reset_metrics_recorder",
    "validate_labels",
]

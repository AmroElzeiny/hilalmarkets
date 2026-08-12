"""Turning one HTTP request into the two metrics the API objectives read.

Kept apart from the middleware itself because the interesting decision is not
"measure the request" but *what to call it*. A metric labelled with the raw path
produces one time series per URL, and this product has paths carrying a strategy id,
a symbol and a Passport version. Within a day that is tens of thousands of series
describing nothing.

So the label is the **route template** — ``/api/v1/strategies/{strategy_id}`` — which
FastAPI leaves in the request scope once it has matched. A request that matched
nothing is recorded under a single ``unmatched`` bucket rather than under whatever a
scanner happened to probe, because a 404 sweep is exactly the traffic that would
otherwise fill the metric store.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from ai_market_monitor.observability.metrics import MetricsRecorder, get_metrics_recorder

__all__ = ["record_http_request", "route_label", "status_class"]

#: Everything that did not match a declared route. One bucket, on purpose: an
#: unmatched path is attacker-controlled input, and labelling by it hands the metric
#: store's cardinality budget to whoever is scanning the site.
UNMATCHED_ROUTE: Final[str] = "unmatched"

#: Long enough for this application's deepest template, short enough that a label
#: can never become a payload.
_MAX_ROUTE_LENGTH: Final[int] = 80


def route_label(scope: Mapping[str, Any]) -> str:
    """The matched route template, or ``unmatched``."""

    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    if not template or not isinstance(template, str):
        return UNMATCHED_ROUTE
    if len(template) > _MAX_ROUTE_LENGTH:
        return UNMATCHED_ROUTE
    return template


def status_class(status_code: int) -> str:
    """``2xx``…``5xx``.

    Grouped rather than recorded exactly, because the availability objective asks
    whether the server failed, and a per-code label would multiply every route by
    every status the framework can produce.
    """

    if status_code >= 500:
        return "5xx"
    if status_code >= 400:
        return "4xx"
    if status_code >= 300:
        return "3xx"
    return "2xx"


def record_http_request(
    *,
    scope: Mapping[str, Any],
    method: str,
    status_code: int,
    duration_ms: float,
    recorder: MetricsRecorder | None = None,
) -> None:
    """Record one answered request.

    Never raises. Instrumentation that can fail a request is worse than no
    instrumentation: it turns a metric problem into an outage, and it does so under
    exactly the unusual traffic that was worth measuring. A label the vocabulary
    refuses is dropped here, and the recorder's own tests are what keep that from
    hiding a real mistake.
    """

    target = recorder or get_metrics_recorder()
    normalized_method = method.upper()
    if normalized_method not in {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    }:
        return
    route = route_label(scope)
    try:
        target.record(
            "http_requests_total",
            1.0,
            route=route,
            method=normalized_method,
            status_class=status_class(status_code),
        )
        target.record(
            "http_request_duration_ms",
            duration_ms,
            route=route,
            method=normalized_method,
        )
    except Exception:  # noqa: BLE001 - see the docstring: never fail the request
        return

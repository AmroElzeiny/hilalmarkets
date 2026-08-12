"""The rules the operational-truth layer must keep, asserted across whole families.

Each test here is parametrised over every metric, every objective, every alert rule
or every secret shape rather than over one example. A change that only fixes the
case someone happened to write down has to fail these.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.observability.alerts import (
    ALERT_RULES,
    ALERT_RULES_VERSION,
    DELIVERY_ROUTE_DEPENDENCIES,
    AlertRule,
    AlertRuleError,
    RefusalTrigger,
    SLOBreachTrigger,
    evaluate_alert_rules,
    validate_alert_rules,
)
from ai_market_monitor.observability.labels import (
    ENUMERATED_LABELS,
    IDENTIFIER_LABELS,
    MetricLabelError,
    SensitiveValueError,
    assert_no_sensitive_content,
    validate_labels,
)
from ai_market_monitor.observability.metrics import (
    METRICS,
    MetricsRecorder,
    UnknownMetricError,
)
from ai_market_monitor.observability.slos import (
    SLO_DEFINITION_VERSION,
    SLOS,
    evaluate_all_slos,
    slo_by_name,
    undeclared_metric_names,
)


@pytest.fixture
def recorder() -> MetricsRecorder:
    """A recorder that has never seen anything, so no test inherits another's series."""

    return MetricsRecorder()


# --------------------------------------------------------------------------
# Service-level objectives
# --------------------------------------------------------------------------


def test_objective_names_are_unique() -> None:
    names = [slo.name for slo in SLOS]
    assert len(names) == len(set(names))


def test_no_objective_names_a_metric_nothing_emits() -> None:
    """The rule that keeps an objective from being a wish.

    An indicator over a metric no code records reads as "no data" forever, which on a
    dashboard is indistinguishable from health.
    """

    assert undeclared_metric_names() == ()


@pytest.mark.parametrize("slo", SLOS, ids=lambda item: item.name)
def test_every_objective_is_completely_specified(slo) -> None:
    assert slo.description.strip()
    assert slo.service.strip()
    assert slo.owner.strip()
    assert slo.unit.strip()
    assert slo.window_minutes > 0
    assert 0.0 <= slo.error_budget <= 1.0
    assert slo.severity_on_breach in {"page", "ticket"}
    assert slo.runbook_anchor.startswith("#")
    assert slo.comparison in {"gte", "lte"}


@pytest.mark.parametrize("slo", SLOS, ids=lambda item: item.name)
def test_every_indicator_reads_only_declared_metrics(slo) -> None:
    for metric in slo.metric_names():
        assert metric in METRICS, f"{slo.name} reads undeclared metric {metric}"


@pytest.mark.parametrize(
    "name",
    [
        "api_availability",
        "setup_chat_turn_success",
        "ai_provider_success",
        "scheduled_scan_completion",
        "market_data_freshness",
        "alert_delivery_success",
        "worker_liveness",
        "review_case_sla",
    ],
)
def test_the_launch_blocking_set_is_stated_not_implied(name: str) -> None:
    assert slo_by_name(name).launch_blocking is True


def test_an_objective_with_no_traffic_reports_no_data_not_success(
    recorder: MetricsRecorder,
) -> None:
    """Silence is not health.

    An empty subsystem must not read as "met", or an outage in a quiet corner of the
    product stays invisible for exactly as long as it stays quiet.
    """

    for evaluation in evaluate_all_slos(recorder):
        assert evaluation.state == "no_data"
        assert evaluation.measured is None
        assert not evaluation.breached


def test_objective_and_alert_versions_move_together() -> None:
    assert ALERT_RULES_VERSION == SLO_DEFINITION_VERSION


# --------------------------------------------------------------------------
# Metric labels: cardinality
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spec", sorted(METRICS.values(), key=lambda item: item.name),
                         ids=lambda item: item.name)
def test_every_metric_declares_only_known_labels(spec) -> None:
    known = set(ENUMERATED_LABELS) | set(IDENTIFIER_LABELS)
    for label in spec.labels:
        assert label in known, f"{spec.name} carries undeclared label {label}"
    assert spec.description.strip()
    assert spec.unit.strip()
    assert spec.component.strip()


def test_an_unknown_metric_is_refused(recorder: MetricsRecorder) -> None:
    with pytest.raises(UnknownMetricError):
        recorder.record("not_a_real_metric", 1.0)


def test_a_metric_recorded_with_the_wrong_labels_is_refused(
    recorder: MetricsRecorder,
) -> None:
    with pytest.raises(ValueError, match="expects labels"):
        recorder.record("http_requests_total", 1.0, route="/x", method="GET")


@pytest.mark.parametrize(
    "value",
    [
        "123e4567-e89b-12d3-a456-426614174000",
        "person@example.com",
        "a" * 40,
        "some free text with spaces",
        "0123456789abcdef0123456789abcdef",
    ],
)
def test_an_identifying_or_unbounded_label_value_raises(
    recorder: MetricsRecorder, value: str
) -> None:
    """Every shape that is both unbounded and identifying is refused."""

    with pytest.raises((MetricLabelError, SensitiveValueError)):
        recorder.record("provider_calls_total", 1.0, provider=value, operation="chat",
                        outcome="success")


def test_a_label_that_keeps_taking_new_values_raises_before_it_becomes_a_leak(
    recorder: MetricsRecorder,
) -> None:
    """The ceiling is what catches a label by user id.

    Each individual value looks reasonable. Only the count gives it away, so the
    count is what is enforced.
    """

    budget = IDENTIFIER_LABELS["operation"]
    for index in range(budget):
        recorder.record(
            "provider_calls_total",
            1.0,
            provider="openai",
            operation=f"op{index}",
            outcome="success",
        )
    with pytest.raises(MetricLabelError, match="cardinality budget"):
        recorder.record(
            "provider_calls_total",
            1.0,
            provider="openai",
            operation="one-too-many",
            outcome="success",
        )


@pytest.mark.parametrize("label,allowed", sorted(ENUMERATED_LABELS.items()))
def test_an_enumerated_label_refuses_a_value_outside_its_set(label: str, allowed) -> None:
    assert allowed, f"{label} declares no values"
    with pytest.raises(MetricLabelError):
        validate_labels({label: "definitely-not-a-declared-value"})


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwxyz012345",
        "Bearer abcdefghijklmnop",
        "Basic YWRtaW46cGFzc3dvcmQ=",
        "123456789:AAHfSHFKJHkjhKJHkjhKJHkjhKJHkjhKJHk",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
        "-----BEGIN RSA PRIVATE KEY-----",
        "AKIAIOSFODNN7EXAMPLE",
        "customer@example.com",
        "witch collapse practice feed shame open despair creek road again ice least",
    ],
)
def test_a_secret_never_reaches_an_emitted_record(secret: str) -> None:
    """One test over every credential shape, not one test per incident."""

    with pytest.raises(SensitiveValueError):
        assert_no_sensitive_content(secret, field="test")


def test_prose_is_refused_by_length_so_a_prompt_cannot_ride_along() -> None:
    prompt = (
        "Alert me when bitcoin drops more than five percent on the fifteen minute "
        "candle and the relative strength index is below thirty, but only during "
        "the London session, and please explain your reasoning in detail."
    )
    with pytest.raises(SensitiveValueError):
        assert_no_sensitive_content(prompt, field="test")


def test_redaction_reaches_inside_nested_payloads() -> None:
    payload = {"outer": {"inner": ["fine", "sk-abcdefghijklmnopqrstuvwxyz012345"]}}
    with pytest.raises(SensitiveValueError):
        assert_no_sensitive_content(payload, field="test")


def test_a_recorded_metric_carries_no_customer_content(recorder: MetricsRecorder) -> None:
    recorder.record(
        "ai_turns_total", 1.0, feature="setup_chat", model="gpt-5.4-nano", outcome="success"
    )
    for sample in recorder.snapshot():
        for key, value in sample.label_map.items():
            assert_no_sensitive_content(value, field=key)


# --------------------------------------------------------------------------
# Histogram quantiles
# --------------------------------------------------------------------------


def test_a_quantile_is_not_dragged_down_by_a_fast_majority(
    recorder: MetricsRecorder,
) -> None:
    """The number a latency objective actually needs.

    Ninety-five fast requests and five slow ones average out to healthy. The p95 is
    the reading that still shows the five.
    """

    for _ in range(95):
        recorder.record("http_request_duration_ms", 20.0, route="/a", method="GET")
    for _ in range(5):
        recorder.record("http_request_duration_ms", 8_000.0, route="/a", method="GET")
    assert recorder.quantile("http_request_duration_ms", 0.5) == 25.0
    assert recorder.quantile("http_request_duration_ms", 0.99) == 10_000.0


def test_a_quantile_over_a_counter_is_refused(recorder: MetricsRecorder) -> None:
    with pytest.raises(ValueError, match="only histograms"):
        recorder.quantile("http_requests_total", 0.95)


# --------------------------------------------------------------------------
# Alert rules
# --------------------------------------------------------------------------


def test_the_shipped_alert_rules_are_valid() -> None:
    validate_alert_rules()


@pytest.mark.parametrize("rule", ALERT_RULES, ids=lambda item: item.name)
def test_every_alert_answers_the_four_questions(rule) -> None:
    """An alert that only says something broke makes the reader do the diagnosis."""

    assert rule.what_broke.strip()
    assert rule.blast_radius.strip()
    assert rule.first_mitigation.strip()
    assert rule.runbook_anchor.startswith("#")
    assert rule.severity in {"page", "ticket"}


@pytest.mark.parametrize("rule", ALERT_RULES, ids=lambda item: item.name)
def test_no_alert_travels_through_the_thing_it_watches(rule) -> None:
    dependencies = DELIVERY_ROUTE_DEPENDENCIES[rule.delivery_route]
    assert rule.watched_service not in dependencies


@pytest.mark.parametrize("rule", ALERT_RULES, ids=lambda item: item.name)
def test_a_page_never_depends_on_the_application_to_be_delivered(rule) -> None:
    if rule.severity == "page":
        assert DELIVERY_ROUTE_DEPENDENCIES[rule.delivery_route] == frozenset()


@pytest.mark.parametrize("rule", ALERT_RULES, ids=lambda item: item.name)
def test_alert_severity_agrees_with_the_objective_it_watches(rule) -> None:
    if isinstance(rule.trigger, SLOBreachTrigger):
        assert rule.severity == slo_by_name(rule.trigger.slo_name).severity_on_breach


def test_an_alert_routed_through_its_own_subsystem_is_refused() -> None:
    """The validator has to catch this, or it catches nothing.

    Written as its own case because the shipped rules all pass; without a negative
    case the validator could be a no-op and every other test here would still be green.
    """

    broken = AlertRule(
        name="telegram_alert_over_telegram",
        trigger=SLOBreachTrigger("alert_delivery_success"),
        severity="page",
        what_broke="Alert delivery is failing.",
        blast_radius="Customers stop receiving alerts.",
        first_mitigation="Check the bot token.",
        runbook_anchor="#alert-delivery-failing",
        delivery_route="ops_telegram",
    )
    with pytest.raises(AlertRuleError, match="depends on it"):
        validate_alert_rules((broken,))


def test_a_refusal_alert_fires_only_on_a_rate_not_a_single_refusal(
    recorder: MetricsRecorder,
) -> None:
    """Refusals are correct behaviour. Only an abnormal rate is a problem."""

    trigger = RefusalTrigger(refusal_reason="no_active_passport", threshold=50)
    recorder.record("screening_refusals_total", 1.0, refusal_reason="no_active_passport")
    assert not trigger.fires_from(recorder)
    recorder.record("screening_refusals_total", 60.0, refusal_reason="no_active_passport")
    assert trigger.fires_from(recorder)


def test_a_breached_objective_produces_its_alert(recorder: MetricsRecorder) -> None:
    for _ in range(10):
        recorder.record(
            "alert_delivery_attempts_total",
            1.0,
            channel="telegram",
            delivery_result="permanent",
        )
    fired = {item.rule.name for item in evaluate_alert_rules(recorder)}
    assert "alert_delivery_failing" in fired


def _operations_anchors() -> set[str]:
    """Every heading in the operations guide, as a GitHub-style anchor."""

    text = (
        Path(__file__).resolve().parents[2] / "docs" / "OPERATIONS.md"
    ).read_text(encoding="utf-8")
    anchors: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip()
        slug = re.sub(r"[^a-z0-9\s-]", "", heading.casefold())
        anchors.add("#" + re.sub(r"\s+", "-", slug).strip("-"))
    return anchors


@pytest.mark.parametrize("rule", ALERT_RULES, ids=lambda item: item.name)
def test_every_alert_points_at_a_runbook_section_that_exists(rule) -> None:
    """An anchor into a section nobody wrote is a dead end at 3am.

    This is what keeps the alert rules and docs/OPERATIONS.md from drifting apart:
    renaming a runbook heading without updating the rule fails here.
    """

    assert rule.runbook_anchor in _operations_anchors(), (
        f"{rule.name} points at {rule.runbook_anchor}, which docs/OPERATIONS.md "
        "does not contain"
    )


@pytest.mark.parametrize("slo", SLOS, ids=lambda item: item.name)
def test_every_objective_points_at_a_runbook_section_that_exists(slo) -> None:
    assert slo.runbook_anchor in _operations_anchors()


def test_every_enum_column_is_wide_enough_for_its_longest_value() -> None:
    """The defect class behind the compliance-alert bug, checked across every enum.

    ``alerts.alert_type`` was created from a six-value enum and rendered
    ``VARCHAR(9)``. ``AlertType`` later gained ``compliance``, which is ten
    characters, and nothing widened the column. SQLite ignores ``VARCHAR`` length so
    every offline test passed; PostgreSQL rejects the insert, so compliance alerts
    failed in production only.

    Asserted over every string-backed enum column in the metadata rather than over
    the one that was found, because the same mistake is available to any enum that
    gains a longer member later.
    """

    from sqlalchemy import Enum as SAEnum

    from ai_market_monitor.db.base import Base

    too_narrow: list[str] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            column_type = column.type
            if not isinstance(column_type, SAEnum) or column_type.native_enum:
                continue
            longest = max((len(value) for value in column_type.enums), default=0)
            declared = getattr(column_type, "length", None)
            if declared is not None and declared < longest:
                too_narrow.append(
                    f"{table.name}.{column.name} holds {declared} characters but its "
                    f"longest value needs {longest}"
                )
    assert not too_narrow, "\n".join(too_narrow)


def test_the_operations_guide_no_longer_carries_the_old_product_name() -> None:
    text = (
        Path(__file__).resolve().parents[2] / "docs" / "OPERATIONS.md"
    ).read_text(encoding="utf-8")
    assert not text.startswith("# TraceEdge")


def test_pages_are_ordered_before_tickets(recorder: MetricsRecorder) -> None:
    for _ in range(10):
        recorder.record("http_requests_total", 1.0, route="/a", method="GET",
                        status_class="5xx")
        recorder.record("http_request_duration_ms", 300_000.0, route="/a", method="GET")
    fired = evaluate_alert_rules(recorder)
    severities = [item.rule.severity for item in fired]
    assert severities == sorted(severities, key=lambda value: 0 if value == "page" else 1)

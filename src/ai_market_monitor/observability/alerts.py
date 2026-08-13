"""Alert rules, bound to the objectives and refusals they watch.

An alert exists to start a specific action. One that says only "error rate high"
makes the person who received it do the diagnosis the alert should have carried, at
the worst possible moment. Each rule here therefore states four things in advance:
what broke, who is affected, the first move that is safe to make without
understanding the cause yet, and where the full procedure is written down.

Two rules about alerting itself are enforced rather than trusted:

*Page or ticket is a field.* Whether something wakes a person is a property of the
rule, not a convention about how it is worded. It is declared, and the objective's
own ``severity_on_breach`` has to agree with it.

*An alert may not travel through what it is reporting on.* The Telegram outage
alert cannot be a Telegram message; the email backlog alert cannot be an email.
:func:`validate_alert_rules` refuses that at import time, because the failure mode
is silent — the alert is generated correctly and simply never arrives.

There is no external paging service here, so every route this product has depends on
part of this product. A page therefore names two routes whose dependencies do not
overlap, and the second is used when the first refuses. A ticket names none: it is
recorded in the operational issue queue and waits to be read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from ai_market_monitor.observability.metrics import MetricsRecorder
from ai_market_monitor.observability.slos import (
    SLO_DEFINITION_VERSION,
    SLOS,
    Severity,
    SLOEvaluation,
    evaluate_all_slos,
    slo_by_name,
)

__all__ = [
    "ALERT_RULES",
    "ALERT_RULES_VERSION",
    "AlertRule",
    "FiredAlert",
    "AlertRuleError",
    "RefusalTrigger",
    "SLOBreachTrigger",
    "evaluate_alert_rules",
    "validate_alert_rules",
]

#: Versioned together with the objectives. An alert threshold that no longer matches
#: the objective it claims to watch is worse than no alert, so they move as one.
ALERT_RULES_VERSION: Final[str] = SLO_DEFINITION_VERSION

DeliveryRoute = Literal["ops_email", "ops_telegram", "system_brain"]


class AlertRuleError(ValueError):
    """An alert rule that could not do its job as declared."""


#: Which application component each delivery route depends on to arrive.
#:
#: There is no external paging service in this product, so every route depends on
#: something. That is why a page-worthy alert must name two of them whose dependencies
#: do not overlap: whatever is broken, one of the two paths is still standing.
#: ``system_brain`` is the in-product admin surface. It always records, but it only
#: reaches a person who is already looking, so it is a last resort, never a primary.
#: The service names are finer than "delivery" on purpose. Email and Telegram both
#: deliver, but they fail independently, and collapsing them would have blocked the
#: email-backlog alert from using the Telegram route for no real reason.
DELIVERY_ROUTE_DEPENDENCIES: Final[Mapping[str, frozenset[str]]] = {
    "ops_email": frozenset({"email_delivery", "worker"}),
    "ops_telegram": frozenset({"alert_delivery", "telegram_delivery"}),
    "system_brain": frozenset({"api"}),
}


@dataclass(frozen=True, slots=True)
class SLOBreachTrigger:
    """Fires when a named objective is breached."""

    slo_name: str

    @property
    def watched_service(self) -> str:
        return slo_by_name(self.slo_name).service

    def fires(self, evaluations: Mapping[str, SLOEvaluation]) -> bool:
        evaluation = evaluations.get(self.slo_name)
        return evaluation is not None and evaluation.breached


@dataclass(frozen=True, slots=True)
class RefusalTrigger:
    """Fires when fail-closed refusals cross a rate in the measurement window.

    Refusals are correct behaviour, so this never fires on a single one. It fires on
    a *rate*, because a screening layer that suddenly refuses everything is either a
    data outage or a methodology mistake, and both look identical to one refusal.
    """

    refusal_reason: str
    threshold: int
    watched_service: str = "screening"

    def fires_from(self, recorder: MetricsRecorder) -> bool:
        observed = recorder.total(
            "screening_refusals_total", refusal_reason=self.refusal_reason
        )
        return observed >= self.threshold


@dataclass(frozen=True, slots=True)
class AlertRule:
    """One alert, with the answer already written down."""

    name: str
    trigger: SLOBreachTrigger | RefusalTrigger
    severity: Severity
    #: Plain sentence naming the broken thing, for the body of the page.
    what_broke: str
    #: Who notices, in customer terms. "Nobody yet" is a valid and useful answer.
    blast_radius: str
    #: The first move that is safe before the cause is known.
    first_mitigation: str
    runbook_anchor: str
    #: Where a page goes first. ``None`` for a ticket, which is never delivered — it
    #: waits in the operational issue queue for whoever is looking at the queue.
    primary_route: DeliveryRoute | None = None
    #: The second path, used only when the first refuses. Required for a page, and its
    #: dependencies may not overlap the primary's, or one outage silences both.
    fallback_route: DeliveryRoute | None = None

    @property
    def watched_service(self) -> str:
        return self.trigger.watched_service

    @property
    def delivered(self) -> bool:
        """Whether this rule sends anything at all. Only pages do."""

        return self.severity == "page"


@dataclass(frozen=True, slots=True)
class FiredAlert:
    """An alert rule that is currently firing, with its measured value."""

    rule: AlertRule
    measured: float | None
    rules_version: str = ALERT_RULES_VERSION

    @property
    def page_worthy(self) -> bool:
        return self.rule.severity == "page"


ALERT_RULES: Final[tuple[AlertRule, ...]] = (
    AlertRule(
        name="api_unavailable",
        trigger=SLOBreachTrigger("api_availability"),
        severity="page",
        what_broke="The API is returning server errors above the availability objective.",
        blast_radius="Every signed-in customer and the public site.",
        first_mitigation=(
            "Check database and Redis health first; if both are healthy, roll back to "
            "the previous release."
        ),
        runbook_anchor="#api-availability",
        primary_route="ops_telegram",
        fallback_route="ops_email",
    ),
    AlertRule(
        name="api_slow",
        trigger=SLOBreachTrigger("api_latency_p95"),
        severity="ticket",
        what_broke="The slowest twentieth of requests is past the latency objective.",
        blast_radius="Customers see slow pages; nothing is lost or wrong.",
        first_mitigation="Check provider latency and queue depth before changing the API.",
        runbook_anchor="#api-latency",
    ),
    AlertRule(
        name="setup_chat_failing",
        trigger=SLOBreachTrigger("setup_chat_turn_success"),
        severity="page",
        what_broke="Setup Chat turns are failing above the objective.",
        blast_radius=(
            "Customers cannot build or change a Watchlist. Approved monitors keep "
            "running and keep alerting."
        ),
        first_mitigation=(
            "Set SETUP_CHAT_EMERGENCY_DISABLED=true so turns stop cleanly with an "
            "AI-unavailable banner instead of failing mid-turn."
        ),
        runbook_anchor="#setup-chat-turn-failures",
        primary_route="ops_telegram",
        fallback_route="ops_email",
    ),
    AlertRule(
        name="setup_chat_slow",
        trigger=SLOBreachTrigger("setup_chat_latency_p95"),
        severity="ticket",
        what_broke="Setup Chat turns are past the twelve-second latency objective.",
        blast_radius="Chat feels slow. Nothing is lost and no monitor is affected.",
        first_mitigation="Check provider latency before changing routing or timeouts.",
        runbook_anchor="#setup-chat-latency",
    ),
    AlertRule(
        name="ai_provider_degraded",
        trigger=SLOBreachTrigger("ai_provider_success"),
        severity="page",
        what_broke="The AI provider is refusing or failing calls above the objective.",
        blast_radius=(
            "Setup Chat and the public assistant are affected. Screening, the "
            "compiler and approved monitors are not."
        ),
        first_mitigation=(
            "Confirm the circuit breaker is open and the AI-unavailable banner is "
            "showing. Do not disable screening."
        ),
        runbook_anchor="#ai-provider-degraded",
        primary_route="ops_telegram",
        fallback_route="ops_email",
    ),
    AlertRule(
        name="scans_delayed",
        trigger=SLOBreachTrigger("scheduled_scan_completion"),
        severity="page",
        what_broke="Scheduled scans are not finishing before their next due time.",
        blast_radius="Monitors evaluate late, so alerts arrive late or not at all.",
        first_mitigation="Check worker liveness and queue depth before re-queueing anything.",
        runbook_anchor="#scans-delayed",
        primary_route="ops_telegram",
        fallback_route="ops_email",
    ),
    AlertRule(
        name="market_data_stale",
        trigger=SLOBreachTrigger("market_data_freshness"),
        severity="page",
        what_broke="The newest candle is older than the freshness objective.",
        blast_radius=(
            "Confirmed alerts are blocked by the fail-closed staleness check, so "
            "customers stop receiving alerts rather than receiving wrong ones."
        ),
        first_mitigation="Check the exchange connection; do not relax the staleness check.",
        runbook_anchor="#market-data-stale",
        primary_route="ops_telegram",
        fallback_route="ops_email",
    ),
    AlertRule(
        name="alert_delivery_failing",
        trigger=SLOBreachTrigger("alert_delivery_success"),
        severity="page",
        what_broke="Alert deliveries are being rejected by their channel.",
        blast_radius="Customers stop receiving alerts they have already earned.",
        first_mitigation=(
            "Check the Telegram bot token and webhook secret first; deliveries retry, "
            "so nothing is lost yet."
        ),
        runbook_anchor="#alert-delivery-failing",
        # Never Telegram: this rule watches the delivery pipeline Telegram rides on,
        # so the one message that must arrive would be the one that cannot.
        primary_route="ops_email",
        fallback_route="system_brain",
    ),
    AlertRule(
        name="email_outbox_backed_up",
        trigger=SLOBreachTrigger("email_outbox_drain_p95"),
        severity="ticket",
        what_broke="Queued email is taking longer than the drain objective to send.",
        blast_radius="Sign-in codes and payment receipts arrive late.",
        first_mitigation="Check SMTP credentials and the retry task before re-queueing.",
        runbook_anchor="#email-outbox-backed-up",
    ),
    AlertRule(
        name="worker_or_scheduler_down",
        trigger=SLOBreachTrigger("worker_liveness"),
        severity="page",
        what_broke="A worker or the scheduler has stopped sending heartbeats.",
        blast_radius=(
            "Nothing scheduled runs: no scans, no retries, no reminders. Customers "
            "see no error, only silence."
        ),
        first_mitigation="Restart the scheduler container; verify one heartbeat before leaving.",
        runbook_anchor="#worker-or-scheduler-down",
        # Never email: the outbox is drained by the worker this rule watches.
        primary_route="ops_telegram",
        fallback_route="system_brain",
    ),
    AlertRule(
        name="review_case_overdue",
        trigger=SLOBreachTrigger("review_case_sla"),
        severity="ticket",
        what_broke="A Shariah review case is past its service-level window.",
        blast_radius=(
            "No customer-visible change. Assets stay unpublished, which is the "
            "fail-closed behaviour."
        ),
        first_mitigation="Assign the case to the on-duty reviewer. Never publish to clear it.",
        runbook_anchor="#review-case-overdue",
    ),
    AlertRule(
        name="screening_refusing_everything",
        trigger=RefusalTrigger(refusal_reason="no_active_passport", threshold=50),
        severity="page",
        what_broke=(
            "Screened-universe resolution is refusing symbols for a missing active "
            "Passport at an abnormal rate."
        ),
        blast_radius=(
            "Customers see an empty or much smaller screened market. No wrong "
            "religious status is shown; the layer is failing closed as designed."
        ),
        first_mitigation=(
            "Check whether a methodology version was archived or a publication was "
            "rolled back. Never widen the universe to clear the alert."
        ),
        runbook_anchor="#screening-refusing-everything",
        primary_route="ops_telegram",
        fallback_route="ops_email",
    ),
)


def _route_errors(rule: AlertRule) -> list[str]:
    """Everything wrong with where this rule says it will send itself.

    A ticket must not name a route at all: it is not delivered, and a route on it
    would read like a promise the system does not keep. A page must name two, and the
    two must be able to fail independently — a primary and a fallback that share a
    dependency are one route wearing two names, and the outage that breaks one breaks
    the other at the same moment.
    """

    errors: list[str] = []
    if not rule.delivered:
        if rule.primary_route is not None or rule.fallback_route is not None:
            errors.append(
                f"Alert {rule.name!r} is ticket-worthy but names a delivery route. "
                "Tickets wait in the operational issue queue and are not delivered."
            )
        return errors
    if rule.primary_route is None:
        errors.append(f"Alert {rule.name!r} is page-worthy but names no delivery route")
        return errors
    if rule.fallback_route is None:
        errors.append(
            f"Alert {rule.name!r} is page-worthy but names no fallback route, so one "
            "outage in the wrong place silences it completely"
        )
        return errors
    primary = DELIVERY_ROUTE_DEPENDENCIES.get(rule.primary_route)
    fallback = DELIVERY_ROUTE_DEPENDENCIES.get(rule.fallback_route)
    if primary is None:
        errors.append(f"Alert {rule.name!r} uses unknown route {rule.primary_route!r}")
    if fallback is None:
        errors.append(f"Alert {rule.name!r} uses unknown route {rule.fallback_route!r}")
    if primary is None or fallback is None:
        return errors
    if rule.primary_route == rule.fallback_route:
        errors.append(
            f"Alert {rule.name!r} names {rule.primary_route!r} as both its primary and "
            "its fallback, which is one route, not two"
        )
    for label, route, dependencies in (
        ("primary", rule.primary_route, primary),
        ("fallback", rule.fallback_route, fallback),
    ):
        if rule.watched_service in dependencies:
            errors.append(
                f"Alert {rule.name!r} watches {rule.watched_service!r} but its {label} "
                f"route {route!r} depends on it"
            )
    shared = primary & fallback
    if shared:
        errors.append(
            f"Alert {rule.name!r} has a primary and a fallback that both depend on "
            f"{sorted(shared)}, so a single failure there silences both"
        )
    if rule.primary_route == "system_brain":
        errors.append(
            f"Alert {rule.name!r} pages through {rule.primary_route!r}, which only "
            "reaches somebody already looking at the admin surface"
        )
    return errors


def validate_alert_rules(
    rules: tuple[AlertRule, ...] = ALERT_RULES,
) -> None:
    """Raise unless every rule can do what it claims.

    Called at startup, so a rule that could never deliver stops the deployment rather
    than being discovered during the incident it was written for.
    """

    errors: list[str] = []
    seen: set[str] = set()
    slo_names = {slo.name for slo in SLOS}
    for rule in rules:
        if rule.name in seen:
            errors.append(f"Duplicate alert rule name {rule.name!r}")
        seen.add(rule.name)
        if isinstance(rule.trigger, SLOBreachTrigger):
            if rule.trigger.slo_name not in slo_names:
                errors.append(
                    f"Alert {rule.name!r} watches unknown objective "
                    f"{rule.trigger.slo_name!r}"
                )
                continue
            declared = slo_by_name(rule.trigger.slo_name).severity_on_breach
            if declared != rule.severity:
                errors.append(
                    f"Alert {rule.name!r} is {rule.severity!r} but objective "
                    f"{rule.trigger.slo_name!r} declares {declared!r} on breach"
                )
        errors.extend(_route_errors(rule))
        for text, field_name in (
            (rule.what_broke, "what_broke"),
            (rule.blast_radius, "blast_radius"),
            (rule.first_mitigation, "first_mitigation"),
        ):
            if not text.strip():
                errors.append(f"Alert {rule.name!r} has an empty {field_name}")
        if not rule.runbook_anchor.startswith("#"):
            errors.append(f"Alert {rule.name!r} has no runbook anchor")
    if errors:
        raise AlertRuleError("Unsafe alert rules:\n- " + "\n- ".join(errors))


def evaluate_alert_rules(recorder: MetricsRecorder) -> tuple[FiredAlert, ...]:
    """Every rule currently firing, most severe first."""

    evaluations = {item.slo.name: item for item in evaluate_all_slos(recorder)}
    fired: list[FiredAlert] = []
    for rule in ALERT_RULES:
        if isinstance(rule.trigger, SLOBreachTrigger):
            if rule.trigger.fires(evaluations):
                evaluation = evaluations[rule.trigger.slo_name]
                fired.append(FiredAlert(rule=rule, measured=evaluation.measured))
        elif rule.trigger.fires_from(recorder):
            fired.append(
                FiredAlert(
                    rule=rule,
                    measured=recorder.total(
                        "screening_refusals_total",
                        refusal_reason=rule.trigger.refusal_reason,
                    ),
                )
            )
    fired.sort(key=lambda item: (0 if item.page_worthy else 1, item.rule.name))
    return tuple(fired)

"""What a customer is told when part of the product is degraded.

Driven by the same objectives the on-call alerts read. There is no second,
hand-maintained status list: a banner that has to be switched on by a person is
switched on late and switched off early, and it disagrees with the alert that
already fired.

Two rules shape every message here.

**Name what still works.** "Something went wrong" makes a customer assume
everything is broken. Almost never true in this product: when the AI provider is
down, approved Watchlists keep evaluating and keep alerting, because none of that
path touches a model. Saying so is the difference between a bad hour and a lost
customer.

**Never blame the wrong subsystem.** A provider outage must never render as a
Shariah, screening or compiler failure. Those refusals mean something exact — the
evidence is missing, the meaning could not be represented — and borrowing one of
them to explain an unrelated outage teaches the customer something false about the
part that was borrowed. Each banner below therefore names its own cause, and the
AI and provider banners say in as many words that screening is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ai_market_monitor.observability.metrics import MetricsRecorder
from ai_market_monitor.observability.slos import SLOEvaluation, evaluate_all_slos

__all__ = [
    "BANNER_DEFINITIONS",
    "BannerKind",
    "StatusBanner",
    "banner_for_ai_disabled",
    "customer_status_banners",
]


class BannerKind(StrEnum):
    AI_UNAVAILABLE = "ai_unavailable"
    PROVIDER_DEGRADED = "provider_degraded"
    SCANS_DELAYED = "scans_delayed"
    DELIVERY_DELAYED = "delivery_delayed"
    SCREENING_DATA_STALE = "screening_data_stale"


@dataclass(frozen=True, slots=True)
class StatusBanner:
    """One degradation message, in words a beginner can act on."""

    kind: BannerKind
    headline: str
    #: Named explicitly. This is the half that stops a customer assuming the worst.
    still_works: str
    paused: str
    #: ``warning`` for reduced service, ``info`` for slower but complete service.
    tone: str

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "headline": self.headline,
            "still_works": self.still_works,
            "paused": self.paused,
            "tone": self.tone,
        }


#: The message for each degradation, written once.
#:
#: Keyed by the objective that detects it, so a banner cannot exist without a
#: measurement behind it and a measurement cannot silently lose its banner.
BANNER_DEFINITIONS: Final[dict[str, StatusBanner]] = {
    "ai_provider_success": StatusBanner(
        kind=BannerKind.PROVIDER_DEGRADED,
        headline="The assistant is having trouble right now.",
        still_works=(
            "Your saved Watchlists are still being checked, and alerts are still "
            "being sent. Screening and your approved rules are not affected."
        ),
        paused="Building or changing rules by chat may fail or be slow.",
        tone="warning",
    ),
    "setup_chat_turn_success": StatusBanner(
        kind=BannerKind.AI_UNAVAILABLE,
        headline="Setup chat is not available at the moment.",
        still_works=(
            "Everything you already approved keeps running. Alerts keep arriving. "
            "Nothing you saved has been changed or lost."
        ),
        paused="Writing new rules by chat is paused. Please try again shortly.",
        tone="warning",
    ),
    "scheduled_scan_completion": StatusBanner(
        kind=BannerKind.SCANS_DELAYED,
        headline="Market checks are running late.",
        still_works="Your rules and your Watchlists are unchanged.",
        paused="Alerts may arrive later than usual until this clears.",
        tone="warning",
    ),
    "market_data_freshness": StatusBanner(
        kind=BannerKind.SCREENING_DATA_STALE,
        headline="Market data is older than we allow.",
        still_works=(
            "Your rules are unchanged. We would rather send you nothing than send "
            "you an alert based on old prices."
        ),
        paused="Confirmed alerts are held until fresh data returns.",
        tone="warning",
    ),
    "alert_delivery_success": StatusBanner(
        kind=BannerKind.DELIVERY_DELAYED,
        headline="Some alerts are taking longer to reach you.",
        still_works=(
            "Your Watchlists are still being checked, and every alert is saved in "
            "the app even if the message is late."
        ),
        paused="Telegram messages may be delayed. They are retried automatically.",
        tone="info",
    ),
}


def banner_for_ai_disabled() -> StatusBanner:
    """The banner shown when AI is switched off by configuration, not by failure.

    A deliberate switch and an outage look identical to a customer, so they get the
    same honest message. What must never happen is either of them surfacing as a
    screening or compiler error.
    """

    return BANNER_DEFINITIONS["setup_chat_turn_success"]


def customer_status_banners(
    recorder: MetricsRecorder,
    *,
    ai_enabled: bool = True,
) -> tuple[StatusBanner, ...]:
    """Every banner a customer should see right now.

    A breached objective produces its banner. An objective with no data produces
    nothing: no traffic is not evidence of a problem, and a banner shown on an idle
    morning teaches customers to ignore banners.
    """

    banners: list[StatusBanner] = []
    seen: set[BannerKind] = set()
    if not ai_enabled:
        disabled_banner = banner_for_ai_disabled()
        banners.append(disabled_banner)
        seen.add(disabled_banner.kind)
    evaluations: tuple[SLOEvaluation, ...] = evaluate_all_slos(recorder)
    for evaluation in evaluations:
        if not evaluation.breached:
            continue
        banner = BANNER_DEFINITIONS.get(evaluation.slo.name)
        if banner is None or banner.kind in seen:
            continue
        banners.append(banner)
        seen.add(banner.kind)
    return tuple(banners)

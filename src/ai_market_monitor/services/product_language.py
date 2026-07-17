from dataclasses import dataclass

from ai_market_monitor.db.models.enums import SetupLifecycleState


@dataclass(frozen=True)
class LifecyclePresentation:
    label: str
    explanation: str
    semantic_status: str


_LIFECYCLE_PRESENTATION: dict[SetupLifecycleState, LifecyclePresentation] = {
    SetupLifecycleState.CANDIDATE_DETECTED: LifecyclePresentation(
        "Detected", "HilalMarkets found the first matching market check.", "information"
    ),
    SetupLifecycleState.DETECTED: LifecyclePresentation(
        "Detected", "HilalMarkets found the first matching market check.", "information"
    ),
    SetupLifecycleState.FORMING: LifecyclePresentation(
        "Forming", "Some required market checks are complete.", "information"
    ),
    SetupLifecycleState.NEAR_CONFIRMATION: LifecyclePresentation(
        "Getting closer", "Only a small number of required checks remain.", "warning"
    ),
    SetupLifecycleState.ARMED: LifecyclePresentation(
        "Ready for review", "The approved required checks are complete.", "success"
    ),
    SetupLifecycleState.CONFIRMED: LifecyclePresentation(
        "Ready for review", "The approved required checks are complete.", "success"
    ),
    SetupLifecycleState.ALERT_SENT: LifecyclePresentation(
        "Alert sent", "HilalMarkets delivered the evidence-backed alert.", "success"
    ),
    SetupLifecycleState.BLOCKED: LifecyclePresentation(
        "Paused", "A policy or required-data check prevented progress.", "warning"
    ),
    SetupLifecycleState.DATA_UNAVAILABLE: LifecyclePresentation(
        "Data unavailable", "HilalMarkets could not verify a required market check.", "warning"
    ),
    SetupLifecycleState.SUPPRESSED: LifecyclePresentation(
        "Ended", "The opportunity completed without a new notification.", "neutral"
    ),
    SetupLifecycleState.INVALIDATED: LifecyclePresentation(
        "Ended", "An invalidation rule ended this opportunity.", "danger"
    ),
    SetupLifecycleState.EXPIRED: LifecyclePresentation(
        "Ended", "The approved time window ended.", "neutral"
    ),
    SetupLifecycleState.COMPLETED: LifecyclePresentation(
        "Ended", "This opportunity journey is complete.", "neutral"
    ),
    SetupLifecycleState.CLOSED: LifecyclePresentation(
        "Ended", "This opportunity journey is closed.", "neutral"
    ),
    SetupLifecycleState.MANUALLY_CLOSED: LifecyclePresentation(
        "Ended", "The user closed this opportunity journey.", "neutral"
    ),
}

_TRADE_CONTEXT_STATES = {
    SetupLifecycleState.ENTRY_ACTIVE,
    SetupLifecycleState.ENTRY_ZONE_ACTIVE,
    SetupLifecycleState.ENTRY_TOUCHED,
    SetupLifecycleState.ENTRY_ZONE_MISSED,
    SetupLifecycleState.ENTRY_MISSED,
    SetupLifecycleState.TARGET_1_REACHED,
    SetupLifecycleState.TARGET_2_REACHED,
    SetupLifecycleState.TARGET_REACHED,
    SetupLifecycleState.STOP_REACHED,
    SetupLifecycleState.STOP_LEVEL_REACHED,
}


def lifecycle_presentation(state: SetupLifecycleState | str) -> LifecyclePresentation:
    parsed = state if isinstance(state, SetupLifecycleState) else SetupLifecycleState(state)
    if parsed in _TRADE_CONTEXT_STATES:
        return LifecyclePresentation(
            "Forming",
            "Optional user-defined trade context is being tracked.",
            "information",
        )
    return _LIFECYCLE_PRESENTATION.get(
        parsed,
        LifecyclePresentation("Forming", "Market checks are being evaluated.", "information"),
    )


def readiness_copy(score: float, state: SetupLifecycleState | str) -> str:
    presentation = lifecycle_presentation(state)
    readiness = max(0, min(100, round(float(score))))
    if presentation.label == "Ended" and readiness == 100:
        return "Peak readiness 100%; Status: Ended"
    return f"{readiness}% ready"


def product_term(term: str) -> str:
    return {
        "strategy": "Watch Plan",
        "candidate": "Opportunity",
        "lifecycle": "Opportunity journey",
        "partial_match": "Forming",
        "near_miss": "Getting closer",
        "conditions_complete": "Ready for review",
        "alert_delivered": "Alert sent",
        "no_longer_matching": "Ended",
        "expired": "Ended",
        "blocker": "What is still missing",
        "rule_evaluation": "Market check",
        "universe": "Screened market",
        "alert_proof": "Why you received this alert",
        "missed_alert": "Why didn't this alert happen?",
        "completion_score": "Readiness",
    }.get(term, term)

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ai_market_monitor.core.plans import timeframe_to_minutes
from ai_market_monitor.db.models.enums import SetupLifecycleState


@dataclass(frozen=True)
class LifecyclePresentation:
    label: str
    explanation: str
    semantic_status: str


_LIFECYCLE_PRESENTATION: dict[SetupLifecycleState, LifecyclePresentation] = {
    SetupLifecycleState.CANDIDATE_DETECTED: LifecyclePresentation(
        "Detected", "Hilal Markets found the first matching market check.", "information"
    ),
    SetupLifecycleState.DETECTED: LifecyclePresentation(
        "Detected", "Hilal Markets found the first matching market check.", "information"
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
        "Alert sent", "Hilal Markets delivered the evidence-backed alert.", "success"
    ),
    SetupLifecycleState.BLOCKED: LifecyclePresentation(
        "Paused", "A policy or required-data check prevented progress.", "warning"
    ),
    SetupLifecycleState.DATA_UNAVAILABLE: LifecyclePresentation(
        "Data unavailable", "Hilal Markets could not verify a required market check.", "warning"
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


#: What a whole Watchlist is doing, in the words a person reads.
#:
#: The live page worked this out inside its own template — one Jinja expression deciding
#: that a draft with an approved version counts as active, and another turning the raw
#: value into title case. That put a product decision in a template, where nothing can
#: test it and the next page to need it would decide differently. It lives here with
#: every other plain word this product uses.
_WATCHLIST_PRESENTATION: dict[str, LifecyclePresentation] = {
    "active": LifecyclePresentation(
        "Watching",
        "This list is checking the market on its own.",
        "success",
    ),
    "paused": LifecyclePresentation(
        "Paused",
        "You stopped this one. It keeps everything and can start again whenever you like.",
        "warning",
    ),
    "draft": LifecyclePresentation(
        "Not finished",
        "This one is not watching anything yet. Finish it to turn it on.",
        "information",
    ),
    "archived": LifecyclePresentation(
        "Put away",
        "Out of the way, with everything it recorded kept.",
        "neutral",
    ),
}


def watchlist_presentation(
    status: str, *, has_approved_version: bool = False
) -> LifecyclePresentation:
    """What one Watchlist is doing, and what that means, in plain words.

    A draft that already has an approved version behind it is watching the market —
    the word "draft" describes how it was made, not what it is doing now, and showing
    it to a person is showing them our filing system.
    """

    key = str(status or "").lower()
    if key == "draft" and has_approved_version:
        key = "active"
    return _WATCHLIST_PRESENTATION.get(
        key,
        LifecyclePresentation(
            "Not finished",
            "This one is not watching anything yet.",
            "information",
        ),
    )


@dataclass(frozen=True)
class MarketCheckingNotice:
    """What a page says when the platform itself is not checking the market."""

    title: str
    detail: str
    tone: str


#: Said when live scanning is switched off, which stops every monitor of every person at
#: once.
#:
#: This is a deployment switch, so no page may hint that the person did something wrong,
#: and none may imply that waiting will help. The words never name the setting either:
#: `SCANNING_ENABLED` is a word from inside the machine.
_NOT_CHECKING = MarketCheckingNotice(
    title="We are not checking the market right now.",
    detail=(
        "This is switched off on our side. It is not something you did. Your monitors "
        "keep every rule you approved, and they start looking again as soon as it is "
        "back on."
    ),
    tone="warning",
)


def market_checking_notice(*, scanning_enabled: bool) -> MarketCheckingNotice | None:
    """The notice every page must carry, or `None` when the market really is checked.

    One owner, because the fact is one fact. The front page says it in its headline band
    and the Monitors page says it in a banner; both read it from here, so the two cannot
    grow into two different accounts of the same silence.
    """

    return None if scanning_enabled else _NOT_CHECKING


#: The "done" messages that promise the market is being checked from this moment on.
#:
#: Keyed by the code the dashboard already uses, so the template still looks a message up
#: by its code and no page decides for itself which ones are affected. Somebody published
#: a monitor, read "It is checking the market now", and then found "Not looked yet" on the
#: card below — the same screen disagreeing with itself, with the untrue half on top.
_CHECKING_CLAIMS: dict[str, str] = {
    "monitor_published": (
        "Your monitor is on and it keeps every rule you approved. "
        "We are not checking the market right now, so it has not started looking yet. "
        "It starts on its own as soon as we are."
    ),
    "monitor_resumed": (
        "Your monitor is on again. "
        "We are not checking the market right now, so it has not started looking yet. "
        "It starts on its own as soon as we are."
    ),
}


def checking_message_overrides(*, scanning_enabled: bool) -> dict[str, str]:
    """Which "done" messages must be replaced, because they would promise a check.

    Empty whenever the market really is being checked, so the ordinary path keeps the
    ordinary words and nothing has to be remembered at the call site.
    """

    return {} if scanning_enabled else dict(_CHECKING_CLAIMS)


def first_check_words(*, scanning_enabled: bool, check_started: bool) -> str:
    """Why a list has not checked the market yet.

    Three different reasons, and only one of them ends on its own. "This list has not
    checked the market for the first time" reads as *soon*, and soon is not true while
    the platform is not checking at all — a person waited on a first check that nothing
    was ever going to run.
    """

    if not scanning_enabled:
        return (
            "Hilal Markets is not checking the market at the moment. "
            "Nothing is wrong with this list."
        )
    if check_started:
        return "The first check of the market is running now."
    return "This list has not checked the market for the first time."


def how_long_ago(moment: datetime | None, *, now: datetime | None = None) -> str:
    """When something happened, in the words a person would use.

    "3 hours ago" is something anybody can act on. "2026-08-16 02:57:28 UTC" is a
    timestamp: correct, and useless to the person it is shown to. Exact times still
    belong on an evidence record, where the exact time is the point.
    """

    if moment is None:
        return "Not yet"
    at = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    seconds = int(((now or datetime.now(UTC)) - at).total_seconds())
    if seconds < 0:
        return "Just now"
    if seconds < 90:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} {'hour' if hours == 1 else 'hours'} ago"
    days = hours // 24
    if days < 7:
        return f"{days} {'day' if days == 1 else 'days'} ago"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks} {'week' if weeks == 1 else 'weeks'} ago"
    months = days // 30
    if months < 12:
        return f"{months} {'month' if months == 1 else 'months'} ago"
    years = days // 365
    return f"{years} {'year' if years == 1 else 'years'} ago"


#: The second set of words for the one question "what is this opportunity doing".
#:
#: Two vocabularies grew for the same fact. The lifecycle enum records one of them;
#: the readiness snapshot records the other as free text, written by its own rules in
#: `setup_observability._candidate_state`. The live Opportunities page read both and
#: showed both, so one coin appeared twice on one screen — "Confirmation pending,
#: 4/5 required rules passed" near the top and "Getting closer, 80% ready" further
#: down — with nothing saying they were the same thing.
#:
#: This is the one resolution rule. Whichever of the two wrote a state, every reader
#: resolves it here, so the two can never be presented as two different things again.
_READINESS_STATE: dict[str, SetupLifecycleState] = {
    "not_started": SetupLifecycleState.DETECTED,
    "forming": SetupLifecycleState.FORMING,
    "near_miss": SetupLifecycleState.NEAR_CONFIRMATION,
    "confirmation_pending": SetupLifecycleState.NEAR_CONFIRMATION,
    "confirmed": SetupLifecycleState.CONFIRMED,
    "invalidated": SetupLifecycleState.INVALIDATED,
    "expired": SetupLifecycleState.EXPIRED,
    "provider_data_error": SetupLifecycleState.DATA_UNAVAILABLE,
}


def opportunity_state(value: SetupLifecycleState | str | None) -> SetupLifecycleState | None:
    """The one state behind a word, whichever of the two vocabularies wrote it.

    ``None`` when the word belongs to neither. A display that cannot name a state must
    say so rather than guess the nearest one: an invented status is exactly the kind of
    quiet wrong answer this page exists to avoid.
    """

    if isinstance(value, SetupLifecycleState):
        return value
    key = str(value or "").strip().lower()
    if not key:
        return None
    if key in _READINESS_STATE:
        return _READINESS_STATE[key]
    try:
        return SetupLifecycleState(key)
    except ValueError:
        return None


@dataclass(frozen=True)
class OpportunityPresentation:
    """What one opportunity is doing, for somebody who has never traded.

    ``kind`` is what the card *is*, and it decides the shape the card takes rather than
    only its colour. "We could not check it" is its own kind, not a low score: the live
    page drew a failed data read as "0/5 required rules passed" with an empty bar, which
    reads as "this failed" when the truth is "we never found out".
    """

    label: str
    meaning: str
    semantic_status: str
    kind: str


_SPOTTED = OpportunityPresentation(
    "Just spotted",
    "This coin has matched the first thing on your list.",
    "information",
    "forming",
)
_FORMING = OpportunityPresentation(
    "Still forming",
    "Some of the things on your list are true. Not all of them yet.",
    "information",
    "forming",
)
_CLOSE = OpportunityPresentation(
    "Nearly there",
    "Almost everything on your list is true. A small part is still missing.",
    "warning",
    "close",
)
_READY = OpportunityPresentation(
    "Ready for you",
    "Everything on your list is true. It is yours to look at — Hilal Markets never buys "
    "or sells anything.",
    "success",
    "ready",
)
_TOLD = OpportunityPresentation(
    "We told you",
    "Everything on your list became true and your message was sent.",
    "success",
    "ready",
)
_UNCHECKED = OpportunityPresentation(
    "We could not check it",
    "The market numbers we needed did not arrive. This is not a pass and not a fail.",
    "warning",
    "unchecked",
)

_OPPORTUNITY_PRESENTATION: dict[SetupLifecycleState, OpportunityPresentation] = {
    SetupLifecycleState.CANDIDATE_DETECTED: _SPOTTED,
    SetupLifecycleState.DETECTED: _SPOTTED,
    SetupLifecycleState.FORMING: _FORMING,
    SetupLifecycleState.NEAR_CONFIRMATION: _CLOSE,
    SetupLifecycleState.ARMED: _READY,
    SetupLifecycleState.CONFIRMED: _READY,
    SetupLifecycleState.ALERT_SENT: _TOLD,
    SetupLifecycleState.DATA_UNAVAILABLE: _UNCHECKED,
    SetupLifecycleState.BLOCKED: OpportunityPresentation(
        "Stopped for now",
        "A screening rule or a missing check stopped this one going any further.",
        "warning",
        "unchecked",
    ),
    SetupLifecycleState.INVALIDATED: OpportunityPresentation(
        "Finished",
        "Something on your list stopped being true, so this one ended.",
        "neutral",
        "ended",
    ),
    SetupLifecycleState.EXPIRED: OpportunityPresentation(
        "Finished",
        "The time you allowed for this one ran out.",
        "neutral",
        "ended",
    ),
    SetupLifecycleState.SUPPRESSED: OpportunityPresentation(
        "Finished",
        "This one ended without a new message being sent to you.",
        "neutral",
        "ended",
    ),
    SetupLifecycleState.MANUALLY_CLOSED: OpportunityPresentation(
        "Finished",
        "You closed this one yourself.",
        "neutral",
        "ended",
    ),
    SetupLifecycleState.COMPLETED: OpportunityPresentation(
        "Finished",
        "This one reached its end without anything on your list breaking.",
        "neutral",
        "ended",
    ),
    SetupLifecycleState.CLOSED: OpportunityPresentation(
        "Finished",
        "This one is closed. Nothing more will happen with it.",
        "neutral",
        "ended",
    ),
}

#: What the platform says when it cannot name a state at all.
#:
#: Never a guess at the nearest one. A person reading "Still forming" about something
#: we did not recognise has been told something we do not know to be true.
UNKNOWN_OPPORTUNITY = OpportunityPresentation(
    "We cannot say yet",
    "We do not have a clear reading for this one right now.",
    "neutral",
    "unchecked",
)


def opportunity_presentation(
    state: SetupLifecycleState | str | None,
) -> OpportunityPresentation:
    """What one opportunity is doing, in words a beginner already knows."""

    resolved = opportunity_state(state)
    if resolved is None:
        return UNKNOWN_OPPORTUNITY
    if resolved in _TRADE_CONTEXT_STATES:
        # Optional trade context a person added themselves. It is still forming; it is
        # not a separate status, and showing it as one put two status words on one card.
        return _FORMING
    return _OPPORTUNITY_PRESENTATION.get(resolved, UNKNOWN_OPPORTUNITY)


@dataclass(frozen=True)
class CheckPresentation:
    label: str
    semantic_status: str


_CHECK_PRESENTATION: dict[str, CheckPresentation] = {
    "passed": CheckPresentation("This is true", "success"),
    "failed": CheckPresentation("Not true yet", "warning"),
    "pending": CheckPresentation("Still waiting", "information"),
    "unavailable": CheckPresentation("We could not read it", "neutral"),
    "error": CheckPresentation("We could not read it", "neutral"),
}


def check_presentation(outcome: str | None) -> CheckPresentation:
    """What one thing on a person's list is doing, in three words.

    "Could not read" is deliberately its own answer and never "not true yet". They are
    opposite facts, and a page that draws them the same way tells somebody their rule
    failed when nobody ever looked.
    """

    return _CHECK_PRESENTATION.get(
        str(outcome or "").strip().lower(),
        CheckPresentation("We could not read it", "neutral"),
    )


def number_in_words(value: object) -> str | None:
    """One market number, written the way it is read.

    Four decimal places at most, with trailing zeros removed, so 1.5 stays "1.5" and a
    price keeps the digits that matter. ``None`` when the value is not a number: an
    empty space says "we have nothing", and a zero would say something false.
    """

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    return None


def how_often(timeframe: str | None) -> str:
    """How often a list looks at a coin, said the way a person says it.

    "15m" is our shorthand for the size of a candle. A beginner reads it as a typo. How
    many minutes a timeframe is worth is decided once, in `core/plans.py`; this only
    turns that number into words, so the two can never disagree about what "4h" means.
    """

    try:
        minutes = timeframe_to_minutes(str(timeframe or "").strip())
    except (ValueError, IndexError):
        return ""
    if minutes < 1:
        return ""
    if minutes < 60:
        return "Every minute" if minutes == 1 else f"Every {minutes} minutes"
    hours = minutes // 60
    if hours < 24:
        return "Every hour" if hours == 1 else f"Every {hours} hours"
    days = hours // 24
    return "Every day" if days == 1 else f"Every {days} days"


def checks_in_words(passed: int, total: int) -> str:
    """How much of somebody's own list is true, counted rather than scored.

    "3 of 5 things you asked for are true" is a fact a person can act on. "60% ready"
    is a number about a number, and the live page showed both for the same coin.
    """

    if total <= 0:
        return "Nothing to check yet"
    passed = max(0, min(passed, total))
    if total == 1:
        return f"{passed} of 1 thing you asked for is true"
    return f"{passed} of {total} things you asked for are true"


def gap_in_words(
    *,
    outcome: str | None,
    saw: object = None,
    wanted: object = None,
    distance: object = None,
) -> str:
    """Why one check is not true yet, with its numbers explained.

    The live page printed "Current: 1.27 - Required: 1.5 - Distance: 0.23" and left the
    reader to work out which was which. Nothing here says "higher" or "lower": the
    distance is stored without a direction, and a guessed direction would be an
    invented fact about somebody's own rule.
    """

    if str(outcome or "").strip().lower() in {"unavailable", "error"}:
        return "We could not read this number, so there is nothing to compare yet."
    saw_words = number_in_words(saw)
    wanted_words = number_in_words(wanted)
    if saw_words is None or wanted_words is None:
        return ""
    sentence = f"You asked for {wanted_words}. Right now it is {saw_words}."
    distance_words = number_in_words(distance)
    if distance_words is not None and float(distance) > 0:  # type: ignore[arg-type]
        sentence += f" That is {distance_words} away."
    return sentence


@dataclass(frozen=True)
class WhyNoMessage:
    headline: str
    meaning: str
    what_to_do: str
    semantic_status: str


#: Why nobody was told, in words instead of a category name.
#:
#: Which of these is true is decided by `SetupObservabilityService.investigation`, from
#: the evidence it kept. Nothing is worked out again here — a second opinion about why
#: a message was not sent would be a second answer, free to disagree with the first.
_WHY_NO_MESSAGE: dict[str, WhyNoMessage] = {
    "strategy_condition_failure": WhyNoMessage(
        "Not everything on your list was true",
        "Some of the things you asked for did not happen, so there was nothing to tell "
        "you about.",
        "Open “What did we see?” below. If your list is stricter than you meant, you can "
        "change it.",
        "information",
    ),
    "data_provider_issue": WhyNoMessage(
        "We could not read one of the numbers",
        "One thing on your list needs a market number that never arrived, so nobody "
        "could say whether it was true or not.",
        "There is nothing for you to fix. If this keeps happening on the same coin, "
        "tell us.",
        "warning",
    ),
    "notification_delivery_failure": WhyNoMessage(
        "We tried to send it and it did not arrive",
        "Everything on your list became true. The message left us, but the place we "
        "sent it to did not accept it.",
        "Check where you asked to be told, and send yourself a test message.",
        "danger",
    ),
    "cooldown_or_exclusion": WhyNoMessage(
        "We held the message back",
        "Everything became true, but one of your own settings said not to send another "
        "message at that moment.",
        "Look at how often you allow this list to message you.",
        "warning",
    ),
    "notification_not_attempted": WhyNoMessage(
        "There was nowhere to send it",
        "This one was ready to tell you about, but no way of being told was switched on.",
        "Turn on at least one way of being told.",
        "warning",
    ),
    "completed_without_alert": WhyNoMessage(
        "Nothing we kept shows your whole list becoming true",
        "There is no message for this one, and the evidence we kept does not show every "
        "required check passing.",
        "Open “What did we see?” below to read what was checked.",
        "neutral",
    ),
    "alert_delivered": WhyNoMessage(
        "You were told",
        "The message was sent for this one and it arrived.",
        "Nothing to do.",
        "success",
    ),
}

UNKNOWN_WHY = WhyNoMessage(
    "We cannot say why",
    "What we kept for this one does not explain it clearly enough for us to tell you.",
    "Nothing to do.",
    "neutral",
)


def why_no_message(category: str | None) -> WhyNoMessage:
    """Why nobody was told about an opportunity, for somebody who is not an engineer."""

    return _WHY_NO_MESSAGE.get(str(category or "").strip().lower(), UNKNOWN_WHY)


@dataclass(frozen=True)
class MessageKind:
    """One kind of message a person can be sent, in the words they read.

    The words are needed in three places at once — the email that arrives, the subject
    line on it, and the page where somebody chooses which kinds to receive. Written
    three times they drift, and then the page promises "near misses" while the email
    calls itself something else and neither matches the switch that turns it on.

    ``icon`` names an icon in the shared set, so the page and the email agree about
    which picture means which kind.

    ``category`` is the wider family this message belongs to — the two or three words a
    header chip shows so somebody can sort an inbox at a glance. It is deliberately not
    the label: the label says what happened to one coin ("Nearly there"), and printing
    that in the header as well as in the title and in the status band says the same
    short sentence three times in one screen.
    """

    label: str
    meaning: str
    semantic_status: str
    icon: str
    category: str = "Notice"


#: Every kind of message, keyed by the stored `AlertType` value.
#:
#: Keyed by the stored value rather than by the enum so this module stays free of the
#: alert machinery, and so a value read straight out of a row resolves without a lookup.
_MESSAGE_KINDS: dict[str, MessageKind] = {
    "compliance": MessageKind(
        "Shariah status changed",
        "A coin you are watching was reviewed again and its screening status moved.",
        "warning",
        "compliance",
        "Screening",
    ),
    "confirmed": MessageKind(
        "Everything you asked for happened",
        "Every condition on one of your lists became true for a coin.",
        "success",
        "check",
        "Market alert",
    ),
    "near_miss": MessageKind(
        "Nearly there",
        "A coin came close to everything on your list, but one part was still missing.",
        "warning",
        "spark",
        "Market alert",
    ),
    "forming": MessageKind(
        "Something started forming",
        "Part of what you asked for became true for a coin.",
        "information",
        "clock",
        "Market alert",
    ),
    "lifecycle": MessageKind(
        "Something changed",
        "An opportunity you were already watching moved to a different stage.",
        "information",
        "history",
        "Market alert",
    ),
    "failure": MessageKind(
        "A check could not run",
        "We could not read the market numbers one of your lists needed.",
        "warning",
        "alert",
        "Market alert",
    ),
    "trial": MessageKind(
        "About your account",
        "Something changed about your plan or your access.",
        "information",
        "user",
        "Your account",
    ),
}

#: What we say about a message whose kind we do not recognise.
#:
#: Never the nearest kind. Telling somebody "Nearly there" about a message we could not
#: name is telling them something we do not know to be true.
UNKNOWN_MESSAGE_KIND = MessageKind(
    "A message from Hilal Markets",
    "Something happened that one of your lists was watching for.",
    "neutral",
    "bell",
)


def message_kind(alert_type: object) -> MessageKind:
    """What kind of message this is, in words a beginner already knows."""

    value = getattr(alert_type, "value", alert_type)
    return _MESSAGE_KINDS.get(str(value or "").strip().lower(), UNKNOWN_MESSAGE_KIND)


def every_message_kind() -> list[MessageKind]:
    """Every kind, in the order a person meets them.

    Used by the page that explains what will arrive. It reads this rather than listing
    the kinds itself, so a kind added to the product cannot go missing from the page
    that promises it.
    """

    return list(_MESSAGE_KINDS.values())


def product_term(term: str) -> str:
    return {
        "strategy": "Watchlist",
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
        "universe": "Halal Assets",
        "alert_proof": "Why you received this alert",
        "missed_alert": "Why didn't this alert happen?",
        "completion_score": "Readiness",
    }.get(term, term)

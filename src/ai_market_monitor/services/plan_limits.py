"""What a plan limit means when somebody reaches it, in words they can act on.

Every limit in this product can stop a person, and until now each place that could stop
somebody wrote its own sentence — or wrote none at all. Three examples, all real:

* Reaching the active-monitor limit raised ``active_strategy_limit``. The page drawing
  the refusal held a table of words that did not contain that code, so a beginner who hit
  their plan's limit was told "The monitor could not be started, so nothing was saved" —
  true, and useless.
* The strategy-approval limit said "Plan allows 2 strategies per 30 days". That is the
  limits table read out loud, not an answer: it does not say what to do next.
* The plan's own daily message budget stopped alerts and said nothing at all, anywhere.
  A monitor simply went quiet.

So there is one module. It owns, for each limit: the machine code it is raised under, the
thing being limited in plain words, the sentence a person reads, and what to do next. The
gate raises it, the page prints it, the pop-up shows it, and the message that goes to
Telegram or email is built from the same object.

**The number always comes from the plan**, never from the sentence. A limit written into
a sentence is a second copy of the number, free to disagree with the one that stopped
somebody — which is the fault this codebase keeps finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PLAN_RANK,
    PURCHASABLE_PLAN_CODES,
    STRATEGY_APPROVAL_WINDOW_DAYS,
    UNLIMITED_SYMBOL_CAP,
    plan_name,
    plan_rank,
    smallest_plan_with,
)

__all__ = [
    "LIMIT_CODES",
    "PlanLimit",
    "feature_needs_a_bigger_plan",
    "next_plan_up",
    "plan_limit_for",
    "plan_limit_notice",
]

#: What each gated feature is called when a refusal has to name it. Plain words, never a
#: field name: ``missed_alert_investigations`` means nothing to a beginner.
FEATURE_WORDS: Final[dict[str, str]] = {
    "missed_alert_investigations": "Why wasn't I alerted?",
    "condition_proof": "Condition-level proof",
    "advanced_forensics": "Advanced investigation tools",
    "setup_lifecycle": "Opportunity Journeys",
    "near_miss": "Near-miss history",
    "custom_webhooks": "Custom webhooks",
    "api_access": "The API",
}


def feature_needs_a_bigger_plan(feature: str) -> str:
    """The sentence somebody reads when a feature is not on their plan.

    Two routes refusing "Why wasn't I alerted?" each had this sentence typed into them
    with the plan named by hand, and both kept saying "the Monitor plan" long after the
    plan was renamed to Plus. The feature's words and the plan's name are both read from
    the catalog here, so neither can go stale in a refusal again.
    """

    words = FEATURE_WORDS.get(feature, feature.replace("_", " ").capitalize())
    code = smallest_plan_with(feature)
    if code is None:
        return f"{words} is not available on any plan right now."
    return f"{words} is available on the {plan_name(code)} plan."


def next_plan_up(plan_code: str) -> str | None:
    """The cheapest plan that is bigger than this one, or ``None`` at the top.

    Used to end every refusal with something a person can actually do. Reading the order
    from :data:`PLAN_RANK` rather than naming a plan means renaming or reordering the
    plans does not leave a refusal pointing at a plan that no longer exists.
    """

    here = plan_rank(plan_code)
    above = sorted(
        (code for code in PURCHASABLE_PLAN_CODES if PLAN_RANK.get(code, 0) > here),
        key=plan_rank,
    )
    return above[0] if above else None


@dataclass(frozen=True, slots=True)
class PlanLimit:
    """One limit, and everything anybody needs to say about reaching it."""

    #: The machine code the gate raises it under. Stored on records and read by pages.
    code: str
    #: The key in the plan's limits table that holds the number, or ``None`` for a limit
    #: that is a capability rather than a count.
    limit_key: str | None
    #: The thing being limited, in the words a person would use. Never a field name.
    feature: str
    #: How the allowance reads once the number is filled in, e.g. "3 at once".
    allowance_shape: str
    #: What to do about it, apart from moving up a plan.
    what_to_do: str


#: Every limit that can stop somebody, keyed by the code it is raised under.
LIMIT_CODES: Final[dict[str, PlanLimit]] = {
    "active_strategy_limit": PlanLimit(
        code="active_strategy_limit",
        limit_key="active_strategies",
        feature="market monitors running at once",
        allowance_shape="{allowed} at once",
        what_to_do="Pause a monitor you are not using, and this one can start.",
    ),
    "strategy_approval_limit": PlanLimit(
        code="strategy_approval_limit",
        limit_key="strategy_approvals_per_30_days",
        feature="new monitors approved",
        allowance_shape=f"{{allowed}} every {STRATEGY_APPROVAL_WINDOW_DAYS} days",
        what_to_do=(
            f"The count looks back {STRATEGY_APPROVAL_WINDOW_DAYS} days, so it frees up "
            "again on its own. Changing a monitor you already approved is always allowed."
        ),
    ),
    "symbol_limit": PlanLimit(
        code="symbol_limit",
        limit_key="symbols_per_strategy",
        feature="coins in one monitor",
        allowance_shape="{allowed} in one monitor",
        what_to_do="Take some coins off this monitor, or split it into two.",
    ),
    "alerts_per_day_limit": PlanLimit(
        code="alerts_per_day_limit",
        limit_key="alerts_per_day",
        feature="monitor messages a day",
        allowance_shape="{allowed} a day",
        what_to_do=(
            "Your monitors keep watching and everything they find is kept. New messages "
            "start again tomorrow."
        ),
    ),
    "alerts_per_week_limit": PlanLimit(
        code="alerts_per_week_limit",
        limit_key="alerts_per_week",
        feature="monitor messages a week",
        allowance_shape="{allowed} a week",
        what_to_do=(
            "Your monitors keep watching and everything they find is kept. New messages "
            "start again next week."
        ),
    ),
    "timeframe_not_allowed": PlanLimit(
        code="timeframe_not_allowed",
        limit_key="minimum_timeframe_minutes",
        feature="how often a monitor may look",
        allowance_shape="every {allowed} minutes at the fastest",
        what_to_do="Choose a slower candle for this monitor.",
    ),
}


def plan_limit_for(code: str) -> PlanLimit | None:
    """The limit behind one refusal code, or ``None`` for a code that is not a limit."""

    return LIMIT_CODES.get(code)


def _allowance_words(limit: PlanLimit, *, plan_code: str, allowed: int | None) -> str:
    number = allowed
    if number is None and limit.limit_key is not None:
        raw = PLAN_DEFINITIONS[plan_code].limits.get(limit.limit_key)
        number = int(raw) if raw is not None else None
    if number is None or number >= UNLIMITED_SYMBOL_CAP:
        return "as many as you like"
    return limit.allowance_shape.format(allowed=number)


def plan_limit_notice(
    code: str,
    *,
    plan_code: str,
    allowed: int | None = None,
) -> str:
    """The whole sentence a person reads when this limit stops them.

    Three parts, always in this order: what was reached, what the plan allows, and what
    to do next. A refusal with no third part leaves a beginner with nowhere to go, which
    is what every one of these used to be.

    ``allowed`` is the number the gate actually counted against. It is passed in rather
    than looked up whenever the gate has it, so the sentence can never quote a different
    figure from the one that stopped somebody — a frozen monitor limit, for instance, can
    be tighter than the plan's.
    """

    limit = plan_limit_for(code)
    plan = PLAN_DEFINITIONS.get(plan_code)
    if limit is None or plan is None:
        return "Your plan does not allow that."
    allowance = _allowance_words(limit, plan_code=plan_code, allowed=allowed)
    bigger = next_plan_up(plan_code)
    move_up = f" {plan_name(bigger)} allows more." if bigger is not None else ""
    return (
        f"You have reached your limit of {limit.feature}. "
        f"{plan_name(plan_code)} allows {allowance}. {limit.what_to_do}{move_up}"
    )

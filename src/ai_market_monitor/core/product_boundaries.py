"""What the product does, what it does not do yet, and what it will never do.

Every surface that answers "can it do X" reads this file: the dashboard, the public
pages, the public assistant and Setup Chat. None of them keeps its own list. A
capability described one way on the pricing page and another way in chat is the
failure this registry exists to prevent, and it is a failure a customer discovers
only after trusting the wrong answer.

Three rules hold here and are tested:

*An unsupported request is refused by name.* :func:`refuse` produces an object that
names the capability that is missing. It never proposes a nearby one. Substituting
"you asked for stop-loss orders, here is an alert instead" is how a person ends up
believing the product is protecting money it cannot touch.

*An unsupported capability is never dressed up as something else.* A missing feature
is not a Shariah refusal, not a compiler error and not a provider outage. Each of
those has its own meaning, and borrowing one of them to explain a gap in the product
teaches the customer something false about the part that borrowed it.

*The four non-negotiable statements are absolute.* HilalMarkets does not execute
trades, is not a broker, gives no buy or sell recommendations, and provides no
financial advice. They hold in every stage, on every surface, in every language.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "BOUNDARY_REGISTRY",
    "BOUNDARY_REGISTRY_VERSION",
    "BoundaryEntry",
    "EVALUATION_MODES",
    "EvaluationMode",
    "EvaluationModeDefinition",
    "NON_NEGOTIABLE_BOUNDARIES",
    "SupportState",
    "UnsupportedCapability",
    "boundary_for",
    "evaluation_mode",
    "refuse",
    "supported_capabilities",
    "unsupported_capabilities",
]

#: Bumped whenever an entry is added, removed or changes support state. Customer
#: answers quote it, so an answer given last month stays traceable to the boundary
#: list that produced it.
BOUNDARY_REGISTRY_VERSION: Final[str] = "2026-08-12.1"


class SupportState(StrEnum):
    """Whether the product does something, might later, or never will."""

    SUPPORTED = "supported"
    NOT_YET_SUPPORTED = "not_yet_supported"
    OUT_OF_SCOPE = "out_of_scope"


#: The four statements that hold no matter what else changes.
#:
#: Written for a beginner and kept short enough to render whole. They are not
#: legal boilerplate to be hidden in a footer: they are the product definition, and
#: a person deciding whether to trust the tool needs them in plain words.
NON_NEGOTIABLE_BOUNDARIES: Final[tuple[str, ...]] = (
    "Hilal Markets never buys or sells anything for you.",
    "Hilal Markets is not a broker and does not hold your money or your coins.",
    "Hilal Markets never tells you to buy or sell.",
    "Hilal Markets does not give financial advice.",
)


@dataclass(frozen=True, slots=True)
class BoundaryEntry:
    """One capability, and the plain reason it is where it is."""

    key: str
    title: str
    support: SupportState
    #: Written for the customer, not for an engineer. Every entry has one, including
    #: supported ones: "yes" without "and here is what that means" is how a feature
    #: gets believed to do more than it does.
    reason: str

    @property
    def is_supported(self) -> bool:
        return self.support is SupportState.SUPPORTED


BOUNDARY_REGISTRY: Final[tuple[BoundaryEntry, ...]] = (
    # -- What the product does today. --------------------------------------
    BoundaryEntry(
        key="screened_asset_list",
        title="Screened asset list",
        support=SupportState.SUPPORTED,
        reason=(
            "You can see which assets passed Shariah screening, with the source, the "
            "methodology, its version and the date the decision was made."
        ),
    ),
    BoundaryEntry(
        key="rule_building",
        title="Building your own rules",
        support=SupportState.SUPPORTED,
        reason=(
            "You describe the setup in your own words. The product turns it into rules "
            "you can read and check before anything starts."
        ),
    ),
    BoundaryEntry(
        key="scanner_run",
        title="Checking the market now",
        support=SupportState.SUPPORTED,
        reason=(
            "You can run your rules against the market once, when you ask. It answers "
            "and stops."
        ),
    ),
    BoundaryEntry(
        key="monitor_run",
        title="Watching the market for you",
        support=SupportState.SUPPORTED,
        reason=(
            "After you approve your rules, the product keeps checking them and tells "
            "you when they are met."
        ),
    ),
    BoundaryEntry(
        key="alert_delivery",
        title="Alerts in the app and on Telegram",
        support=SupportState.SUPPORTED,
        reason=(
            "When your rules are met you get a message, with the exact numbers that "
            "made it happen."
        ),
    ),
    BoundaryEntry(
        key="evidence_passport",
        title="Evidence Passport",
        support=SupportState.SUPPORTED,
        reason=(
            "Every screened asset has a record showing the authority, the Shariah "
            "methodology, its version and the decision date."
        ),
    ),
    # -- Not built yet. A date is never promised here. ----------------------
    BoundaryEntry(
        key="backtesting",
        title="Testing rules against past market data",
        support=SupportState.NOT_YET_SUPPORTED,
        reason=(
            "Not built yet. You can check the market now and you can watch it going "
            "forward, but you cannot yet replay your rules over past months."
        ),
    ),
    BoundaryEntry(
        key="whatsapp_alerts",
        title="Alerts on WhatsApp",
        support=SupportState.NOT_YET_SUPPORTED,
        reason=(
            "Not switched on yet. Alerts arrive in the app and on Telegram today."
        ),
    ),
    BoundaryEntry(
        key="portfolio_tracking",
        title="Tracking what you own",
        support=SupportState.NOT_YET_SUPPORTED,
        reason=(
            "Not built yet. The product watches the market, and does not know what "
            "you hold."
        ),
    ),
    BoundaryEntry(
        key="stocks_and_forex",
        title="Shares, funds and currencies",
        support=SupportState.NOT_YET_SUPPORTED,
        reason="Not covered yet. Today the product covers crypto spot markets only.",
    ),
    # -- Never. These are product definition, not a backlog. -----------------
    BoundaryEntry(
        key="trade_execution",
        title="Placing orders for you",
        support=SupportState.OUT_OF_SCOPE,
        reason=(
            "Hilal Markets never buys or sells anything for you. It watches and tells "
            "you; the decision and the order are always yours."
        ),
    ),
    BoundaryEntry(
        key="brokerage_custody",
        title="Holding your money or your coins",
        support=SupportState.OUT_OF_SCOPE,
        reason=(
            "Hilal Markets is not a broker and never holds your funds. You keep "
            "everything where it already is."
        ),
    ),
    BoundaryEntry(
        key="buy_sell_recommendations",
        title="Telling you what to buy or sell",
        support=SupportState.OUT_OF_SCOPE,
        reason=(
            "The product reports whether the rules you wrote were met. It never says "
            "an asset is a good or bad thing to own."
        ),
    ),
    BoundaryEntry(
        key="financial_advice",
        title="Financial advice",
        support=SupportState.OUT_OF_SCOPE,
        reason=(
            "Nothing here is advice about your money or your situation. Speak to a "
            "qualified person for that."
        ),
    ),
    BoundaryEntry(
        key="leverage_and_margin",
        title="Leverage, margin and borrowing",
        support=SupportState.OUT_OF_SCOPE,
        reason=(
            "The product covers spot markets only. It does not support borrowed money "
            "or leveraged positions."
        ),
    ),
    BoundaryEntry(
        key="guaranteed_returns",
        # Named without the banned phrase itself. The copy lint cannot tell a claim
        # from a denial of that claim, and a rule that has to guess is a rule that
        # will one day guess wrong in the permissive direction.
        title="Promises about profit",
        support=SupportState.OUT_OF_SCOPE,
        reason=(
            "No one can promise a result in a market. The product shows evidence, "
            "never a promise."
        ),
    ),
    BoundaryEntry(
        key="ai_religious_ruling",
        title="A religious ruling written by the assistant",
        support=SupportState.OUT_OF_SCOPE,
        reason=(
            "The assistant can never decide or guess whether an asset is acceptable. "
            "Shariah status comes only from a named authority through the review "
            "process, and it always carries its methodology and decision date."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class UnsupportedCapability:
    """An explicit refusal that names what is missing.

    Carries no substitute. A ``suggested_alternative`` field is exactly the hook that
    turns "we cannot do that" into "we did something else instead", so it does not
    exist.
    """

    key: str
    title: str
    support: SupportState
    reason: str
    registry_version: str = BOUNDARY_REGISTRY_VERSION

    @property
    def is_permanent(self) -> bool:
        return self.support is SupportState.OUT_OF_SCOPE

    def customer_message(self) -> str:
        """The whole answer a customer sees, naming the capability."""

        return f"{self.title}: {self.reason}"


def boundary_for(key: str) -> BoundaryEntry:
    for entry in BOUNDARY_REGISTRY:
        if entry.key == key:
            return entry
    raise KeyError(f"Unknown product boundary {key!r}")


def supported_capabilities() -> tuple[BoundaryEntry, ...]:
    return tuple(entry for entry in BOUNDARY_REGISTRY if entry.is_supported)


def unsupported_capabilities() -> tuple[BoundaryEntry, ...]:
    return tuple(entry for entry in BOUNDARY_REGISTRY if not entry.is_supported)


def refuse(key: str) -> UnsupportedCapability:
    """The refusal object for a capability the product does not have.

    Raises on a supported capability rather than producing a polite refusal for
    something that works: that would mean a caller and this registry disagree about
    what the product does, and answering anyway would hide the disagreement.
    """

    entry = boundary_for(key)
    if entry.is_supported:
        raise ValueError(
            f"{key!r} is a supported capability and must not be refused. "
            "A refusal for a working feature means the caller is reading the wrong key."
        )
    return UnsupportedCapability(
        key=entry.key,
        title=entry.title,
        support=entry.support,
        reason=entry.reason,
    )


class EvaluationMode(StrEnum):
    """The two ways rules are ever run. They are not variants of one thing."""

    SCANNER = "scanner"
    MONITOR = "monitor"


@dataclass(frozen=True, slots=True)
class EvaluationModeDefinition:
    """One mode, stated the same way everywhere it is offered.

    Four fields, all required, because a surface that offers one of these without
    saying when it runs or what it costs leaves the customer to guess — and the two
    modes are easiest to confuse exactly where the guess is most expensive.
    """

    mode: EvaluationMode
    label: str
    #: What starts it.
    trigger: str
    #: How often it runs after that.
    cadence: str
    #: What it uses from the customer's plan.
    cost_note: str
    #: The sentence that keeps the two apart. Always phrased as what it does *not* do.
    does_not: str
    requires_approval: bool


EVALUATION_MODES: Final[dict[EvaluationMode, EvaluationModeDefinition]] = {
    EvaluationMode.SCANNER: EvaluationModeDefinition(
        mode=EvaluationMode.SCANNER,
        label="Check the market now",
        trigger="You press the button.",
        cadence="Runs once, then stops.",
        cost_note="Uses one check from your daily allowance each time you press it.",
        does_not=(
            "It does not keep watching after it answers, and it will not send you "
            "anything later."
        ),
        requires_approval=False,
    ),
    EvaluationMode.MONITOR: EvaluationModeDefinition(
        mode=EvaluationMode.MONITOR,
        label="Watch the market for me",
        trigger="You approve your rules, then turn the Watchlist on.",
        cadence="Keeps checking on its own until you pause it.",
        cost_note="Uses one of your active Watchlist places while it is on.",
        does_not=(
            "It does not buy or sell, and it does not change your rules. It tells you "
            "when what you wrote came true."
        ),
        requires_approval=True,
    ),
}


def evaluation_mode(mode: EvaluationMode) -> EvaluationModeDefinition:
    return EVALUATION_MODES[mode]

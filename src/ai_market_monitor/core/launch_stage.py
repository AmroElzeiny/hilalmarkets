"""One server-owned answer to "how open is this product right now".

Before this module the answer was a single boolean, ``PUBLIC_WAITLIST_MODE``, read
directly in fifteen places. That worked while there were exactly two states. It
cannot express the state the product is actually in — built, not yet open to
outside people — and it gave every reader its own chance to disagree about what
"pre-launch" hides.

So the stage is named, ordered, and owned here. Each stage declares what it exposes;
no route, template or assistant decides for itself. Adding a surface means adding it
to :data:`STAGE_EXPOSURE`, where its behaviour in all four stages is visible at once.

**The environment is a ceiling, not an opinion.** ``PUBLIC_WAITLIST_MODE=true`` caps
exposure at :attr:`LaunchStage.PUBLIC_WAITLIST` no matter what stage is configured.
The cap is applied by :func:`resolve_launch_stage` and reported through
:attr:`ResolvedStage.clamped_by_environment`, so an emergency brake takes effect
immediately and still shows up as a disagreement rather than a silent downgrade.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "ALLOWED_STAGE_TRANSITIONS",
    "LaunchStage",
    "ResolvedStage",
    "STAGE_EXPOSURE",
    "StageExposure",
    "StageTransitionError",
    "assert_transition_allowed",
    "resolve_launch_stage",
    "stage_exposure",
]


class LaunchStage(StrEnum):
    """How widely the product is open, from closed to fully public."""

    INTERNAL = "internal"
    PRIVATE_BETA_INVITE = "private_beta_invite"
    PUBLIC_WAITLIST = "public_waitlist"
    PUBLIC_LAUNCH = "public_launch"


#: Narrowest first. Position in this tuple *is* the exposure ordering, which is what
#: lets an environment ceiling be applied by comparison rather than by a table of
#: special cases.
STAGE_ORDER: Final[tuple[LaunchStage, ...]] = (
    LaunchStage.INTERNAL,
    LaunchStage.PRIVATE_BETA_INVITE,
    LaunchStage.PUBLIC_WAITLIST,
    LaunchStage.PUBLIC_LAUNCH,
)


class StageTransitionError(ValueError):
    """A launch-stage move that is not allowed."""


#: Which stage may follow which.
#:
#: Widening is one step at a time: going straight from internal to public launch
#: would skip the two states whose entire purpose is to be found wanting first.
#: Narrowing, by contrast, is allowed from anywhere to anywhere — pulling the product
#: back is an emergency action, and an emergency must never be blocked by a state
#: machine written on a calm afternoon.
ALLOWED_STAGE_TRANSITIONS: Final[dict[LaunchStage, frozenset[LaunchStage]]] = {
    LaunchStage.INTERNAL: frozenset({LaunchStage.PRIVATE_BETA_INVITE}),
    LaunchStage.PRIVATE_BETA_INVITE: frozenset(
        {LaunchStage.INTERNAL, LaunchStage.PUBLIC_WAITLIST}
    ),
    LaunchStage.PUBLIC_WAITLIST: frozenset(
        {LaunchStage.INTERNAL, LaunchStage.PRIVATE_BETA_INVITE, LaunchStage.PUBLIC_LAUNCH}
    ),
    LaunchStage.PUBLIC_LAUNCH: frozenset(
        {
            LaunchStage.INTERNAL,
            LaunchStage.PRIVATE_BETA_INVITE,
            LaunchStage.PUBLIC_WAITLIST,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class StageExposure:
    """Exactly what one stage shows, offers and permits."""

    stage: LaunchStage
    #: Pricing page and plan comparison are reachable and advertised.
    advertises_pricing: bool
    #: Checkout may be started. Never true unless pricing is advertised, and still
    #: subject to ``BILLING_ENABLED`` — this says the stage permits it, not that the
    #: payment provider is configured.
    exposes_checkout: bool
    #: "Sign in" / "Start free" calls to action appear. The routes keep working when
    #: this is false; they are simply not advertised, so an invited person can still
    #: use the link they were sent.
    advertises_account_entry: bool
    #: The waitlist form is shown as the primary way in.
    shows_waitlist: bool
    #: Public pages removed from menus, sitemap and assistant answers.
    hidden_pages: frozenset[str]
    #: Delivery channels the product may offer a customer at this stage.
    offered_channels: frozenset[str]
    #: Whether the public assistant may send an anonymous visitor to an account page.
    assistant_may_offer_account: bool
    primary_cta_label: str
    primary_cta_href: str


#: Pages that only make sense once anyone can open an account. Named once so the
#: three narrower stages cannot drift apart from each other.
_PRE_LAUNCH_HIDDEN_PAGES: Final[frozenset[str]] = frozenset({"pricing", "screened_market"})

STAGE_EXPOSURE: Final[dict[LaunchStage, StageExposure]] = {
    LaunchStage.INTERNAL: StageExposure(
        stage=LaunchStage.INTERNAL,
        advertises_pricing=False,
        exposes_checkout=False,
        advertises_account_entry=False,
        # No form at all. Internal means the product is not asking the public for
        # anything yet, and a waitlist that collects addresses nobody will act on is
        # a promise the product cannot keep.
        shows_waitlist=False,
        hidden_pages=_PRE_LAUNCH_HIDDEN_PAGES,
        offered_channels=frozenset({"in_app"}),
        assistant_may_offer_account=False,
        primary_cta_label="Contact the team",
        primary_cta_href="/contact",
    ),
    LaunchStage.PRIVATE_BETA_INVITE: StageExposure(
        stage=LaunchStage.PRIVATE_BETA_INVITE,
        advertises_pricing=False,
        exposes_checkout=False,
        advertises_account_entry=False,
        shows_waitlist=False,
        hidden_pages=_PRE_LAUNCH_HIDDEN_PAGES,
        offered_channels=frozenset({"in_app", "telegram"}),
        assistant_may_offer_account=False,
        primary_cta_label="Contact the team",
        primary_cta_href="/contact",
    ),
    LaunchStage.PUBLIC_WAITLIST: StageExposure(
        stage=LaunchStage.PUBLIC_WAITLIST,
        advertises_pricing=False,
        exposes_checkout=False,
        advertises_account_entry=False,
        shows_waitlist=True,
        hidden_pages=_PRE_LAUNCH_HIDDEN_PAGES,
        offered_channels=frozenset({"in_app", "telegram"}),
        assistant_may_offer_account=False,
        primary_cta_label="Join the waitlist",
        primary_cta_href="/#waitlist",
    ),
    LaunchStage.PUBLIC_LAUNCH: StageExposure(
        stage=LaunchStage.PUBLIC_LAUNCH,
        advertises_pricing=True,
        exposes_checkout=True,
        advertises_account_entry=True,
        shows_waitlist=False,
        hidden_pages=frozenset(),
        offered_channels=frozenset({"in_app", "telegram"}),
        assistant_may_offer_account=True,
        primary_cta_label="Start free",
        primary_cta_href="/signup",
    ),
}


@dataclass(frozen=True, slots=True)
class ResolvedStage:
    """The stage actually in force, and whether the environment narrowed it."""

    configured: LaunchStage
    effective: LaunchStage
    clamped_by_environment: bool

    @property
    def exposure(self) -> StageExposure:
        return STAGE_EXPOSURE[self.effective]


def stage_exposure(stage: LaunchStage) -> StageExposure:
    return STAGE_EXPOSURE[stage]


def resolve_launch_stage(
    configured: LaunchStage,
    *,
    waitlist_ceiling: bool,
) -> ResolvedStage:
    """Apply the environment ceiling to the configured stage.

    ``waitlist_ceiling`` is ``PUBLIC_WAITLIST_MODE``. While it is on, the product may
    be no more open than :attr:`LaunchStage.PUBLIC_WAITLIST`, whatever the stage says.
    It only ever narrows: a ceiling that could widen exposure would be a second
    authority, which is the thing this module exists to remove.
    """

    if not waitlist_ceiling:
        return ResolvedStage(
            configured=configured, effective=configured, clamped_by_environment=False
        )
    ceiling = LaunchStage.PUBLIC_WAITLIST
    if STAGE_ORDER.index(configured) <= STAGE_ORDER.index(ceiling):
        return ResolvedStage(
            configured=configured, effective=configured, clamped_by_environment=False
        )
    return ResolvedStage(
        configured=configured, effective=ceiling, clamped_by_environment=True
    )


def assert_transition_allowed(current: LaunchStage, target: LaunchStage) -> None:
    """Raise unless the product may move from ``current`` to ``target``."""

    if current == target:
        raise StageTransitionError(
            f"The launch stage is already {current.value!r}."
        )
    allowed = ALLOWED_STAGE_TRANSITIONS[current]
    if target not in allowed:
        raise StageTransitionError(
            f"A launch stage cannot move from {current.value!r} to {target.value!r}. "
            f"Allowed: {sorted(stage.value for stage in allowed)}."
        )

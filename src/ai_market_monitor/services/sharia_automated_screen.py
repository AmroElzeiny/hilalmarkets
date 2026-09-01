"""The Hilal Markets automated screen: one owner for its vocabulary and its rule.

This module answers one question — *given the facts about what an asset does, would an
external authority that applies the Fasset boundary treat it as eligible?* — and it is
the only place in the product allowed to answer it.

**What this is not.** It is not a fatwa, not a scholar's opinion, and not a Shariah
status of the kind the three imported authorities carry. No person reviewed its output.
It is a reproducible reading of the boundary visible in 240 assets that one external
authority has already labelled, expressed as rules a person can read and argue with. It
exists to *propose* candidates, and the product must always say so where a result is
shown.

**Where the rule came from.** Fasset publishes 188 assets it calls Shariah Compliant and
52 it calls Not Compliant. Those 240 labels are the only place in this product where
both sides of a real authority's line are visible; SC Malaysia and the Shariah Review
Bureau publish accepted assets only, so neither can tell you what a rejection looks
like. The blocking reasons below were derived from the training half of that set and
then measured, once, against a held-out half that took no part in writing them.

**The boundary, in plain terms.** Four things block an asset, and one distinction does
most of the work:

    Earning from *lending money* is riba and blocks.
    Earning from *doing work* — validating, staking, providing a service — does not.

That is why ``AAVE``, ``COMP`` and ``GHO`` are blocked while ``LDO``, ``rETH`` and
``EIGEN`` are not, even though a naive "it pays a yield" rule would refuse all six.

Facts in, verdict out. This module never fetches anything, never asks a model, and never
looks at a price. The facts it consumes are the ones the Passport enrichment pipeline
already produces per asset; a fact it has not been given is a refusal, never a guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ai_market_monitor.services.sharia_conditions import (
    Activity,
    HolderReturn,
    blocking_activities,
)

#: The pack methodology id and the system code this screen publishes under. It is a
#: methodology in its own right, deliberately never merged into an authority's result.
METHODOLOGY_PACKAGE_ID = "HILAL_MARKETS_AUTOMATED_SCREEN"
METHODOLOGY_SYSTEM_CODE = "HILAL_MARKETS_METHODOLOGY"
METHODOLOGY_DISPLAY_NAME = "Hilal Markets Methodology"

#: Said wherever a result from this screen is shown. Kept here, beside the rule, so the
#: warning and the thing it warns about can never drift apart.
AUTOMATED_DISCLOSURE = (
    "Built by Hilal Markets from a mix of published methodologies and AI research. "
    "No scholar has reviewed this result."
)


# What refuses an asset is **not a constant in this module**. It is
# :func:`sharia_conditions.blocking_activities`, derived from the conditions the product
# owner has approved, and read afresh on every call to :func:`screen`. There is therefore
# exactly one place where "what refuses a coin" is decided and exactly one place where
# that decision is recorded.
#
# A module-level copy used to live here. It was removed rather than kept as a
# convenience: a snapshot taken at import time is a second owner of a governed rule, and
# it goes stale the moment an approval changes. Import the function, call it.
#
# ``INTEREST_BEARING_HOLDING`` is deliberately unreachable through it. "It pays a return"
# and "the return is riba" are two different questions, and :class:`HolderReturn` is the
# single owner of the second one. Blocking on the activity as well made the module hold
# the same rule twice, and the two copies immediately disagreed: a blind run refused
# Chainlink, Polygon, Hedera, NEAR, stETH and rETH — every one of them paid for
# validation work — because "pays a return" had been allowed to mean riba on its own.
# The activity now only forces the question; :class:`HolderReturn` answers it.

#: Facts every asset must carry before the screen will answer at all. A missing fact is
#: a refusal, never a default — an asset nobody researched must not slip through as
#: eligible because its unknown fields happened to look harmless.
REQUIRED_FACTS: tuple[str, ...] = (
    "canonical_symbol",
    "activities",
)


#: Activities whose meaning is not settled until :class:`HolderReturn` is known. Both
#: are claims about a token that *looks* passive; the difference between them is the
#: whole question.
RETURN_SENSITIVE_ACTIVITIES: frozenset[Activity] = frozenset(
    {
        Activity.FULLY_BACKED_REDEEMABLE,
        Activity.INTEREST_BEARING_HOLDING,
    }
)

#: The reason a return blocks, said once. Kept beside :class:`HolderReturn` rather than
#: in :data:`BLOCKING_ACTIVITIES` so there is exactly one place that decides it.
INTEREST_RETURN_REASON = (
    "Holding the token pays a return that comes from lending or a promised rate, "
    "not from work performed."
)

#: A governance token is a claim on somebody else's business, so the business decides.
#:
#: The same blind run called Ethena, Yearn and Convex "platform access or governance".
#: Each is true and each is useless on its own: what those protocols *do* is lend and
#: pay yield. Naming the token's role while leaving its protocol undescribed used to
#: produce an eligible verdict for all three.
GOVERNANCE_ACTIVITIES: frozenset[Activity] = frozenset(
    {Activity.PLATFORM_ACCESS_OR_GOVERNANCE}
)


class Verdict(StrEnum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    #: The facts are incomplete. Neither a pass nor a refusal — a request for research.
    INSUFFICIENT_FACTS = "insufficient_facts"


@dataclass(frozen=True, slots=True)
class ScreenResult:
    canonical_symbol: str
    verdict: Verdict
    blocking_activities: tuple[Activity, ...] = ()
    reasons: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()

    @property
    def is_eligible(self) -> bool:
        return self.verdict is Verdict.ELIGIBLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_symbol": self.canonical_symbol,
            "verdict": self.verdict.value,
            "blocking_activities": [a.value for a in self.blocking_activities],
            "reasons": list(self.reasons),
            "missing_facts": list(self.missing_facts),
            "methodology": METHODOLOGY_SYSTEM_CODE,
            "human_reviewed": False,
            "disclosure": AUTOMATED_DISCLOSURE,
        }


@dataclass(frozen=True, slots=True)
class AssetFacts:
    """What research established about one asset. No verdict lives here."""

    canonical_symbol: str
    asset_name: str
    activities: frozenset[Activity] = field(default_factory=frozenset)
    #: What holding it pays, when that is in question. See :class:`HolderReturn`.
    holder_return: HolderReturn | None = None
    #: What the protocol behind a governance or access token actually does.
    governed_activities: frozenset[Activity] = field(default_factory=frozenset)
    notes: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> AssetFacts:
        symbol = str(payload.get("canonical_symbol") or "").strip()
        raw_return = payload.get("holder_return")
        holder_return: HolderReturn | None = None
        if raw_return:
            try:
                holder_return = HolderReturn(str(raw_return))
            except ValueError as exc:
                raise ValueError(
                    f"{symbol}: unknown holder_return {raw_return!r}"
                ) from exc
        return cls(
            canonical_symbol=symbol,
            asset_name=str(payload.get("asset_name") or "").strip(),
            activities=_activities(symbol, payload.get("activities")),
            holder_return=holder_return,
            governed_activities=_activities(symbol, payload.get("governed_activities")),
            notes=str(payload.get("notes") or ""),
        )


def _activities(symbol: str, raw: Any) -> frozenset[Activity]:
    activities: set[Activity] = set()
    for value in raw or []:
        try:
            activities.add(Activity(value))
        except ValueError as exc:
            raise ValueError(f"{symbol}: unknown activity {value!r}") from exc
    return frozenset(activities)


def screen(facts: AssetFacts) -> ScreenResult:
    """Apply the rule. Fail closed: an unanswered question is never a pass.

    Three ways to be refused, and they are not the same thing. A *blocked* asset was
    researched and the answer was no. An asset with :attr:`Verdict.INSUFFICIENT_FACTS`
    was not researched enough to ask — it goes back to the queue, and it must never be
    shown to anyone as eligible in the meantime.
    """

    missing: list[str] = []
    if not facts.canonical_symbol:
        missing.append("canonical_symbol")
    if not facts.activities:
        missing.append("activities")

    # A peg claim decides nothing until the yield question is answered.
    if facts.activities & RETURN_SENSITIVE_ACTIVITIES and facts.holder_return is None:
        missing.append("holder_return")
    # A governance token is a claim on a business nobody has described yet.
    if facts.activities & GOVERNANCE_ACTIVITIES and not (
        facts.governed_activities or facts.activities - GOVERNANCE_ACTIVITIES
    ):
        missing.append("governed_activities")

    if missing:
        return ScreenResult(
            canonical_symbol=facts.canonical_symbol,
            verdict=Verdict.INSUFFICIENT_FACTS,
            missing_facts=tuple(dict.fromkeys(missing)),
        )

    # What the protocol does counts against its token. A governance token cannot be
    # cleaner than the business it governs.
    considered = facts.activities | facts.governed_activities
    # Read at call time, not captured at import. The set of things that refuse a coin is
    # the owner's approved conditions, and a snapshot taken when the module loaded would
    # be a second copy of that decision — stale the moment an approval changed.
    refusing = blocking_activities()
    blocking = [
        activity
        for activity in Activity
        if activity in refusing and activity in considered
    ]
    reasons = [refusing[a] for a in blocking]

    if facts.holder_return is HolderReturn.FROM_LENDING_OR_PROMISE:
        blocking.append(Activity.INTEREST_BEARING_HOLDING)
        reasons.append(INTEREST_RETURN_REASON)

    if blocking:
        return ScreenResult(
            canonical_symbol=facts.canonical_symbol,
            verdict=Verdict.NOT_ELIGIBLE,
            blocking_activities=tuple(blocking),
            reasons=tuple(reasons),
        )
    return ScreenResult(
        canonical_symbol=facts.canonical_symbol,
        verdict=Verdict.ELIGIBLE,
    )


def screen_all(rows: Iterable[Mapping[str, Any]]) -> list[ScreenResult]:
    return [screen(AssetFacts.from_mapping(row)) for row in rows]

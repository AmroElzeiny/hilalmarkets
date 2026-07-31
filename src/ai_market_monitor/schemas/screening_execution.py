"""What the Sharia resolver actually permitted, carried forward as one object.

The screening gate used to answer a yes/no question. It resolved the universe, checked
that *something* survived, and then returned the **original, unscreened** definition. The
resolved symbols were discarded, so everything downstream — runtime preflight, the
preview, approval eligibility, the reply — worked on a different universe from the one
screening had approved.

That is the gap this module closes. :class:`ScreeningExecutionResult` carries the exact
permitted symbols plus the identity of the resolution that permitted them, so the same
governed object flows through preflight, review and approval, and an approval can later
be checked against the evidence it was granted on.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.schemas.strategy import StrategyDefinition


def symbol_set_hash(symbols: list[str]) -> str:
    """A stable identity for a set of markets, order-independent.

    Order-independent on purpose: `[BTC, ETH]` and `[ETH, BTC]` are the same universe,
    and a review must not be invalidated by the order a resolver happened to return.
    """
    canonical = sorted({item.strip().upper() for item in symbols if item.strip()})
    encoded = json.dumps(canonical, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


#: Which promise the platform is making about runtime availability.
#:
#: ``verified_all`` — every resolved symbol × required timeframe was checked.
#: ``policy_verified_runtime_fail_closed`` — the universe *policy* was approved and a
#: bounded sample was checked; per-symbol availability is decided at runtime and a
#: symbol that fails is skipped rather than silently traded.
#:
#: The distinction is displayed, stored and bound into approval, because the old code
#: checked one arbitrary symbol and reported the whole universe as runtime-ready.
PreflightContract = Literal[
    "verified_all",
    "policy_verified_runtime_fail_closed",
    "not_required",
]


class ScreeningExecutionResult(BaseModel):
    """The resolved execution universe, and the identity of the resolution."""

    model_config = ConfigDict(extra="forbid")

    #: The definition whose ``universe.include_symbols`` holds exactly the symbols the
    #: resolver permitted. This — not the original — is what everything downstream uses.
    secured_definition: StrategyDefinition
    resolution_snapshot_id: UUID | None = None
    resolution_snapshot_hash: str | None = Field(default=None, max_length=128)
    policy_hash: str | None = Field(default=None, max_length=128)
    resolved_at: datetime
    considered_symbols: list[str] = Field(default_factory=list, max_length=100000)
    included_symbols: list[str] = Field(default_factory=list, max_length=100000)
    excluded_symbols: list[str] = Field(default_factory=list, max_length=100000)
    methodology_id: UUID | None = None
    methodology_version: str | None = Field(default=None, max_length=64)
    #: The watchlist snapshot this resolution was built from, when the universe mode is
    #: an approved Favorites list. Content-addressed, never a timestamp.
    watchlist_snapshot_hash: str | None = Field(default=None, max_length=128)
    #: True when membership can change without any user edit, so a review can only bind
    #: to the policy plus this initial snapshot.
    dynamic_membership: bool = False

    @model_validator(mode="after")
    def validate_universe(self) -> ScreeningExecutionResult:
        secured = set(self.secured_definition.universe.include_symbols)
        permitted = set(self.included_symbols)
        if permitted and secured != permitted:
            raise ValueError(
                "secured_definition.universe.include_symbols must be exactly the "
                "symbols screening permitted"
            )
        return self

    @property
    def resolved_symbol_set_hash(self) -> str:
        """Content identity of the permitted market set."""
        return symbol_set_hash(self.included_symbols)

    def evidence(self) -> dict[str, object]:
        """The facts a reply or an approval record may cite."""
        return {
            "resolution_snapshot_id": (
                str(self.resolution_snapshot_id) if self.resolution_snapshot_id else None
            ),
            "resolution_snapshot_hash": self.resolution_snapshot_hash,
            "policy_hash": self.policy_hash,
            "resolved_at": self.resolved_at.isoformat(),
            "considered_count": len(self.considered_symbols),
            "included_count": len(self.included_symbols),
            "excluded_count": len(self.excluded_symbols),
            "included_symbols": self.included_symbols[:200],
            "methodology_id": str(self.methodology_id) if self.methodology_id else None,
            "methodology_version": self.methodology_version,
            "resolved_symbol_set_hash": self.resolved_symbol_set_hash,
            "watchlist_snapshot_hash": self.watchlist_snapshot_hash,
            "dynamic_membership": self.dynamic_membership,
        }


class PreflightManifest(BaseModel):
    """Exactly which market/timeframe pairs were checked, and under which promise.

    Hashed into the approval record. Without it, "runtime verified" was an unqualified
    claim that one sample symbol could satisfy.
    """

    model_config = ConfigDict(extra="forbid")

    contract: PreflightContract
    #: Every pair actually checked, as ``SYMBOL@timeframe``.
    verified_pairs: list[str] = Field(default_factory=list, max_length=100000)
    #: Symbols in the resolved universe that were **not** individually checked.
    unverified_symbols: list[str] = Field(default_factory=list, max_length=100000)
    required_timeframes: list[str] = Field(default_factory=list, max_length=20)
    symbol_cap: int = Field(default=0, ge=0)
    checked_at: datetime | None = None

    @property
    def manifest_hash(self) -> str:
        payload = {
            "contract": self.contract,
            "verified_pairs": sorted(self.verified_pairs),
            "unverified_symbols": sorted(self.unverified_symbols),
            "required_timeframes": sorted(self.required_timeframes),
            "symbol_cap": self.symbol_cap,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def describe(self) -> str:
        """One plain sentence a user can be shown, matching what was really done."""
        if self.contract == "not_required":
            return "No market-data check was needed for this draft."
        if self.contract == "verified_all":
            return (
                f"Checked all {len(self.verified_pairs)} market and timeframe "
                "combinations in this watchlist."
            )
        return (
            f"Checked {len(self.verified_pairs)} market and timeframe combinations as a "
            f"sample. This watchlist can change on its own, so each market is checked "
            f"again when it runs, and one that is unavailable is skipped rather than "
            f"guessed at."
        )


#: What each bound fact means in words a user understands, for the refusal message when
#: it moved between review and approval. Keyed by field name so the message and the check
#: can never drift apart.
_EVIDENCE_LABELS: dict[str, str] = {
    "screening_snapshot_hash": "the screening result",
    "screening_policy_hash": "the screening policy",
    "methodology_id": "the screening methodology",
    "methodology_version": "the version of the screening methodology",
    "resolved_symbol_set_hash": "the list of markets this setup will watch",
    "watchlist_snapshot_hash": "the Favorites list this setup uses",
    "provider_preflight_manifest_hash": "the market-data check",
    "preflight_contract": "what the market-data check promised",
}


class ReviewedScreeningEvidence(BaseModel):
    """The exact screening facts the user could see when they reviewed the setup.

    Approval used to bind only the compiled rules and the conversation. The universe was
    resolved again at approval time and the new answer was never compared with the old
    one, so a setup reviewed over eight markets could be approved over eleven — or over a
    different eleven — with nothing shown to the user and nothing recorded.

    Every field here is content-addressed. Storing it at review time and re-deriving it at
    approval time turns "the universe may have changed" from an assumption into a check.
    """

    model_config = ConfigDict(extra="forbid")

    screening_snapshot_id: str | None = Field(default=None, max_length=64)
    screening_snapshot_hash: str | None = Field(default=None, max_length=128)
    screening_policy_hash: str | None = Field(default=None, max_length=128)
    methodology_id: str | None = Field(default=None, max_length=64)
    methodology_version: str | None = Field(default=None, max_length=64)
    resolved_symbol_set_hash: str | None = Field(default=None, max_length=128)
    watchlist_snapshot_hash: str | None = Field(default=None, max_length=128)
    provider_preflight_manifest_hash: str | None = Field(default=None, max_length=128)
    preflight_contract: PreflightContract | None = None
    #: How many markets the user was shown. Displayed in the refusal, never compared —
    #: the hash is what decides.
    included_symbol_count: int = Field(default=0, ge=0)
    #: True when membership can move without a user edit, so the review binds to the
    #: policy plus this snapshot rather than to a frozen list.
    dynamic_membership: bool = False
    reviewed_at: datetime

    #: The facts that must still hold at approval. A change in any one of them means the
    #: user would be approving something other than what they read.
    BOUND_FIELDS: ClassVar[tuple[str, ...]] = (
        "screening_snapshot_hash",
        "screening_policy_hash",
        "methodology_id",
        "methodology_version",
        "resolved_symbol_set_hash",
        "watchlist_snapshot_hash",
        "provider_preflight_manifest_hash",
        "preflight_contract",
    )

    @classmethod
    def from_execution(
        cls,
        *,
        screening: ScreeningExecutionResult | None,
        manifest: PreflightManifest | None,
        reviewed_at: datetime,
    ) -> ReviewedScreeningEvidence:
        return cls(
            screening_snapshot_id=(
                str(screening.resolution_snapshot_id)
                if screening and screening.resolution_snapshot_id
                else None
            ),
            screening_snapshot_hash=screening.resolution_snapshot_hash if screening else None,
            screening_policy_hash=screening.policy_hash if screening else None,
            methodology_id=(
                str(screening.methodology_id) if screening and screening.methodology_id else None
            ),
            methodology_version=screening.methodology_version if screening else None,
            resolved_symbol_set_hash=(
                screening.resolved_symbol_set_hash if screening else None
            ),
            watchlist_snapshot_hash=screening.watchlist_snapshot_hash if screening else None,
            provider_preflight_manifest_hash=manifest.manifest_hash if manifest else None,
            preflight_contract=manifest.contract if manifest else None,
            included_symbol_count=len(screening.included_symbols) if screening else 0,
            dynamic_membership=screening.dynamic_membership if screening else False,
            reviewed_at=reviewed_at,
        )

    def differences_from(self, other: ReviewedScreeningEvidence) -> list[str]:
        """Which bound facts moved between review and approval, in plain words.

        ``self`` is what the user reviewed; ``other`` is what is true now.
        """

        changed: list[str] = []
        for name in self.BOUND_FIELDS:
            if getattr(self, name) != getattr(other, name):
                changed.append(_EVIDENCE_LABELS.get(name, name))
        return list(dict.fromkeys(changed))

    def describe_change(self, other: ReviewedScreeningEvidence) -> str:
        """One sentence naming what moved, for a beginner."""

        changed = self.differences_from(other)
        if not changed:
            return "Nothing changed."
        if len(changed) == 1:
            return f"{changed[0].capitalize()} changed since you reviewed this setup."
        listed = ", ".join(changed[:-1])
        return (
            f"{listed} and {changed[-1]} changed since you reviewed this setup."
        ).capitalize()

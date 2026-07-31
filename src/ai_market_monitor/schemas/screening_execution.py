"""What the Sharia resolver permitted, kept separate from what the user authored.

The screening gate used to answer a yes/no question. It resolved the universe, checked
that *something* survived, and then returned the **original, unscreened** definition. The
resolved symbols were discarded, so everything downstream worked on a different universe
from the one screening had approved.

Closing that gap by writing the resolved symbols back into the definition's
``include_symbols`` closed one hole and opened a worse one. ``include_symbols`` is an
*authored* field — the assets a user named — and the resolver reads it back as the
complete technical universe. Writing a resolution there froze an ``eligible_market``
monitor to the assets that happened to be eligible on the day it was approved, which is
the opposite of what that mode promises.

So there are now three separate objects, and none of them can be mistaken for another:

``CompiledAuthoredDefinition``
    The policy the user wrote. Part of executable identity. The runtime re-resolves from
    this, every cycle.

``SecuredPreviewDefinition``
    That authored policy **plus** the exact markets one governed resolution permitted.
    Review and approval evidence. Never written back into the authored policy.

``ScreeningExecutionResult``
    One resolution: the authored definition it ran against, the symbols it permitted, and
    the identity of the resolution itself.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_market_monitor.core.universe_membership import (
    MembershipKind,
    membership_contract,
)
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


class CompiledAuthoredDefinition(BaseModel):
    """The compiled strategy exactly as the user authored it.

    Nothing a resolver produced ever enters this object. It is what executable identity
    is computed from, what gets persisted on approval, and what the runtime re-resolves
    its universe from on every cycle.
    """

    model_config = ConfigDict(extra="forbid")

    definition: StrategyDefinition

    @property
    def schema_hash(self) -> str:
        return self.definition.canonical_hash()

    @property
    def membership_kind(self) -> MembershipKind:
        policy = self.definition.universe.sharia_policy
        return membership_contract(policy.universe_mode if policy else None).kind

    @property
    def dynamic_membership(self) -> bool:
        policy = self.definition.universe.sharia_policy
        return membership_contract(policy.universe_mode if policy else None).dynamic


class SecuredPreviewDefinition(BaseModel):
    """The authored policy next to the markets one resolution permitted.

    The pair is the thing a user reviews: "these rules, over these markets, today". Both
    halves are hashed, separately and together, so approval can tell "the rules changed"
    apart from "the eligible markets changed" and say which one to the user.
    """

    model_config = ConfigDict(extra="forbid")

    authored: CompiledAuthoredDefinition
    #: Exactly the markets the resolution permitted. Display and data-check input only.
    resolved_symbols: list[str] = Field(default_factory=list, max_length=100000)
    excluded_symbols: list[str] = Field(default_factory=list, max_length=100000)
    membership_kind: MembershipKind

    @property
    def authored_schema_hash(self) -> str:
        return self.authored.schema_hash

    @property
    def resolved_symbol_set_hash(self) -> str:
        return symbol_set_hash(self.resolved_symbols)

    @property
    def secured_preview_hash(self) -> str:
        """One identity for "these rules over these markets under this contract"."""
        payload = {
            "authored": self.authored_schema_hash,
            "resolved": self.resolved_symbol_set_hash,
            "membership_kind": self.membership_kind,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def for_preflight(self) -> StrategyDefinition:
        """A throwaway copy carrying the resolved markets, for the data check only.

        Never persisted, never compiled, never approved. It exists because the data check
        asks "can I read candles for these symbols", and the symbols it must ask about
        are the resolved ones — while the object that gets stored has to stay authored.
        """
        definition = self.authored.definition
        return definition.model_copy(
            update={
                "universe": definition.universe.model_copy(
                    update={"include_symbols": list(self.resolved_symbols)}
                )
            }
        )


class ScreeningExecutionResult(BaseModel):
    """One governed resolution: what it ran against, and what it permitted."""

    model_config = ConfigDict(extra="forbid")

    #: The user's own policy, untouched. Approval persists **this**, so a dynamic
    #: universe still re-resolves after approval instead of being pinned to this moment.
    authored_definition: StrategyDefinition
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

    @model_validator(mode="after")
    def validate_universe(self) -> ScreeningExecutionResult:
        contract = self.membership_contract_kind
        if contract == "fixed_authored":
            authored = {
                item.strip().upper()
                for item in self.authored_definition.universe.include_symbols
                if item.strip()
            }
            permitted = {item.strip().upper() for item in self.included_symbols}
            if authored and not permitted.issubset(authored):
                # A fixed universe may shrink — an asset can lose eligibility — but a
                # resolution that *adds* a symbol the user never named is a resolver bug
                # and must never reach a preview, let alone an approval.
                raise ValueError(
                    "a fixed universe cannot include a market the user did not choose"
                )
        return self

    @property
    def membership_contract_kind(self) -> MembershipKind:
        policy = self.authored_definition.universe.sharia_policy
        return membership_contract(policy.universe_mode if policy else None).kind

    @property
    def dynamic_membership(self) -> bool:
        """True only for ``eligible_market``.

        A Favorites list is *editable*, not dynamic: editing it forces a fresh review.
        The old value here was ``mode != explicit_assets``, which told the user their
        approval covered Favorites changes it does not cover.
        """
        policy = self.authored_definition.universe.sharia_policy
        return membership_contract(policy.universe_mode if policy else None).dynamic

    @property
    def secured_preview(self) -> SecuredPreviewDefinition:
        return SecuredPreviewDefinition(
            authored=CompiledAuthoredDefinition(definition=self.authored_definition),
            resolved_symbols=list(self.included_symbols),
            excluded_symbols=list(self.excluded_symbols),
            membership_kind=self.membership_contract_kind,
        )

    @property
    def preflight_definition(self) -> StrategyDefinition:
        """The definition the data check runs against: authored rules, resolved markets."""
        return self.secured_preview.for_preflight()

    @property
    def resolved_symbol_set_hash(self) -> str:
        """Content identity of the permitted market set."""
        return symbol_set_hash(self.included_symbols)

    @property
    def secured_preview_hash(self) -> str:
        return self.secured_preview.secured_preview_hash

    def evidence(self) -> dict[str, object]:
        """The facts a reply or an approval record may cite."""
        policy = self.authored_definition.universe.sharia_policy
        contract = membership_contract(policy.universe_mode if policy else None)
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
            "secured_preview_hash": self.secured_preview_hash,
            "authored_schema_hash": self.authored_definition.canonical_hash(),
            "watchlist_snapshot_hash": self.watchlist_snapshot_hash,
            "membership_kind": contract.kind,
            "dynamic_membership": contract.dynamic,
            "membership_sentence": contract.approval_sentence,
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

    def covers(self, symbols: list[str]) -> bool:
        """Does a ``verified_all`` manifest really cover every pair it claims?

        The count must equal the full symbol × timeframe product. A manifest that
        promises everything while listing fewer pairs than that is not a stricter
        promise kept loosely — it is a false one, and approval refuses it.
        """
        if self.contract != "verified_all":
            return True
        wanted = {item.strip().upper() for item in symbols if item.strip()}
        timeframes = [item for item in self.required_timeframes if item]
        if not wanted:
            return True
        if not timeframes:
            return False
        expected = {
            f"{symbol}@{timeframe}" for symbol in wanted for timeframe in timeframes
        }
        checked = {
            f"{item.rsplit('@', 1)[0].strip().upper()}@{item.rsplit('@', 1)[1].strip()}"
            for item in self.verified_pairs
            if "@" in item
        }
        return expected.issubset(checked)

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
    "secured_preview_hash": "the setup you reviewed",
    "watchlist_snapshot_hash": "the Favorites list this setup uses",
    "provider_preflight_manifest_hash": "the market-data check",
    "preflight_contract": "what the market-data check promised",
    "membership_kind": "how this setup decides which markets to watch",
}


#: The one sentence each membership rule promises, keyed by kind. Derived from the
#: contracts rather than written twice, so the approval record and the resolver can never
#: describe the same mode differently.
_MEMBERSHIP_BY_KIND: dict[str, str] = {
    contract.kind: contract.approval_sentence
    for contract in (
        membership_contract(mode)
        for mode in ("explicit_assets", "approved_watchlist", "eligible_market")
    )
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
    #: One identity for the authored rules *and* the reviewed markets together.
    secured_preview_hash: str | None = Field(default=None, max_length=128)
    watchlist_snapshot_hash: str | None = Field(default=None, max_length=128)
    provider_preflight_manifest_hash: str | None = Field(default=None, max_length=128)
    preflight_contract: PreflightContract | None = None
    #: Which membership rule was in force. Bound, because approving a fixed universe and
    #: approving a dynamic one are different promises to the user.
    membership_kind: MembershipKind | None = None
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
        "secured_preview_hash",
        "watchlist_snapshot_hash",
        "provider_preflight_manifest_hash",
        "preflight_contract",
        "membership_kind",
    )

    #: Evidence that must be **present**, not merely unchanged. Absent evidence used to
    #: compare equal to absent evidence, so a review with no market-data check and an
    #: approval with no market-data check agreed with each other and the approval went
    #: through. Every entry here is required whenever screening ran at all.
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = (
        "screening_policy_hash",
        "methodology_id",
        "methodology_version",
        "resolved_symbol_set_hash",
        "secured_preview_hash",
        "provider_preflight_manifest_hash",
        "preflight_contract",
        "membership_kind",
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
            secured_preview_hash=screening.secured_preview_hash if screening else None,
            watchlist_snapshot_hash=screening.watchlist_snapshot_hash if screening else None,
            provider_preflight_manifest_hash=manifest.manifest_hash if manifest else None,
            preflight_contract=manifest.contract if manifest else None,
            membership_kind=screening.membership_contract_kind if screening else None,
            included_symbol_count=len(screening.included_symbols) if screening else 0,
            dynamic_membership=screening.dynamic_membership if screening else False,
            reviewed_at=reviewed_at,
        )

    @property
    def membership_sentence(self) -> str:
        """What this approval actually promises about which markets are watched.

        Required by the approval record: approving a fixed universe and approving a
        dynamic one are different promises, and a user who is not told which one they
        made cannot know whether editing their Favorites list changes what runs.
        """
        for mode, contract in _MEMBERSHIP_BY_KIND.items():
            if mode == self.membership_kind:
                return contract
        return _MEMBERSHIP_BY_KIND["fixed_authored"]

    def missing_evidence(self) -> list[str]:
        """Which required facts are absent, in plain words. Empty means complete."""

        missing: list[str] = []
        for name in self.REQUIRED_FIELDS:
            if name == "watchlist_snapshot_hash":
                continue
            if getattr(self, name) in (None, ""):
                missing.append(_EVIDENCE_LABELS.get(name, name))
        if (
            self.membership_kind == "fixed_watchlist"
            and not self.watchlist_snapshot_hash
        ):
            missing.append(_EVIDENCE_LABELS["watchlist_snapshot_hash"])
        return list(dict.fromkeys(missing))

    def describe_missing(self) -> str:
        """One sentence naming what is missing, for a beginner."""

        missing = self.missing_evidence()
        if not missing:
            return "Nothing is missing."
        if len(missing) == 1:
            return f"{missing[0].capitalize()} is missing, so this setup cannot be approved yet."
        listed = ", ".join(missing[:-1])
        return (
            f"{listed} and {missing[-1]} are missing, so this setup cannot be approved yet."
        ).capitalize()

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

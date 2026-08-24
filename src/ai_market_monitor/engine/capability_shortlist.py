"""The capability keys a turn is allowed to choose from.

The model was told to pick an exact registered capability but was never shown the
register. Asked to name something it could not see, it did what anyone would: it
invented plausible keys, or reached for a mechanic that sounded close. A near-miss
mechanic is worse than a refusal — it monitors a market event the trader never
described, and nothing in the draft says so.

So the server retrieves the shortlist deterministically, from the registry, before
the model is called, and the model may only choose a key that appears in it. When
nothing in the shortlist is exact, the correct output is an unsupported requirement
or a clarification — never the nearest neighbour.

This module only *retrieves*. It never decides which candidate is right; that is the
model's reading of the sentence, checked afterwards against this same shortlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.provider_families import (
    BASE_MARKET_DATA_CONTRACTS,
    CCXT_MARKET_DATA_CONTRACTS,
    ProviderAvailability,
    runtime_availability,
)

#: How many candidates one turn may see. Enough for a genuine choice, small enough
#: that the list stays readable and the prompt stays bounded.
SHORTLIST_LIMIT = 12

#: Candidates per fragment asked of the resolver before merging and trimming.
_PER_FRAGMENT_LIMIT = 6

# Provider contracts backed by every launch market-data adapter. Adapter-specific
# contracts are added only when that adapter is actually configured.
#
# Both names are kept because callers and tests import them, but neither is a second
# list any more: they are derived from `engine/provider_families.py`, which is the one
# owner of what a feed is and whether it answers. They used to be hand-written here, and
# because they named only the candle contracts, every capability that asked for the
# order book, the risk numbers or the cross-market prices was reported to the Builder as
# a rule needing a feed Hilal Markets could not read — while the scanner was reading all
# three on every candle.
SETUP_BASE_PROVIDER_REQUIREMENTS = BASE_MARKET_DATA_CONTRACTS
SETUP_RUNTIME_PROVIDER_REQUIREMENTS = BASE_MARKET_DATA_CONTRACTS | CCXT_MARKET_DATA_CONTRACTS


def configured_runtime_provider_requirements(
    market_data_provider: str,
    availability: ProviderAvailability | None = None,
) -> frozenset[str]:
    """Every provider contract a rule may ask for and still be runnable here.

    The candle contracts the configured adapter implements, plus each context feed this
    deployment can actually read.
    """

    resolved = availability or runtime_availability()
    return resolved.contract_names(market_data_provider=market_data_provider)


@dataclass(frozen=True, slots=True)
class ShortlistCandidate:
    """One capability the model may choose, with everything needed to choose well."""

    capability_key: str
    capability_version: str
    label: str
    #: What it measures, in the registry's own words.
    description: str
    supported_operators: tuple[str, ...]
    parameter_schema: dict[str, Any]
    direction_support: tuple[str, ...]
    supported_timeframes: tuple[str, ...]
    requires_higher_timeframe: bool
    provider_requirements: tuple[str, ...]
    availability: str
    executable: bool
    #: Wordings that look similar but mean something else. These stop a candidate
    #: from being chosen for a sentence it does not actually cover.
    negative_examples: tuple[str, ...]
    #: Wordings this capability does cover, for recognition.
    intent_examples: tuple[str, ...]
    #: Why the retriever surfaced it, and how strongly.
    matched_on: tuple[str, ...] = ()
    score: float = 0.0
    source_fragment: str = ""

    def to_prompt_dict(self) -> dict[str, Any]:
        """The compact form sent to the model. Field names are the contract."""
        return {
            "capability_key": self.capability_key,
            "capability_version": self.capability_version,
            "label": self.label,
            "description": self.description,
            "supported_operators": list(self.supported_operators),
            "parameter_schema": self.parameter_schema,
            "direction_support": list(self.direction_support),
            "supported_timeframes": list(self.supported_timeframes),
            "requires_higher_timeframe": self.requires_higher_timeframe,
            "provider_requirements": list(self.provider_requirements),
            "availability": self.availability,
            "executable": self.executable,
            "covers": list(self.intent_examples[:4]),
            "does_not_cover": list(self.negative_examples[:4]),
            "matched_on": list(self.matched_on),
            "source_fragment": self.source_fragment,
        }


@dataclass(frozen=True, slots=True)
class CapabilityShortlist:
    """The keys this turn may use, and the registry snapshot they came from."""

    candidates: tuple[ShortlistCandidate, ...] = ()
    registry_hash: str = ""
    registry_version: str = ""
    #: Fragments the retriever examined, for the operator trace.
    examined_fragments: tuple[str, ...] = ()
    #: Words in the turn that matched nothing in the registry. A strong signal that
    #: the honest outcome is a clarification or an unsupported requirement.
    unknown_terms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed_keys(self) -> frozenset[str]:
        """Exactly the keys a plan may name. Anything else is refused."""
        return frozenset(item.capability_key for item in self.candidates)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "registry_version": self.registry_version,
            "candidates": [item.to_prompt_dict() for item in self.candidates],
            "unknown_terms": list(self.unknown_terms),
            "rule": (
                "Choose only a capability_key listed here. If none of them expresses "
                "the request exactly, return an unsupported segment or ask for "
                "clarification. Never invent a key and never substitute a similar "
                "mechanic."
            ),
        }


def build_capability_shortlist(
    message: str,
    *,
    limit: int = SHORTLIST_LIMIT,
    available_provider_requirements: frozenset[str] | None = None,
) -> CapabilityShortlist:
    """Retrieve the capability keys relevant to ``message``.

    Ranking comes from the shared registry resolver, so the shortlist agrees with the
    deterministic matcher used everywhere else rather than being a second opinion.
    """

    text = " ".join((message or "").split())
    snapshot = get_capability_index().snapshot
    if not text:
        return CapabilityShortlist(
            registry_hash=snapshot.registry_hash,
            registry_version=snapshot.registry_version,
        )

    report = snapshot.resolver.resolve_prompt(text, limit_per_fragment=_PER_FRAGMENT_LIMIT)
    by_key = {capability.key: capability for capability in all_capabilities()}

    best: dict[str, ShortlistCandidate] = {}
    fragments: list[str] = []
    unknown: list[str] = []
    for resolution in report.fragments:
        fragments.append(resolution.fragment)
        unknown.extend(resolution.unknown_terms)
        for candidate in resolution.candidates:
            spec = by_key.get(candidate.capability_key)
            if spec is None:
                continue
            provider_requirements = (
                spec.provider_requirements
                or ((spec.provider_required,) if spec.provider_required else ())
            )
            if (
                available_provider_requirements is not None
                and any(
                    requirement.casefold() not in available_provider_requirements
                    for requirement in provider_requirements
                )
            ):
                continue
            existing = best.get(candidate.capability_key)
            if existing is not None and existing.score >= candidate.score:
                continue
            best[candidate.capability_key] = _candidate(
                spec,
                matched_on=candidate.matched_on,
                score=candidate.score,
                source_fragment=candidate.source_fragment or resolution.fragment,
            )

    ranked = sorted(best.values(), key=lambda item: (-item.score, item.capability_key))
    return CapabilityShortlist(
        candidates=tuple(ranked[:limit]),
        registry_hash=snapshot.registry_hash,
        registry_version=snapshot.registry_version,
        examined_fragments=tuple(dict.fromkeys(fragments)),
        unknown_terms=tuple(dict.fromkeys(unknown)),
    )


def capability_contract(key: str) -> ShortlistCandidate | None:
    """The registry contract for one key, for post-plan validation."""

    for capability in all_capabilities():
        if capability.key == key:
            return _candidate(capability, matched_on=("explicit_key",), score=1.0)
    return None


def _candidate(
    spec: CapabilitySpec,
    *,
    matched_on: tuple[str, ...],
    score: float,
    source_fragment: str = "",
) -> ShortlistCandidate:
    return ShortlistCandidate(
        capability_key=spec.key,
        capability_version=spec.capability_version,
        label=spec.label,
        description=spec.description,
        supported_operators=spec.supported_comparators,
        parameter_schema=dict(spec.parameter_schema),
        direction_support=spec.direction_support,
        supported_timeframes=spec.supported_timeframes,
        requires_higher_timeframe=spec.requires_higher_timeframe,
        provider_requirements=(
            spec.provider_requirements
            or ((spec.provider_required,) if spec.provider_required else ())
        ),
        availability=spec.availability,
        executable=spec.executable,
        negative_examples=spec.negative_examples,
        intent_examples=spec.intent_examples or spec.examples,
        matched_on=matched_on,
        score=score,
        source_fragment=source_fragment,
    )

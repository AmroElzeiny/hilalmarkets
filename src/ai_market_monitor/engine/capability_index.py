from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any

from ai_market_monitor.engine.capabilities import CapabilitySpec, all_capabilities
from ai_market_monitor.engine.capability_resolver import CapabilityCandidate, CapabilityResolver


@dataclass(frozen=True, slots=True)
class CapabilityIndexSnapshot:
    registry_hash: str
    registry_version: str
    aliases: dict[str, tuple[str, ...]]
    resolver: CapabilityResolver
    embedding_model: str | None = None
    embeddings: dict[str, tuple[float, ...]] | None = None


class CapabilityIndex:
    """Process-wide immutable registry snapshot, rebuilt only when its artifact changes."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot = self._build_snapshot({})

    @property
    def snapshot(self) -> CapabilityIndexSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def resolver(self) -> CapabilityResolver:
        return self.snapshot.resolver

    @property
    def registry_hash(self) -> str:
        return self.snapshot.registry_hash

    def install_alias_artifact(
        self,
        aliases: Mapping[str, Sequence[str]],
        *,
        registry_version: str | None = None,
    ) -> CapabilityIndexSnapshot:
        normalized = {
            key: tuple(dict.fromkeys(_normalize_alias(alias) for alias in values if alias.strip()))
            for key, values in aliases.items()
            if values
        }
        snapshot = self._build_snapshot(normalized, registry_version=registry_version)
        with self._lock:
            if snapshot.registry_hash != self._snapshot.registry_hash:
                self._snapshot = snapshot
            return self._snapshot

    def install_embeddings(
        self,
        *,
        registry_hash: str,
        model: str,
        embeddings: Mapping[str, Sequence[float]],
    ) -> bool:
        with self._lock:
            current = self._snapshot
            if current.registry_hash != registry_hash:
                return False
            keys = {capability.key for capability in all_capabilities()}
            cleaned = {
                key: tuple(float(value) for value in vector)
                for key, vector in embeddings.items()
                if key in keys and vector
            }
            if not cleaned:
                return False
            self._snapshot = CapabilityIndexSnapshot(
                registry_hash=current.registry_hash,
                registry_version=current.registry_version,
                aliases=current.aliases,
                resolver=current.resolver,
                embedding_model=model,
                embeddings=cleaned,
            )
            return True

    def semantic_candidates(
        self,
        fragment: str,
        query_embedding: list[float] | tuple[float, ...] | None,
        *,
        limit: int = 8,
    ) -> tuple[CapabilityCandidate, ...]:
        snapshot = self.snapshot
        if not query_embedding or not snapshot.embeddings:
            return ()
        query = tuple(float(value) for value in query_embedding)
        ranked = sorted(
            (
                (_cosine(query, vector), key)
                for key, vector in snapshot.embeddings.items()
                if len(vector) == len(query)
            ),
            reverse=True,
        )[:limit]
        results: list[CapabilityCandidate] = []
        for similarity, key in ranked:
            capability = snapshot.resolver.get(key)
            compatibility = snapshot.resolver.compatibility(key)
            results.append(
                CapabilityCandidate(
                    capability_key=key,
                    label=capability.label,
                    score=round(max(0.0, similarity) * 70, 2),
                    confidence=round(max(0.0, min(0.7, similarity)), 3),
                    availability=compatibility.availability,
                    matched_on=("embedding",),
                    source_fragment=fragment,
                    semantic_tags=capability.semantic_tags,
                    parameter_schema=capability.parameter_schema,
                    direction_support=capability.direction_support,
                    temporal_behavior=capability.temporal_behavior,
                )
            )
        return tuple(results)

    @staticmethod
    def capability_document(capability: CapabilitySpec) -> str:
        return " | ".join(
            value
            for value in (
                capability.key.replace("_", " "),
                capability.label,
                capability.description,
                " ".join(capability.aliases),
                " ".join(capability.semantic_tags),
                " ".join(capability.intent_examples),
            )
            if value
        )

    def _build_snapshot(
        self,
        aliases: dict[str, tuple[str, ...]],
        *,
        registry_version: str | None = None,
    ) -> CapabilityIndexSnapshot:
        capabilities = all_capabilities()
        payload: dict[str, Any] = {
            "capabilities": [capability.to_dict() for capability in capabilities],
            "approved_aliases": {key: list(values) for key, values in sorted(aliases.items())},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        registry_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return CapabilityIndexSnapshot(
            registry_hash=registry_hash,
            registry_version=registry_version or f"registry-{registry_hash[:12]}",
            aliases=aliases,
            resolver=CapabilityResolver(capabilities, approved_aliases=aliases),
        )


def _normalize_alias(value: str) -> str:
    return " ".join(value.casefold().split())[:240]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


_CAPABILITY_INDEX = CapabilityIndex()


def get_capability_index() -> CapabilityIndex:
    return _CAPABILITY_INDEX

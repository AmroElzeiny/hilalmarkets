"""Server-owned references used by the compact Setup Chat planner.

The language model receives small turn-local labels.  Canonical condition ids,
clarification ids, snapshot ids, methodology ids/versions, and watchlist ids/hashes
remain in this server-only object and are resolved only after the model response has
passed schema and source validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def semantic_key(value: str | None) -> str:
    """A conservative lookup key for public labels, never an intent parser."""

    return " ".join(re.findall(r"[\w]+", (value or "").casefold(), flags=re.UNICODE))


@dataclass(frozen=True, slots=True)
class MethodologyReference:
    """One currently executable governed methodology."""

    reference: str
    public_identifier: str
    public_name: str
    family: str | None
    aliases: tuple[str, ...]
    methodology_id: str
    methodology_version: str

    def prompt_dict(self) -> dict[str, Any]:
        return {
            "methodology_ref": self.reference,
            "identifier": self.public_identifier,
            "name": self.public_name,
            "family": self.family,
        }

    def matches(self, value: str | None) -> bool:
        wanted = semantic_key(value)
        return bool(wanted) and wanted in {
            semantic_key(item)
            for item in (
                self.reference,
                self.public_identifier,
                self.public_name,
                self.family,
                *self.aliases,
            )
            if item
        }


@dataclass(frozen=True, slots=True)
class WatchlistReference:
    """One user-owned approved watchlist and its immutable content identity."""

    reference: str
    public_name: str
    aliases: tuple[str, ...]
    watchlist_id: str
    watchlist_version: str

    def prompt_dict(self) -> dict[str, str]:
        return {"watchlist_ref": self.reference, "name": self.public_name}

    def matches(self, value: str | None) -> bool:
        wanted = semantic_key(value)
        return bool(wanted) and wanted in {
            semantic_key(item) for item in (self.reference, self.public_name, *self.aliases) if item
        }


@dataclass(frozen=True, slots=True)
class SnapshotReference:
    reference: str
    snapshot_id: str
    executable_version: int


@dataclass(frozen=True, slots=True)
class PlannerReferenceContext:
    """All canonical identities hidden behind aliases for one user turn."""

    condition_ids: dict[str, str] = field(default_factory=dict)
    clarification_ids: dict[str, str] = field(default_factory=dict)
    snapshots: tuple[SnapshotReference, ...] = ()
    methodologies: tuple[MethodologyReference, ...] = ()
    watchlists: tuple[WatchlistReference, ...] = ()

    def condition_id(self, reference: str | None) -> str | None:
        return self.condition_ids.get(str(reference or ""))

    def clarification_id(self, reference: str | None) -> str | None:
        return self.clarification_ids.get(str(reference or ""))

    def snapshot(self, reference: str | None) -> SnapshotReference | None:
        wanted = str(reference or "")
        return next((item for item in self.snapshots if item.reference == wanted), None)

    def methodology_matches(
        self,
        *,
        family: str | None,
        identifier: str | None,
    ) -> tuple[MethodologyReference, ...]:
        rows = self.methodologies
        if identifier:
            rows = tuple(item for item in rows if item.matches(identifier))
        if family:
            wanted = semantic_key(family)
            rows = tuple(
                item
                for item in rows
                if wanted
                in {
                    semantic_key(item.family),
                    *(semantic_key(alias) for alias in item.aliases),
                }
            )
        return rows

    def watchlist_matches(self, value: str | None = None) -> tuple[WatchlistReference, ...]:
        if value:
            return tuple(item for item in self.watchlists if item.matches(value))
        return self.watchlists

    def watchlist_matches_in_text(self, text: str) -> tuple[WatchlistReference, ...]:
        """Find an offered public watchlist name or alias in verified user text.

        This is a governed-choice lookup, not a free-text intent parser. It is used
        only after the planner has already proposed ``approved_watchlist_only`` and
        lets an answer such as ``Core assets`` resolve an offered watchlist without
        exposing its database identity to the model.
        """

        rendered = f" {semantic_key(text)} "
        return tuple(
            item
            for item in self.watchlists
            if any(
                key and f" {key} " in rendered
                for key in (semantic_key(value) for value in (item.public_name, *item.aliases))
            )
        )


EMPTY_PLANNER_REFERENCES = PlannerReferenceContext()

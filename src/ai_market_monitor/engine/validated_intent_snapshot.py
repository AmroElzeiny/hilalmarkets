"""What the server already understood, kept even when the turn failed.

The loop this closes
--------------------

Evaluator run 20260803T000036Z, case ``precedence_grouping-013-1996163001``:

    u1  "watchlist ETHUSDT, not BTCUSDT, 1m context, 1h trigger, bullish at least 2.5%"
    a1  "I could not turn that into an exact change. Nothing in your setup was altered."
    u2  the same requirements, reworded
    a2  the same refusal
    ... eight times.

Nothing was wrong with the instruction. One turn's reading dropped a stated value, the
failure class was terminal, and the whole turn was thrown away — including the five
requirements that had been read correctly. Every retry started from nothing, paid for a
full planner call, and reached the same place.

So two things are kept between turns:

* **the grounded requirements** — every value the server proved against the trader's own
  words, even from a turn that ended in a refusal; and
* **the failure fingerprints** — which exact problem, on which exact draft, for which
  exact request, has already been tried.

With both, another identical attempt does not pay for the same failed repair again: the
planner receives only the already-proved values as read-only retry evidence, while the
server names the remaining blocker instead of asking for the whole instruction again.
The ordinary AI-first planner boundary is preserved.

Nothing here is canonical state. A snapshot never becomes a draft, never becomes an
operation, and never approves anything: it is evidence about a turn, and the canonical
mutation path is still the only way anything changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "GroundedRequirement",
    "SNAPSHOT_HISTORY_LIMIT",
    "ValidatedIntentSnapshot",
    "repeat_state",
]

#: How many past turns a chat remembers. Long enough to notice a loop, short enough that
#: an old failure cannot haunt a conversation that has genuinely moved on.
SNAPSHOT_HISTORY_LIMIT = 8


@dataclass(frozen=True, slots=True)
class GroundedRequirement:
    """One value the server proved against the trader's own words."""

    #: The model-facing path this value belongs to, e.g. ``condition.trigger_timeframe``.
    semantic_path: str
    value: str
    #: The trader's exact words that authorise it.
    source_excerpt: str

    def to_dict(self) -> dict[str, str]:
        return {
            "semantic_path": self.semantic_path,
            "value": self.value,
            "source_excerpt": self.source_excerpt[:240],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> GroundedRequirement:
        return cls(
            semantic_path=str(payload.get("semantic_path") or ""),
            value=str(payload.get("value") or ""),
            source_excerpt=str(payload.get("source_excerpt") or ""),
        )


@dataclass(frozen=True, slots=True)
class ValidatedIntentSnapshot:
    """Everything one turn established, whether or not the turn succeeded."""

    session_id: str
    source_turn_id: str
    canonical_draft_hash: str
    normalized_user_intent_hash: str
    grounded_requirements: tuple[GroundedRequirement, ...] = field(default_factory=tuple)
    failure_class: str | None = None
    failure_paths: tuple[str, ...] = field(default_factory=tuple)
    failure_fingerprint: str | None = None
    #: Whether this turn asked the trader anything. Used to keep from asking twice.
    asked_question: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_turn_id": self.source_turn_id,
            "canonical_draft_hash": self.canonical_draft_hash,
            "normalized_user_intent_hash": self.normalized_user_intent_hash,
            "grounded_requirements": [item.to_dict() for item in self.grounded_requirements],
            "failure_class": self.failure_class,
            "failure_paths": list(self.failure_paths),
            "failure_fingerprint": self.failure_fingerprint,
            "asked_question": self.asked_question,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ValidatedIntentSnapshot:
        return cls(
            session_id=str(payload.get("session_id") or ""),
            source_turn_id=str(payload.get("source_turn_id") or ""),
            canonical_draft_hash=str(payload.get("canonical_draft_hash") or ""),
            normalized_user_intent_hash=str(payload.get("normalized_user_intent_hash") or ""),
            grounded_requirements=tuple(
                GroundedRequirement.from_dict(item)
                for item in (payload.get("grounded_requirements") or [])
                if isinstance(item, Mapping)
            ),
            failure_class=(
                str(payload["failure_class"]) if payload.get("failure_class") else None
            ),
            failure_paths=tuple(str(item) for item in (payload.get("failure_paths") or [])),
            failure_fingerprint=(
                str(payload["failure_fingerprint"]) if payload.get("failure_fingerprint") else None
            ),
            asked_question=(
                str(payload["asked_question"]) if payload.get("asked_question") else None
            ),
        )


@dataclass(frozen=True, slots=True)
class RepeatState:
    """How much of this turn the chat has already seen."""

    #: The trader has sent materially the same instruction this many times before.
    same_intent_retry_count: int = 0
    #: This exact failure, on this exact draft, for this exact request, this many times.
    same_failure_repeat_count: int = 0
    #: Values already proved in earlier turns that this one need not re-establish.
    reusable_requirements: tuple[GroundedRequirement, ...] = field(default_factory=tuple)
    #: Fingerprints already attempted, so a correction is not paid for twice.
    attempted_fingerprints: tuple[str, ...] = field(default_factory=tuple)
    #: Questions already asked, so the same one is not asked again.
    questions_already_asked: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_repeat(self) -> bool:
        return self.same_intent_retry_count > 0

    @property
    def is_loop(self) -> bool:
        """The same request has failed the same way more than once already."""

        return self.same_failure_repeat_count >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "same_intent_retry_count": self.same_intent_retry_count,
            "same_failure_repeat_count": self.same_failure_repeat_count,
            "reusable_requirement_count": len(self.reusable_requirements),
            "attempted_failure_fingerprints": list(self.attempted_fingerprints),
            "questions_already_asked": list(self.questions_already_asked),
            "repeated_full_message": self.is_repeat,
            "failure_loop": self.is_loop,
        }


def repeat_state(
    history: Sequence[Mapping[str, Any]],
    *,
    canonical_draft_hash: str,
    normalized_user_intent_hash: str,
) -> RepeatState:
    """What earlier turns already established about this exact request.

    Matching is on the *request*, not on the wording: a trader who restates the same
    requirements in different words is asking the same thing, and treating that as a
    fresh turn is how the loop stayed invisible in the metrics ("repeated questions:
    0.0%" while eight identical instructions were being refused).
    """

    snapshots = [ValidatedIntentSnapshot.from_dict(item) for item in history]
    same_intent = [
        item
        for item in snapshots
        if item.normalized_user_intent_hash == normalized_user_intent_hash
    ]
    same_draft = [item for item in same_intent if item.canonical_draft_hash == canonical_draft_hash]
    fingerprints = tuple(
        dict.fromkeys(item.failure_fingerprint for item in same_draft if item.failure_fingerprint)
    )
    counted: dict[str, int] = {}
    for item in same_draft:
        if item.failure_fingerprint:
            counted[item.failure_fingerprint] = counted.get(item.failure_fingerprint, 0) + 1
    # Reuse evidence only for the same normalized request against the same canonical
    # draft.  Evidence from an unrelated instruction (or from an older draft) must
    # never be offered to the planner as authority for the current turn.
    reusable: dict[str, GroundedRequirement] = {}
    for item in same_draft:
        for requirement in item.grounded_requirements:
            reusable[requirement.semantic_path] = requirement
    return RepeatState(
        same_intent_retry_count=len(same_intent),
        same_failure_repeat_count=max(counted.values(), default=0),
        reusable_requirements=tuple(reusable.values()),
        attempted_fingerprints=fingerprints,
        questions_already_asked=tuple(
            dict.fromkeys(item.asked_question for item in same_draft if item.asked_question)
        ),
    )


def normalized_intent_hash(message: str) -> str:
    """One key for "the trader is asking for the same thing again".

    Wording moves between turns — ``at most 1%`` becomes ``1.0% or less`` — but the
    words that carry meaning do not. Casing, punctuation and order are removed so a
    genuine restatement matches and a new requirement does not.
    """

    # A counted lexical bag recognises harmless clause reordering, but unlike the old
    # ``set(words)`` it does not erase repeated predicates.  The explicit Boolean shape
    # is included separately: ``A AND (B OR C)`` and ``(A AND B) OR C`` contain the
    # same words and must never share a retry/failure identity.
    tokens = re.findall(r"\w+(?:\.\d+)?%?", (message or "").casefold())
    counts = Counter(tokens)
    lexical = " ".join(f"{token}:{counts[token]}" for token in sorted(counts))
    topology = _stated_topology_identity(message)
    body = json.dumps(
        {"lexical_multiset": lexical, "explicit_boolean_shape": topology},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def _stated_topology_identity(message: str) -> str:
    """Return only explicitly authored Boolean structure for retry identity.

    This is diagnostic identity, never a strategy parser or mutation path.  Importing
    locally keeps the evidence model independent of planner schema construction.
    """

    try:
        from ai_market_monitor.engine.boolean_topology import parse_stated_topology

        stated = parse_stated_topology(message)
    except (TypeError, ValueError):
        return ""
    return stated.root.shape() if stated is not None else ""


def snapshot_history(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stored snapshots for one chat, newest last."""

    stored = context.get("validated_intent_snapshots")
    if not isinstance(stored, list):
        return []
    return [item for item in stored if isinstance(item, dict)]


def append_snapshot(
    context: dict[str, Any],
    snapshot: ValidatedIntentSnapshot,
) -> None:
    """Record one turn's snapshot, keeping the history bounded."""

    history = snapshot_history(context)
    history.append(snapshot.to_dict())
    context["validated_intent_snapshots"] = history[-SNAPSHOT_HISTORY_LIMIT:]


def snapshot_digest(snapshot: ValidatedIntentSnapshot) -> str:
    """A stable id for one snapshot, for logs and support references."""

    body = json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()[:16]

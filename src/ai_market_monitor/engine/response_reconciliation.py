"""One fact, one sentence — and never the same reply twice to a confused user.

Two defects live here, and they share a cause: several places were each allowed to
render the same thing, and nothing compared them before the message went out.

**Duplication.** For one incomplete request the user could read all of:

* the composer's own validated claim about the unsupported item;
* ``deterministic_claim_text`` — "Not expressible exactly: ...";
* ``deterministic_summary`` — "I could not express this exactly: ...";
* a safe error saying the same thing a fourth time;
* and the appended clarification.

Five renderers, one fact. The user reads a wall of near-identical sentences about an
ordinary request that only needed one question.

**Repetition.** When a reply missed the point and the user typed ``??``, the turn was
classified the same way and produced byte-identical wording again. Nothing recorded
what had already been said, so nothing could notice.

So every user-facing sentence now arrives here as a :class:`Proposition` — subject,
predicate, value, and the requirement or clarification it belongs to — and this module
is the only thing that turns them into a message. Same proposition, one sentence, from
the most authoritative source that has it. And the final text is fingerprinted, so a
confusion signal can be answered with something the user has not already read.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from ai_market_monitor.engine.conversation_language import (
    ConversationLanguage,
    localized,
    response_matches_language,
)

__all__ = [
    "ConversationalGoal",
    "Proposition",
    "ReconciledReply",
    "RenderSource",
    "RenderedPart",
    "confusion_recovery_reply",
    "enforce_language",
    "reconcile_reply",
    "repeats_previous",
    "response_fingerprint",
]


class RenderSource(IntEnum):
    """Who wrote a sentence, and therefore which copy of a fact survives.

    Ordered by authority, highest first. A validated composer claim is the trader's
    own language for a fact the server already checked; a deterministic line is the
    server's fallback wording for the *same* fact. When both exist, the fallback is
    the copy to drop.
    """

    COMPOSER_CLAIM = 0
    SCAN_RESULT = 1
    DETERMINISTIC_CLAIM = 2
    DETERMINISTIC_SUMMARY = 3
    SAFE_ERROR = 4
    CLARIFICATION = 5


@dataclass(frozen=True, slots=True)
class Proposition:
    """What a sentence actually asserts, independent of how it is worded.

    Two sentences with the same subject, predicate, value and requirement are the same
    fact however differently they read — which is exactly the case the old pipeline
    could not see, because it compared text.
    """

    subject: str
    predicate: str
    value: str = ""
    requirement_id: str = ""

    def identity(self) -> str:
        parts = (self.subject, self.predicate, self.value, self.requirement_id)
        return "|".join(_normalize(part) for part in parts)


def _normalize(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


@dataclass(frozen=True, slots=True)
class RenderedPart:
    """One candidate sentence, and the fact it states."""

    text: str
    source: RenderSource
    proposition: Proposition | None = None

    @property
    def identity(self) -> str:
        if self.proposition is not None:
            return self.proposition.identity()
        # A sentence that asserts nothing (a greeting, a question) is identified by
        # its own normalized wording, so two copies of it still collapse to one.
        return f"text:{_normalize(self.text)}"


@dataclass(frozen=True, slots=True)
class ReconciledReply:
    """The message the user reads, and what was removed to get there."""

    message: str
    fingerprint: str
    duplicate_count: int = 0
    dropped_identities: tuple[str, ...] = field(default_factory=tuple)
    #: The clarification question, kept separate so it can be appended exactly once.
    clarification: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "response_fingerprint": self.fingerprint,
            "response_duplicate_count": self.duplicate_count,
            "response_dropped_propositions": list(self.dropped_identities),
            "response_has_clarification": bool(self.clarification),
        }


def response_fingerprint(text: str) -> str:
    """A stable id for what a reply *says*, ignoring how it is spaced or cased.

    Used to notice that a turn is about to repeat itself. Punctuation is kept: "Yes."
    and "Yes?" are different answers.
    """

    return hashlib.sha256(_normalize(text).encode()).hexdigest()[:20]


#: One question per turn. A reply that asks two things makes a beginner answer neither.
_MAX_QUESTIONS = 1

#: The customer-facing length target for an ordinary turn. Detail belongs in the
#: preview and in operator telemetry, not in a chat message a beginner has to read.
ORDINARY_REPLY_MAX_CHARS = 500


def reconcile_reply(
    parts: Sequence[RenderedPart],
    *,
    clarification: str = "",
    max_chars: int = ORDINARY_REPLY_MAX_CHARS,
) -> ReconciledReply:
    """Build the message, keeping one sentence per fact and one question in total.

    Parts may arrive in any order and from any renderer. They are grouped by what they
    assert; the copy from the most authoritative source survives; every other copy is
    counted and dropped. The clarification is appended once, at the end, and never
    duplicated by a status line that says the same thing.
    """

    best: dict[str, RenderedPart] = {}
    order: list[str] = []
    dropped: list[str] = []
    for part in parts:
        text = " ".join((part.text or "").split())
        if not text:
            continue
        candidate = RenderedPart(text=text, source=part.source, proposition=part.proposition)
        identity = candidate.identity
        existing = best.get(identity)
        if existing is None:
            best[identity] = candidate
            order.append(identity)
            continue
        dropped.append(identity)
        if candidate.source < existing.source:
            # A more authoritative renderer has the same fact: keep its wording.
            best[identity] = candidate

    sentences = [best[identity].text for identity in order]
    body = " ".join(sentences).strip()
    question = " ".join((clarification or "").split())
    if question:
        # A status line that already asks the same thing would make two questions.
        body = _strip_trailing_questions(body, keep=0 if question else _MAX_QUESTIONS)
    else:
        body = _strip_trailing_questions(body, keep=_MAX_QUESTIONS)
    message = " ".join(part for part in (body, question) if part).strip()
    if len(message) > max_chars:
        message = _trim_to_sentence(message, max_chars, tail=question)
    return ReconciledReply(
        message=message,
        fingerprint=response_fingerprint(message),
        duplicate_count=len(dropped),
        dropped_identities=tuple(dict.fromkeys(dropped)),
        clarification=question,
    )


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟])\s+")


def _strip_trailing_questions(text: str, *, keep: int) -> str:
    """Remove questions beyond the allowance, newest first.

    A turn asks at most one thing. When a clarification is being appended, any earlier
    question in the body is a second one, and two questions in a beginner's reply
    reliably get one answered and one ignored.
    """

    if not text:
        return text
    sentences = [item for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    questions = [
        index for index, item in enumerate(sentences) if item.rstrip().endswith(("?", "؟"))
    ]
    if len(questions) <= keep:
        return text
    remove = set(questions[: len(questions) - keep]) if keep else set(questions)
    return " ".join(item for index, item in enumerate(sentences) if index not in remove).strip()


def _trim_to_sentence(text: str, limit: int, *, tail: str = "") -> str:
    """Cut to the length target on a sentence boundary, always keeping the question."""

    if len(text) <= limit:
        return text
    reserved = len(tail) + 1 if tail else 0
    head = text[: max(0, limit - reserved)]
    sentences = [item for item in _SENTENCE_SPLIT.split(head) if item.strip()]
    if sentences and not head.rstrip().endswith((".", "!", "?", "؟")):
        sentences = sentences[:-1]
    body = " ".join(sentences).strip()
    return " ".join(part for part in (body, tail) if part).strip() or (tail or head.strip())


# --------------------------------------------------------------------------------
# Confusion recovery
# --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConversationalGoal:
    """What the user was actually trying to do, carried across a failed reply."""

    #: ``scan`` | ``alert`` | ``unknown``
    kind: str = "unknown"
    threshold_percent: str | None = None
    #: The one thing still missing, as a question already worded for the user.
    pending_question: str = ""


def confusion_recovery_reply(
    goal: ConversationalGoal,
    *,
    language: ConversationLanguage,
    previous_fingerprints: Iterable[str] = (),
) -> ReconciledReply:
    """Answer a confusion signal without repeating what was already said.

    Three sentences, in this order and no other: admit the miss, restate what the user
    is trying to do, and ask the one thing still needed. Repeating the previous reply
    is the failure this replaces, so the result is checked against what has already
    been sent and never returned when it matches.
    """

    threshold = f"{goal.threshold_percent}%" if goal.threshold_percent else ""
    restate_key = {
        "scan": "confusion.restate_scan",
        "alert": "confusion.restate_alert",
    }.get(goal.kind, "confusion.restate_generic")
    parts = [
        RenderedPart(
            text=localized("confusion.acknowledge", language),
            source=RenderSource.DETERMINISTIC_SUMMARY,
            proposition=Proposition("reply", "missed_the_question", "", "confusion"),
        ),
        RenderedPart(
            text=localized(restate_key, language, threshold=threshold or "—"),
            source=RenderSource.DETERMINISTIC_SUMMARY,
            proposition=Proposition("goal", "restated", goal.kind, "confusion"),
        ),
    ]
    # A question written in another language would switch the conversation mid-reply,
    # which is the exact failure this recovery exists to avoid repeating.
    question = goal.pending_question
    if question and not response_matches_language(question, language):
        question = ""
    reconciled = reconcile_reply(parts, clarification=question)
    seen = {item for item in previous_fingerprints if item}
    if reconciled.fingerprint in seen:
        # Saying the same thing again is the defect. Fall back to the one part that
        # still moves the conversation: the question on its own.
        question_only = goal.pending_question or localized("confusion.restate_generic", language)
        return ReconciledReply(
            message=question_only,
            fingerprint=response_fingerprint(question_only),
            duplicate_count=reconciled.duplicate_count + 1,
            dropped_identities=reconciled.dropped_identities,
            clarification=goal.pending_question,
        )
    return reconciled


def repeats_previous(text: str, previous_fingerprints: Iterable[str]) -> bool:
    """Whether this exact reply has already been sent in this conversation."""

    return response_fingerprint(text) in {item for item in previous_fingerprints if item}


def enforce_language(
    text: str,
    language: ConversationLanguage,
    *,
    fallback_key: str = "refuse.generic",
    **values: object,
) -> str:
    """Return ``text`` when it is in the conversation's language, else the server's own.

    This is the check the product was missing entirely. "Write in the user's language"
    was an instruction to a model and nothing else, so any turn that fell back to
    deterministic wording — a refusal, a summary, a safe error — switched the
    conversation to English mid-thread. Validating here means a wrong-language reply is
    replaced by correct-language wording the server owns, rather than shipped.
    """

    body = " ".join((text or "").split())
    if body and response_matches_language(body, language):
        return body
    return localized(fallback_key, language, **values)

"""The adversarial conversation corpus, and the six things that must be true of every case.

**What this file is not.** It is not a second copy of the committed language-quality
corpus. That one asks "does the product understand this sentence?", which is a quality
question with a spectrum of answers. This one asks "can this sentence make the product do
something it must never do?", which has exactly two answers. The two are kept apart
because a case that reads well as an understanding example is usually a poor attack, and
mixing them produces a file where a failure could mean either.

**Where the cases live.** ``tests/fixtures/oi_adversarial_qa_corpus.jsonl``, committed,
synthetic, and read through :mod:`hm_oi.conversation_source` — which will only open files
on its allowlist. Conversation redaction and retention are not finished in the product
(see ``docs/OI_AUTONOMOUS_BUILDER.md``), so no real customer conversation may be read by
this phase at all, and that is enforced in code rather than promised in a document.

**The safety half and the liveness half.**

Each invariant below has two directions, and only one of them belongs in an invariant
test:

*Safety* — the product must never end up holding something the trader did not say. A
question must not change a value. A rejected number must not survive. Approval must not
be inferred. These are owned entirely by the deterministic layer, they hold whatever the
model does, and they are asserted.

*Liveness* — the product should end up holding what the trader did say. Did the
correction actually land? Did the Arabic sentence compile? Those are owned partly by the
model, so asserting them here would fail for reasons that are not defects and would train
everyone to ignore the suite.

So the liveness direction is **measured and reported**, never asserted. A case where the
correction did not land is recorded as an observation with its numbers; a case where a
rejected value survived is a finding. :class:`InvariantVerdict` keeps the difference
visible rather than collapsing both into pass/fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from hm_oi.conversation_source import resolve_corpus
from hm_oi.redaction import redact_structure

__all__ = [
    "ADVERSARIAL_CORPUS_PATH",
    "AdversarialCase",
    "CORPUS_VERSION",
    "CaseShape",
    "ConversationInvariant",
    "InvariantVerdict",
    "REQUIRED_SHAPES",
    "InvariantResult",
    "load_adversarial_corpus",
    "shapes_covered",
]

#: Semantic version of the corpus content. The major number moves when a case's expected
#: outcome changes meaning; the minor when cases are added. A report quotes it so a run
#: from last month stays comparable.
CORPUS_VERSION: Final[str] = "1.1.0"

ADVERSARIAL_CORPUS_PATH: Final[str] = "tests/fixtures/oi_adversarial_qa_corpus.jsonl"


class CaseShape(StrEnum):
    """The seventeen conversational shapes this phase is required to cover.

    Named as an enum rather than free text so a missing shape is a failing test rather
    than something nobody notices.
    """

    GREETING_BEFORE_COMMAND = "greeting_before_command"
    THANKS_AFTER_COMMAND = "thanks_after_command"
    QUESTION_CONTAINING_NUMBERS = "question_containing_numbers"
    INSTRUCTION_PLUS_QUESTION = "instruction_plus_question"
    CORRECTION_PLUS_FRIENDLY_COMMENT = "correction_plus_friendly_comment"
    VAGUE_REFERENCE = "vague_reference"
    PREVIOUS_TURN_REFERENCE = "previous_turn_reference"
    ARABIC = "arabic"
    EGYPTIAN_ARABIC = "egyptian_arabic"
    ARABIZI = "arabizi"
    MIXED_ARABIC_ENGLISH = "mixed_arabic_english"
    TYPO_HEAVY = "typo_heavy"
    CONFLICTING_REQUESTS = "conflicting_requests"
    UNSUPPORTED_CONCEPTS = "unsupported_concepts"
    ACCIDENTAL_APPROVAL_LANGUAGE = "accidental_approval_language"
    FRUSTRATED_USER = "frustrated_user"
    REPEATED_CORRECTIONS = "repeated_corrections"


#: Every shape must appear. The corpus is incomplete without all seventeen, and an
#: incomplete adversarial corpus gives a false sense of coverage.
REQUIRED_SHAPES: Final[frozenset[CaseShape]] = frozenset(CaseShape)


class ConversationInvariant(StrEnum):
    """The six properties asserted on every case."""

    #: Greetings, thanks and praise never contribute a value to the draft.
    SOCIAL_TEXT_IS_NEVER_EXECUTABLE = "social_text_is_never_executable"
    #: A turn that only asks something changes nothing.
    A_QUESTION_IS_NEVER_A_MUTATION = "a_question_is_never_a_mutation"
    #: A correction never leaves the draft holding the value it rejected.
    A_CORRECTION_TARGETS_THE_CORRECT_OBJECT = "a_correction_targets_the_correct_object"
    #: A reference never resolves to something nobody said.
    REFERENCES_RESOLVE_CORRECTLY = "references_resolve_correctly"
    #: A capability the registry marks unsupported never becomes a supported one.
    UNSUPPORTED_CONCEPTS_STAY_UNSUPPORTED = "unsupported_concepts_stay_unsupported"
    #: Agreement, praise and thanks are never approval.
    APPROVAL_IS_NEVER_INFERRED = "approval_is_never_inferred"


class InvariantVerdict(StrEnum):
    """What checking one invariant on one case produced.

    ``NOT_APPLICABLE`` is a real answer and is reported as itself. Counting it as a pass
    would let a corpus that exercises nothing report six green invariants.
    """

    HOLDS = "holds"
    VIOLATED = "violated"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class InvariantResult:
    """One invariant checked against one case."""

    case_id: str
    invariant: ConversationInvariant
    verdict: InvariantVerdict
    #: What was seen, in words. Present even when it holds, so a reader can check.
    detail: str
    #: The liveness observation, where there is one: did the thing the trader wanted
    #: actually happen? Never part of the verdict.
    liveness_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "invariant": str(self.invariant),
            "verdict": str(self.verdict),
            "detail": self.detail,
            "liveness_note": self.liveness_note,
        }


@dataclass(frozen=True, slots=True)
class AdversarialCase:
    """One attack conversation and what must remain true after it."""

    case_id: str
    corpus_version: str
    shape: CaseShape
    language: str
    history: tuple[str, ...]
    prompt: str
    #: Spans of the prompt that are pure conversation. Used by the social-text invariant.
    social_spans: tuple[str, ...] = ()
    #: True when the whole turn only asks something.
    is_question_only: bool = False
    #: Always false in this corpus. Present so the field cannot be assumed.
    grants_approval: bool = False
    #: Values named only to be rejected. None of them may survive the turn.
    negated_values: tuple[Any, ...] = ()
    #: Boundary-registry keys the turn asks for and the product does not have.
    unsupported_concepts: tuple[str, ...] = ()
    #: The canonical field a correction is aimed at, if any.
    correction_target: str | None = None
    #: The canonical field a reference points at, if any.
    reference_resolves_to: str | None = None
    review_note: str = ""

    @property
    def turns(self) -> tuple[str, ...]:
        return (*self.history, self.prompt)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "corpus_version": self.corpus_version,
            "shape": str(self.shape),
            "language": self.language,
            "history": list(self.history),
            "prompt": self.prompt,
            "social_spans": list(self.social_spans),
            "is_question_only": self.is_question_only,
            "grants_approval": self.grants_approval,
            "negated_values": list(self.negated_values),
            "unsupported_concepts": list(self.unsupported_concepts),
            "correction_target": self.correction_target,
            "reference_resolves_to": self.reference_resolves_to,
            "review_note": self.review_note,
        }


class CorpusMalformed(ValueError):
    """A case is missing something the checkers need."""


def _tuple_of(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def load_adversarial_corpus(root: Path | None = None) -> tuple[AdversarialCase, ...]:
    """Read the corpus through the allowlist, redacted.

    Redaction runs even though every line is synthetic. Validation case 7 puts a
    synthetic key and a synthetic seed phrase into a fixture on purpose, and the
    guarantee is that nothing downstream sees them - which is only true if redaction
    happens where the file is read, not where a report is written.
    """

    path = resolve_corpus(ADVERSARIAL_CORPUS_PATH, root)
    cases: list[AdversarialCase] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CorpusMalformed(
                    f"{ADVERSARIAL_CORPUS_PATH}:{number} is not valid JSON: {exc}"
                ) from exc
            safe = redact_structure(raw, limit=4000)
            try:
                shape = CaseShape(str(safe["shape"]))
            except (KeyError, ValueError) as exc:
                raise CorpusMalformed(
                    f"{ADVERSARIAL_CORPUS_PATH}:{number} names an unknown shape "
                    f"{safe.get('shape')!r}. Allowed: "
                    + ", ".join(sorted(item.value for item in CaseShape))
                ) from exc
            case_id = str(safe.get("case_id") or "").strip()
            if not case_id:
                raise CorpusMalformed(f"{ADVERSARIAL_CORPUS_PATH}:{number} has no case_id.")
            if not str(safe.get("review_note") or "").strip():
                raise CorpusMalformed(
                    f"{ADVERSARIAL_CORPUS_PATH}:{number} ({case_id}) has no review_note. "
                    "A case nobody explained is a case nobody can judge."
                )
            cases.append(
                AdversarialCase(
                    case_id=case_id,
                    corpus_version=str(safe.get("corpus_version") or CORPUS_VERSION),
                    shape=shape,
                    language=str(safe.get("language") or "english"),
                    history=tuple(str(item) for item in _tuple_of(safe.get("history"))),
                    prompt=str(safe.get("prompt") or ""),
                    social_spans=tuple(
                        str(item) for item in _tuple_of(safe.get("social_spans"))
                    ),
                    is_question_only=bool(safe.get("is_question_only")),
                    grants_approval=bool(safe.get("grants_approval")),
                    negated_values=_tuple_of(safe.get("negated_values")),
                    unsupported_concepts=tuple(
                        str(item) for item in _tuple_of(safe.get("unsupported_concepts"))
                    ),
                    correction_target=(
                        str(safe["correction_target"])
                        if safe.get("correction_target")
                        else None
                    ),
                    reference_resolves_to=(
                        str(safe["reference_resolves_to"])
                        if safe.get("reference_resolves_to")
                        else None
                    ),
                    review_note=str(safe.get("review_note") or ""),
                )
            )
    if not cases:
        raise CorpusMalformed(f"{ADVERSARIAL_CORPUS_PATH} is empty.")
    return tuple(cases)


def shapes_covered(cases: tuple[AdversarialCase, ...]) -> frozenset[CaseShape]:
    return frozenset(case.shape for case in cases)


@dataclass(frozen=True, slots=True)
class CorpusRunSummary:
    """What a whole corpus run produced, counted by verdict."""

    corpus_version: str
    results: tuple[InvariantResult, ...] = field(default_factory=tuple)

    @property
    def violations(self) -> tuple[InvariantResult, ...]:
        return tuple(
            item for item in self.results if item.verdict is InvariantVerdict.VIOLATED
        )

    def counts(self) -> dict[str, int]:
        counted = {verdict.value: 0 for verdict in InvariantVerdict}
        for item in self.results:
            counted[item.verdict.value] += 1
        return counted

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_version": self.corpus_version,
            "counts": self.counts(),
            "results": [item.to_dict() for item in self.results],
        }

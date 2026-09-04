"""What kind of problem a review case is, said in words a person can sort by.

The Cases page printed one free-text sentence per row — whatever the pipeline, the
importer or a reader happened to write — under a column called "Why". Two hundred rows of
that is not a queue a person can work: the sentences are written by five different
producers, they answer five different questions, and none of them says which of the rows
are the *same* problem.

This module is the one owner of the answer. It reads only facts the case already carries
and returns two things:

* a **tag** — a short, fixed label naming the kind of problem, so rows of the same kind
  read the same and can be counted;
* a **reason** — one specific sentence about *this* asset, naming the exact thing that is
  missing or the exact thing to look at.

Three rules it keeps, and each one closes a real way of being wrong:

**It never says or implies a Shariah status.** No tag here means halal, haram, eligible or
excluded, and none is derived from reading text for suspicious words. The tag that comes
closest — :data:`ACTIVITY_TO_CHECK` — says *the research pipeline marked this one as
possibly affecting the methodology*, which is a routing signal the governed pipeline
already produced. It routes a case to a reviewer; the reviewer decides.

**It never asks a person for something the machine should find.** The source-gap tag says
the system keeps looking, because it does: ``sharia_source_resolution`` walks its layers
again on every sweep. Telling a reviewer to "add the correct address" for two hundred
coins was asking a person to do the machine's job.

**It reads only what a list view already has.** A tag that needed the evidence tables
would make one row cost one query, and the Cases page shows three hundred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_market_monitor.db.models.enums import ReviewCaseType
from ai_market_monitor.services.sharia_source_catalog import (
    REQUIRED_CATEGORIES,
    TRACKED_CATEGORIES,
    VERIFIED,
    category_label,
)

#: Every tag a case can carry. The key is what code and tests use; the label is what a
#: person reads; the tone is what the stylesheet paints.
#:
#: Ordered by how much they need a person, most first. :func:`classify` returns the first
#: one that fits, so a case that is several things at once is filed under the one that
#: actually stops it.
SOURCES_MISSING = "sources_missing"
ACTIVITY_TO_CHECK = "activity_to_check"
IDENTITY_UNCLEAR = "identity_unclear"
READER_REPORT = "reader_report"
EVIDENCE_DISAGREES = "evidence_disagrees"
FACTS_MISSING = "facts_missing"
RESEARCH_FAILED = "research_failed"
WORKING_ON_IT = "working_on_it"
ON_HOLD = "on_hold"
READY = "ready"
DECIDED = "decided"

Tone = Literal["attention", "watch", "waiting", "ready", "done"]


@dataclass(frozen=True, slots=True)
class CaseTagDefinition:
    key: str
    #: Two or three words. It is a column heading for one row, not a sentence.
    label: str
    tone: Tone
    #: What the tag means, for the tooltip and for anybody reading this file.
    meaning: str


TAG_DEFINITIONS: dict[str, CaseTagDefinition] = {
    SOURCES_MISSING: CaseTagDefinition(
        key=SOURCES_MISSING,
        label="Pages not found",
        tone="attention",
        meaning=(
            "The system has no working official news page for this coin yet. It keeps "
            "looking on every sweep."
        ),
    ),
    ACTIVITY_TO_CHECK: CaseTagDefinition(
        key=ACTIVITY_TO_CHECK,
        label="Read before deciding",
        tone="attention",
        meaning=(
            "The research marked something about this coin as possibly touching the "
            "methodology. It is a routing note, not an answer: only your review decides "
            "the Shariah status."
        ),
    ),
    IDENTITY_UNCLEAR: CaseTagDefinition(
        key=IDENTITY_UNCLEAR,
        label="Which coin is this?",
        tone="attention",
        meaning="Two records could be this coin, or none of them clearly is.",
    ),
    READER_REPORT: CaseTagDefinition(
        key=READER_REPORT,
        label="A reader wrote in",
        tone="attention",
        meaning="Somebody using the product says a published fact looks wrong.",
    ),
    EVIDENCE_DISAGREES: CaseTagDefinition(
        key=EVIDENCE_DISAGREES,
        label="Evidence disagrees",
        tone="watch",
        meaning="Two pieces of the evidence say different things and nobody has settled it.",
    ),
    FACTS_MISSING: CaseTagDefinition(
        key=FACTS_MISSING,
        label="Needs more facts",
        tone="watch",
        meaning="The research folder is not finished — some facts are still missing.",
    ),
    RESEARCH_FAILED: CaseTagDefinition(
        key=RESEARCH_FAILED,
        label="Research failed",
        tone="attention",
        meaning="The last attempt to gather the evidence did not finish.",
    ),
    WORKING_ON_IT: CaseTagDefinition(
        key=WORKING_ON_IT,
        label="System is working",
        tone="waiting",
        meaning="The evidence is being gathered right now. Nothing for you to do yet.",
    ),
    ON_HOLD: CaseTagDefinition(
        key=ON_HOLD,
        label="On safety hold",
        tone="attention",
        meaning="The public Passport was taken down and this needs looking at.",
    ),
    READY: CaseTagDefinition(
        key=READY,
        label="Ready for you",
        tone="ready",
        meaning="Everything the methodology asks for is in. It needs your decision.",
    ),
    DECIDED: CaseTagDefinition(
        key=DECIDED,
        label="Decided",
        tone="done",
        meaning="This one already has its final decision.",
    ),
}

#: States that mean the case is finished. Nothing is asked of anybody.
_FINISHED_STATES = frozenset(
    {"approved", "published", "rejected", "stored", "superseded", "resolved"}
)

#: How bad the research said the possible effect on the methodology could be, at or above
#: which the case is routed to a reviewer to read first.
#:
#: The words come from ``ShariaFactualAnalysis.potential_impact_severity``, which the
#: governed research pipeline writes onto the case as ``risk_severity``. Nothing here
#: reads free text looking for words like "interest" or "gambling" — that would be a
#: heuristic deciding a Shariah question, which this product never does.
_SEVERITIES_WORTH_READING = frozenset({"high", "critical"})


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    """How many working official links one asset holds, per required category."""

    proved: dict[str, int]
    #: Every address this coin has stored, working or not. An honest count of attempts.
    tried: int
    #: How many of those are **not** working right now.
    #:
    #: Its own number, because ``tried`` alone cannot carry the sentence the queue prints.
    #: "10 address(es) have been tried and none worked yet" was built from ``tried`` on
    #: its own, so a coin holding two working community pages and eight dead news guesses
    #: was described as having ten failures — and the reviewer was told nothing worked
    #: when two things plainly did. Counting the failures separately is what lets the
    #: sentence say only what is true.
    failed: int = 0

    @property
    def working(self) -> int:
        """Addresses that answer and count. Never negative, whatever the caller passed."""

        return max(self.tried - self.failed, 0)

    @property
    def missing_categories(self) -> tuple[str, ...]:
        """Which **required** categories this asset cannot show one working link for.

        ``proved`` counts every tracked category, community included, so a page can show
        how many of each a coin holds. Only the required ones can be *missing*: a project
        that runs no forum has nothing anybody can go and find, and reporting that as a
        gap filled the queue with rows no reviewer could ever clear.
        """

        return tuple(
            category for category in REQUIRED_CATEGORIES if self.proved.get(category, 0) < 1
        )


EMPTY_COVERAGE = SourceCoverage(proved={}, tried=0)


def coverage_from_rows(rows) -> dict:
    """Turn plain ``(asset_id, category, verification_state, is_active)`` rows into coverage.

    Takes rows rather than model objects on purpose: the Cases page selects four columns
    for three hundred assets, and loading whole ``OfficialSource`` objects to count them
    is what turned another list view into a 1.6 GB query.
    """

    proved: dict[object, dict[str, int]] = {}
    tried: dict[object, int] = {}
    failed: dict[object, int] = {}
    for asset_id, category, verification_state, is_active in rows:
        tried[asset_id] = tried.get(asset_id, 0) + 1
        if verification_state != VERIFIED or not is_active:
            # Counted here and not just skipped. A row that is switched off or was never
            # proved is an address that did not work, and the queue's sentence is built
            # from that number — not from how many rows exist.
            failed[asset_id] = failed.get(asset_id, 0) + 1
            continue
        if category not in TRACKED_CATEGORIES:
            continue
        bucket = proved.setdefault(asset_id, {})
        bucket[category] = bucket.get(category, 0) + 1
    return {
        asset_id: SourceCoverage(
            proved=proved.get(asset_id, {}),
            tried=count,
            failed=failed.get(asset_id, 0),
        )
        for asset_id, count in tried.items()
    }


@dataclass(frozen=True, slots=True)
class CaseSignal:
    """One case's kind of problem, and the one sentence that explains it."""

    tag: str
    reason: str

    @property
    def label(self) -> str:
        return TAG_DEFINITIONS[self.tag].label

    @property
    def tone(self) -> str:
        return TAG_DEFINITIONS[self.tag].tone

    @property
    def meaning(self) -> str:
        return TAG_DEFINITIONS[self.tag].meaning


def classify(
    *,
    case_type: str,
    state: str,
    risk_severity: str,
    asset_name: str,
    done: bool,
    dossier_present: bool,
    evidence_completeness: float,
    missing_information_count: int,
    contradiction_count: int,
    coverage: SourceCoverage = EMPTY_COVERAGE,
    fallback_reason: str = "",
) -> CaseSignal:
    """Which kind of problem this case is, and why — in this coin's own words.

    Every argument is a fact the case already carries. Nothing is fetched, nothing is
    guessed, and no argument is a piece of free text that gets searched for keywords.
    """

    name = (asset_name or "this coin").strip() or "this coin"

    if done or state in _FINISHED_STATES:
        return CaseSignal(DECIDED, f"{name} already has its final decision.")

    if state == "safety_hold":
        return CaseSignal(
            ON_HOLD,
            f"The public Passport for {name} was taken down and is waiting for a fresh look.",
        )

    if case_type == ReviewCaseType.OFFICIAL_SOURCE_GAP:
        return CaseSignal(SOURCES_MISSING, _source_gap_reason(name, coverage))

    if case_type == ReviewCaseType.SOURCE_IDENTITY_CONFLICT:
        return CaseSignal(
            IDENTITY_UNCLEAR,
            f"It is not settled which coin this row is. Confirm the identity for {name} "
            "before anything else.",
        )

    if case_type == ReviewCaseType.USER_FACTUAL_REPORT:
        return CaseSignal(
            READER_REPORT,
            f"A reader says a published fact about {name} is wrong. Check it against the "
            "official pages.",
        )

    if state == "research_failed":
        return CaseSignal(
            RESEARCH_FAILED,
            f"The last attempt to gather evidence for {name} did not finish. Send it for "
            "research again.",
        )

    if state == "researching":
        return CaseSignal(
            WORKING_ON_IT,
            f"The system is gathering the evidence for {name} right now. Come back when "
            "it finishes.",
        )

    # A source that changed, or research that flagged a possible effect. Both come from
    # the pipeline's own structured verdict, never from reading words on a page.
    if case_type == ReviewCaseType.MATERIAL_SOURCE_CHANGE or (
        risk_severity in _SEVERITIES_WORTH_READING
    ):
        return CaseSignal(ACTIVITY_TO_CHECK, _activity_reason(name, case_type, risk_severity))

    if not dossier_present:
        return CaseSignal(
            FACTS_MISSING,
            f"{name} has no research folder yet, so there is nothing to decide on. Send "
            "it for research.",
        )

    if contradiction_count > 0:
        return CaseSignal(
            EVIDENCE_DISAGREES,
            f"{contradiction_count} thing(s) in the evidence for {name} disagree with "
            "each other. Read them and settle it.",
        )

    if missing_information_count > 0 or evidence_completeness < 1:
        return CaseSignal(
            FACTS_MISSING,
            _facts_reason(name, evidence_completeness, missing_information_count),
        )

    if state == "ready_for_review":
        return CaseSignal(
            READY,
            f"Everything the methodology asks for about {name} is in. It needs your decision.",
        )

    # Nothing above fits. Keep the producer's own sentence rather than inventing one: an
    # explanation nobody wrote is worse than a plain one, because a reviewer acts on it.
    return CaseSignal(
        FACTS_MISSING,
        fallback_reason.strip() or f"{name} is waiting on evidence that is not in yet.",
    )


def _source_gap_reason(name: str, coverage: SourceCoverage) -> str:
    """Why this coin still needs a page, saying only what is true of its addresses.

    Three different situations produce this case and they must not share one sentence:

    * nothing has been tried yet — say that, and do not claim any address failed;
    * things were tried and **every one** failed — "none worked yet" is then true;
    * things were tried, some of them work, and the coin is still short of the one
      category the product requires. This is the case the old wording got wrong. It
      counted every stored address, working ones included, and then said none of them
      worked — so a coin with two live community pages and eight dead news guesses was
      reported as ten failures. A reviewer reading "none worked" about pages they can
      open in a browser stops believing the queue.
    """

    missing = coverage.missing_categories
    what = (
        " or ".join(category_label(item) for item in missing) if missing else "official news"
    )
    if not coverage.tried:
        tried = "no address has been tried yet"
    elif not coverage.working:
        tried = f"{coverage.failed} address(es) have been tried and none worked yet"
    elif not coverage.failed:
        tried = f"its {coverage.working} working address(es) are not {what} pages"
    else:
        tried = (
            f"{coverage.failed} address(es) have been tried without success, "
            f"and the {coverage.working} that do work are not {what} pages"
        )
    return (
        f"No working {what} page for {name}: {tried}. The system keeps looking by itself "
        "on every sweep — open this only if you already know the right address."
    )


def _activity_reason(name: str, case_type: str, risk_severity: str) -> str:
    if case_type == ReviewCaseType.MATERIAL_SOURCE_CHANGE:
        return (
            f"An official page for {name} changed in a way the research marked as "
            "possibly affecting the methodology. Read the change before you decide."
        )
    return (
        f"The research marked {name} as {risk_severity} — something it does may touch the "
        "methodology. Read the research folder before you decide."
    )


def _facts_reason(name: str, completeness: float, missing: int) -> str:
    percent = max(0, min(100, round(float(completeness or 0) * 100)))
    if missing > 0:
        return (
            f"The research folder for {name} is {percent}% complete and {missing} "
            "fact(s) the methodology asks for are still missing."
        )
    return (
        f"The research folder for {name} is {percent}% complete, so some official pages "
        "could not be read."
    )

"""From a folder of a project's own pages to one answerable verdict.

Three modules meet here and each keeps its own job:

    ``coin_evidence_crawler``       gathers the pages          — no judgement
    ``sharia_evidence_vocabulary``  reads words into facts     — no rule
    ``sharia_automated_screen``     applies the rule to facts  — no reading

This is the seam between them. It turns what the vocabulary found into the typed facts
the screen consumes, runs the screen, and attaches to every reason the sentence and the
page address it came from — so a verdict can always be checked by opening the page.

**When the answer is "we cannot say".** A coin goes to *Not enough data* on one
condition only: **the folder holds nothing that speaks to the question**. That is either
no readable page at all, or pages that never describe what the project does. It is not
used because one field was awkward. Once there is something to reason about, this
reaches a verdict, and where the evidence is thin the verdict is the careful one.

That rule exists because the failure it replaces was worse than being wrong. A screen
that answers "not enough data" whenever a detail is missing produces a queue nobody can
clear and a product that says nothing about most of the market. A reader is better
served by "we read four pages and this is what they say" than by silence.

**Fail closed, still.** Reaching a verdict is not the same as guessing at one. Where the
project's own words leave a decisive question open, the answer is the refusing one, and
the reason says exactly which question was left open. Nothing here ever resolves a doubt
in the coin's favour.

**What this screen does not attempt, and why it says so once rather than every time.**
Some approved conditions cannot be settled by reading a website at all: whether an
exchange is riba al-fadl needs the mechanism, not the marketing; a debt ratio needs a
balance sheet no coin data provider publishes. Those rules are marked in the register as
needing a person or a number, and this module **skips them silently**.

That is a deliberate reversal, decided on 31 August 2026. They were briefly reported on
every verdict as "unchecked". The reason for dropping that: nobody can act on it. Neither
the product owner nor any Shariah provider can settle riba al-fadl from a queue entry, so
the queue was work that could never be cleared — and a flag that appears on every single
coin carries no information anyway. The screen's reach is instead stated once, plainly,
in the methodology note and in the disclosure that travels with every result: it reads up
to :data:`coin_evidence_crawler.DEFAULT_PAGE_BUDGET` of the project's own pages, and what
cannot be established from those is out of its scope.

The honesty this preserves is the one that matters: nothing skipped here is ever counted
as *passed*. A skipped rule contributes nothing in either direction, and the result is
published under a methodology whose every surface says no scholar reviewed it.

**Nothing this module produces is a Shariah status.** It is a proposal, published under
the Hilal Markets Methodology, marked as not reviewed by any scholar, and it never
touches an authority's assessment. Only the application's own approval route can publish
anything, and it is never called from here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil
from typing import Any

from ai_market_monitor.services.coin_evidence_crawler import EvidenceFolder
from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
    GOVERNANCE_ACTIVITIES,
    METHODOLOGY_SYSTEM_CODE,
    RETURN_SENSITIVE_ACTIVITIES,
    Activity,
    AssetFacts,
    HolderReturn,
    Verdict,
    screen,
)
from ai_market_monitor.services.sharia_conditions import (
    Status,
    condition,
    status_of,
)
from ai_market_monitor.services.sharia_evidence_vocabulary import (
    Finding,
    ReadResult,
    project_terms_for,
    read_documents,
    resolve_holder_return,
)


class EvidenceVerdict(StrEnum):
    """What the evidence supports. Three answers, and they are not interchangeable."""

    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    #: The folder says nothing about what this project does. A request for research,
    #: never a soft "no" — a coin here has not been refused and must never be shown as
    #: though it had been.
    NOT_ENOUGH_DATA = "not_enough_data"


#: Why a coin ended up in *Not enough data*, in words a beginner can act on.
NO_EVIDENCE_REASONS: dict[str, str] = {
    "no_pages_read": (
        "We could not read any page from this project, so we have nothing to judge."
    ),
    "pages_say_nothing": (
        "We read the project's pages, but none of them says what the project actually "
        "does, so there is nothing to judge yet."
    ),
    "governance_only": (
        "The pages say the token is used for voting, but never say what the business "
        "behind it does. Voting on its own does not tell us enough."
    ),
    "too_little_read": (
        "We could not read enough of what this project writes about itself. Nothing we "
        "did read worried us, but that is too little to say a project is clean, so we "
        "have not judged it yet."
    ),
}


@dataclass(frozen=True, slots=True)
class GroundedReason:
    """One sentence of the verdict, and the page that supports it."""

    text: str
    quote: str = ""
    url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "quote": self.quote, "url": self.url}


@dataclass(slots=True)
class EvidenceDecision:
    """A verdict, everything it rests on, and everything it could not settle."""

    symbol: str
    name: str
    verdict: EvidenceVerdict
    reasons: list[GroundedReason] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    blocking_activities: list[Activity] = field(default_factory=list)
    holder_return: HolderReturn | None = None
    holder_return_basis: str = ""
    #: Questions the project's own pages left open. Present on a refusal as well as on a
    #: pass, because "we decided despite this" is something a reader is owed.
    open_questions: list[str] = field(default_factory=list)
    documents_read: int = 0
    primary_documents_read: int = 0
    findings: list[Finding] = field(default_factory=list)
    #: Codes of the approved conditions that actually refused this coin.
    matched_conditions: list[str] = field(default_factory=list)
    #: Codes of conditions that matched but are **not approved**, so they changed
    #: nothing. Carried so the owner can see what approving each would have done, on
    #: real coins, before deciding. Never a refusal and never shown as one.
    proposed_matches: list[str] = field(default_factory=list)

    @property
    def is_publishable_proposal(self) -> bool:
        """Could a reviewer be shown this as a candidate? Never a publication."""

        return self.verdict is not EvidenceVerdict.NOT_ENOUGH_DATA

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "verdict": self.verdict.value,
            "reasons": [item.as_dict() for item in self.reasons],
            "activities": [item.value for item in self.activities],
            "blocking_activities": [item.value for item in self.blocking_activities],
            "holder_return": self.holder_return.value if self.holder_return else None,
            "holder_return_basis": self.holder_return_basis,
            "open_questions": list(self.open_questions),
            "documents_read": self.documents_read,
            "primary_documents_read": self.primary_documents_read,
            "evidence": [item.as_dict() for item in self.findings],
            "matched_conditions": list(self.matched_conditions),
            "proposed_matches": list(self.proposed_matches),
            "methodology": METHODOLOGY_SYSTEM_CODE,
            "human_reviewed": False,
            "disclosure": AUTOMATED_DISCLOSURE,
        }


def decide(
    symbol: str,
    name: str,
    folder: EvidenceFolder,
    *,
    also_known_as: Sequence[str] = (),
) -> EvidenceDecision:
    """Read one coin's folder and answer for it.

    ``also_known_as`` carries the project's other names — its provider slug, the company
    that issues it, the hosts it publishes under. It matters because "this project" is
    otherwise worked out from the ticker and the coin's name alone, and plenty of tokens
    are issued under a different name: GHO is Aave's, EIGEN is EigenLayer's. Without the
    other names, "Aave Labs operates the lending protocol" on GHO's own page reads as a
    sentence about somebody else, and the refusal is dropped — the direction of error
    that matters most.
    """

    # The reader is told whose pages these are. Without it, a sentence crediting another
    # company's lending business — "Folks Finance is ready. From lending and borrowing…"
    # on Algorand's own homepage — counts against this coin instead of that one.
    read = read_documents(
        folder.documents,
        project_terms=project_terms_for(symbol, name, *also_known_as),
    )
    decision = EvidenceDecision(
        symbol=symbol.upper(),
        name=name,
        verdict=EvidenceVerdict.NOT_ENOUGH_DATA,
        documents_read=len(folder.documents),
        primary_documents_read=len(folder.primary_documents),
        findings=list(read.findings),
    )

    if folder.is_empty:
        decision.reasons = [GroundedReason(NO_EVIDENCE_REASONS["no_pages_read"])]
        return decision

    activities, matched, previewed = _activities_from(read, len(folder.primary_documents))
    decision.matched_conditions = sorted(matched)
    decision.proposed_matches = sorted(previewed)
    if not activities:
        decision.reasons = [GroundedReason(NO_EVIDENCE_REASONS["pages_say_nothing"])]
        return decision
    if activities <= GOVERNANCE_ACTIVITIES:
        decision.reasons = [GroundedReason(NO_EVIDENCE_REASONS["governance_only"])]
        return decision

    if not enough_was_read(len(folder.primary_documents)):
        # Before any conclusion, in either direction. Below this line the screen draws
        # findings from what the pages do *not* say — that nothing refuses the coin, and
        # that a token paying no yield has not mentioned one — and neither statement is
        # about the project until enough of the project's own writing has been read.
        #
        # It must also sit **above** the unanswered-question branch further down, which
        # turns a fact the pages never supplied into a refusal. That branch is right when
        # a project described itself at length and still never answered; applied to a
        # single marketing page it turns silence into a "no", which is the one thing
        # *Not enough data* exists to prevent. The token `U` was refused exactly that way.
        decision.reasons = [GroundedReason(NO_EVIDENCE_REASONS["too_little_read"])]
        return decision

    decision.activities = sorted(activities, key=lambda item: item.value)
    holder_return, basis = _holder_return_for(read, activities, folder, decision)
    decision.holder_return = holder_return
    decision.holder_return_basis = basis

    # A governance token is a claim on a business, and the pages we read are that
    # business's own pages — so everything else found on them *is* what it governs.
    governed = (
        frozenset(activities - GOVERNANCE_ACTIVITIES)
        if activities & GOVERNANCE_ACTIVITIES
        else frozenset()
    )
    facts = AssetFacts(
        canonical_symbol=decision.symbol,
        asset_name=name,
        activities=frozenset(activities),
        holder_return=holder_return,
        governed_activities=governed,
    )
    result = screen(facts)

    if result.verdict is Verdict.INSUFFICIENT_FACTS:
        # The screen refuses to answer without a fact this evidence never supplied. The
        # folder is not empty, so the coin is not sent back to the queue: the unanswered
        # question becomes the refusal, named, so it can be researched and cleared.
        decision.verdict = EvidenceVerdict.NOT_ELIGIBLE
        decision.open_questions = [
            _QUESTION_WORDING.get(item, item) for item in result.missing_facts
        ]
        decision.reasons = [
            GroundedReason(
                "We read the project's own pages and they never answer this: "
                + _QUESTION_WORDING.get(item, item)
            )
            for item in result.missing_facts
        ]
        return decision

    decision.blocking_activities = list(result.blocking_activities)
    if result.verdict is Verdict.NOT_ELIGIBLE:
        decision.verdict = EvidenceVerdict.NOT_ELIGIBLE
        decision.reasons = _grounded_refusals(
            result.reasons, result.blocking_activities, read, basis
        )
        return decision

    decision.verdict = EvidenceVerdict.ELIGIBLE
    decision.reasons = _grounded_pass(activities, read)
    return decision


#: The screen names a missing field; a reader needs the question behind it.
_QUESTION_WORDING: dict[str, str] = {
    "holder_return": (
        "does simply holding this token pay the holder anything, and if so, "
        "where does that money come from?"
    ),
    "governed_activities": "what does the business behind this token actually do?",
    "activities": "what does this project do?",
    "canonical_symbol": "which coin is this?",
}


#: What it takes for a page to be *describing* a business rather than *mentioning* it.
#:
#: Either the project said it on two separate pages of its own, or it said it three
#: times on one. Both are the same test in different shapes: a business repeats itself,
#: and a passing reference does not.
#:
#: This is the rule that stopped Ethereum being refused. One paragraph on one page of
#: ethereum.org carried a news digest reading "Lending protocol Moonwell suffered an
#: $8.7 million loss" — somebody else's protocol, named once, in a sentence about a
#: hack. A single mention was enough to refuse Ethereum for running a lending business.
#: Aave's documentation, by contrast, says "lending" on page after page, which is what
#: a lending business looks like in writing.
CORROBORATING_PAGES = 2
CORROBORATING_MENTIONS = 3

#: What share of the project's own descriptive pages must carry a blocking activity.
#:
#: **A fixed page count is not a threshold, it is a threshold divided by a folder size
#: that used to be constant.** "Two pages" meant "two of about ten" while the crawler
#: read twelve pages. On 31 August 2026 the budget went to eighty, and the same rule
#: silently became "two of forty-three" — four per cent, which is what a passing mention
#: looks like, not what a business looks like.
#:
#: It refused Ethereum the same day, on exactly the sentence the corroboration rule was
#: written to stop: "Lending protocol Moonwell suffered an $8.7 million price
#: manipulation attack", one line of a news digest on the front page.
#:
#: So the bar rises with what was read. A business that really lends says so on a
#: sizeable share of its own pages, however many of them we happen to read; a passing
#: reference does not become a description because we read further.
CORROBORATING_SHARE = 0.12


def _corroboration_bar(primary_pages: int) -> int:
    """How many of a project's own pages must carry a refusal before it counts.

    Never below :data:`CORROBORATING_PAGES`, so a small folder behaves exactly as it did
    when the screen was measured at 14/18 — at twelve pages the share rounds to two and
    nothing changes.
    """

    return max(CORROBORATING_PAGES, ceil(primary_pages * CORROBORATING_SHARE))


def enough_was_read(primary_pages: int) -> bool:
    """Whether the reading is wide enough for **absence** to mean anything.

    Two of this module's answers are conclusions drawn from silence, and both used to be
    reachable from a single page:

    * *"holding it pays nothing"* — concluded because no page said otherwise;
    * *"eligible"* — concluded because no approved condition fired.

    Neither is a statement about the project until enough of the project's own writing
    has been read that a mention would have been found. On 31 August 2026 the token
    ``U`` was admitted as eligible on the strength of **one** marketing homepage: the
    screen decided a stablecoin pays its holders nothing because a page that sells the
    peg did not discuss yield. That is the exact failure :class:`HolderReturn` exists to
    prevent — Dai, GHO, USD0, AUSD and FRXUSD all passed once for the same reason — and
    it had simply moved from the facts path to the evidence path.

    The floor is :data:`CORROBORATING_PAGES`, the number a *refusal* already needs, and
    it is the same number on purpose: **a finding needs the same weight of the project's
    own writing whichever direction it points.** A screen that demands two pages to
    refuse and accepts one to pass is not cautious, it is biased towards passing.

    Measured before it was adopted: on the twenty coins of the blind run it moves **no**
    coin at all, and on the ten new ones it moves exactly the coin decided from a single
    page. A floor of three would have moved two coins of the measured baseline, and
    three is not a number anything here can justify.
    """

    return primary_pages >= CORROBORATING_PAGES


def _activities_from(
    read: ReadResult, primary_pages: int = 0
) -> tuple[set[Activity], set[str], set[str]]:
    """What the folder supports: the facts, the rules that fired, and the previews.

    Three guards on a refusal, and none of them guards anything else.

    *Whether the rule is approved.* A condition the owner has not approved cannot become
    a fact at all. It is collected separately as a preview — "this is what approving it
    would have caught" — and it never reaches the screen. This is the governance rule
    written as code: only the owner's decision file can make a rule bite.

    *Where it was said.* An approved condition only counts from a page where the project
    describes itself. A project's newsroom writes about the whole market, so "lending"
    appears on the blog of every chain that has ever had a lending protocol deployed on
    it, and counting that refuses the chain for what somebody else built.

    *How often it was said.* Even on its own pages, one passing mention is not a
    description. See :data:`CORROBORATING_PAGES`.

    Everything descriptive is counted wherever it is found and however rarely. There is
    no harm in learning from a single blog post that a network has validators, and
    requiring corroboration there would only lose real findings.
    """

    allowed: set[Activity] = set()
    matched: set[str] = set()
    previewed: set[str] = set()
    support: dict[str, list[tuple[str, int]]] = {}
    for finding in read.findings:
        # Status first. A proposed riba condition carries no activity at all — it answers
        # the return question — so testing the activity before the status would drop its
        # preview on the floor and the owner would be asked to approve a rule they had
        # never been shown the effect of.
        if finding.condition_code and status_of(finding.condition_code) is not Status.APPROVED:
            previewed.add(finding.condition_code)
            continue
        activity = finding.activity
        if activity is None:
            continue
        if not finding.condition_code:
            allowed.add(activity)  # descriptive, and it can never refuse
            continue
        if finding.primary:
            support.setdefault(finding.condition_code, []).append(
                (finding.url, finding.occurrences)
            )

    bar = _corroboration_bar(primary_pages)
    for code, evidence in support.items():
        pages = len({url for url, _count in evidence})
        mentions = max((count for _url, count in evidence), default=0)
        if pages >= bar or mentions >= CORROBORATING_MENTIONS:
            matched.add(code)
            rule = condition(code)
            if rule.activity is not None:
                allowed.add(rule.activity)
    return allowed, matched, previewed


def _holder_return_for(
    read: ReadResult,
    activities: set[Activity],
    folder: EvidenceFolder,
    decision: EvidenceDecision,
) -> tuple[HolderReturn | None, str]:
    """What holding this token pays, including when the answer is "nothing".

    The vocabulary answers this whenever the pages say anything about a return. When
    they say nothing at all, one case still needs an answer: a token whose whole product
    is a peg. For that token the question is decisive, and a complete set of the
    project's own descriptive pages that never once mentions paying its holders is
    itself the answer — this is a product that does not pay them.

    That reading is only allowed from **primary** pages, and only from enough of them —
    see :func:`enough_was_read`. The sentence above says "a complete set of the
    project's own descriptive pages", and for a long time the code behind it said
    ``if folder.primary_documents:`` — one page was a complete set. A promise in prose
    is not a gate, and this one let a stablecoin's marketing homepage answer the single
    question that decides a stablecoin.

    :func:`decide` already refuses to reach this function without enough pages, so the
    guard below is currently never the thing that stops it. It stays because it is the
    condition that makes *this* function's claim true, and a helper whose correctness
    depends on the order its caller happens to check things in is one bad refactor away
    from being wrong again.
    """

    resolved, basis = resolve_holder_return(read)
    if resolved is not None:
        return resolved, basis
    if not activities & RETURN_SENSITIVE_ACTIVITIES:
        return None, basis
    if enough_was_read(len(folder.primary_documents)):
        return (
            HolderReturn.NONE,
            "the project's own description never says holding it pays anything",
        )
    decision.open_questions.append(_QUESTION_WORDING["holder_return"])
    return None, "too little of the project's own writing could be read to tell"


def _grounded_refusals(
    reasons: tuple[str, ...],
    blocking: tuple[Activity, ...],
    read: ReadResult,
    holder_return_basis: str,
) -> list[GroundedReason]:
    grounded: list[GroundedReason] = []
    for index, text in enumerate(reasons):
        activity = blocking[index] if index < len(blocking) else None
        evidence = read.for_activity(activity)[:1] if activity is not None else []
        if evidence:
            grounded.append(
                GroundedReason(text=text, quote=evidence[0].quote, url=evidence[0].url)
            )
        elif activity is Activity.INTEREST_BEARING_HOLDING:
            # The interest refusal comes from the return question, not from an activity
            # phrase, so its support is the sentence that settled that question.
            support = next(
                (item for item in read.findings if item.return_kind), None
            )
            grounded.append(
                GroundedReason(
                    text=f"{text} We say this because {holder_return_basis}.",
                    quote=support.quote if support else "",
                    url=support.url if support else "",
                )
            )
        else:
            grounded.append(GroundedReason(text=text))
    return grounded


def _grounded_pass(activities: set[Activity], read: ReadResult) -> list[GroundedReason]:
    """What the coin does, said plainly, with a page for each claim."""

    grounded: list[GroundedReason] = []
    for activity in sorted(activities, key=lambda item: item.value):
        evidence = read.for_activity(activity)[:1]
        text = ACTIVITY_IN_PLAIN_WORDS.get(activity, activity.value.replace("_", " "))
        if evidence:
            grounded.append(
                GroundedReason(text=text, quote=evidence[0].quote, url=evidence[0].url)
            )
        else:
            grounded.append(GroundedReason(text=text))
    return grounded


#: What each activity means, for somebody who has never read a whitepaper.
ACTIVITY_IN_PLAIN_WORDS: dict[Activity, str] = {
    Activity.OWN_SETTLEMENT_NETWORK: "It runs its own blockchain network.",
    Activity.STAKING_OR_VALIDATION: (
        "People are paid for helping run and secure the network."
    ),
    Activity.SPOT_EXCHANGE: "It lets people swap one coin for another.",
    Activity.INFRASTRUCTURE_SERVICE: (
        "It sells a service to other software — data, storage, computing or connections."
    ),
    Activity.CONSUMER_APPLICATION: "It is an app or a game people use.",
    Activity.FULLY_BACKED_REDEEMABLE: (
        "Each coin stands for something real that is held and can be given back."
    ),
    Activity.PLATFORM_ACCESS_OR_GOVERNANCE: (
        "The coin is used to vote or to get access inside one platform."
    ),
    Activity.LENDING_BORROWING: "Its business is lending money.",
    Activity.DERIVATIVES_OR_LEVERAGE: "Its business is leverage or betting on prices.",
    Activity.GAMBLING: "Its business is betting.",
    Activity.TOKENIZED_SECURITY: "The coin stands for a share or a bond.",
    Activity.NO_UNDERLYING_UTILITY: "There is no product behind the coin.",
    Activity.INTEREST_BEARING_HOLDING: "Holding the coin pays the holder a return.",
    Activity.CONVENTIONAL_FINANCE: (
        "Its business is banking, insurance or credit of the ordinary interest-paying kind."
    ),
    Activity.PROHIBITED_GOODS: "It sells alcohol, pork, tobacco or drugs.",
    Activity.ADULT_OR_IMMORAL_TRADE: "Its business is adult material.",
    Activity.OCCULT_OR_IDOLATRY: "It sells fortune telling, magic, or objects of worship.",
    Activity.UNLAWFUL_WEAPONS: "It sells weapons for unlawful fighting.",
    Activity.DISPUTED_ENTERTAINMENT: (
        "Its business is music, film or nightlife. Scholars disagree about this one."
    ),
    Activity.PONZI_OR_PYRAMID: "It pays old members with new members' money.",
    Activity.MARKET_MANIPULATION: "It makes money by pushing prices around unfairly.",
    Activity.DECEPTIVE_DISCLOSURE: "It hides something a buyer needs to know.",
    Activity.SELLING_WHAT_IS_NOT_OWNED: "It sells things the seller does not own yet.",
    Activity.DEBT_TRADING: "Its business is buying and selling other people's debts.",
    Activity.DEFERRED_CURRENCY_EXCHANGE: (
        "It swaps one money for another without both sides handing over at the same time."
    ),
    Activity.UNBACKED_COMMODITY_CLAIM: (
        "It promises something real behind the coin that is not actually held."
    ),
    Activity.HOARDING_OR_CONTROL: "A small group controls the supply or the voting.",
    Activity.ILLICIT_FINANCE_SERVICE: "It sells itself as a way to hide money.",
    Activity.CORRUPT_CONTRACT_FORM: "The deal is written in a shape Islam does not allow.",
    Activity.GUARANTEED_CAPITAL_OR_RETURN: (
        "It promises your money back whatever happens, which turns it into a loan."
    ),
    Activity.MIXED_PROHIBITED_INCOME: (
        "Part of its money comes from a forbidden source."
    ),
    Activity.MISUSE_OF_CUSTOMER_ASSETS: (
        "It uses what customers left with it without asking them."
    ),
    Activity.DISPUTED_LIQUIDITY_PROVISION: (
        "People who add money to its pools can get back a different mix than they put in."
    ),
}


__all__ = [
    "ACTIVITY_IN_PLAIN_WORDS",
    "NO_EVIDENCE_REASONS",
    "EvidenceDecision",
    "EvidenceVerdict",
    "GroundedReason",
    "decide",
]

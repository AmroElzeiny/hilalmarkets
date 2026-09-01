"""The rules the condition register must hold, for every condition, not for examples.

The register decides what the product will say about people's money, so the properties
here are governance guarantees rather than code hygiene. Three matter most, and each is
asserted across the whole family:

1. **An unapproved condition changes nothing.** Every proposed rule is fed a page that
   states it plainly, three times, on the project's own documentation — the strongest
   evidence the screen accepts — and must still not refuse the coin.
2. **An approved condition does refuse.** The mirror. Without it the first property
   could be satisfied by a register that does nothing at all.
3. **The measured vocabulary is pinned.** The four rules that were blind-measured at
   14/18 on 30 August 2026 carry their exact phrase lists. Widening one silently would
   invalidate that number while leaving it printed in the documentation.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_market_monitor.services.coin_evidence_crawler import (
    EvidenceDocument,
    EvidenceFolder,
)
from ai_market_monitor.services.sharia_conditions import (
    CONDITIONS,
    DECISIONS_FILE,
    Activity,
    Condition,
    Detection,
    HolderReturn,
    Status,
    applied_conditions,
    approved_conditions,
    blocking_activities,
    conditions_by_status,
    out_of_reach_conditions,
    status_of,
    text_detectable_conditions,
)
from ai_market_monitor.services.sharia_evidence_screen import (
    ACTIVITY_IN_PLAIN_WORDS,
    CORROBORATING_PAGES,
    EvidenceVerdict,
    _corroboration_bar,
    decide,
)
from ai_market_monitor.services.sharia_evidence_vocabulary import (
    ACTIVITY_SIGNALS,
    CONDITION_SIGNALS,
    SOURCE_IS_LENDING_PHRASES,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)

TEXT_CONDITIONS = list(text_detectable_conditions())
PROPOSED_TEXT = [c for c in TEXT_CONDITIONS if status_of(c.code) is not Status.APPROVED]
APPROVED_TEXT = [c for c in TEXT_CONDITIONS if status_of(c.code) is Status.APPROVED]


def _ids(items: list[Condition]) -> list[str]:
    return [item.code for item in items]


def _stating(phrase: str, *, item: Condition | None = None) -> EvidenceFolder:
    """A folder that states one phrase as plainly and as often as the screen allows.

    Three mentions on the project's own documentation clears
    :data:`CORROBORATING_MENTIONS`, so anything that *can* refuse, will. Nothing weaker
    would prove that a proposed rule is inert rather than merely under-evidenced.

    A condition that answers the **return** question needs one thing more. Its phrases
    say where a holder's money comes from, not what the project does, so a page carrying
    only those lands in *Not enough data* — correctly, since it never said what the
    project is. Those conditions are given a token whose whole product is a peg, which
    is the case where "what does holding it pay?" actually decides the verdict.

    **Two pages, not one.** The screen refuses to conclude anything from fewer than
    ``CORROBORATING_PAGES`` of a project's own pages — see
    ``sharia_evidence_screen.enough_was_read`` — because "nothing refused it" and "it
    pays its holders nothing" are both claims about the reading until enough has been
    read. A folder of one page therefore tests that floor rather than the vocabulary,
    which is not what any test in this file is about. The second page is an "about the
    team" page carrying nothing the vocabulary matches: it widens the reading without
    adding a fact to it.
    """

    opening = (
        "THING is a stablecoin, fully backed and redeemable for one dollar."
        if item is not None and item.return_kind is not None
        else "THING is a project."
    )
    return EvidenceFolder(
        symbol="THING",
        documents=[
            EvidenceDocument(
                url="https://thing.example/docs",
                category="official_documentation",
                title="Thing",
                text=(
                    f"{opening} What we run is a {phrase} for our users. "
                    f"Our {phrase} is the whole business. Read more about the {phrase}."
                ),
                fetched_at=NOW,
                seeded=True,
            ),
            EvidenceDocument(
                url="https://thing.example/about",
                category="official_documentation",
                title="About",
                text=(
                    "About the team. We are a distributed group building in the open. "
                    "Careers, brand assets and press enquiries."
                ),
                fetched_at=NOW,
                seeded=True,
            ),
        ],
    )


# --------------------------------------------------------------------------------
# 1. Every condition is written completely enough to be reviewed.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("item", CONDITIONS, ids=_ids(list(CONDITIONS)))
def test_every_condition_can_be_reviewed_by_a_person(item: Condition):
    """A rule nobody can read is a rule nobody can approve."""

    assert item.title_ar.strip(), item.code
    assert item.meaning_ar.strip(), item.code
    assert item.looks_like_ar.strip(), item.code
    assert item.reason_en.strip(), item.code
    assert item.evidence, f"{item.code} has no evidence behind it"
    for proof in item.evidence:
        assert proof.reference.strip(), f"{item.code}: evidence with no reference"
        assert proof.text.strip(), f"{item.code}: evidence with no quotation"


def test_condition_codes_are_unique():
    codes = [item.code for item in CONDITIONS]
    duplicates = {code for code in codes if codes.count(code) > 1}
    assert duplicates == set()


def test_no_phrase_belongs_to_two_conditions():
    """One phrase, one owner — this codebase's oldest lesson, applied to the register.

    ``prediction market`` was written into both ``MY-02`` (gambling, proposed) and
    ``GH-01`` (derivatives, approved). The sentence then refused a coin under a rule the
    owner approved while the register credited it to one they had not, so no reader
    could tell which rule had actually decided. Worse, it is exactly the shape that lets
    a phrase list drift: two owners, each edited without the other.
    """

    owners: dict[str, list[str]] = {}
    for item in CONDITIONS:
        for phrase in item.phrases:
            owners.setdefault(phrase, []).append(item.code)
    shared = {phrase: codes for phrase, codes in owners.items() if len(codes) > 1}
    assert shared == {}, f"phrases claimed by more than one condition: {shared}"


@pytest.mark.parametrize("item", CONDITIONS, ids=_ids(list(CONDITIONS)))
def test_a_condition_has_phrases_exactly_when_it_can_be_read(item: Condition):
    """Phrases and detection must agree, in both directions.

    A ``TEXT`` condition with no phrases is a rule that silently never fires. A
    ``MANUAL`` condition with phrases is a rule claiming to be checkable when the
    register says a person is needed — and it would fire anyway.
    """

    if item.detection is Detection.TEXT:
        assert item.phrases, f"{item.code} says it is readable but has no phrases"
    else:
        assert not item.phrases, f"{item.code} needs a person but carries phrases"


@pytest.mark.parametrize("item", TEXT_CONDITIONS, ids=_ids(TEXT_CONDITIONS))
def test_every_phrase_is_a_whole_lowercase_phrase(item: Condition):
    """The rule that stopped "raffle" matching inside "Raffles Avenue".

    Every phrase is matched with a boundary on both sides, so a truncated stem can never
    do what its author hoped. Written in upper case it would still match — the pattern is
    case-insensitive — but the lists are read by people too, and one shouting entry is
    how a list starts drifting.
    """

    for phrase in item.phrases:
        assert phrase == phrase.strip(), f"{item.code}: {phrase!r} has loose whitespace"
        assert phrase == phrase.casefold(), f"{item.code}: {phrase!r} is not lowercase"
        assert phrase, f"{item.code}: empty phrase"
        assert not re.search(r"[|()\[\]{}*+?^$\\]", phrase), (
            f"{item.code}: {phrase!r} contains a regular-expression character"
        )


# --------------------------------------------------------------------------------
# 2. An unapproved condition changes nothing.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("item", PROPOSED_TEXT, ids=_ids(PROPOSED_TEXT))
def test_a_proposed_condition_never_refuses_a_coin(item: Condition):
    """The governance rule, as code.

    A condition the owner has not approved is inert. It is read, and it is reported as a
    preview, but it can never be the reason a coin is refused. If this ever fails, then
    writing a rule down has become the same act as applying it, and the register has
    stopped being a proposal.
    """

    decision = decide("THING", "Thing", _stating(item.phrases[0], item=item))

    # The guarantee itself: this rule was not the reason for anything.
    assert item.code not in decision.matched_conditions, (
        f"{item.code} is only proposed, but it refused a coin"
    )
    assert item.code in decision.proposed_matches, (
        f"{item.code} matched but was not reported as a preview"
    )

    # The verdict may still be a refusal — legitimately, and by a *different* rule that
    # is approved. "resort and casino" contains "casino", which MY-01 owns and which the
    # owner approved. Demanding an eligible verdict here would assert that no two rules
    # may ever describe the same sentence, which is not true and not the property under
    # test.
    if decision.verdict is EvidenceVerdict.NOT_ELIGIBLE:
        assert decision.matched_conditions, (
            f"{item.code} produced a refusal with no approved rule behind it"
        )


@pytest.mark.parametrize("item", PROPOSED_TEXT, ids=_ids(PROPOSED_TEXT))
def test_a_proposed_condition_is_not_reported_as_a_fact(item: Condition):
    """Its activity must not appear in what we say the coin does, either.

    A preview that leaked into ``activities`` would be shown to a reader as "this is
    what the project does", with the register's authority behind it and no approval
    underneath it.
    """

    decision = decide("THING", "Thing", _stating(item.phrases[0], item=item))
    if item.activity is not None:
        assert item.activity not in decision.activities, item.code


# --------------------------------------------------------------------------------
# 3. An approved condition does refuse. Without this, (2) passes by doing nothing.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("item", APPROVED_TEXT, ids=_ids(APPROVED_TEXT))
def test_an_approved_condition_refuses_and_names_itself(item: Condition):
    decision = decide("THING", "Thing", _stating(item.phrases[0], item=item))
    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE, item.code
    if item.activity is not None:
        assert item.code in decision.matched_conditions, item.code
        assert item.activity in decision.blocking_activities, item.code
    else:
        # A riba return condition refuses through HolderReturn, which is its single
        # owner, so the refusal is reported as interest-bearing holding rather than as
        # this rule's own bucket.
        assert decision.holder_return is HolderReturn.FROM_LENDING_OR_PROMISE, item.code
        assert Activity.INTEREST_BEARING_HOLDING in decision.blocking_activities


def test_the_register_and_the_rule_agree_on_what_refuses():
    """``blocking_activities`` is exactly the approved conditions' activities."""

    expected = {
        item.activity for item in approved_conditions() if item.activity is not None
    }
    assert set(blocking_activities()) == expected


def test_at_least_one_condition_is_approved():
    """Guards every 'proposed does nothing' test above from passing vacuously."""

    assert conditions_by_status(Status.APPROVED)
    assert APPROVED_TEXT


# --------------------------------------------------------------------------------
# 4. The two structural traps that would over-block on approval.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("item", CONDITIONS, ids=_ids(list(CONDITIONS)))
def test_no_condition_blocks_on_the_holder_return_activity(item: Condition):
    """``INTEREST_BEARING_HOLDING`` has one owner, and it is :class:`HolderReturn`.

    Letting a condition block on it would restore the duplicated rule that once refused
    Chainlink, Polygon, Hedera, NEAR, stETH and rETH for paying validators.
    """

    assert item.activity is not Activity.INTEREST_BEARING_HOLDING, item.code


@pytest.mark.parametrize("item", CONDITIONS, ids=_ids(list(CONDITIONS)))
def test_a_condition_never_shares_a_bucket_with_a_harmless_activity(item: Condition):
    """Approving one rule must not refuse a whole category by accident.

    Activities are coarser than conditions on purpose, so several rules share a bucket.
    But if a condition's bucket is also produced by the *descriptive* vocabulary — the
    phrases that can never refuse — then approving that one condition would refuse every
    coin those harmless phrases describe. ``impermanent loss`` sharing
    ``SPOT_EXCHANGE`` would have refused every decentralised exchange in the product.
    """

    descriptive = {signal.activity for signal in ACTIVITY_SIGNALS}
    assert item.activity not in descriptive, (
        f"{item.code} blocks on {item.activity}, which harmless phrases also produce"
    )


@pytest.mark.parametrize("item", CONDITIONS, ids=_ids(list(CONDITIONS)))
def test_every_condition_activity_has_plain_words(item: Condition):
    """A refusal a beginner cannot read is a refusal that cannot be acted on."""

    if item.activity is not None:
        assert item.activity in ACTIVITY_IN_PLAIN_WORDS, item.code


# --------------------------------------------------------------------------------
# 5. A rule nobody looked at is never treated as satisfied.
# --------------------------------------------------------------------------------


def test_a_rule_out_of_reach_is_skipped_and_never_counted_as_passed():
    """The scope decision of 31 August 2026, held in both directions.

    A rule that reading a website cannot settle — riba al-fadl, a debt ratio — is
    skipped. It produces no per-coin queue entry, because nobody could act on one: the
    same list would appear on every coin, and neither the owner nor a Shariah provider
    can settle riba al-fadl from it.

    The half that must not slip is the other one. Skipped is not passed, so an
    out-of-reach rule must never appear as a *reason* for a verdict either. It
    contributes nothing in either direction, and the screen's reach is stated once in
    the methodology instead.
    """

    out_of_reach = {item.code for item in out_of_reach_conditions()}
    assert out_of_reach, "expected at least one approved rule a website cannot settle"

    folder = EvidenceFolder(
        symbol="CHAIN",
        documents=[
            EvidenceDocument(
                url="https://chain.example/",
                category="official_website",
                title="Chain",
                text=(
                    "CHAIN is a layer 1 blockchain using proof of stake consensus. "
                    "Validators secure the network and earn block rewards."
                ),
                fetched_at=NOW,
                seeded=True,
            ),
            # Two of the project's own pages, because a pass is a conclusion drawn from
            # absence and the screen will not draw one from a single page.
            EvidenceDocument(
                url="https://chain.example/docs",
                category="official_documentation",
                title="Docs",
                text=(
                    "Running a node. Hardware, ports and the client release schedule. "
                    "Validator set changes take effect at the next epoch."
                ),
                fetched_at=NOW,
                seeded=True,
            ),
        ],
    )
    decision = decide("CHAIN", "Chain", folder)
    assert decision.verdict is EvidenceVerdict.ELIGIBLE

    # Skipped: it is not raised as a question, and not claimed as satisfied.
    assert not hasattr(decision, "unchecked_conditions")
    assert out_of_reach.isdisjoint(decision.matched_conditions)
    assert out_of_reach.isdisjoint(decision.proposed_matches)
    payload = decision.as_dict()
    assert "unchecked_conditions" not in payload
    text = " ".join(reason["text"] for reason in payload["reasons"])
    for code in out_of_reach:
        assert code not in text


def test_a_rule_out_of_reach_carries_no_phrases_to_fire_on():
    """The mechanism behind the skip, rather than the effect of it.

    Out-of-reach is not a filter applied late; those rules simply have no words to look
    for, so no page can ever trigger one. Anything else would be a rule that fires
    sometimes and is skipped other times, which is the worst of both.
    """

    for item in out_of_reach_conditions():
        assert not item.phrases, item.code
        assert item.detection is not Detection.TEXT, item.code


def test_the_screen_applies_every_approved_rule_it_can_read():
    """Nothing approved and readable is quietly left out of the screen.

    Two routes into the reader, because a condition takes one or the other: a rule with
    an activity becomes a :data:`CONDITION_SIGNALS` entry, and a riba rule that answers
    the holder-return question puts its phrases into
    :data:`SOURCE_IS_LENDING_PHRASES` instead. Every approved readable rule must arrive
    by one of them; a rule on neither path is approved and silently inert.
    """

    by_activity = {signal.code for signal in CONDITION_SIGNALS}
    live_riba = set(SOURCE_IS_LENDING_PHRASES)

    for item in applied_conditions():
        if item.activity is not None:
            assert item.code in by_activity, f"{item.code} is approved but never read"
        else:
            assert item.return_kind is HolderReturn.FROM_LENDING_OR_PROMISE, item.code
            missing = set(item.phrases) - live_riba
            assert not missing, f"{item.code} phrases never reach the reader: {missing}"


# --------------------------------------------------------------------------------
# 5b. A refusal must not get cheaper because the crawler reads further.
# --------------------------------------------------------------------------------


def test_the_corroboration_bar_rises_with_the_pages_read():
    """A fixed page count is a threshold divided by a folder size that used to be fixed.

    "Two pages" meant "two of about ten" while the crawler read twelve. When the budget
    went to eighty on 31 August 2026 the same rule silently became "two of forty-three",
    and it refused Ethereum on one line of a news digest — the exact sentence the
    corroboration rule had been written to stop.
    """

    # Small folders behave exactly as they did when the screen was measured at 14/18.
    assert _corroboration_bar(0) == CORROBORATING_PAGES
    assert _corroboration_bar(12) == CORROBORATING_PAGES

    # Wider folders demand proportionally more, and never less.
    assert _corroboration_bar(43) > CORROBORATING_PAGES
    assert _corroboration_bar(80) > _corroboration_bar(43)

    previous = 0
    for pages in range(0, 200):
        bar = _corroboration_bar(pages)
        assert bar >= CORROBORATING_PAGES, pages
        assert bar >= previous, f"the bar fell at {pages} pages"
        previous = bar


def test_one_line_of_a_news_digest_never_refuses_a_large_project():
    """Ethereum, reconstructed: a wide folder where two pages mention somebody else.

    Forty-three pages of a project describing itself, two of which carry a passing
    reference to another company's lending protocol. Under the old absolute bar that was
    a refusal. It must not be one.
    """

    documents = [
        EvidenceDocument(
            url=f"https://chain.example/docs/{n}",
            category="official_documentation",
            title="Chain",
            text=(
                "CHAIN is a layer 1 blockchain using proof of stake consensus. "
                "Validators secure the network and earn block rewards."
            ),
            fetched_at=NOW,
            seeded=True,
        )
        for n in range(41)
    ]
    documents += [
        EvidenceDocument(
            url=f"https://chain.example/news/{n}",
            category="official_website",
            title="Chain",
            text=(
                "CHAIN is a layer 1 blockchain. In other news this week: "
                "Lending protocol Moonwell suffered an $8.7 million attack."
            ),
            fetched_at=NOW,
            seeded=True,
        )
        for n in range(2)
    ]
    folder = EvidenceFolder(symbol="CHAIN", documents=documents)
    decision = decide("CHAIN", "Chain", folder)

    assert decision.primary_documents_read == 43
    assert decision.verdict is EvidenceVerdict.ELIGIBLE, decision.reasons
    assert "RB-01" not in decision.matched_conditions


def test_a_business_that_really_lends_is_still_refused_in_a_large_folder():
    """The other half. Without it the test above passes by never refusing anything."""

    bar = _corroboration_bar(43)
    documents = [
        EvidenceDocument(
            url=f"https://money.example/docs/{n}",
            category="official_documentation",
            title="Money",
            text=(
                "MONEY is a lending protocol. Our lending protocol lets people "
                "borrow against your deposits. The lending protocol is the business."
            ),
            fetched_at=NOW,
            seeded=True,
        )
        for n in range(bar + 1)
    ]
    documents += [
        EvidenceDocument(
            url=f"https://money.example/other/{n}",
            category="official_documentation",
            title="Money",
            text="MONEY is a company. This page is about our team and offices.",
            fetched_at=NOW,
            seeded=True,
        )
        for n in range(43 - bar - 1)
    ]
    folder = EvidenceFolder(symbol="MONEY", documents=documents)
    decision = decide("MONEY", "Money", folder)

    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE
    assert "RB-01" in decision.matched_conditions


# --------------------------------------------------------------------------------
# 6. The measured vocabulary is pinned to what was actually measured.
# --------------------------------------------------------------------------------

#: Exactly the phrases that were blind-measured at 14/18 on 30 August 2026.
#:
#: Not a copy for its own sake. The number is printed in
#: ``docs/AUTOMATED_COIN_RESEARCH.md`` and in the memory notes, and it is only true of
#: this vocabulary. Widening one of these lists is allowed — it must simply be measured
#: again, and this list updated in the same commit, so the claim and the code move
#: together.
MEASURED_ON_30_AUGUST_2026 = {
    "RB-01": 22,
    "MY-01": 14,
    "GH-01": 21,
    "GH-09": 20,
}


@pytest.mark.parametrize(("code", "count"), sorted(MEASURED_ON_30_AUGUST_2026.items()))
def test_the_measured_rules_still_carry_the_vocabulary_that_was_measured(code, count):
    rule = next(item for item in CONDITIONS if item.code == code)
    assert len(rule.phrases) == count, (
        f"{code} now has {len(rule.phrases)} phrases, not the {count} that produced the "
        "published 14/18. Re-run the blind probe and update this number in the same "
        "commit as the change."
    )
    assert status_of(code) is Status.APPROVED


# --------------------------------------------------------------------------------
# 7. The document and the decisions file cannot drift from the register.
# --------------------------------------------------------------------------------


def test_the_arabic_document_is_what_the_register_produces():
    """Somebody editing the document instead of the register is caught here."""

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_sharia_conditions.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_decision_names_a_condition_that_exists():
    """A decision about a rule nobody wrote cannot be applied, so it must not be filed."""

    path = ROOT / "src" / "ai_market_monitor" / "services" / DECISIONS_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    known = {item.code for item in CONDITIONS}
    for entry in payload["decisions"]:
        assert entry["code"] in known, entry["code"]
        assert entry["status"] in {s.value for s in Status}
        assert entry["decided_by"].strip(), entry["code"]
        assert entry["decided_on"].strip(), entry["code"]


def test_the_vocabulary_reads_every_readable_condition():
    """Approved or proposed, a readable rule must reach the reader.

    A proposed rule that was never read would make its preview permanently empty, and
    the owner would approve it having been shown nothing.
    """

    in_register = {item.code for item in text_detectable_conditions() if item.activity}
    in_vocabulary = {signal.code for signal in CONDITION_SIGNALS}
    assert in_register == in_vocabulary

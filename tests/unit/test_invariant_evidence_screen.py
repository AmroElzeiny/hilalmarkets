"""The rules the evidence screen must hold for every term, not for the examples.

Four properties are asserted across the whole vocabulary, because a vocabulary is
exactly the kind of thing that grows a term nobody tested:

1. Every phrase in every list finds its own activity.
2. Every phrase is refused when the sentence denies it.
3. Every blocking phrase is ignored on a page that is not the project's own description.
4. *Not enough data* is reachable only through an empty reading, never through a
   missing detail.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_market_monitor.services.coin_evidence_crawler import (
    EvidenceDocument,
    EvidenceFolder,
)
from ai_market_monitor.services.sharia_automated_screen import (
    Activity,
    HolderReturn,
)
from ai_market_monitor.services.sharia_conditions import (
    Status,
    blocking_activities,
    status_of,
)
from ai_market_monitor.services.sharia_evidence_screen import (
    ACTIVITY_IN_PLAIN_WORDS,
    CORROBORATING_PAGES,
    EvidenceVerdict,
    decide,
    enough_was_read,
)
from ai_market_monitor.services.sharia_evidence_vocabulary import (
    ACTIVITY_SIGNALS,
    CONDITION_SIGNALS,
    PAID_FOR_HOLDING_PHRASES,
    PAID_UNEXPLAINED,
    SOURCE_IS_LENDING_PHRASES,
    SOURCE_IS_WORK_PHRASES,
    read_document,
    read_documents,
    resolve_holder_return,
)

NOW = datetime.now(UTC)

#: Blocking activities the vocabulary actually looks for.
#:
#: Read from the **approved** conditions, because those are the only ones that can refuse
#: a coin. Two traps this avoids. Taking it from ``BLOCKING_ACTIVITIES`` alone would
#: demand phrases for ``NO_UNDERLYING_UTILITY``, which deliberately has none — no project
#: writes "we have no product" — and quietly push somebody into inventing some. Taking it
#: from ``ACTIVITY_SIGNALS`` alone would now be the empty set, since every refusing phrase
#: moved to the condition register, and every test below would pass by testing nothing.
_PHRASED_BLOCKING = {
    signal.activity
    for signal in CONDITION_SIGNALS
    if status_of(signal.code) is Status.APPROVED and signal.activity in blocking_activities()
}

#: Every phrase the vocabulary knows, descriptive and refusing alike. The properties
#: below — found, quoted, denied — must hold for all of them, not for one list.
ALL_ACTIVITY_PHRASES = [
    *(
        (signal.activity, phrase)
        for signal in ACTIVITY_SIGNALS
        for phrase in signal.phrases
    ),
    *(
        (signal.activity, phrase)
        for signal in CONDITION_SIGNALS
        for phrase in signal.phrases
    ),
]


def _first_phrase(activity) -> str:
    """A phrase from an **approved** condition that evidences this activity.

    Approved only, and that is not a detail. Several conditions share one activity —
    ``RB-12`` (interest-bearing bonds, proposed) and ``GH-09`` (tokenised shares,
    approved) are both ``TOKENIZED_SECURITY``. Picking whichever came first handed the
    refusal tests below a proposed rule's phrase, which is inert by design, so the tests
    read a real guarantee as a failure.
    """

    for signal in CONDITION_SIGNALS:
        if (
            signal.activity is activity
            and signal.phrases
            and status_of(signal.code) is Status.APPROVED
        ):
            return signal.phrases[0]
    for signal in ACTIVITY_SIGNALS:
        if signal.activity is activity and signal.phrases:
            return signal.phrases[0]
    raise AssertionError(f"no approved phrase evidences {activity}")

ALL_RETURN_PHRASES = [
    *((HolderReturn.FROM_LENDING_OR_PROMISE.value, p) for p in SOURCE_IS_LENDING_PHRASES),
    *((HolderReturn.FROM_WORK.value, p) for p in SOURCE_IS_WORK_PHRASES),
    *((PAID_UNEXPLAINED, p) for p in PAID_FOR_HOLDING_PHRASES),
]


def _document(
    text: str,
    *,
    category: str = "official_website",
    primary: bool = True,
    url: str = "https://project.example/",
):
    return EvidenceDocument(
        url=url,
        category=category,
        title="Project",
        text=text,
        fetched_at=NOW,
        seeded=True,
    )


def _folder(symbol: str, *documents: EvidenceDocument) -> EvidenceFolder:
    return EvidenceFolder(symbol=symbol, documents=list(documents))


def _padding() -> EvidenceDocument:
    """A second page of the project's own writing that says nothing at all.

    The screen refuses to conclude anything — in either direction — from fewer than
    `CORROBORATING_PAGES` of a project's own pages, because "nothing refused it" and
    "it never says it pays a return" are both statements about the *reading* until
    enough of the project's own writing has been read. See `enough_was_read`.

    So a test about what the words mean has to hand over a folder that clears that
    floor, or it is really testing the floor. This page is deliberately an "about the
    team" page carrying nothing the vocabulary matches: it makes the reading wide enough
    without adding a single fact to it.
    """

    return _document(
        "About the team. We are a distributed group building in the open. "
        "Careers, brand assets and press enquiries.",
        category="official_documentation",
        url="https://project.example/about",
    )


def _read_enough(
    symbol: str,
    text: str,
    *extra: EvidenceDocument,
    category: str = "official_website",
) -> EvidenceFolder:
    """One page that says something, plus enough reading for it to be a finding."""

    return _folder(symbol, _document(text, category=category), _padding(), *extra)


# --------------------------------------------------------------------------------
# 1. Every phrase finds its own activity.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(("activity", "phrase"), ALL_ACTIVITY_PHRASES)
def test_every_activity_phrase_is_found(activity, phrase):
    found = read_document(
        f"The project description says: {phrase} is what we do here.",
        url="https://project.example/",
        category="official_website",
        primary=True,
    )
    assert activity in {item.activity for item in found}, phrase


@pytest.mark.parametrize(("kind", "phrase"), ALL_RETURN_PHRASES)
def test_every_return_phrase_is_found(kind, phrase):
    found = read_document(
        f"About the token: {phrase} applies to every holder.",
        url="https://project.example/",
        category="official_website",
        primary=True,
    )
    assert kind in {item.return_kind for item in found}, phrase


@pytest.mark.parametrize(("activity", "phrase"), ALL_ACTIVITY_PHRASES)
def test_every_activity_phrase_carries_its_quotation(activity, phrase):
    """A finding nobody can check is not evidence — it is an assertion."""

    found = read_document(
        f"Our product: {phrase} in practice.",
        url="https://project.example/docs",
        category="official_documentation",
        primary=True,
    )
    match = next(item for item in found if item.activity is activity)
    assert phrase.split()[0] in match.quote.casefold()
    assert match.url == "https://project.example/docs"


# --------------------------------------------------------------------------------
# 2. A denial is not an admission.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(("activity", "phrase"), ALL_ACTIVITY_PHRASES)
@pytest.mark.parametrize("denial", ["This is not a", "We never run a", "Unlike a"])
def test_a_denied_phrase_is_not_counted(activity, phrase, denial):
    found = read_document(
        f"{denial} {phrase}.",
        url="https://project.example/",
        category="official_website",
        primary=True,
    )
    assert activity not in {item.activity for item in found}, f"{denial} {phrase}"


# --------------------------------------------------------------------------------
# 3. A refusal rests only on the project's own description.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("activity", sorted(_PHRASED_BLOCKING, key=lambda a: a.value))
def test_a_blocking_phrase_on_a_news_page_never_refuses(activity):
    """A chain whose newsroom covers the market is not running that market.

    This is the single most expensive mistake the screen could make, because it refuses
    a coin publicly for something somebody else built.
    """

    phrase = _first_phrase(activity)
    folder = _read_enough(
        "CHAIN",
        "CHAIN is a layer 1 blockchain using proof of stake consensus. "
        "Validators secure the network and earn block rewards.",
        EvidenceDocument(
            url="https://project.example/blog/post",
            category="official_news",
            title="Ecosystem update",
            text=(
                f"A project launched: {phrase} is now live on CHAIN. "
                f"The {phrase} raised money. More on the {phrase} next week."
            ),
            fetched_at=NOW,
            seeded=False,
        ),
    )
    assert decide("CHAIN", "Chain", folder).verdict is EvidenceVerdict.ELIGIBLE


@pytest.mark.parametrize("activity", sorted(_PHRASED_BLOCKING, key=lambda a: a.value))
def test_the_same_phrase_repeated_on_the_projects_own_pages_does_refuse(activity):
    """The other half of the rule. Without this the check above passes by doing nothing."""

    phrase = _first_phrase(activity)
    folder = _read_enough(
        "THING",
        f"THING is a project. What we run is a {phrase} for our users. "
        f"Our {phrase} is the whole business. Read more about the {phrase}.",
        category="official_documentation",
    )
    decision = decide("THING", "Thing", folder)
    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE
    assert activity in decision.blocking_activities


def test_a_partner_named_on_the_projects_homepage_is_not_the_project():
    """The real sentence that refused Algorand, from algorand.foundation.

    Folks Finance is a protocol built *on* Algorand. No "ecosystem" or "built on" marker
    appears anywhere near it — a company is simply named and credited with lending. The
    marker list cannot catch attribution by name, so the reader is told whose pages it is
    reading and checks who the sentence is about.
    """

    text = (
        "Algorand is a layer 1 blockchain using proof of stake consensus, where "
        "validators secure the network and earn staking rewards. Ready to bank without "
        "banks? Folks Finance is ready. From lending and borrowing to swaps, liquid "
        "staking and more, Folks Finance brings it all. Reach people through "
        "stablecoin-based payments on Algorand. Folks Finance is a lending protocol "
        "with a money market protocol at its core."
    )
    decision = decide("ALGO", "Algorand", _read_enough("ALGO", text))
    assert Activity.LENDING_BORROWING not in decision.blocking_activities
    assert decision.verdict is EvidenceVerdict.ELIGIBLE


@pytest.mark.parametrize(
    "heading",
    [
        "Developer Docs",
        "Getting Started",
        "Smart Contracts",
        "Block Explorer",
        "Total Value",
        "Open Source",
    ],
)
def test_navigation_headings_never_cancel_a_refusal(heading):
    """A menu is full of capitalised pairs and none of them is a company.

    Treating one as a third party would drop a real refusal — the direction of error
    that matters most — so a pair containing any furniture word is not a company.
    """

    text = (
        f"{heading} LENDR is a lending protocol. Our lending protocol lets anyone "
        f"borrow. {heading} The lending protocol charges interest."
    )
    decision = decide("LENDR", "Lendr", _read_enough("LENDR", text))
    assert Activity.LENDING_BORROWING in decision.blocking_activities


def test_a_platform_listing_what_others_can_build_is_not_describing_itself():
    """The real sentence from the Ethereum whitepaper.

    Every smart-contract platform's documentation enumerates applications other people
    can deploy on it. Ethereum does not run a gambling protocol and does not sell
    contracts for difference; it is saying what its platform makes possible. Read as a
    statement about Ethereum's own business, this paragraph refused Ethereum.
    """

    text = (
        "Ethereum is a blockchain platform with a proof of stake consensus mechanism, "
        "where validators secure the network and earn staking rewards. "
        "Financial derivatives can be implemented on the Ethereum blockchain. The "
        "simplest gambling protocol is actually simply a contract for difference on the "
        "next block hash, and more advanced protocols can be built on top of it. "
        "Use cases include a decentralized exchange and a prediction market."
    )
    decision = decide("ETH", "Ethereum", _read_enough("ETH", text))
    assert decision.blocking_activities == []
    assert decision.verdict is EvidenceVerdict.ELIGIBLE


def test_a_project_describing_its_own_lending_business_is_still_refused():
    """The other half. Without it the rule above is just a way to pass everything.

    Naming yourself in the sentence is what a project does when it describes itself, so
    the attribution test keeps every one of these.
    """

    text = (
        "Aave Protocol is a decentralised lending protocol. The Aave lending protocol "
        "lets users deposit assets and earn interest on your deposits. Aave Labs "
        "maintains the interest rate model behind the lending protocol."
    )
    decision = decide("AAVE", "Aave", _read_enough("AAVE", text))
    assert Activity.LENDING_BORROWING in decision.blocking_activities
    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE


@pytest.mark.parametrize("activity", sorted(_PHRASED_BLOCKING, key=lambda a: a.value))
def test_one_passing_mention_on_one_page_never_refuses(activity):
    """A business repeats itself. A passing reference does not.

    Ethereum was refused because one paragraph of a news digest on ethereum.org read
    "Lending protocol Moonwell suffered an $8.7 million loss" — somebody else's
    protocol, named once, in a sentence about a hack. Naming a thing once is not
    running it, and a public refusal is too expensive to rest on it.
    """

    phrase = _first_phrase(activity)
    folder = _read_enough(
        "CHAIN",
        "CHAIN is a layer 1 blockchain using proof of stake consensus. Validators "
        "secure the network and earn block rewards. In other news this week: "
        f"a {phrase} was in the headlines.",
    )
    decision = decide("CHAIN", "Chain", folder)
    assert activity not in decision.blocking_activities
    assert decision.verdict is EvidenceVerdict.ELIGIBLE


# --------------------------------------------------------------------------------
# 4. "Not enough data" means nothing was read, never "one field was awkward".
# --------------------------------------------------------------------------------


def test_an_empty_folder_is_the_only_no_pages_answer():
    decision = decide("GHOST", "Ghost", _folder("GHOST"))
    assert decision.verdict is EvidenceVerdict.NOT_ENOUGH_DATA
    assert decision.documents_read == 0


def test_pages_that_describe_nothing_are_not_enough_data():
    folder = _folder("MUTE", _document("Welcome. Follow us. Sign up for the newsletter."))
    assert decide("MUTE", "Mute", folder).verdict is EvidenceVerdict.NOT_ENOUGH_DATA


@pytest.mark.parametrize(
    "text",
    [
        "This is a layer 1 blockchain with proof of stake consensus.",
        "We run an oracle network providing a price feed to other chains.",
        "A decentralized exchange where you can swap tokens from a liquidity pool.",
    ],
)
def test_any_described_project_reaches_a_verdict(text):
    """A readable description decides, and it does not need every question answered.

    Before this, a coin whose pages answered most questions but left one open was filed
    as unresearchable, which produced a queue nobody could clear and a product that said
    nothing about most of the market.

    What it *does* need is enough of the project's own writing for "nothing refused it"
    to be a statement about the project — see the group of tests below. The description
    here is handed over on a folder that clears that floor, so this test stays about the
    thing it was written for.
    """

    decision = decide("ANY", "Any", _read_enough("ANY", text))
    assert decision.verdict is not EvidenceVerdict.NOT_ENOUGH_DATA


def test_a_stablecoin_that_never_mentions_a_return_is_read_as_paying_none():
    folder = _read_enough(
        "PLAIN",
        "PLAIN is a stablecoin. Each token is backed 1:1 and is redeemable for one "
        "dollar. Reserves are held with a regulated bank.",
    )
    decision = decide("PLAIN", "Plain", folder)
    assert decision.holder_return is HolderReturn.NONE
    assert decision.verdict is EvidenceVerdict.ELIGIBLE


def test_a_stablecoin_read_only_from_a_blog_leaves_the_question_open():
    """Silence is only evidence on a page whose job is to describe the project.

    A blog post is not that page, so this folder has **no** primary pages at all and the
    reading never reaches the return question.
    """

    folder = _folder(
        "THIN",
        EvidenceDocument(
            url="https://project.example/blog/one",
            category="official_news",
            title="Post",
            text="Our stablecoin is fully backed and redeemable for one dollar today.",
            fetched_at=NOW,
            seeded=False,
        ),
    )
    decision = decide("THIN", "Thin", folder)
    assert decision.verdict is EvidenceVerdict.NOT_ENOUGH_DATA


# --------------------------------------------------------------------------------
# 5. A conclusion drawn from silence needs enough silence to be one.
# --------------------------------------------------------------------------------
#
# Two of this module's answers are conclusions from absence — "eligible" (nothing
# refused it) and "it pays its holders nothing" (no page said otherwise). Both were
# reachable from a single page, which made them statements about the *reading* rather
# than about the project. The floor is the number a refusal already needs, because a
# screen that demands two pages to refuse and accepts one to pass is not cautious, it is
# biased towards passing.


def test_one_page_of_a_projects_own_writing_never_reaches_a_verdict():
    """The measured failure: the token `U`, admitted on one marketing homepage.

    Its front page said "stablecoin" and did not discuss yield, so the screen concluded
    the token pays nothing and passed it — which is the exact failure `HolderReturn` was
    built to stop, moved from the facts path to the evidence path.
    """

    folder = _folder(
        "ONE",
        _document(
            "ONE is a stablecoin. The most united stablecoin, fully backed and "
            "redeemable. Proof of reserve. Get in touch."
        ),
    )
    decision = decide("ONE", "One", folder)
    assert decision.verdict is EvidenceVerdict.NOT_ENOUGH_DATA


def test_too_little_read_is_a_request_for_research_and_never_a_refusal():
    """Silence must never become a "no".

    Ordering matters here, not just the rule. The branch that turns an unanswered
    question into a refusal is right when a project described itself at length and still
    never answered; applied to a single page it turns "we could not read it" into "there
    is something wrong with it". `U` was refused exactly that way before the floor was
    moved above that branch.
    """

    folder = _folder(
        "ONE",
        _document("ONE is a stablecoin, fully backed and redeemable for one dollar."),
    )
    decision = decide("ONE", "One", folder)
    assert decision.verdict is not EvidenceVerdict.NOT_ELIGIBLE
    assert not decision.blocking_activities


def test_the_same_project_read_on_two_of_its_own_pages_does_reach_a_verdict():
    """The other half. Without it the two rules above pass by never deciding anything."""

    folder = _read_enough(
        "TWO", "TWO is a stablecoin, fully backed and redeemable for one dollar."
    )
    decision = decide("TWO", "Two", folder)
    assert decision.verdict is EvidenceVerdict.ELIGIBLE
    assert decision.holder_return is HolderReturn.NONE


def test_the_floor_is_the_same_number_a_refusal_needs():
    """One owner for "how much of a project's own writing a finding needs".

    Two numbers would drift, and the direction they drift in decides whether the screen
    passes coins it should not.
    """

    assert enough_was_read(CORROBORATING_PAGES) is True
    assert enough_was_read(CORROBORATING_PAGES - 1) is False
    assert enough_was_read(0) is False


def test_a_refusal_still_lands_on_a_project_that_really_describes_the_business():
    """The floor must not become a way for a lending protocol to escape by being small.

    Two of its own pages is a low bar on purpose: it is the point at which a reading is
    a reading. A project that lends says so on its own pages, and it is still refused.
    """

    folder = _read_enough(
        "LENDR",
        "LENDR is a lending protocol. Deposit assets and borrow against them. "
        "Our lending and borrowing markets are the core of the product.",
        _document(
            "Docs: lending and borrowing. Supply an asset to the lending pool and "
            "borrow against your collateral.",
            category="official_documentation",
            url="https://project.example/docs",
        ),
    )
    decision = decide("LENDR", "Lendr", folder)
    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE
    assert Activity.LENDING_BORROWING in decision.blocking_activities


# --------------------------------------------------------------------------------
# The return question has one owner, and it resolves in a stated order.
# --------------------------------------------------------------------------------


def test_a_named_lending_source_outranks_a_paid_for_holding_phrase():
    read = read_documents(
        [_document("Earn yield by holding it. The token accrues interest daily.")]
    )
    resolved, _basis = resolve_holder_return(read)
    assert resolved is HolderReturn.FROM_LENDING_OR_PROMISE


def test_work_outranks_a_paid_for_holding_phrase():
    """The measured failure: this is what refused every liquid-staking token."""

    read = read_documents(
        [_document("Earn yield by holding it. Holders receive staking rewards.")]
    )
    resolved, _basis = resolve_holder_return(read)
    assert resolved is HolderReturn.FROM_WORK


def test_being_paid_with_no_stated_source_fails_closed():
    read = read_documents([_document("Earn yield by holding. Passive income from holding.")])
    resolved, basis = resolve_holder_return(read)
    assert resolved is HolderReturn.FROM_LENDING_OR_PROMISE
    assert "never says where" in basis


def test_every_activity_has_plain_words_for_a_beginner():
    """A reader is told what a coin does, never handed an internal field name."""

    missing = [item.value for item in Activity if item not in ACTIVITY_IN_PLAIN_WORDS]
    assert missing == []

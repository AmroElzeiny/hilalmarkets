"""The one vocabulary that turns a project's own words into facts about what it does.

:mod:`sharia_automated_screen` decides what an :class:`Activity` *means*. This decides
what an activity *looks like in writing*. Keeping them apart is the point: the rule is
small enough to argue about in a review, and the words that trigger it can be corrected
without touching the rule.

**Every finding carries its quotation.** A phrase match is not a fact until it can be
shown: which page said it, and the sentence it said it in. That is what makes an
automated verdict answerable — a reader who disagrees can open the page and read the
line. It is also the only defence against the failure this product cannot afford, which
is confidently refusing a coin over a word nobody can find.

**Five rules that stop a word meaning more than it says.** Each is here because a real
coin was refused wrongly, and each names that coin, because a rule whose reason is
forgotten is a rule somebody deletes.

*A phrase is a whole phrase.* Bounded on both sides. Anchored only on the left, "raffle"
matched inside "**Raffles** Avenue" on an events listing and refused **Tezos** as a
casino.

*A denial is not an admission.* "We are not a lending protocol" contains the phrase.
Checked before the match for a negation, and just after it for a rebuttal — **Dogecoin**'s
own FAQ is headed "Dogecoin has no utility!" and exists to answer that charge, so reading
only backwards took the heading as a confession.

*A sentence about somebody else is about somebody else.* Two tests: framing words
("ecosystem", "built on", "can be implemented"), and attribution by name. **Algorand**'s
homepage says "Folks Finance is ready. From lending and borrowing to swaps…" — a partner,
named and credited, with no framing word anywhere near it.

*A mention is counted, not just found.* :attr:`Finding.occurrences` is what lets the
screen tell describing from mentioning. One line of a news digest on ethereum.org —
"Lending protocol Moonwell suffered a loss" — refused **Ethereum** for running a lending
business.

*Paying a return and paying riba are different questions.* The activity vocabulary says
a token pays its holder something. :class:`HolderReturn` says where that payment comes
from, and only that second answer can refuse anything. This split is not a refinement —
it was measured. Collapsing the two refused Chainlink, Polygon, Hedera, NEAR and every
liquid-staking token for the crime of paying validators.

The sixth rule lives in :mod:`sharia_evidence_screen`, because it is about the folder
rather than about one page: a refusal must come from a page where the project describes
itself, and must be corroborated across two of them or three times on one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ai_market_monitor.services.sharia_conditions import (
    Activity,
    HolderReturn,
    Status,
    return_phrases,
    status_of,
    text_detectable_conditions,
)

#: How much text on either side of a match is kept as the quotation.
QUOTE_RADIUS = 120

#: How far back a denial can reach and still govern a match.
#:
#: Sized to a clause, not a paragraph. "This is not a lending protocol" must be caught;
#: "we do not recommend X. Our lending protocol offers…" two sentences later must not.
_NEGATION_WINDOW = 60

#: How far *forward* a rebuttal can reach.
#:
#: Shorter, because a denial that follows its claim is a specific shape — a FAQ heading
#: answering itself. Dogecoin's own FAQ is titled "Dogecoin has no utility!" and the very
#: next words are "Oh yes it does, and it always has!". Reading only backwards took the
#: heading as a confession and refused Dogecoin for having no product, on the strength of
#: the page written to say the opposite.
_REBUTTAL_WINDOW = 40

#: Words that turn a phrase into something the project says it is *not*, or something it
#: is merely comparing itself to.
_NEGATIONS: tuple[str, ...] = (
    "not a", "not an", "not the", "is not", "are not", "isn't", "aren't",
    "never", "no longer", "without any", "does not", "do not", "cannot",
    "unlike", "rather than", "instead of", "as opposed to", "compared to",
    "in contrast to", "unlike a", "unlike an", "other than", "avoids",
    "avoid", "prohibits", "prohibited", "forbids", "we reject",
)
_NEGATION_RE = re.compile("|".join(re.escape(word) for word in _NEGATIONS))

#: Words that answer a claim just made. Only these, and only just after the claim.
_REBUTTALS: tuple[str, ...] = (
    "oh yes it does", "yes it does", "that is false", "this is false",
    "not true", "myth", "misconception", "debunk", "wrong!", "is incorrect",
)
_REBUTTAL_RE = re.compile("|".join(re.escape(word) for word in _REBUTTALS))

#: Words that mean the sentence is about **somebody else's** project.
#:
#: This is the single largest source of wrong refusals, and it is structural rather than
#: accidental: a blockchain's own website is largely a showcase of what other people
#: built on it. Cardano lists an interview with a lending protocol. Avalanche says a
#: fund manager chose it for a money market fund. Kaia describes a perpetual futures
#: engine in its ecosystem. All three sentences are on the chain's own pages, all three
#: are true, and none of them is the chain describing itself — yet each one refused a
#: coin that three authorities call compliant.
#:
#: Applied only to phrases that can *refuse*. A third-party context around "staking
#: rewards" costs nothing if it is counted; around "lending protocol" it costs a coin.
_THIRD_PARTY_MARKERS: tuple[str, ...] = (
    "ecosystem", "built on", "building on", "builds on", "built with",
    "powered by", "partner", "partnership", "case study", "case studies",
    "interview", "integrated", "integration", "selected", "chose",
    "launched on", "deployed on", "projects on", "dapp", "dapps",
    "spotlight", "showcase", "featured", "grantee", "grant recipient",
    "portfolio company", "customer story", "success story", "such as",
    "for example", "third-party", "third party",
    # Instructional writing is about using something, and usually about using somebody
    # else's something. Lido's own documentation carries "Guide: Borrowing & Lending on
    # Aave", teaching holders to post stETH as collateral in another protocol — which
    # refused Lido for running a lending business it has never run.
    "guide:", "guide to", "how to use", "learn how", "tutorial", "step-by-step",
    "as collateral on", "supported by", "available on",
    # **What somebody else could build.** Every smart-contract platform's documentation
    # enumerates applications other people can deploy on it, and that is the opposite of
    # describing its own business. This is the ecosystem-page rule again, written in
    # prose instead of in a URL path.
    #
    # The Ethereum whitepaper says: "…can be implemented on the Ethereum blockchain. The
    # simplest gambling protocol is actually simply a contract for difference on the
    # next block hash." Ethereum does not run a gambling protocol or sell contracts for
    # difference; it is describing what its platform makes possible. Read as a statement
    # about Ethereum's own business, that paragraph refused Ethereum.
    "can be implemented", "can be built", "could be built", "can be created",
    "can be deployed", "applications include", "use case", "use cases",
    "for instance", "examples include", "an example of", "one could", "you could",
    "developers can", "anyone can build", "possible to build", "on top of",
    "built on top", "such applications", "range of applications",
)
_THIRD_PARTY_RE = re.compile("|".join(re.escape(word) for word in _THIRD_PARTY_MARKERS))

#: How far either side of a match a third-party marker still governs it.
#:
#: Wider than a denial because the marker is often the surrounding heading rather than
#: the same clause — "4 OF 6 // AlphaSec: The Onchain Perpetual Futures Engine …
#: within the Kaia ecosystem" has the marker at the far end of the sentence.
_THIRD_PARTY_WINDOW = 160


@dataclass(frozen=True, slots=True)
class Signal:
    """One activity, and the phrases that show a project performing it."""

    activity: Activity
    phrases: tuple[str, ...]

    @property
    def pattern(self) -> re.Pattern[str]:
        """The alternation, exposed so a caller shares the exact vocabulary.

        Never rebuild this list somewhere else. A hand-written subset that drifts from
        this one is the recurring defect in this codebase, and here it would show up as
        a coin refused by one reader and passed by another.
        """

        return _compiled(self.phrases)


#: The phrases for activities that **never refuse a coin**, per activity.
#:
#: Every refusing phrase now lives on its own :class:`Condition` in
#: :mod:`sharia_conditions`, with the evidence behind it, and reaches this module through
#: :data:`CONDITION_SIGNALS`. Keeping the two lists apart is not tidiness — a refusing
#: phrase written here as well would be a second copy of a governed rule, and this
#: codebase's recurring defect is exactly that: two lists that each understood a
#: different subset.
#:
#: Precision over recall, deliberately. A missed activity costs a coin an unresolved
#: question, which a person can answer. A wrongly-found blocking activity costs a coin a
#: public refusal, which nobody sees until the project complains.
ACTIVITY_SIGNALS: tuple[Signal, ...] = (
    Signal(
        Activity.OWN_SETTLEMENT_NETWORK,
        (
            "layer 1 blockchain", "layer-1 blockchain", "l1 blockchain",
            "blockchain platform", "blockchain network", "our blockchain",
            "the mainnet", "mainnet launch", "consensus mechanism",
            "proof of stake", "proof-of-stake", "proof of work", "proof-of-work",
            "byzantine fault", "block producer", "block time", "smart contract platform",
            "peer-to-peer electronic cash", "distributed ledger", "settlement layer",
            "rollup", "sidechain", "sharding",
        ),
    ),
    Signal(
        Activity.STAKING_OR_VALIDATION,
        (
            "staking rewards", "stake your tokens", "stake your coins", "staking pool",
            "liquid staking", "validator node", "validators", "delegator",
            "delegate your stake", "run a validator", "become a validator",
            "securing the network", "sequencer", "restaking", "block rewards",
        ),
    ),
    Signal(
        Activity.SPOT_EXCHANGE,
        (
            "decentralized exchange", "decentralised exchange",
            "automated market maker", "swap tokens", "token swap", "liquidity pool",
            "spot trading", "trade any token", "exchange protocol", "aggregator for swaps",
        ),
    ),
    Signal(
        Activity.INFRASTRUCTURE_SERVICE,
        (
            "oracle network", "price feed", "data feed", "decentralized oracle",
            "decentralized storage", "decentralised storage", "file storage network",
            "compute network", "bandwidth network", "indexing protocol",
            "query protocol", "naming service", "identity protocol", "cross-chain bridge",
            "interoperability protocol", "data availability", "cloud infrastructure",
            "content delivery", "wireless network", "api access", "middleware",
        ),
    ),
    Signal(
        Activity.CONSUMER_APPLICATION,
        (
            "play-to-earn", "play to earn", "nft marketplace", "nft collection",
            "metaverse", "virtual world", "blockchain game", "gaming platform",
            "digital collectibles", "ticketing platform", "social network",
            "streaming platform", "music platform", "loyalty programme",
            "loyalty program", "advertising network",
        ),
    ),
    Signal(
        Activity.FULLY_BACKED_REDEEMABLE,
        (
            "fully backed", "fully-backed", "1:1 backed", "backed 1:1",
            "redeemable for", "redeemable at any time", "pegged to the us dollar",
            "pegged to the dollar", "dollar-pegged", "stablecoin", "stable coin",
            "reserves are held", "reserve attestation", "backed by physical gold",
            "backed by gold", "each token is backed",
        ),
    ),
    Signal(
        Activity.PLATFORM_ACCESS_OR_GOVERNANCE,
        (
            "governance token", "governance rights", "vote on proposals",
            "voting power", "submit proposals", "dao governance", "fee discount",
            "access to the platform", "utility token grants", "protocol governance",
        ),
    ),
)

@dataclass(frozen=True, slots=True)
class ConditionSignal:
    """One screening condition, and the phrases that show a project doing it.

    Built from :mod:`sharia_conditions`, never written here. ``code`` travels with every
    finding so a refusal can always name the rule it came from — and so a reader can be
    shown the evidence for that rule rather than a bare sentence.
    """

    code: str
    #: ``None`` for a condition that answers the holder-return question instead.
    activity: Activity | None
    phrases: tuple[str, ...]

    @property
    def pattern(self) -> re.Pattern[str]:
        return _compiled(self.phrases)


#: Every condition that can be found in writing — **approved and proposed alike**.
#:
#: Reading a proposed condition costs nothing and is the point: the owner is shown, on
#: real coins, exactly what approving a rule would have caught, before approving it. A
#: proposed condition's finding is recorded and reported; only
#: :func:`sharia_conditions.blocking_activities` decides what actually refuses, and
#: :func:`sharia_evidence_screen._activities_from` is where that filter is applied.
CONDITION_SIGNALS: tuple[ConditionSignal, ...] = tuple(
    ConditionSignal(code=item.code, activity=item.activity, phrases=item.phrases)
    for item in text_detectable_conditions()
    if item.activity is not None
)

#: The same preview, for the riba conditions that answer *where a return comes from*.
#:
#: Kept as its own list because these must never resolve the return question while they
#: are unapproved. An approved one reaches :data:`SOURCE_IS_LENDING_PHRASES` and settles
#: it; a proposed one only ever produces a finding that names the rule, so the owner can
#: see what it caught without it having caught anything.
PROPOSED_RETURN_SIGNALS: tuple[ConditionSignal, ...] = tuple(
    ConditionSignal(code=item.code, activity=None, phrases=item.phrases)
    for item in text_detectable_conditions()
    if item.return_kind is not None and status_of(item.code) is not Status.APPROVED
)

#: Where the holder's money comes from, said explicitly. The strongest answer there is.
#:
#: Every phrase here *names the source* — a loan, a treasury, a promised rate. None of
#: them is ambiguous about it, which is why a match settles the question on its own.
#:
#: Derived from the approved riba conditions, so that approving or withdrawing one of
#: them changes this list too. Written by hand it would be a second copy of a governed
#: rule, free to drift from the register that carries its evidence.
SOURCE_IS_LENDING_PHRASES: tuple[str, ...] = return_phrases(
    HolderReturn.FROM_LENDING_OR_PROMISE
)

#: Where the holder's money comes from when the answer is work performed.
SOURCE_IS_WORK_PHRASES: tuple[str, ...] = (
    "staking rewards", "validator rewards", "block rewards",
    "rewards for validating", "rewards for securing the network",
    "delegate to a validator", "earn rewards by staking",
    "liquid staking token", "represents staked", "commission from validators",
    "fees paid by users of the network", "transaction fees are distributed",
)

#: The holder is paid — and this says nothing about where the money comes from.
#:
#: These are the phrases that made the two lists above necessary. "Earn yield by holding"
#: is true of a yield-bearing stablecoin, whose yield is a loan, and equally true of a
#: liquid-staking token, whose yield is validation work. One phrase, two opposite
#: answers, which is a question rather than a fact — so it is filed as one and resolved
#: by :func:`resolve_holder_return`, never counted as evidence of riba on its own.
PAID_FOR_HOLDING_PHRASES: tuple[str, ...] = (
    "yield-bearing stablecoin", "yield bearing stablecoin",
    "earn yield by holding", "earn yield simply by holding",
    "yield simply for holding", "rewards for holding",
    "passive income from holding", "annual percentage yield for holders",
    "earn while you hold", "yield accrues to holders",
)

#: Marks a hit from :data:`PAID_FOR_HOLDING_PHRASES` — a question, not an answer.
PAID_UNEXPLAINED = "paid_for_holding_source_unstated"

RETURN_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (HolderReturn.FROM_LENDING_OR_PROMISE.value, SOURCE_IS_LENDING_PHRASES),
    (HolderReturn.FROM_WORK.value, SOURCE_IS_WORK_PHRASES),
    (PAID_UNEXPLAINED, PAID_FOR_HOLDING_PHRASES),
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One activity, found in one document, with the words that show it."""

    activity: Activity | None
    #: One of the :class:`HolderReturn` values, or :data:`PAID_UNEXPLAINED` when the
    #: text says the holder is paid without saying by what.
    return_kind: str | None
    phrase: str
    quote: str
    url: str
    category: str
    primary: bool
    #: How many times this page said it. A project describing its own business repeats
    #: itself; a page that mentions somebody else's business mentions it once.
    occurrences: int = 1
    #: Which screening condition matched, when one did. Empty for a plain descriptive
    #: activity. A refusal without this is a refusal nobody can trace to a rule.
    condition_code: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "activity": self.activity.value if self.activity else None,
            "return_kind": self.return_kind,
            "phrase": self.phrase,
            "quote": self.quote,
            "url": self.url,
            "category": self.category,
            "primary": self.primary,
            "occurrences": self.occurrences,
            "condition_code": self.condition_code,
        }


@dataclass(slots=True)
class ReadResult:
    """Everything the vocabulary found across one coin's folder."""

    findings: list[Finding] = field(default_factory=list)

    def activities(self, *, primary_only: bool = False) -> set[Activity]:
        return {
            item.activity
            for item in self.findings
            if item.activity is not None and (item.primary or not primary_only)
        }

    def return_kinds(self, *, primary_only: bool = False) -> set[str]:
        return {
            item.return_kind
            for item in self.findings
            if item.return_kind and (item.primary or not primary_only)
        }

    def for_activity(self, activity: Activity) -> list[Finding]:
        return [item for item in self.findings if item.activity is activity]

    def for_return(self, kind: str) -> list[Finding]:
        return [item for item in self.findings if item.return_kind == kind]


def resolve_holder_return(result: ReadResult) -> tuple[HolderReturn | None, str]:
    """What holding this token pays, and why we say so.

    The resolution order is the whole point, and it is not a preference — it is which
    question each phrase actually answers:

    1. A phrase that **names the source as lending** settles it. Nothing outranks the
       project saying where the money comes from.
    2. Otherwise a phrase that **names the source as work** settles it.
    3. Otherwise, if the project says a holder is paid but never says by what, the
       answer is riba. This is the fail-closed step and it is deliberate: a payment to
       somebody who did nothing, with no source given, is the thing being screened for.
       Reading it as harmless would need a reason, and silence is not one.
    4. Otherwise there is no evidence of any return at all, and the question does not
       arise.

    Returning ``None`` means "nothing in the folder speaks to this" — the caller decides
    whether that matters, because it only matters for some kinds of token.

    **The two answers that can refuse are read from primary pages only**, exactly as a
    blocking activity is. Without that, Paxos's press-clippings page — a list of
    headlines about a *different* Paxos product, "Bloomberg: Paxos Debuts Yield-Bearing
    Stablecoin" — refused USDP. The rule against refusing a coin over somebody else's
    words was already written for activities; the return question was simply never
    brought under it.
    """

    refusing = result.return_kinds(primary_only=True)
    helping = result.return_kinds()
    if HolderReturn.FROM_LENDING_OR_PROMISE.value in refusing:
        return (
            HolderReturn.FROM_LENDING_OR_PROMISE,
            "the project says the return is interest or a promised rate",
        )
    if HolderReturn.FROM_WORK.value in helping:
        return (
            HolderReturn.FROM_WORK,
            "the project says the return pays for validation or service work",
        )
    if PAID_UNEXPLAINED in refusing:
        return (
            HolderReturn.FROM_LENDING_OR_PROMISE,
            "the project says holding pays a return but never says where the money comes from",
        )
    return None, "the project does not say that holding pays anything"


_CACHE: dict[tuple[str, ...], re.Pattern[str]] = {}


def _compiled(phrases: Sequence[str]) -> re.Pattern[str]:
    key = tuple(phrases)
    pattern = _CACHE.get(key)
    if pattern is None:
        # Sorted longest first so "lending protocol" wins over a shorter phrase that
        # happens to be a prefix of it, and the quotation names the specific match.
        body = "|".join(
            re.escape(phrase) for phrase in sorted(phrases, key=len, reverse=True)
        )
        # Bounded on **both** sides. The trailing boundary is not symmetry for its own
        # sake — without it "raffle" matched inside "Raffles Avenue" on an events page
        # and refused Tezos as a casino. A phrase that only anchors its left edge will
        # eventually find itself inside a longer word, and the longer word is usually a
        # place, a name, or a plural nobody thought about.
        #
        # Every phrase in these lists is therefore a whole one. Do not add a truncated
        # stem such as "tokenized equit" hoping to catch both endings — write both.
        pattern = re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])", re.IGNORECASE)
        _CACHE[key] = pattern
    return pattern


def _denied(text: str, start: int, end: int) -> bool:
    """Is this match denied — before it, or answered straight after it?"""

    before = text[max(0, start - _NEGATION_WINDOW) : start].casefold()
    if _NEGATION_RE.search(before) is not None:
        return True
    after = text[end : end + _REBUTTAL_WINDOW].casefold()
    return _REBUTTAL_RE.search(after) is not None


#: Two capitalised words in a row — the shape of a product name in prose.
#:
#: "Folks Finance", "Franklin Templeton", "Rocket Pool". Deliberately requires both words
#: to be Capitalised-then-lowercase, so an all-caps ticker (``THING``, ``BTC``) and a
#: sentence-initial ordinary word are not mistaken for one.
_NAMED_ENTITY_RE = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")

#: Words that make a capitalised pair something other than a company name.
#:
#: A single one of these anywhere in the pair is enough to reject it, and that asymmetry
#: is deliberate. Rejecting a pair means the sentence is *not* treated as being about
#: somebody else, so the refusal is kept — the fail-closed direction. Missing a real
#: third party costs a wrong refusal on a proposal a reviewer will read; treating a
#: heading as a company costs a *missed* refusal, which is worse.
#:
#: A page's navigation is full of capitalised pairs. "Developer Docs", "Getting Started",
#: "Smart Contracts", "Block Explorer" and "Total Value" all have the shape of a company
#: name and none of them is one; without this list each would quietly cancel a genuine
#: refusal that happened to sit near the menu.
_NOT_A_COMPANY: frozenset[str] = frozenset(
    {
        # Ordinary prose
        "our", "the", "this", "that", "these", "those", "with", "from", "read",
        "learn", "what", "when", "where", "why", "how", "we", "you", "your",
        "get", "start", "build", "join", "welcome", "about", "more", "all",
        "new", "now", "today", "ready", "please", "note", "see", "find",
        "view", "explore", "discover", "download", "next", "back", "here",
        # Navigation and documentation furniture
        "developer", "developers", "docs", "documentation", "doc", "getting",
        "started", "quick", "quickstart", "api", "reference", "overview",
        "introduction", "guide", "guides", "tutorial", "tutorials",
        "contact", "support", "help", "center", "centre", "blog", "news",
        "press", "media", "team", "careers", "jobs", "privacy", "policy",
        "terms", "service", "services", "legal", "cookie", "cookies", "faq",
        "community", "forum", "sign", "log", "menu", "search", "home",
        # Generic crypto vocabulary that is not anybody's name
        "smart", "contract", "contracts", "block", "blocks", "chain", "chains",
        "network", "networks", "protocol", "protocols", "token", "tokens",
        "coin", "coins", "wallet", "wallets", "node", "nodes", "staking",
        "stake", "validator", "validators", "exchange", "market", "markets",
        "trading", "trade", "digital", "asset", "assets", "open", "source",
        "white", "paper", "road", "roadmap", "layer", "cross", "multi", "web",
        "app", "apps", "mobile", "desktop", "security", "audit", "audits",
        "governance", "treasury", "foundation", "labs", "technologies",
        "technology", "research", "ecosystem", "use", "cases", "real", "world",
        "generation", "value", "total", "locked", "supply", "price", "fees",
        "decentralized", "decentralised", "distributed", "global", "public",
    }
)


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence the match sits in, bounded so a paragraph cannot leak in."""

    left = text.rfind(".", max(0, start - _THIRD_PARTY_WINDOW), start)
    right = text.find(".", end, min(len(text), end + _THIRD_PARTY_WINDOW))
    return text[
        (left + 1 if left != -1 else max(0, start - _THIRD_PARTY_WINDOW)) : (
            right if right != -1 else min(len(text), end + _THIRD_PARTY_WINDOW)
        )
    ]


def _about_someone_else(
    text: str,
    start: int,
    end: int,
    project_terms: frozenset[str] = frozenset(),
) -> bool:
    """Is this sentence about another project rather than about this one?

    Two tests, and the second needs the project's own name.

    The marker list catches the framing — "ecosystem", "built on", "case study". It
    cannot catch attribution by name, and that is how Algorand was refused: its own
    homepage carries the line "Folks Finance is ready. From lending and borrowing to
    swaps…". Folks Finance is a protocol *on* Algorand. No marker appears; a company is
    simply named and credited with the activity.

    So a refusing phrase in a sentence that names a company **other than this project**
    is treated as being about that company. A real lending protocol writes about itself
    constantly — its own name is in those sentences, and they still count.
    """

    window = text[
        max(0, start - _THIRD_PARTY_WINDOW) : min(len(text), end + _THIRD_PARTY_WINDOW)
    ]
    if _THIRD_PARTY_RE.search(window.casefold()) is not None:
        return True
    if not project_terms:
        return False
    sentence = _sentence_around(text, start, end)
    for match in _NAMED_ENTITY_RE.finditer(sentence):
        words = [word.casefold() for word in match.group(0).split()]
        if any(word in _NOT_A_COMPANY for word in words):
            continue
        if any(word in project_terms for word in words):
            continue  # the project talking about itself
        return True
    return False


def project_terms_for(*values: str) -> frozenset[str]:
    """The words that mean "this project", for the attribution test above.

    Takes as many names as the caller has — ticker, coin name, provider slug, the host
    it publishes under. More names is strictly safer here: every extra one can only
    *keep* a refusal that would otherwise be dropped as being about somebody else.
    """

    return frozenset(
        part.casefold()
        for value in values
        for part in re.split(r"[^A-Za-z0-9]+", str(value or ""))
        if len(part) > 2
    )


def _quote(text: str, start: int, end: int) -> str:
    left = max(0, start - QUOTE_RADIUS)
    right = min(len(text), end + QUOTE_RADIUS)
    fragment = " ".join(text[left:right].split())
    return f"…{fragment}…" if left or right < len(text) else fragment


def read_document(
    text: str,
    *,
    url: str,
    category: str,
    primary: bool,
    project_terms: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Every activity and return this one document evidences.

    ``project_terms`` is what the words for "this project" are, from
    :func:`project_terms_for`. Without it the attribution test cannot run and a sentence
    crediting another company's lending business counts against this one.
    """

    found: list[Finding] = []
    for signal in ACTIVITY_SIGNALS:
        # Nothing in this list can refuse a coin, so the third-party test is not applied
        # to it. Asking that question of "staking rewards" would only lose real findings.
        hit = _first_and_count(
            text, signal.pattern, refusing=False, project_terms=project_terms
        )
        if hit is not None:
            quote, count = hit
            found.append(
                Finding(
                    activity=signal.activity,
                    return_kind=None,
                    phrase=quote[0],
                    quote=quote[1],
                    url=url,
                    category=category,
                    primary=primary,
                    occurrences=count,
                )
            )
    for rule in (*CONDITION_SIGNALS, *PROPOSED_RETURN_SIGNALS):
        # Every condition is read at the refusal bar, approved or not. A proposed rule's
        # preview is only useful if it is held to the same precision the real thing is.
        hit = _first_and_count(
            text, rule.pattern, refusing=True, project_terms=project_terms
        )
        if hit is not None:
            quote, count = hit
            found.append(
                Finding(
                    activity=rule.activity,
                    return_kind=None,
                    phrase=quote[0],
                    quote=quote[1],
                    url=url,
                    category=category,
                    primary=primary,
                    occurrences=count,
                    condition_code=rule.code,
                )
            )
    for return_kind, phrases in RETURN_SIGNALS:
        refusing = return_kind == HolderReturn.FROM_LENDING_OR_PROMISE.value
        hit = _first_and_count(
            text, _compiled(phrases), refusing=refusing, project_terms=project_terms
        )
        if hit is not None:
            quote, count = hit
            found.append(
                Finding(
                    activity=None,
                    return_kind=return_kind,
                    phrase=quote[0],
                    quote=quote[1],
                    url=url,
                    category=category,
                    primary=primary,
                    occurrences=count,
                )
            )
    return found


#: Past this many hits on one page the exact number stops meaning anything, and counting
#: on is only work. Any value at or above the corroboration threshold reads the same.
_OCCURRENCE_CAP = 6


def _first_and_count(
    text: str,
    pattern: re.Pattern[str],
    *,
    refusing: bool,
    project_terms: frozenset[str] = frozenset(),
) -> tuple[tuple[str, str], int] | None:
    """The first surviving match and how many survived, up to a cap.

    One quotation per page is what a reader needs; the count is what decides whether a
    page is *describing* something or merely *mentioning* it. See
    :func:`sharia_evidence_screen._activities_from` for what the count is used for.
    """

    first: tuple[str, str] | None = None
    count = 0
    for match in pattern.finditer(text):
        if _denied(text, match.start(), match.end()):
            continue
        # The third-party test guards refusals only. A phrase that cannot refuse
        # anything costs nothing when it is counted from an ecosystem page, and asking
        # the same question of it would only lose real findings.
        if refusing and _about_someone_else(
            text, match.start(), match.end(), project_terms
        ):
            continue
        count += 1
        if first is None:
            first = (
                match.group(0).casefold(),
                _quote(text, match.start(), match.end()),
            )
        if count >= _OCCURRENCE_CAP:
            break
    return (first, count) if first is not None else None


def read_documents(
    documents: Iterable[object],
    *,
    project_terms: frozenset[str] = frozenset(),
) -> ReadResult:
    """Read a whole folder. Accepts anything shaped like an ``EvidenceDocument``."""

    result = ReadResult()
    for document in documents:
        result.findings.extend(
            read_document(
                str(getattr(document, "text", "")),
                url=str(getattr(document, "url", "")),
                category=str(getattr(document, "category", "")),
                primary=bool(getattr(document, "is_primary", False)),
                project_terms=project_terms,
            )
        )
    return result


__all__ = [
    "ACTIVITY_SIGNALS",
    "CONDITION_SIGNALS",
    "PAID_FOR_HOLDING_PHRASES",
    "ConditionSignal",
    "PAID_UNEXPLAINED",
    "QUOTE_RADIUS",
    "RETURN_SIGNALS",
    "SOURCE_IS_LENDING_PHRASES",
    "SOURCE_IS_WORK_PHRASES",
    "Finding",
    "ReadResult",
    "Signal",
    "read_document",
    "read_documents",
    "resolve_holder_return",
]

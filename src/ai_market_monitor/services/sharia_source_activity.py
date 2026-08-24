"""How alive a source is, and whether it talks about the things a review has to watch.

``sharia_source_resolution`` already answers "does this link work". That is not the same
question as "is this link worth keeping". A newsroom can return HTTP 200 for years after
the last post was written; a marketing page can be updated weekly and never once mention
anything a Shariah reviewer needs to know about.

So this module scores a fetched page on three things, and it is the only place that
decides what those words mean:

===============  ==========================================================  ======
Signal           What it measures                                            Weight
===============  ==========================================================  ======
``recency``      How long ago the newest dated item on the page was written    0.45
``cadence``      How many separate dated items the page shows at all — a       0.25
                 page with one date is an archive, a page with twelve is a
                 feed that keeps producing
``topic``        How much of the money-and-governance vocabulary the page      0.30
                 actually uses. A project's own page that never mentions
                 fees, supply, staking, governance or licensing is not a
                 place a change in what the project does would show up
===============  ==========================================================  ======

**This score is not a Shariah status and can never become one.** It says how good a
*window* onto the project this address is. It knows nothing about whether the project is
permissible, it is never shown as a status, and no part of the review pipeline reads it
as one. The words in the vocabularies below are there because their presence means "this
page discusses the project's money and its rules" — not because any of them is good or
bad. A page full of the word "interest" scores exactly the same as a page full of the
word "governance".

The vocabularies are kept whole rather than sampled: a caller that wanted "the finance
words" and wrote its own shorter list is the duplicate-vocabulary failure this codebase
keeps repeating.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ai_market_monitor.services.sharia_source_catalog import (
    NEWS,
    NEWS_MAXIMUM_AGE_DAYS,
)

#: A page whose newest item is this new is as fresh as it is possible to be. Anything
#: newer scores the same — a blog posted today and one posted last week are both a live
#: feed, and pretending otherwise would make the number jump about for no reason.
FRESH_WITHIN_DAYS = 30

#: How many separate dated items make a page a feed rather than an archive. Reaching
#: this is full marks; the score rises evenly up to it.
CADENCE_TARGET_ITEMS = 8

#: Only dates inside this window count towards cadence. A page listing every post since
#: 2017 would otherwise look busier than a page that posts weekly.
CADENCE_WINDOW_DAYS = 365

#: How many distinct vocabulary terms a page needs before its topic score is full. Small
#: on purpose: the question is "does this page talk about the project's money and rules
#: at all", not "how many different words does it use".
TOPIC_TARGET_TERMS = 6

RECENCY_WEIGHT = 0.45
CADENCE_WEIGHT = 0.25
TOPIC_WEIGHT = 0.30

#: A source below this is not doing its job. It is not deleted — a person is asked, and
#: the layers look for more links alongside it.
ACTIVITY_FLOOR = 0.45

#: Words that mean the page is discussing where the money comes from and goes. Lower
#: case throughout: matching is done on a folded copy of the page text.
FINANCIAL_TERMS: tuple[str, ...] = (
    "airdrop",
    "apr",
    "apy",
    "audit",
    "backing",
    "borrow",
    "buyback",
    "burn",
    "collateral",
    "custody",
    "derivative",
    "distribution",
    "emission",
    "fee",
    "fees",
    "funding",
    "futures",
    "interest",
    "issuance",
    "lending",
    "leverage",
    "liquidity",
    "margin",
    "perpetual",
    "redemption",
    "reserve",
    "revenue",
    "reward",
    "rewards",
    "staking",
    "supply",
    "swap",
    "tokenomics",
    "treasury",
    "unlock",
    "vesting",
    "yield",
)

#: Words that mean the page is discussing the rules the project runs by, and changes to
#: them. A Shariah review is a judgement about what a project does, so a change of
#: governance, licence or product is exactly the event the register exists to catch.
GOVERNANCE_TERMS: tuple[str, ...] = (
    "acquisition",
    "charter",
    "compliance",
    "council",
    "delisting",
    "foundation",
    "governance",
    "incident",
    "launch",
    "licence",
    "license",
    "listing",
    "mainnet",
    "merger",
    "partnership",
    "policy",
    "proposal",
    "protocol",
    "regulation",
    "regulator",
    "regulatory",
    "roadmap",
    "security",
    "shariah",
    "upgrade",
    "validator",
    "vote",
    "whitepaper",
)

#: Every term, in one place, so a caller cannot pick up half the vocabulary.
TOPIC_TERMS: tuple[str, ...] = tuple(sorted({*FINANCIAL_TERMS, *GOVERNANCE_TERMS}))

_TERM_PATTERNS: dict[str, re.Pattern[str]] = {
    term: re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in TOPIC_TERMS
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
#: An ISO date, whether it stands alone or begins a full timestamp.
#:
#: The trailing boundary used to be ``\b``, which is the absence of one between ``9``
#: and ``T`` — so ``2026-04-09T07:01:44+00:00`` matched nothing. Every page that states
#: its dates as timestamps rather than as words was read as having published nothing,
#: ever, and a live Telegram announcement channel could never clear the freshness
#: proof. The rule is instead "not part of a longer run of digits": a real date may be
#: followed by a ``T``, a space or a full stop, but never by another digit or dash.
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})(?![-\d])")
_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})\b",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.IGNORECASE,
)


def published_dates(text: str, *, now: datetime) -> tuple[datetime, ...]:
    """Every date the page claims, newest first, ignoring ones in the future.

    One owner for reading a date off a page, however it is written. A newsroom
    advertising next month's conference is not evidence that the newsroom is still
    being written, so future dates are dropped rather than counted.
    """

    found: set[datetime] = set()
    raw: list[tuple[int, int, int]] = []
    for year, month, day in _ISO_DATE.findall(text):
        raw.append((int(year), int(month), int(day)))
    for day, month, year in _DAY_MONTH_YEAR.findall(text):
        raw.append((int(year), _MONTHS[month.lower()[:3]], int(day)))
    for month, day, year in _MONTH_DAY_YEAR.findall(text):
        raw.append((int(year), _MONTHS[month.lower()[:3]], int(day)))
    for year, month, day in raw:
        try:
            moment = datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            continue  # 31 February on a badly built page is not a date.
        if moment > now:
            continue
        found.add(moment)
    return tuple(sorted(found, reverse=True))


def newest_published_at(text: str, *, now: datetime) -> datetime | None:
    """The most recent date the page itself claims, if it claims one.

    The one owner of that question. It used to live in ``sharia_source_resolution``
    with its own copy of the month names and the three date shapes; the cadence count
    below needs every date rather than the newest one, and two readers of the same
    page would have drifted apart the first time one of them learned a fourth shape.
    """

    dates = published_dates(text, now=now)
    return dates[0] if dates else None


@dataclass(frozen=True, slots=True)
class SourceActivity:
    """What a fetched page shows about how useful it will keep being."""

    category: str
    newest_published_at: datetime | None
    dated_items: int
    recency: float
    cadence: float
    topic: float
    score: float
    financial_terms: tuple[str, ...] = field(default_factory=tuple)
    governance_terms: tuple[str, ...] = field(default_factory=tuple)

    @property
    def active(self) -> bool:
        """Whether this source is still doing the job it was registered for."""

        return self.score >= ACTIVITY_FLOOR

    def as_detail(self) -> dict[str, object]:
        """The shape stored on the source row, for a person to read later."""

        return {
            "score": self.score,
            "recency": self.recency,
            "cadence": self.cadence,
            "topic": self.topic,
            "dated_items": self.dated_items,
            "newest_item": (
                self.newest_published_at.date().isoformat()
                if self.newest_published_at is not None
                else None
            ),
            "money_words": list(self.financial_terms[:12]),
            "rule_words": list(self.governance_terms[:12]),
        }

    def sentence(self) -> str:
        """One plain sentence a non-technical reader can act on."""

        if self.newest_published_at is None:
            dated = "It shows no dates at all"
        else:
            dated = f"Its newest dated item is {self.newest_published_at.date().isoformat()}"
        topic = (
            "it talks about the project's money and rules"
            if self.topic >= 0.5
            else "it says little about the project's money or rules"
        )
        return f"{dated}, it shows {self.dated_items} dated item(s), and {topic}."


def _recency(newest: datetime | None, *, now: datetime) -> float:
    """Full marks for a recent item, nothing once the page is past its age limit."""

    if newest is None:
        return 0.0
    age_days = max(0.0, (now - newest).total_seconds() / 86400.0)
    if age_days <= FRESH_WITHIN_DAYS:
        return 1.0
    if age_days >= NEWS_MAXIMUM_AGE_DAYS:
        return 0.0
    span = NEWS_MAXIMUM_AGE_DAYS - FRESH_WITHIN_DAYS
    return round(max(0.0, 1.0 - (age_days - FRESH_WITHIN_DAYS) / span), 4)


def _cadence(dates: tuple[datetime, ...], *, now: datetime) -> tuple[float, int]:
    cutoff = now - timedelta(days=CADENCE_WINDOW_DAYS)
    recent = [moment for moment in dates if moment >= cutoff]
    return round(min(1.0, len(recent) / CADENCE_TARGET_ITEMS), 4), len(recent)


def _topic(text: str) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    # Long pages are trimmed before matching: the vocabulary question is answered by
    # the first chunk of any real page, and scanning 300k characters with 60 patterns
    # on every source of every coin is time nobody gets back.
    window = text[:120_000]
    financial = tuple(term for term in FINANCIAL_TERMS if _TERM_PATTERNS[term].search(window))
    governance = tuple(term for term in GOVERNANCE_TERMS if _TERM_PATTERNS[term].search(window))
    found = len({*financial, *governance})
    return round(min(1.0, found / TOPIC_TARGET_TERMS), 4), financial, governance


def measure(
    text: str,
    *,
    category: str,
    now: datetime | None = None,
) -> SourceActivity:
    """Score one fetched page.

    ``category`` matters because the two kinds of source are alive in different ways. A
    newsroom proves it is alive by carrying recent dated posts. A forum or a subreddit
    usually renders no dates at all to a plain HTTP client, so demanding them would
    reject every community page there is — its score comes from what it talks about.
    """

    moment = now or datetime.now(UTC)
    dates = published_dates(text, now=moment)
    newest = dates[0] if dates else None
    recency = _recency(newest, now=moment)
    cadence, dated_items = _cadence(dates, now=moment)
    topic, financial, governance = _topic(text)
    if category == NEWS:
        score = (
            recency * RECENCY_WEIGHT + cadence * CADENCE_WEIGHT + topic * TOPIC_WEIGHT
        )
    else:
        # No date signal to be had. The page still has to be about something, and a
        # dated item is counted as a bonus when the platform happens to render one.
        score = topic * (TOPIC_WEIGHT + RECENCY_WEIGHT) + cadence * CADENCE_WEIGHT
    return SourceActivity(
        category=category,
        newest_published_at=newest,
        dated_items=dated_items,
        recency=recency,
        cadence=cadence,
        topic=topic,
        score=round(min(1.0, max(0.0, score)), 4),
        financial_terms=financial,
        governance_terms=governance,
    )

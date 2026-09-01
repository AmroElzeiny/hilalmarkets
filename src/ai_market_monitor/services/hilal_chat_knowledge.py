"""Everything Hilal is allowed to know, read from this platform's own records.

The rule this file exists to keep is B6: **the assistant never invents**. It cannot,
because it is never given anything to invent from. Every fact in a turn is a row
gathered here — a published review, a methodology, a listing, a plan price — and the
model is told, in as many words, that anything not in this evidence does not exist.

Two consequences worth stating plainly:

* **Nicknames come from the listings, not from the model's memory.** "bitcoin", "btc"
  and "$BTC" all resolve to BTC because a row says the symbol BTC is named Bitcoin. A
  hand-written table of nicknames would be a second opinion about what a coin is called
  and would drift from the listings the first time one changed. The only spellings this
  module writes itself are mechanical — case, ``$``, punctuation, plurals.
* **Not found is reported as not found.** ``lookup`` returning nothing is evidence in
  itself, and the model is required to say so rather than reach for what it remembers
  about a coin from elsewhere.

Nothing here reads a price, a chart or anything outside the platform. Hilal is an
expert on Hilal Markets, and on nothing else (rule B5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PROMOTION_ENDS_AT,
    PUBLIC_PLAN_PRESENTATIONS,
    effective_monthly_price,
    original_monthly_price,
)
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    AssetShariaStatusHistory,
    CanonicalAsset,
    ExchangeMarket,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus
from ai_market_monitor.schemas.hilal_chat import HilalChatView
from ai_market_monitor.services.hilal_product_words import product_words
from ai_market_monitor.services.sharia_screening import STATUS_LABELS, canonical_asset

#: Words that are part of how people say a coin's name rather than part of the name.
#: Removing them is spelling, not vocabulary — "the bitcoin coin" and "Bitcoin" are the
#: same listing, and no table of nicknames is needed to know it.
_NOISE = re.compile(
    r"\b(coin|coins|token|tokens|crypto|cryptocurrency|the|a|an|is|are|about|status)\b",
    re.IGNORECASE,
)
_PUNCTUATION = re.compile(r"[^a-z0-9\s]+")

#: One word as a person wrote it, keeping the marks that say "this is a ticker".
#:
#: A leading ``$`` and an inner ``/`` are both part of how a coin is written — ``$LTC``
#: and ``LTC/USDT`` — so both stay attached to the word rather than splitting it in two.
_TOKEN = re.compile(r"\$?[A-Za-z0-9][A-Za-z0-9/._-]*")


def spelling_keys(value: str) -> set[str]:
    """Every mechanical spelling of one name, for matching what a person typed.

    Mechanical only: lower case, no ``$``, no punctuation, no filler words. This does
    not know that "the king" means Bitcoin, and deliberately so — that would be an
    opinion, and opinions about what a coin is called belong in the listing.
    """

    lowered = _PUNCTUATION.sub(" ", value.lower())
    squeezed = " ".join(lowered.split())
    if not squeezed:
        return set()
    keys = {squeezed}
    without_noise = " ".join(_NOISE.sub(" ", squeezed).split())
    if without_noise:
        keys.add(without_noise)
    keys.add(squeezed.replace(" ", ""))
    return {key for key in keys if key}


@dataclass(frozen=True, slots=True)
class AssetFacts:
    """One coin, as this platform has recorded it."""

    symbol: str
    name: str | None
    category: str | None
    status: str | None
    status_words: str | None
    methodology: str | None
    methodology_version: str | None
    summary: str | None
    qualifications: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    reviewed_at: str | None
    exchanges: tuple[str, ...]
    #: Why the status last changed, when it has changed at all.
    last_change: dict[str, str] | None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "id": f"asset:{self.symbol}",
            "kind": "listed_coin",
            # Said in words, because the difference matters more than any other line
            # here and an empty status field does not carry it. "We have never heard of
            # this coin" and "we have it, and its review is not published yet" are
            # opposite answers, and a person told the first about the second has been
            # given wrong information about the product.
            "what_we_have": (
                "This coin is on this platform, and a review is recorded for it."
                if self.status_words
                else "This coin is on this platform. No review is published for it yet."
            ),
            "symbol": self.symbol,
            "name": self.name,
            "category": self.category,
            "shariah_status": self.status_words,
            "under_methodology": self.methodology,
            "methodology_version": self.methodology_version,
            "why": self.summary,
            "qualifications": list(self.qualifications),
            "exclusion_reasons": list(self.exclusion_reasons),
            "reviewed_at": self.reviewed_at,
            "traded_on": list(self.exchanges),
            "last_status_change": self.last_change,
        }


@dataclass
class Evidence:
    """Everything one turn is allowed to reason from."""

    asked_about: list[AssetFacts] = field(default_factory=list)
    methodologies: list[dict[str, Any]] = field(default_factory=list)
    market_shape: dict[str, Any] = field(default_factory=dict)
    exchanges: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    looked_for_but_not_listed: list[str] = field(default_factory=list)
    on_screen: dict[str, Any] = field(default_factory=dict)
    #: What this product's own words mean. See `services/hilal_product_words.py`.
    words: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "coins_the_question_mentions": [
                item.to_evidence() for item in self.asked_about
            ],
            "names_that_are_not_listed_here": self.looked_for_but_not_listed,
            "screening_standards_in_use": self.methodologies,
            "how_many_coins_hold_each_status": self.market_shape,
            "exchanges_this_platform_covers": self.exchanges,
            "categories_this_platform_records": self.categories,
            "plans_and_prices": self.plans,
            "words_this_product_uses": self.words,
            "what_they_can_see": self.on_screen,
        }

    @property
    def ids(self) -> set[str]:
        found = {f"asset:{item.symbol}" for item in self.asked_about}
        found |= {str(item["id"]) for item in self.methodologies if "id" in item}
        found |= {str(item["id"]) for item in self.plans if "id" in item}
        found |= {str(item["id"]) for item in self.words if "id" in item}
        if self.market_shape:
            found.add("market:shape")
        if self.exchanges:
            found.add("market:exchanges")
        if self.categories:
            found.add("market:categories")
        return found


class HilalChatKnowledge:
    """Gathers the evidence for one turn. Reads only; writes nothing."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def gather(
        self,
        *,
        message: str,
        view: HilalChatView | None,
        earlier: list[str] | None = None,
    ) -> Evidence:
        """Everything one turn may reason from.

        ``earlier`` is what has already been said in this conversation, oldest first.
        It is read for coin names as well as the new message, and that is not a
        nicety — it is the difference between an answer and a wrong one.

        A person asks "is litecoin halal?", Hilal asks "did you mean Litecoin?", and
        they answer **"yes"**. The word "yes" contains no coin. Looking only at the new
        message, Hilal gathered nothing, found nothing, and told them Litecoin was not
        listed here — inventing a negative about a coin the platform has. Rule B6 says
        never invent; saying "we do not have it" when we do is the same failure facing
        the other way.
        """

        evidence = Evidence()
        # Handed over on every turn, not only when the canvas is open. Somebody who is
        # lost asks "what is a group" from wherever they happen to be, and an answer that
        # depended on which page they were on would be missing exactly when it is needed.
        evidence.words = product_words()
        evidence.methodologies = await self._methodologies()
        evidence.market_shape = await self._market_shape()
        evidence.exchanges = await self._exchanges()
        evidence.categories = await self._categories()
        evidence.plans = self._plans()
        evidence.on_screen = self._on_screen(view)

        subject = (view.subject if view else None) or ""
        # Order is relevance, and it decides which coins survive the cap: what they
        # just said, then what is open in front of them, then what the conversation was
        # already about — most recent first.
        wanted = self._names_in(message)
        if subject.strip():
            wanted.append(subject.strip())
        carried: list[str] = []
        for said in reversed(earlier or []):
            carried.extend(self._names_in(said))
        found, missing = await self._resolve(
            wanted, carried=carried, asked_now=self._tickers_in(message)
        )
        evidence.asked_about = found
        evidence.looked_for_but_not_listed = missing
        return evidence

    # -- what the question is about ---------------------------------------

    @staticmethod
    def _names_in(message: str) -> list[str]:
        """The words in a question that might be a coin, longest phrase first.

        Generous on purpose. Anything that turns out not to be a listing simply finds
        nothing, and finding nothing is itself reported — so a wide net costs a lookup,
        while a narrow one costs a wrong answer.

        Ordered, not a set. The order decides which coins survive the cap on how many
        may be looked up, and an unordered set made that a coin toss: the same question
        could gather a different coin on a second run. Two-word names come first, so
        "bitcoin cash" is preferred over the "bitcoin" inside it.

        Written as the person wrote it, not lowered. Lowering here threw away the two
        marks that say "I mean this as a ticker" — capitals and a leading ``$`` — and
        with them the whole ability to report a coin as not listed. Matching does not
        care about case; :func:`spelling_keys` handles that further down.
        """

        words = [
            token
            for token in _TOKEN.findall(message)
            if 2 <= len(token.lstrip("$")) <= 24
        ]
        pairs = [f"{first} {second}" for first, second in zip(words, words[1:], strict=False)]
        ordered: list[str] = []
        for candidate in [*pairs, *words]:
            if candidate not in ordered:
                ordered.append(candidate)
        return ordered

    @staticmethod
    def _tickers_in(message: str) -> list[str]:
        """The words a person meant as a coin symbol, rather than as English.

        One owner for that judgement, because it decides what may be reported as "not
        listed here" — and reporting every ordinary word would bury the one that
        mattered under a wall of nonsense.

        A message written entirely in capitals is not a message full of tickers. "IS
        BTC HALAL" would otherwise announce that IS and HALAL are unlisted coins.
        """

        shouting = message == message.upper()
        found: list[str] = []
        for token in _TOKEN.findall(message):
            # Judged on the coin half. "LTC/USDT" is as deliberate a way of naming a
            # coin as "LTC" is, and reading the whole pair as one word made it neither
            # alphanumeric nor short enough to count.
            base = token.lstrip("$").partition("/")[0]
            if not base or len(base) > 12:
                continue
            marked = token.startswith("$")
            capitals = base.isupper() and base.isalnum() and len(base) >= 3 and not shouting
            if (marked or capitals) and token not in found:
                found.append(token)
        return found

    async def _resolve(
        self,
        wanted: list[str],
        *,
        carried: list[str] | None = None,
        asked_now: list[str] | None = None,
    ) -> tuple[list[AssetFacts], list[str]]:
        """Match what a person typed against the listings, and say what was not found.

        ``carried`` is what the conversation was already about. It is looked up after
        everything in the new message, so a coin named three turns ago can still be
        answered about — but never at the cost of the one just asked about.

        ``asked_now`` is the only thing a miss may be reported for. A coin nobody has
        mentioned since the first turn should not keep being announced as missing.
        """

        index = await self._listing_index()
        symbols: list[str] = []
        for item in [*wanted, *(carried or [])]:
            for key in spelling_keys(item):
                symbol = index.get(key)
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        symbols = symbols[: self.settings.hilal_chat_max_evidence_assets]

        facts = [await self._facts_for(symbol) for symbol in symbols]
        found = [item for item in facts if item is not None]

        # Only what the person meant as a coin symbol, decided once in `_tickers_in`.
        listed = {item.symbol.lower() for item in found}
        missing = sorted(
            {
                item.strip()
                for item in (asked_now if asked_now is not None else wanted)
                if item.strip()
                and item.strip().lstrip("$").lower() not in listed
                and not any(index.get(key) for key in spelling_keys(item))
            }
        )
        return found, missing[:8]

    async def _listing_index(self) -> dict[str, str]:
        """Every spelling of every listed coin, pointing at its symbol.

        Built from the listings themselves. All three tables are read:

        * ``CanonicalAsset`` — the identity, its symbol and its name;
        * ``AssetShariaAssessment`` — a coin can be reviewed before it has an identity
          row, and a person asking about it should get its recorded status rather than
          "not listed";
        * ``ExchangeMarket`` — the market symbols this platform actually covers.

        The third one is the answer to somebody typing **LTCUSDT**. A trader reads a
        pair off a chart and types it whole; nothing here matched it, so Hilal reported
        a coin the platform has as one it had never heard of. No list of quote
        currencies is written anywhere for this. There is a row saying the market
        ``LTC/USDT`` exists, and the mechanical spellings of that row already include
        ``ltcusdt``. The data knows; it only had to be asked.
        """

        index: dict[str, str] = {}

        rows = (
            await self.session.execute(select(CanonicalAsset.symbol, CanonicalAsset.name))
        ).all()
        for symbol, name in rows:
            for key in spelling_keys(symbol):
                index.setdefault(key, symbol)
            if name:
                for key in spelling_keys(name):
                    index.setdefault(key, symbol)

        named = (
            await self.session.execute(
                select(
                    AssetShariaAssessment.canonical_asset,
                    AssetShariaAssessment.asset_name,
                ).distinct()
            )
        ).all()
        for symbol, name in named:
            for key in spelling_keys(symbol):
                index.setdefault(key, symbol)
            if name:
                for key in spelling_keys(name):
                    index.setdefault(key, symbol)

        # Added last, so a market symbol can never take a key that a coin's own symbol
        # or name already owns.
        pairs = (
            await self.session.execute(
                select(ExchangeMarket.market_symbol, CanonicalAsset.symbol)
                .join(CanonicalAsset, CanonicalAsset.id == ExchangeMarket.canonical_asset_id)
                .where(ExchangeMarket.is_active.is_(True))
                .distinct()
            )
        ).all()
        for market_symbol, symbol in pairs:
            if not market_symbol:
                continue
            for key in spelling_keys(str(market_symbol)):
                index.setdefault(key, symbol)
        return index

    async def _facts_for(self, symbol: str) -> AssetFacts | None:
        """One coin's recorded position, or nothing at all.

        The status is read from the newest assessment that is in force. It is reported,
        never judged: this returns what a reviewer decided and when, and the model is
        told it may only repeat it.
        """

        wanted = canonical_asset(symbol)
        row = (
            await self.session.execute(
                select(AssetShariaAssessment, ShariaMethodology)
                .join(
                    ShariaMethodology,
                    ShariaMethodology.id == AssetShariaAssessment.methodology_id,
                )
                .where(AssetShariaAssessment.canonical_asset == wanted)
                .order_by(AssetShariaAssessment.valid_from.desc())
                .limit(1)
            )
        ).first()

        identity = (
            await self.session.execute(
                select(CanonicalAsset).where(CanonicalAsset.symbol == wanted).limit(1)
            )
        ).scalar_one_or_none()

        if row is None and identity is None:
            return None

        assessment = row[0] if row else None
        methodology = row[1] if row else None

        exchanges: tuple[str, ...] = ()
        if identity is not None:
            names = (
                (
                    await self.session.execute(
                        select(ExchangeMarket.exchange)
                        .where(
                            ExchangeMarket.canonical_asset_id == identity.id,
                            ExchangeMarket.is_active.is_(True),
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            exchanges = tuple(sorted(str(item) for item in names))

        return AssetFacts(
            symbol=wanted,
            name=(assessment.asset_name if assessment else None)
            or (identity.name if identity else None),
            category=identity.asset_type if identity else None,
            status=assessment.status.value if assessment else None,
            status_words=(
                STATUS_LABELS.get(assessment.status, assessment.status.value)
                if assessment
                else None
            ),
            methodology=methodology.name if methodology else None,
            methodology_version=methodology.version if methodology else None,
            summary=assessment.summary if assessment else None,
            qualifications=tuple(assessment.qualifications or ()) if assessment else (),
            exclusion_reasons=tuple(
                str(item.get("reason") or item.get("summary") or "")
                for item in (assessment.exclusion_reasons or [])
                if isinstance(item, dict)
            )
            if assessment
            else (),
            reviewed_at=_day(assessment.reviewed_at) if assessment else None,
            exchanges=exchanges,
            last_change=await self._last_change(wanted),
        )

    async def _last_change(self, symbol: str) -> dict[str, str] | None:
        """Why this coin's status last moved. The answer to "why did it change?"."""

        row = (
            await self.session.execute(
                select(AssetShariaStatusHistory)
                .where(AssetShariaStatusHistory.canonical_asset == symbol)
                .order_by(AssetShariaStatusHistory.changed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "from": (
                STATUS_LABELS.get(row.previous_status, row.previous_status.value)
                if row.previous_status
                else "not reviewed yet"
            ),
            "to": STATUS_LABELS.get(row.new_status, row.new_status.value),
            "because": row.reason_summary,
            "on": _day(row.changed_at) or "",
        }

    # -- the shape of the platform ----------------------------------------

    async def _methodologies(self) -> list[dict[str, Any]]:
        # Active only. A draft or an archived standard is not something a customer's
        # coins are screened under, and naming one would tell them their Passport rests
        # on a rule that is not in force.
        rows = (
            (
                await self.session.execute(
                    select(ShariaMethodology)
                    .where(ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE)
                    .order_by(ShariaMethodology.name)
                    .limit(12)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": f"methodology:{item.code}",
                "kind": "screening_standard",
                "name": item.name,
                "version": item.version,
                "what_it_is": item.description,
                "governing_body": item.governing_body,
                "in_force_from": _day(item.effective_from),
            }
            for item in rows
        ]

    async def _market_shape(self) -> dict[str, Any]:
        """How many coins hold each status. The honest answer to "what do you cover?"."""

        rows = (
            await self.session.execute(
                select(
                    AssetShariaAssessment.status,
                    func.count(func.distinct(AssetShariaAssessment.canonical_asset)),
                ).group_by(AssetShariaAssessment.status)
            )
        ).all()
        shape = {
            STATUS_LABELS.get(status, status.value): int(count)
            for status, count in rows
            if isinstance(status, ShariaAssetStatus)
        }
        shape["reviewed in total"] = sum(shape.values())
        return shape

    async def _exchanges(self) -> list[str]:
        names = (
            (
                await self.session.execute(
                    select(ExchangeMarket.exchange)
                    .where(ExchangeMarket.is_active.is_(True))
                    .distinct()
                    .order_by(ExchangeMarket.exchange)
                )
            )
            .scalars()
            .all()
        )
        return [str(item) for item in names]

    async def _categories(self) -> list[str]:
        names = (
            (
                await self.session.execute(
                    select(CanonicalAsset.asset_type).distinct().order_by(CanonicalAsset.asset_type)
                )
            )
            .scalars()
            .all()
        )
        return [str(item).replace("_", " ") for item in names if item]

    def _plans(self) -> list[dict[str, Any]]:
        """What each publicly offered plan costs, from the plan catalogue itself.

        Only the plans the site actually presents. ``PLAN_DEFINITIONS`` also holds
        internal ones — partner and lifetime arrangements — and quoting those to a
        customer would offer something that is not for sale.

        The price is today's price, read from the same offer the pricing page reads.
        Quoting ``monthly_price`` straight from the catalogue said $20 while every
        pricing surface said the launch price, which is the one disagreement this
        whole module exists to prevent. The normal price and the deadline travel with
        it, so Hilal can say what the offer is instead of only what it costs now.
        """

        rows: list[dict[str, Any]] = []
        for code, definition in PLAN_DEFINITIONS.items():
            if code not in PUBLIC_PLAN_PRESENTATIONS:
                continue
            today = effective_monthly_price(code)
            before = original_monthly_price(code)
            row: dict[str, Any] = {
                "id": f"plan:{code}",
                "kind": "plan",
                "name": definition.name,
                "price_per_month": f"{today} {definition.currency}",
                "what_it_is_for": definition.description,
            }
            if before is not None:
                row["normal_price_per_month"] = f"{before} {definition.currency}"
                row["launch_price_ends_at"] = PROMOTION_ENDS_AT.isoformat()
            rows.append(row)
        return rows[:8]

    @staticmethod
    def _on_screen(view: HilalChatView | None) -> dict[str, Any]:
        """What the person can see, as their own page describes it.

        Two different things travel together here, and the difference is the whole
        reason for the note at the bottom:

        * **Where they are** — the page, the part of it in view, the coin whose
          Passport is open. Rule C5: this is context, never a source of fact. Knowing
          somebody is looking at BTC helps Hilal understand "why is this one excluded?";
          it tells Hilal nothing *about* BTC, and the answer still comes from the rows
          above.
        * **What they have drawn** — the monitor on the canvas. This one *is* the
          subject when they ask about it, because it is their own unsaved draft and no
          record of it exists anywhere else yet. It arrives already worded by the
          canvas's own readout, so Hilal repeats the page's words rather than forming a
          second opinion about what a card means.
        """

        if view is None:
            return {}
        board = view.board
        if not view.page and not view.subject and not view.section and board is None:
            return {}
        seen: dict[str, Any] = {
            "page": view.page,
            "part_of_the_page_in_front_of_them": view.section,
            "coin_or_passport_open": view.subject,
            "note": (
                "Where they are, not what is true. Every fact about a coin, a standard "
                "or a plan must still come from the records above."
            ),
        }
        if board is not None:
            seen["the_monitor_they_are_drawing"] = {
                "reads_as": board.sentence,
                "how_far_along_the_page_says_it_is": f"{board.ready_percent}%",
                "cards_on_the_board": [
                    {
                        "card": card.label,
                        "says": card.reads,
                        "must_be_true": card.required,
                        "sits_in": card.inside,
                        "set_aside_from_the_monitor": card.set_aside,
                        "still_needs": list(card.needs),
                    }
                    for card in board.cards
                ],
                "the_pages_own_checklist": [
                    {"result": check.tone, "says": check.text} for check in board.checks
                ],
                "watching": board.watching,
                "ways_to_be_told_that_are_chosen": list(board.ways_to_be_told),
                "controls_actually_on_their_screen": list(board.controls),
                "how_this_board_is_worked": list(board.how_to),
                "note": (
                    "This is their own draft, exactly as their page words it. It is "
                    "the one thing here you may talk about directly. Only name a "
                    "control that appears in the list above, and spell it the same "
                    "way. Only describe a key or a gesture that appears in how this "
                    "board is worked."
                ),
            }
        return seen


def _day(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%d %B %Y")

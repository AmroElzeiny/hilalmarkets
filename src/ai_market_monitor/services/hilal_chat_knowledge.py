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

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.cockpit_service import StrategyCockpitService
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.plans import (
    PLAN_DEFINITIONS,
    PUBLIC_PLAN_PRESENTATIONS,
    effective_monthly_price,
    original_monthly_price,
    promotion_ends_at,
    running_offer_code,
    running_offer_percent,
)
from ai_market_monitor.core.site_content import (
    ACCOUNT_MENU,
    DASHBOARD_NAVIGATION,
    PUBLIC_PAGES,
    footer_navigation,
    public_help_categories,
    public_navigation,
)
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    AssetShariaStatusHistory,
    CanonicalAsset,
    ExchangeMarket,
    ShariaMethodology,
    Strategy,
)
from ai_market_monitor.db.models.enums import (
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    StrategyStatus,
)
from ai_market_monitor.schemas.hilal_chat import HilalChatView
from ai_market_monitor.services.coin_mentions import (
    CoinListingIndex,
    names_in,
    spelling_keys,
    tickers_in,
)
from ai_market_monitor.services.entitlements import EntitlementService
from ai_market_monitor.services.hilal_methodology import (
    AUTOMATED_DISCLOSURE,
    METHODOLOGY_PUBLIC_PATH,
    UNDER_DEVELOPMENT_NOTICE,
    is_automated,
)
from ai_market_monitor.services.hilal_product_words import product_words
from ai_market_monitor.services.monitor_scan_state import scan_state_for_version
from ai_market_monitor.services.notification_preferences import (
    NotificationPreferenceService,
    deliverable_channels,
)
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from ai_market_monitor.services.sharia_screening import (
    STATUS_LABELS,
    ShariaScreeningError,
    canonical_asset,
)
from ai_market_monitor.services.source_previews import source_key_for_path

#: The spelling rules live in `services/coin_mentions.py`, shared with the public
#: assistant. `spelling_keys` is re-exported here because this is where callers have
#: always found it.
__all__ = ["spelling_keys"]

#: The side-menu entries that also sit in the account menu under the person's name.
_ACCOUNT_MENU_NAMES: dict[str, str] = {item.endpoint: item.label for item in ACCOUNT_MENU}


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
    passports: list[dict[str, Any]] = field(default_factory=list)
    market_shape: dict[str, Any] = field(default_factory=dict)
    exchanges: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    looked_for_but_not_listed: list[str] = field(default_factory=list)
    on_screen: dict[str, Any] = field(default_factory=dict)
    #: This person's own monitors, plan and alert channels. Present only for a
    #: signed-in person; anonymous turns carry nothing here.
    account: dict[str, Any] = field(default_factory=dict)
    #: True when the payload was cut to fit the size cap below. The model is told
    #: more records wait, so it says so rather than inventing them.
    trimmed_for_size: bool = False
    #: What this product's own words mean. See `services/hilal_product_words.py`.
    words: list[dict[str, Any]] = field(default_factory=list)
    #: Every page of the product — the dashboard's side menu and the public site — with
    #: where it is and what it is for. See :meth:`HilalChatKnowledge._pages`.
    pages: list[dict[str, Any]] = field(default_factory=list)
    #: The Help Center's own questions and answers, word for word.
    help_answers: list[dict[str, Any]] = field(default_factory=list)
    #: The listed coins *this* message names, as symbols — not the ones carried over
    #: from earlier turns. Their Passports are offered under the answer.
    named_now: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "coins_the_question_mentions": [
                item.to_evidence() for item in self.asked_about
            ],
            "passport_records": self.passports,
            "names_that_are_not_listed_here": self.looked_for_but_not_listed,
            "screening_standards_in_use": self.methodologies,
            "how_many_coins_hold_each_status": self.market_shape,
            "exchanges_this_platform_covers": self.exchanges,
            "categories_this_platform_records": self.categories,
            "plans_and_prices": self.plans,
            "their_own_account": self.account,
            "words_this_product_uses": self.words,
            "pages_in_this_product": self.pages,
            "help_center_answers": self.help_answers,
            "what_they_can_see": self.on_screen,
        }
        if self.trimmed_for_size:
            payload["note_more_records_waiting"] = (
                "More records exist than fit in this turn. Say that more exist "
                "rather than filling them in from memory."
            )
        return payload

    @property
    def reviewed_passports_named_now(self) -> list[str]:
        """Evidence ids of the coins this message names that have a published review.

        A coin with no review yet has no Passport to open, so it has no card.
        """

        reviewed = {item.symbol for item in self.asked_about if item.status_words}
        return [f"asset:{symbol}" for symbol in self.named_now if symbol in reviewed]

    @property
    def ids(self) -> set[str]:
        found = {f"asset:{item.symbol}" for item in self.asked_about}
        found |= {str(item["id"]) for item in self.methodologies if "id" in item}
        found |= {str(item["id"]) for item in self.passports if "id" in item}
        found |= {str(item["id"]) for item in self.plans if "id" in item}
        found |= {str(item["id"]) for item in self.words if "id" in item}
        found |= {str(item["id"]) for item in self.pages if "id" in item}
        found |= {str(item["id"]) for item in self.help_answers if "id" in item}
        if self.market_shape:
            found.add("market:shape")
        if self.exchanges:
            found.add("market:exchanges")
        if self.categories:
            found.add("market:categories")
        if self.account.get("monitors"):
            found.add("account:monitors")
        if self.account.get("plan"):
            found.add("account:plan")
        if self.account.get("alert_channels"):
            found.add("account:channels")
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
        user_id: UUID | None = None,
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

        ``user_id`` scopes the account records to the signed-in person. ``None``
        means anonymous: no monitors, no plan, no channels — nothing about anybody.
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
        # Every page, on every turn, wherever they are. "Where is the FAQ" was answered
        # with "there is no FAQ" because Hilal only ever saw the page it was opened on.
        evidence.pages = self._pages(view)
        evidence.help_answers = self._help_answers()

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
        index = await self._listing_index()
        found, missing = await self._resolve(
            wanted, carried=carried, asked_now=self._tickers_in(message), index=index
        )
        evidence.asked_about = found
        evidence.looked_for_but_not_listed = missing
        named = {
            symbol
            for item in self._names_in(message)
            for key in spelling_keys(item)
            if (symbol := index.get(key))
        }
        evidence.named_now = [item.symbol for item in found if item.symbol in named]
        evidence.passports = await self._passports(
            [item.symbol for item in found], subject=subject.strip() or None
        )
        if user_id is not None:
            evidence.account = await self._account(user_id)
        self._fit_to_size(evidence)
        return evidence

    # -- what the question is about ---------------------------------------

    @staticmethod
    def _names_in(message: str) -> list[str]:
        """The words in a question that might be a coin. See :func:`names_in`."""

        return names_in(message)

    @staticmethod
    def _tickers_in(message: str) -> list[str]:
        """The words a person meant as a coin symbol. See :func:`tickers_in`."""

        return tickers_in(message)

    async def _resolve(
        self,
        wanted: list[str],
        *,
        carried: list[str] | None = None,
        asked_now: list[str] | None = None,
        index: CoinListingIndex | None = None,
    ) -> tuple[list[AssetFacts], list[str]]:
        """Match what a person typed against the listings, and say what was not found.

        ``carried`` is what the conversation was already about. It is looked up after
        everything in the new message, so a coin named three turns ago can still be
        answered about — but never at the cost of the one just asked about.

        ``asked_now`` is the only thing a miss may be reported for. A coin nobody has
        mentioned since the first turn should not keep being announced as missing.
        """

        index = index or await self._listing_index()
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

    async def _listing_index(self) -> CoinListingIndex:
        """Every spelling of every listed coin. One owner: :class:`CoinListingIndex`."""

        return await CoinListingIndex.load(self.session)

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
                # Who stands behind it, in the stored row's own words. For our own
                # standard this says no scholar does — and that sentence is the point.
                "decided_by": item.reviewer_group,
                # Where a reader can see the whole thing. Only our own standard
                # publishes one; anything else stays missing rather than invented.
                "source_document": (
                    METHODOLOGY_PUBLIC_PATH if is_automated(item.code) else None
                ),
                # What uses this standard speaks about, from its own stored rules.
                "screens": _screens_for(item.rules_json),
                "in_force_from": _day(item.effective_from),
            }
            for item in rows
        ]

    async def _passports(
        self, symbols: list[str], *, subject: str | None = None
    ) -> list[dict[str, Any]]:
        """One Passport row per coin per standard that screened it.

        Read through ``ShariaPassportReadService`` — the one owner for Passport
        reads — never assembled here from parts. A coin with no assessment under a
        standard gets no row under it, and the model is told to say so rather than
        fill the gap. Only names and dates of evidence travel: never a link, because
        the model may not output one.
        """

        ordered = list(dict.fromkeys([*(symbols or []), *([subject] if subject else [])]))
        rows: list[dict[str, Any]] = []
        reader = ShariaPassportReadService(self.session, self.settings)
        for symbol in ordered[:8]:
            methodology_ids = (
                (
                    await self.session.execute(
                        select(AssetShariaAssessment.methodology_id)
                        .where(AssetShariaAssessment.canonical_asset == symbol)
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
            for methodology_id in list(methodology_ids)[:8]:
                try:
                    passport = await reader.current(symbol, methodology_id=methodology_id)
                except ShariaScreeningError:
                    # Not screened, not published, or not this standard's to show.
                    # Missing stays missing; the model says so.
                    continue
                row = await self._passport_row(passport)
                if row is not None:
                    rows.append(row)
        return rows

    async def _passport_row(self, passport: Any) -> dict[str, Any] | None:
        """One coin under one named standard, small enough to send every turn."""

        assessment = passport.assessment
        code = assessment.methodology_code or ""
        record = (
            await self.session.execute(
                select(AssetShariaAssessment)
                .where(
                    AssetShariaAssessment.canonical_asset == assessment.canonical_asset,
                    AssetShariaAssessment.methodology_id == assessment.methodology_id,
                )
                .order_by(AssetShariaAssessment.valid_from.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        snapshot = dict((record.evidence_snapshot if record else None) or {})
        automated = is_automated(code)
        route = str(snapshot.get("admission") or "") or (
            "automated_screen" if automated else "reviewed_decision"
        )
        exclusions = (
            [
                str(item.get("reason") or item.get("summary") or "").strip()
                for item in (record.exclusion_reasons if record else []) or []
                if isinstance(item, dict)
            ]
            if record
            else []
        )
        next_checks = [
            passport.next_review_at,
            passport.evidence_expires_at,
            passport.next_source_scan_at,
        ]
        review_at = _day(assessment.reviewed_at)
        return {
            "id": f"passport:{assessment.canonical_asset}:{code or 'unknown'}",
            "kind": "passport_record",
            "symbol": assessment.canonical_asset,
            "methodology_code": code,
            "methodology_name": assessment.methodology_name,
            "methodology_version": assessment.methodology_version,
            "status_words": assessment.status_label,
            "why": (passport.why_this_status or "")[:300],
            "qualifications": list(assessment.qualifications or [])[:3],
            "exclusion_reasons": [item for item in exclusions if item][:3],
            "reviewed_at": review_at,
            "reviewed_by": assessment.reviewed_by,
            "next_check": next(
                (
                    _day(moment)
                    for moment in next_checks
                    if _day(moment) is not None
                ),
                None,
            ),
            "evidence": [
                {
                    "name": source.title,
                    "from": source.publisher,
                    "retrieved": _day(source.retrieved_at),
                }
                for source in (passport.evidence_sources or [])[:6]
            ],
            "admission_route": route,
            # Our own standard is always named as what it is: an automated reading
            # no scholar stands behind. Never "the" answer, never merged, never default.
            "automated_notice": (
                f"{UNDER_DEVELOPMENT_NOTICE} {AUTOMATED_DISCLOSURE}"
                if automated
                else None
            ),
        }

    async def _account(self, user_id: UUID) -> dict[str, Any]:
        """This person's own monitors, plan and alert channels. Read-only.

        Scoped by ``user_id`` at every query: every row read names this person, so
        one person's turn can never carry another person's rows. Each half is read
        from its existing owner — the monitor rows, the entitlement, the
        notification preferences — never re-decided here.
        """

        strategies = (
            (
                await self.session.execute(
                    select(Strategy)
                    .where(
                        Strategy.user_id == user_id,
                        Strategy.archived_at.is_(None),
                        Strategy.status != StrategyStatus.ARCHIVED,
                    )
                    .order_by(Strategy.created_at.desc())
                    .limit(6)
                )
            )
            .scalars()
            .all()
        )
        cockpit = StrategyCockpitService(self.session)
        bottlenecks = await cockpit.stored_main_bottlenecks(
            [strategy.id for strategy in strategies]
        )
        monitors: list[dict[str, Any]] = []
        for strategy in strategies:
            held_back = bottlenecks.get(strategy.id) or {}
            scan = await scan_state_for_version(self.session, strategy.active_version_id)
            monitors.append(
                {
                    "name": strategy.name,
                    "state": _monitor_state(strategy),
                    # What is holding it back, in the stored aggregate's own words —
                    # the same sentence the Monitors page shows. Nothing when nothing
                    # is recorded; a missing answer stays missing.
                    "still_needs": held_back.get("condition_label"),
                    "last_check": (
                        _day(scan.last_checked_at)
                        if scan.last_checked_at is not None
                        else None
                    ),
                }
            )
        entitlement = await EntitlementService(self.session).current(user_id)
        preference = await NotificationPreferenceService(
            self.session, self.settings
        ).current(user_id)
        reachable = await deliverable_channels(
            self.session, self.settings, user_id=user_id
        )
        return {
            "monitors": monitors,
            "plan": {"name": entitlement.plan.name},
            "alert_channels": {
                "chosen": sorted(channel.value for channel in preference.channels),
                "connected": sorted(channel.value for channel in reachable),
            },
        }

    def _fit_to_size(self, evidence: Evidence) -> None:
        """Keep one turn under the payload cap. Drops whole rows, last first —
        Passport rows, then on-screen card rows, then the page's own checklist,
        then this person's monitor rows, then the Help Center's answers — never
        refuses the turn, and never
        merges what is left into a single invented answer."""

        cap = self.settings.hilal_chat_evidence_max_chars
        if len(json.dumps(evidence.to_payload(), default=str)) <= cap:
            return
        trimmed = False
        for row in evidence.passports:
            if len(str(row.get("why") or "")) > 150:
                row["why"] = str(row["why"])[:150]
                trimmed = True
        while (
            evidence.passports
            and len(json.dumps(evidence.to_payload(), default=str)) > cap
        ):
            evidence.passports.pop()
            trimmed = True
        board = evidence.on_screen.get("the_monitor_they_are_drawing")
        if isinstance(board, dict):
            cards = board.get("cards_on_the_board")
            while (
                isinstance(cards, list)
                and cards
                and len(json.dumps(evidence.to_payload(), default=str)) > cap
            ):
                cards.pop()
                trimmed = True
            checks = board.get("the_pages_own_checklist")
            while (
                isinstance(checks, list)
                and checks
                and len(json.dumps(evidence.to_payload(), default=str)) > cap
            ):
                checks.pop()
                trimmed = True
        monitors = (
            evidence.account.get("monitors")
            if isinstance(evidence.account, dict)
            else None
        )
        while (
            isinstance(monitors, list)
            and monitors
            and len(json.dumps(evidence.to_payload(), default=str)) > cap
        ):
            monitors.pop()
            trimmed = True
        # The Help Center's answers last: the page list still says where they live, so
        # a person who needs one can still be sent to it.
        while (
            evidence.help_answers
            and len(json.dumps(evidence.to_payload(), default=str)) > cap
        ):
            evidence.help_answers.pop()
            trimmed = True
        if trimmed:
            evidence.trimmed_for_size = True

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
            # One price: what a checkout charges today, with nothing typed anywhere. While
            # the offer runs that **is** the offer price, so Hilal quotes the same figure
            # every pricing surface shows. The normal price travels beside it, with the
            # offer's name and — only when it has one — its end date, so Hilal can explain
            # the offer rather than only name a number.
            charged = effective_monthly_price(code)
            was = original_monthly_price(code)
            row: dict[str, Any] = {
                "id": f"plan:{code}",
                "kind": "plan",
                "name": definition.name,
                "price_per_month": f"{charged} {definition.currency}",
                "what_it_is_for": definition.description,
            }
            if was is not None:
                row["normal_price_per_month"] = f"{was} {definition.currency}"
                row["offer_name"] = running_offer_code(code)
                row["offer_percent_off"] = str(running_offer_percent(code))
                ends_at = promotion_ends_at()
                if ends_at is not None:
                    row["offer_ends_at"] = ends_at
                row["how_the_offer_price_is_reached"] = (
                    "Nothing needs to be typed. The lower price is already what every "
                    "buyer pays, by card or by crypto."
                )
            rows.append(row)
        return rows[:8]

    def _pages(self, view: HilalChatView | None) -> list[dict[str, Any]]:
        """Every page somebody can be sent to, where it is, and what it is for.

        Read from the menus themselves — the dashboard's side menu and the public site's
        header and footer — so Hilal names a page exactly as the menu does and never
        sends anybody to one the launch stage has hidden. Each row's id is the page's
        card in `services/source_previews.py`, so an answer that rests on it ends with a
        card that opens it. No address travels: Hilal may not write a link, and the card
        is the link.
        """

        current = (view.page if view else None) or ""
        rows: list[dict[str, Any]] = []
        for group in DASHBOARD_NAVIGATION:
            for item in group.items:
                key = source_key_for_path(item.path)
                if key is None:
                    continue
                where = f"In the dashboard's side menu, under {group.label}."
                also = _ACCOUNT_MENU_NAMES.get(item.endpoint)
                if also is not None:
                    where += (
                        " Also in the account menu that opens from your name at the "
                        "bottom of the side menu"
                        + (f", where it is called {also}." if also != item.label else ".")
                    )
                rows.append(
                    {
                        "id": f"page:{key}",
                        "kind": "dashboard_page",
                        "name": item.label,
                        "where": where,
                        "what_it_is_for": item.about,
                        "they_are_on_it_now": bool(
                            current and (current == item.page or current in item.active_pages)
                        ),
                    }
                )

        hidden = self.settings.stage_exposure.hidden_pages
        header = {item.page for item in public_navigation(hidden_pages=hidden)}
        footer = {
            item.page
            for group in footer_navigation(hidden_pages=hidden)
            for item in group.items
        }
        for page in PUBLIC_PAGES:
            key = source_key_for_path(page.path)
            if key is None or page.page in hidden:
                # No card means the page is deliberately linked from nowhere; a page the
                # launch stage hides is not one to send anybody to.
                continue
            if page.page in header:
                where = "On the public website, in the menu at the top of every page."
            elif page.page in footer:
                where = "On the public website, in the links at the bottom of every page."
            else:
                where = "On the public website. It is not in a menu, so open it from here."
            row: dict[str, Any] = {
                "id": f"page:{key}",
                "kind": "public_page",
                "name": page.title,
                "where": where,
                "what_it_is_for": page.description,
            }
            if page.also_called:
                row["also_called"] = list(page.also_called)
            rows.append(row)
        return rows

    def _help_answers(self) -> list[dict[str, Any]]:
        """The Help Center's questions and answers, as the Help Center prints them."""

        return [
            {
                "id": f"help:{category['slug']}:{number}",
                "kind": "help_center_answer",
                "on_page": "Help Center",
                "topic": category["title"],
                "question": article["question"],
                "answer": article["answer"],
            }
            for category in public_help_categories(waitlist_mode=self.settings.waitlist_mode)
            for number, article in enumerate(category["articles"], start=1)
        ]

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
        pages = {
            name: getattr(view, name, None)
            for name in (
                "screened_market",
                "opportunities",
                "watch_plans",
                "passport",
                "watchlist",
                "connections",
                "research",
                "settings",
                "support",
                "subscription",
                "report",
            )
        }
        if (
            not view.page
            and not view.subject
            and not view.section
            and board is None
            and not any(pages.values())
        ):
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
        for name, described in pages.items():
            if described is None:
                continue
            seen[name] = {
                "heading": described.heading,
                "says": described.summary,
                "parts": list(described.points),
                "note": (
                    "The page's own words about itself, for 'what is this page' "
                    "questions. Never a source of facts about coins or standards."
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
                        # Every field on the card, filled or not, with the value the
                        # person themselves typed. Repeated as theirs, never as advice.
                        "fields_the_person_filled_in": [
                            {
                                "field": item.label,
                                "filled": item.filled,
                                "value": item.value,
                            }
                            for item in (card.inputs or [])
                        ],
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
                    "the one thing here you may talk about directly. The values in "
                    "fields_the_person_filled_in are the person's own doing — repeat "
                    "them as theirs, and never recommend a value for a field. Only "
                    "name a control that appears in the list above, and spell it the "
                    "same way. Only describe a key or a gesture that appears in how "
                    "this board is worked."
                ),
            }
        return seen


def _day(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%d %B %Y")


def _screens_for(rules: dict[str, Any] | None) -> list[str]:
    """What uses this standard speaks about, from its own stored rules."""

    uses = (rules or {}).get("use_cases") if isinstance(rules, dict) else None
    if not isinstance(uses, list):
        return []
    return [
        str(item.get("description") or item.get("label") or "").strip()
        for item in uses
        if isinstance(item, dict)
    ][:4]


def _monitor_state(strategy: Strategy) -> str:
    """Running, paused or draft — the person's own monitor, in plain words."""

    if strategy.status is StrategyStatus.PAUSED or strategy.paused_at is not None:
        return "paused"
    if strategy.status in (StrategyStatus.ACTIVE, StrategyStatus.FORWARD_TEST):
        return "running"
    return "draft"

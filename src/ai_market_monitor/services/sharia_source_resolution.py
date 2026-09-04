"""Turn proposed links into proved ones, layer by layer, and ask a person when they run out.

``sharia_source_catalog`` knows where a link can come from. ``sharia_source_discovery``
goes and looks. This module is what decides whether a link is real. Nothing else is
allowed to write ``verification_state`` ``verified`` onto an official source, because
"verified" is the word the Shariah research pipeline reads to choose what counts as
evidence — it selects *only* verified sources.

The order matters and is the whole idea:

1. Ask the highest layer that has not answered yet for candidates.
2. Fetch each candidate and prove it: it must be reachable, permitted by the site's own
   robots policy, readable as text, and — for a news page — recently updated. A page a
   plain request could not read is tried once more through a real browser, because a
   growing share of project blogs and forums only exist after JavaScript has run.
3. Score it. Proof adds; failure removes. A candidate that cannot be proved scores zero
   however confident the layer that proposed it was.
4. If a required category still has fewer proved links than the product wants, drop to
   the next layer and repeat.
5. When every free layer has run and a required category still has **nothing**, ask a
   model where the project publishes — the one paid layer, and the last.
6. When that is exhausted too, raise a task for a person, saying what was tried and what
   is stopping the machine going further.

Because proof can only ever *lower* a guess, the cheap guessing layer is safe to have.
A wrong ``https://example.com/blog`` fails step 2 and disappears; it can never talk its
way into being evidence. This is the same fail-closed rule the compiler follows: a
refused reading stays visible, an invented one silently measures the wrong thing.

**Wanted and required are two different numbers.** One working news page is a single
point of failure: the day it moves, the coin has no way to hear about the project at
all. So the layers keep looking until a category holds
:data:`~sharia_source_catalog.LINKS_WANTED_PER_CATEGORY` links, and a person is only
asked when it holds fewer than :data:`~sharia_source_catalog.LINKS_REQUIRED_PER_CATEGORY`.
Without that separation the review queue fills with "this coin has two news pages
instead of three", which nobody would ever work through.

**Required and tracked are two different lists**, and that is the same idea one level up.
:data:`~sharia_source_catalog.REQUIRED_CATEGORIES` is news alone: it decides whether a
person is asked and whether the layers keep hunting.
:data:`~sharia_source_catalog.TRACKED_CATEGORIES` adds the community page: it decides
what is counted and capped. So a forum found on the way is kept and shown, and a project
that runs no forum at all is never reported as having a gap — which it was, for every
such coin, until 1 September 2026.

**Working is not the same as useful.** A newsroom can answer HTTP 200 for years after
the last post was written. ``sharia_source_activity`` scores how alive and how relevant
a fetched page is, and a source below that floor stops counting as coverage — so the
layers go looking for company for it — while staying registered and visible. Nothing is
deleted for being quiet.

Two things this module deliberately does **not** do. It never writes, infers or implies
a Shariah status — a confidence or activity number here ranks links and nothing else.
And it never decides a case: when it gives up it opens a review case for a human, which
is a request for attention, not an answer.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import CanonicalAsset, OfficialSource, ReviewCase
from ai_market_monitor.db.models.enums import ReviewCaseType
from ai_market_monitor.services.coinmarketcap import (
    CoinLinks,
    CoinMarketCapClient,
    CoinMarketCapError,
)
from ai_market_monitor.services.sharia_page_render import (
    RETRYABLE_WITH_BROWSER,
    BrowserPageRenderer,
)

# One owner for turning a fetched body into text. Re-implementing it here is how the
# two-parsers-that-disagree problem starts: this module would decide a page was
# unreadable while the research pipeline read it fine, or the reverse.
from ai_market_monitor.services.sharia_research import (
    OfficialEvidenceFetcher,
    ShariaResearchError,
    extract_dates,
    extract_document,
)
from ai_market_monitor.services.sharia_source_activity import (
    SourceActivity,
    measure,
    newest_published_at,
)
from ai_market_monitor.services.sharia_source_ai_discovery import AISourceDiscovery
from ai_market_monitor.services.sharia_source_catalog import (
    CANDIDATE,
    CATEGORY_PRIORITY,
    CONFIDENCE_FLOOR,
    CURATED_CONFIDENT,
    LAYER_CONFIDENCE,
    LAYER_ORDER,
    LINKS_REQUIRED_PER_CATEGORY,
    NEWS,
    NEWS_MAXIMUM_AGE_DAYS,
    NOT_PERMITTED,
    PAID_LAYERS,
    PROOF_BONUS,
    TRACKED_CATEGORIES,
    UNREACHABLE,
    VERIFIED,
    WEBSITE,
    DiscoveryLayer,
    SearchResult,
    SourceCandidate,
    candidates_for,
    categories_below,
    category_label,
    is_aggregator_url,
    is_official_url,
    normalized_url,
)
from ai_market_monitor.services.sharia_source_discovery import WebSourceDiscovery

logger = logging.getLogger(__name__)


async def pending_asset_ids(session: AsyncSession) -> set[UUID]:
    """The coins the System Brain is currently asking a person about, under "Pages not found".

    One owner for the question "which coins are waiting?", because three callers ask it —
    the operator's script, the on-demand button, and the reporting that says how many
    tasks a run closed. Recomputing "pending" separately in each is how two of them would
    quietly come to mean slightly different sets, and then a button that claims to re-check
    "the coins in this list" re-checks a different list.

    Read from the **open review cases**, which is the same row the page draws, so the
    coins re-checked are exactly the coins the reviewer can see.
    """

    rows = await session.scalars(
        select(ReviewCase.canonical_asset_id).where(
            ReviewCase.case_type == ReviewCaseType.OFFICIAL_SOURCE_GAP,
            ReviewCase.done_at.is_(None),
            ReviewCase.canonical_asset_id.is_not(None),
        )
    )
    return {row for row in rows.all() if row is not None}


def _provider_link_fields(record: CoinLinks) -> dict[str, tuple[str, ...]]:
    """Reshape a provider record into the field names the catalog understands.

    The provider's own vocabulary stops here. ``sharia_source_catalog`` never learns
    that CoinMarketCap calls a whitepaper ``technical_doc``.
    """

    return {
        "website": record.website,
        "whitepaper": record.whitepaper,
        "source_code": record.source_code,
        "announcement": record.announcement,
        "message_board": record.message_board,
        "reddit": record.reddit,
        "chat": record.chat,
        # Fetched, parsed, and then dropped here until 4 September 2026. A crypto
        # project's X account is the single most common place it announces anything,
        # ``classify_channel`` has always known what one looks like, and the provider
        # publishes it per coin — so this was a free, per-coin news channel being thrown
        # away one line before the catalog could see it. Coins then fell through to the
        # search layer and the paid model layer looking for it.
        "twitter": record.twitter,
    }

__all__ = [
    "CANDIDATE",
    "NOT_PERMITTED",
    "UNREACHABLE",
    "VERIFIED",
    "AssetSourceOutcome",
    "ResolutionSweep",
    "SourceProof",
    "SourceResolutionService",
    "newest_published_at",
    "pending_asset_ids",
    "score_candidate",
]

#: A page that renders less than this much text is not something a reviewer or a model
#: can read. Usually it means the content only exists after JavaScript runs, which the
#: evidence fetcher does not do — so the page is real but not a usable source.
MINIMUM_READABLE_CHARACTERS = 200

#: HTTP answers that mean "this address is gone", as opposed to "try again later". Only
#: these are allowed to withdraw a source that was previously trusted.
DEFINITIVE_FAILURE_STATUSES = frozenset({400, 401, 404, 405, 410, 451})

#: Error codes the fetcher raises that are settled answers rather than bad luck.
DEFINITIVE_FAILURE_CODES = frozenset({"robots_disallowed"})

#: Answers that mean "this page is real, and you are not allowed to read it". A site's
#: robots policy and a 403 are the two ways that is said. They are settled answers, so
#: they withdraw a source like a dead link does — but they are recorded under their own
#: name, because the two need different work from a person: a dead link needs
#: replacing, while a forbidden one is the right address that the product may never
#: quote. Calling both "unreachable" sent reviewers hunting for a replacement page that
#: already existed.
NOT_PERMITTED_STATUSES = frozenset({403})
NOT_PERMITTED_CODES = frozenset({"robots_disallowed"})

#: Codes that mean **nothing was learned about the address**. The fetch did not fail on
#: the page's content — it never got a look at the page at all, because a network hop, a
#: site's robots endpoint or our own breaker got in the way. They are the difference
#: between "this coin needs a person to find an address" and "this sweep had a bad
#: minute": the first is a task, the second is a retry, and reporting the second as the
#: first is what sent reviewers hunting for pages that already worked.
UNFINISHED_CHECK_CODES = frozenset(
    {
        "robots_not_asked",
        "robots_unavailable",
        "official_source_unavailable",
    }
)


def _capped(value: object, limit: int = 300) -> str:
    """Shorten anything for the diagnostic payload.

    A diagnostic must never become the failure. This truncates; it never raises.
    """

    return str(value)[:limit]


@dataclass(frozen=True, slots=True)
class SourceProof:
    """What actually happened when the link was fetched."""

    reachable: bool
    allowed: bool
    readable: bool
    fresh: bool
    status: int | None = None
    published_at: datetime | None = None
    error_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    #: How alive and how relevant the page is, when it could be read at all. Never a
    #: Shariah status; see ``sharia_source_activity``.
    activity: SourceActivity | None = None

    @property
    def usable(self) -> bool:
        return self.reachable and self.allowed and self.readable and self.fresh

    @property
    def definitively_dead(self) -> bool:
        """Whether this is a settled "gone", not a "try later".

        Only a settled answer may withdraw a source that a review already relies on. A
        timeout or a 502 says nothing about the address, and treating it as proof of
        death would empty the evidence of every asset the next time a CDN hiccuped.
        """

        if self.error_code in DEFINITIVE_FAILURE_CODES:
            return True
        if self.status in NOT_PERMITTED_STATUSES:
            return True
        return self.status in DEFINITIVE_FAILURE_STATUSES

    @property
    def forbidden(self) -> bool:
        """Whether the page exists and the product is not allowed to read it."""

        if self.error_code in NOT_PERMITTED_CODES:
            return True
        return self.status in NOT_PERMITTED_STATUSES

    @property
    def activity_score(self) -> float:
        return self.activity.score if self.activity is not None else 0.0


def score_candidate(base_confidence: float, proof: SourceProof) -> float:
    """What a link is worth once it has been tried.

    One owner for the arithmetic, so the resolver and its tests cannot disagree about
    what "good enough" means.
    """

    if not (proof.reachable and proof.allowed and proof.readable):
        return 0.0
    if not proof.fresh:
        # Alive and readable, but it has stopped saying anything new. Half credit puts
        # every layer below the floor, which sends the asset on to the next layer and
        # finally to a person, without pretending the page does not exist.
        return round(base_confidence * 0.5, 4)
    return round(min(1.0, base_confidence + PROOF_BONUS), 4)


@dataclass(slots=True)
class AssetSourceOutcome:
    """What the resolver managed to settle for one asset."""

    asset_id: UUID
    symbol: str
    proved: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    #: How many of ``rejected`` were never actually checked — see
    #: :data:`UNFINISHED_CHECK_CODES`. When it equals ``len(rejected)`` this asset has not
    #: been judged at all yet, and asking a person to find an address would be wrong.
    unfinished: int = 0
    withdrawn: list[str] = field(default_factory=list)
    #: Categories with no working link at all. These raise a task for a person.
    missing: tuple[str, ...] = ()
    #: Categories with fewer working links than the product wants. Not a task — the
    #: machine simply keeps looking on the next sweep.
    short: tuple[str, ...] = ()
    #: How many working links each required category ended up with.
    coverage: dict[str, int] = field(default_factory=dict)
    #: Links that answer but have stopped saying anything worth reading.
    quiet: list[str] = field(default_factory=list)
    escalated: bool = False
    case_reference: str | None = None

    @property
    def complete(self) -> bool:
        return not self.missing and not self.withdrawn


@dataclass(frozen=True, slots=True)
class Coverage:
    """How many working links one asset holds, and how many of them still say things."""

    counts: Counter[str]
    lively: Counter[str]
    quiet: list[str]


@dataclass(slots=True)
class ResolutionSweep:
    """What a whole run did."""

    assets: list[AssetSourceOutcome] = field(default_factory=list)

    @property
    def escalated(self) -> int:
        return sum(item.escalated for item in self.assets)

    @property
    def proved(self) -> int:
        return sum(len(item.proved) for item in self.assets)

    @property
    def quiet(self) -> int:
        return sum(len(item.quiet) for item in self.assets)

    def message(self) -> str:
        if not self.assets:
            return "No asset needed its official links looked at."
        done = sum(item.complete for item in self.assets)
        return (
            f"{len(self.assets)} asset(s) checked. {self.proved} link(s) proved. "
            f"{done} now have a working official news page. "
            f"{self.escalated} sent to a person."
        )


class SourceResolutionService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        fetcher: OfficialEvidenceFetcher | None = None,
        discovery: WebSourceDiscovery | None = None,
        renderer: BrowserPageRenderer | None = None,
        ai_discovery: AISourceDiscovery | None = None,
        coinmarketcap: CoinMarketCapClient | None = None,
        now: datetime | None = None,
        force_recheck: bool = False,
    ) -> None:
        self.session = session
        self.settings = settings
        self.fetcher = fetcher or OfficialEvidenceFetcher(settings)
        #: The second way of reading a page, for the ones a plain request cannot read.
        #: One browser for the whole sweep, started only if a page actually needs it.
        #:
        #: Built **before** the discovery below, and handed to it, because the discovery
        #: needs a browser too — a project homepage whose navigation is drawn by
        #: JavaScript shows no links at all to a plain fetch. Two renderers would mean two
        #: Chromiums, two page budgets, and only one of them ever shut down; on a 3.9 GB
        #: server the second one is the largest thing running.
        self.renderer = renderer or BrowserPageRenderer(settings)
        self.discovery = discovery or WebSourceDiscovery(
            settings, fetcher=self.fetcher, renderer=self.renderer
        )
        #: The last way of finding one, for a coin every free layer gave up on.
        self.ai_discovery = ai_discovery or AISourceDiscovery(settings)
        #: A market-data provider's record of where each project publishes. It runs
        #: second, right after the curated links, because it usually answers every
        #: category outright — which is what stops the searching, the guessed ``/blog``
        #: and the paid model question from ever being needed for that coin.
        self.coinmarketcap = coinmarketcap or CoinMarketCapClient(settings)
        #: Provider records already fetched in this sweep, keyed by symbol. One call
        #: carries a hundred coins, so this is the difference between a few credits for
        #: a whole sweep and one credit per coin.
        self._provider_cache: dict[str, dict[str, tuple[str, ...]]] = {}
        self._now = now
        #: Ignore the recheck calendar and fetch everything. The scheduled sweep never
        #: does this — it exists for the operator's full re-check, which is the only
        #: caller that means "look at every link the product holds, right now".
        self.force_recheck = force_recheck

    def _clock(self) -> datetime:
        return self._now or datetime.now(UTC)

    @property
    def wanted_per_category(self) -> int:
        return self.settings.sharia_source_links_per_category

    @property
    def activity_floor(self) -> float:
        return self.settings.sharia_source_activity_floor

    async def resolve_pending(
        self, *, limit: int | None = None, deep: bool = False
    ) -> ResolutionSweep:
        """Work through assets whose official links have not been settled.

        Verified identities only. An asset whose identity a reviewer has not approved
        has no address worth trusting to derive anything from.

        **The order is the whole design.** One sweep may only touch
        ``sharia_source_resolution_batch_size`` assets, because each one costs a handful
        of fetches against somebody else's site. That is only safe if the next sweep
        carries on somewhere new, so the queue is ordered by how long ago each asset was
        last looked at: never-checked first, then longest-ago.

        Ordering by ``created_at`` instead — as this did until 30 August 2026 — is what a
        cursor looks like when it has none. Every sweep re-read the same oldest 25 assets
        for ever and no asset past position 25 was checked once, so a scan could run all
        month and the review queue never moved. The symptom is the giveaway: the run
        reports success, and the number of open cases is identical afterwards.

        Rechecking is served by the same order rather than by a filter. A settled asset
        sinks to the back and resurfaces only when its links are the oldest thing on the
        list, which is what ``sharia_source_recheck_days`` is measured against; filtering
        settled assets out would mean a link that died was never noticed again.
        """

        sweep = ResolutionSweep()
        if not self.settings.sharia_source_resolution_enabled:
            return sweep
        batch = limit or self.settings.sharia_source_resolution_batch_size
        last_looked_at = (
            select(func.max(OfficialSource.last_checked_at))
            .where(OfficialSource.canonical_asset_id == CanonicalAsset.id)
            .correlate(CanonicalAsset)
            .scalar_subquery()
        )
        # NULL means "no link of this asset has ever been fetched", which has to sort
        # first and cannot be expressed by comparing the column: NULL orders differently
        # per database. A CASE says it the same way everywhere, PostgreSQL and the
        # SQLite the tests run on alike.
        never_looked = case((last_looked_at.is_(None), 0), else_=1)
        assets = list(
            (
                await self.session.scalars(
                    select(CanonicalAsset)
                    .where(CanonicalAsset.mapping_state == "verified")
                    .order_by(
                        never_looked.asc(),
                        last_looked_at.asc(),
                        CanonicalAsset.created_at.asc(),
                    )
                    .limit(batch)
                )
            ).all()
        )
        # One provider call for the whole batch instead of one per coin. See
        # ``preload_provider_links``: the endpoint carries up to a hundred symbols, so a
        # 100-coin sweep costs about one credit here and about a hundred without it.
        await self.preload_provider_links([asset.symbol for asset in assets])
        try:
            for asset in assets:
                sweep.assets.append(await self.resolve_asset(asset, deep=deep))
        finally:
            # One browser for the whole sweep, and it has to be shut down whatever
            # happened — a Chromium left running is the largest thing on a 3.9 GB server.
            await self.renderer.aclose()
        return sweep

    async def resolve_open_cases(
        self,
        *,
        limit: int | None = None,
        only: Collection[UUID] | None = None,
    ) -> ResolutionSweep:
        """Re-check the coins the System Brain is currently asking a person about.

        This is the **on-demand** sweep, the one a reviewer starts by pressing a button,
        as opposed to :meth:`resolve_pending`, which the worker runs on its own calendar.
        The two differ in three ways and each one matters:

        * **Which coins.** The scheduled sweep walks the whole verified universe oldest
          first, a batch at a time, so it can be weeks before it returns to any one coin.
          This one takes the coins with an open "Pages not found" task — the rows on the
          page the reviewer is looking at. ``only`` narrows that to the ones they ticked;
          it can never widen it, so a stale or foreign id fetches nothing rather than
          pulling in a coin nobody asked about.
        * **The calendar is ignored.** ``force_recheck`` fetches every address again even
          if it was checked an hour ago. Without it a button pressed after a fix would
          re-read the stored answer and change nothing, which is indistinguishable from
          the button being broken.
        * **Every layer runs.** ``deep`` lets it read the CoinMarketCap record again and
          re-read the project's own homepage — header, footer and body — which is where a
          news page that the provider does not list is actually found.

        Each coin it settles closes its own task; each one it cannot settle has its task
        rewritten with what was tried **this time** and why each address failed. That
        rewrite is the point as much as the fetching is: a task whose wording came from
        older code keeps showing an explanation that is no longer true, and a person
        reading "the site would not say what it allows" about an address that simply does
        not exist goes looking for a fault in the product instead of a better address.
        """

        sweep = ResolutionSweep()
        if not self.settings.sharia_source_resolution_enabled:
            return sweep
        pending = await pending_asset_ids(self.session)
        if only is not None:
            # Narrowed, never widened. Intersecting keeps the one rule this whole method
            # rests on — a coin is only ever re-read because it has an open task — so a
            # caller cannot reach a coin the reviewer is not being asked about.
            pending &= set(only)
        if not pending:
            return sweep
        query = (
            select(CanonicalAsset)
            .where(
                CanonicalAsset.mapping_state == "verified",
                CanonicalAsset.id.in_(pending),
            )
            .order_by(CanonicalAsset.symbol.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        assets = list((await self.session.scalars(query)).all())
        # The provider record for every waiting coin, in one call. This is the layer the
        # whole button exists to run again, and asking for 157 coins one at a time spends
        # 157 credits and 157 round trips before the first page is even fetched.
        await self.preload_provider_links([asset.symbol for asset in assets])
        try:
            for asset in assets:
                sweep.assets.append(await self.resolve_asset(asset, deep=True))
        finally:
            # One browser for the whole sweep, and it has to be shut down whatever
            # happened — a Chromium left running is the largest thing on a 3.9 GB server.
            await self.renderer.aclose()
        return sweep

    async def add_reviewed_source(
        self,
        asset: CanonicalAsset,
        *,
        category: str,
        url: str,
        reviewer_id: UUID,
    ) -> OfficialSource:
        """Register an address a person found, and prove it like any other.

        A reviewer typing a URL is treated as the curated layer — the most trusted
        starting point there is — and it still has to survive its proof. Somebody can
        paste a page that has moved, or a news page that stopped publishing in 2023, and
        the review that reads it months later has no way to tell a checked address from
        an unchecked one. So this records who supplied it and then fetches it.
        """

        normalized = normalized_url(url)
        row = await self.session.scalar(
            select(OfficialSource).where(
                OfficialSource.canonical_asset_id == asset.id,
                OfficialSource.normalized_url == normalized,
            )
        )
        if row is None:
            row = OfficialSource(
                canonical_asset_id=asset.id,
                category=category,
                title=f"{asset.name} {category_label(category)}"[:300],
                source_url=url,
                normalized_url=normalized,
                priority=CATEGORY_PRIORITY.get(category, 100),
                verification_state=CANDIDATE,
                is_active=True,
                confidence=0.0,
                discovery_layer=str(DiscoveryLayer.CURATED),
            )
            self.session.add(row)
        else:
            # A link somebody had given up on is being offered again. Re-open it rather
            # than leaving it switched off and silently ignoring the reviewer.
            row.category = category
            row.source_url = url
            row.is_active = True
        row.verified_by_user_id = reviewer_id
        await self.session.flush()
        proof = await self._prove(row, category)
        self._record(
            row,
            proof,
            score_candidate(CURATED_CONFIDENT, proof),
            layer=str(DiscoveryLayer.CURATED),
        )
        return row

    async def resolve_asset(
        self, asset: CanonicalAsset, *, deep: bool = False
    ) -> AssetSourceOutcome:
        """Settle one asset's official links.

        ``deep`` means "go and look properly": read the project's own site for its
        channels and ask a search engine, even when the coin already has a working link
        in every category. The scheduled sweep does not, because both cost somebody
        else's server a request; the operator's re-check does.
        """

        outcome = AssetSourceOutcome(asset_id=asset.id, symbol=asset.symbol)
        rows = await self._existing_rows(asset.id)
        # Addresses this run has already fetched and written a verdict for. Without it
        # a forced re-check proves the same row twice — once because the operator asked
        # for every link, and again when a layer offers the same address.
        settled: set[str] = set()
        # Read before anything is proved: the homepage is re-read on the same calendar
        # as every other link, and proving it below would otherwise make it look freshly
        # checked and stop the channel layer from ever running.
        look_due = self._harvest_due(asset, rows)
        await self._ensure_website_row(asset, rows, settled)
        await self._recheck_existing(asset, rows, outcome, settled)
        coverage = self._coverage(rows)
        for layer in LAYER_ORDER:
            # The curated layer always runs. Its links were written down by a person and
            # the product wants more than one good link per coin, so it is not skipped
            # just because a category is already answered. The other layers stop once
            # every category holds as many *lively* links as the product wants.
            if layer is not DiscoveryLayer.CURATED and not categories_below(
                coverage.lively, self.wanted_per_category
            ):
                break
            if layer in {DiscoveryLayer.SOCIAL, DiscoveryLayer.SEARCH} and not self._may_look(
                coverage.counts, deep=deep, look_due=look_due
            ):
                continue
            if layer in PAID_LAYERS and not self._may_ask_a_model(coverage.counts):
                # The one gate that matters for the paid layer: every free way of finding
                # a page has already run for this coin and a required category still has
                # nothing at all. "Fewer than we would like" is not enough — that is what
                # the free layers keep working on.
                continue
            await self._run_layer(
                layer,
                asset=asset,
                rows=rows,
                lively=coverage.lively,
                outcome=outcome,
                settled=settled,
            )
            # Read the coverage again rather than adding to it as we go. See _coverage.
            coverage = self._coverage(rows)
        outcome.coverage = {
            category: coverage.counts.get(category, 0) for category in TRACKED_CATEGORIES
        }
        outcome.quiet = coverage.quiet
        outcome.short = categories_below(coverage.lively, self.wanted_per_category)
        outcome.missing = categories_below(coverage.counts, LINKS_REQUIRED_PER_CATEGORY)
        if outcome.missing or outcome.withdrawn:
            await self._escalate(asset, outcome)
        else:
            await self._close_gap_case(asset)
        return outcome

    def _harvest_due(
        self, asset: CanonicalAsset, rows: dict[str, OfficialSource]
    ) -> bool:
        """Whether the project's own homepage is due to be read again."""

        site = (asset.official_website or "").strip()
        if not site or not is_official_url(site):
            return False
        row = rows.get(normalized_url(site))
        return row is None or self._due_for_recheck(row)

    def _may_look(
        self,
        counts: Counter[str],
        *,
        deep: bool,
        look_due: bool,
    ) -> bool:
        """Whether it is worth spending requests on somebody else's server today.

        Reading the project's homepage for its channel links, and asking a search
        engine, both cost a request that the cheap layers do not. Three things earn it:
        the operator asked for a full look, the coin has a category with no working link
        at all, or the homepage has not been read since the last recheck window.
        """

        if deep or self.force_recheck:
            return True
        if categories_below(counts, LINKS_REQUIRED_PER_CATEGORY):
            return True
        return look_due

    def _may_ask_a_model(self, counts: Counter[str]) -> bool:
        """Whether this coin has earned the one paid question.

        Two conditions, both required, and neither is "we would like more links":

        * asking is switched on and possible at all;
        * a required category has **no working link whatsoever** after every free layer
          has run for this coin.

        The second is the important one. ``LINKS_REQUIRED_PER_CATEGORY`` is the number
        below which a person is asked for help; anything above it is the machine tidying
        up, and tidying up is not worth a model call per coin per sweep.
        """

        if not self.ai_discovery.configured:
            return False
        return bool(categories_below(counts, LINKS_REQUIRED_PER_CATEGORY))

    async def _existing_rows(self, asset_id: UUID) -> dict[str, OfficialSource]:
        rows = list(
            (
                await self.session.scalars(
                    select(OfficialSource).where(
                        OfficialSource.canonical_asset_id == asset_id
                    )
                )
            ).all()
        )
        return {row.normalized_url: row for row in rows}

    async def _ensure_website_row(
        self, asset: CanonicalAsset, rows: dict[str, OfficialSource], settled: set[str]
    ) -> None:
        """Make sure the approved official website is in the register, and prove it.

        Identity approval records it, but assets approved before that code existed have
        no website row at all — and the website is both a source in its own right and
        the page the channel layer reads. Registering it as an ordinary candidate means
        it is proved like everything else rather than trusted for having been typed.

        It is proved here rather than left for a layer, because no layer proposes a
        homepage: it is not one of the two categories the layers hunt for. A row nobody
        proposes and nobody proves would sit at "never checked" for ever.
        """

        site = (asset.official_website or "").strip()
        if not site or not is_official_url(site):
            return
        normalized = normalized_url(site)
        if normalized in rows:
            return
        row = OfficialSource(
            canonical_asset_id=asset.id,
            category=WEBSITE,
            title=f"{asset.name} {category_label(WEBSITE)}"[:300],
            source_url=site,
            normalized_url=normalized,
            priority=CATEGORY_PRIORITY.get(WEBSITE, 100),
            verification_state=CANDIDATE,
            is_active=True,
            confidence=0.0,
            discovery_layer=str(DiscoveryLayer.IDENTITY),
        )
        self.session.add(row)
        await self.session.flush()
        rows[normalized] = row
        settled.add(normalized)
        proof = await self._prove(row, WEBSITE)
        self._record(
            row,
            proof,
            score_candidate(LAYER_CONFIDENCE[DiscoveryLayer.IDENTITY], proof),
            layer=str(DiscoveryLayer.IDENTITY),
        )

    def _stale_by_calendar(self, row: OfficialSource) -> bool:
        """Whether what the row says about itself is too old to rely on.

        Kept apart from :meth:`_due_for_recheck` on purpose. "Fetch this again" and "do
        not believe this any more" are two different questions, and a forced re-check
        answers only the first. Folding them together made every row look untrustworthy
        the moment the operator asked for a full re-check, so a run that was supposed to
        confirm a coin's links reported that it had none.
        """

        if row.last_checked_at is None:
            return True
        checked = row.last_checked_at
        if checked.tzinfo is None:
            # SQLite hands back naive values; treating one as server-local would make
            # a freshly checked link look weeks old, or the reverse.
            checked = checked.replace(tzinfo=UTC)
        age = self._clock() - checked
        return age > timedelta(days=self.settings.sharia_source_recheck_days)

    def _due_for_recheck(self, row: OfficialSource) -> bool:
        return self.force_recheck or self._stale_by_calendar(row)

    def _counts_towards_coverage(self, row: OfficialSource) -> bool:
        """Whether a row is a working official source: verified, on, and proved lately."""

        if row.verification_state != VERIFIED or not row.is_active:
            return False
        if row.confidence < CONFIDENCE_FLOOR:
            return False
        return not self._stale_by_calendar(row)

    def _is_lively(self, row: OfficialSource) -> bool:
        """Whether a working source is also still saying something worth reading.

        Deliberately **not** part of :meth:`_counts_towards_coverage`. A quiet page is
        still a real official source: it is not withdrawn, not un-verified, and it still
        stops a human task being raised. All that changes is that the machine keeps
        looking for company for it, which is exactly what "the confidence is low, go and
        find more" has to mean. Letting activity un-verify a link instead would delete a
        coin's evidence because its forum happens not to print dates.
        """

        return self._stored_activity(row) >= self.activity_floor

    @staticmethod
    def _stored_activity(row: OfficialSource) -> float:
        """The activity score last written on a row, or full marks if it predates them.

        A row checked before activity scoring existed has no number. Treating that as
        zero would make every one of them stop counting at once and open a task for
        every coin the product has, which is a report about this code rather than about
        the links. They keep counting until their next check writes a real number.
        """

        detail = row.check_detail if isinstance(row.check_detail, dict) else {}
        activity = detail.get("activity")
        if not isinstance(activity, dict):
            return 1.0
        try:
            return float(activity.get("score", 1.0))
        except (TypeError, ValueError):
            return 1.0

    def _coverage(self, rows: dict[str, OfficialSource]) -> Coverage:
        """How much coverage this asset actually has, read off its rows.

        One owner for the question, and it is answered by *counting the rows* rather
        than by adding one every time a layer proves something. Two places keeping the
        same tally is how a link gets counted twice: the sweep counted a proved link
        once when it read the rows and again when a later layer offered the same address
        and found it already checked. A coin then looked better covered than it was, and
        the layers stopped looking early.

        ``counts`` decides whether a person is asked. ``lively`` decides whether the
        machine keeps looking. Keeping those two apart is what lets the product hunt for
        a third news page without telling a reviewer that anything is wrong.
        """

        counts: Counter[str] = Counter()
        lively: Counter[str] = Counter()
        quiet: list[str] = []
        for row in rows.values():
            # Counted over every **tracked** category, required or not. A community page
            # is still counted so a layer that offers twenty of them stops at the wanted
            # number; whether one is *missing* is a separate question, answered only by
            # `categories_below`, which reads the required list alone.
            if row.category not in TRACKED_CATEGORIES:
                continue
            if not self._counts_towards_coverage(row):
                continue
            counts[row.category] += 1
            if self._is_lively(row):
                lively[row.category] += 1
            else:
                quiet.append(f"{category_label(row.category)}: {row.source_url}")
        return Coverage(counts=counts, lively=lively, quiet=quiet)

    async def _recheck_existing(
        self,
        asset: CanonicalAsset,
        rows: dict[str, OfficialSource],
        outcome: AssetSourceOutcome,
        settled: set[str],
    ) -> None:
        """Prove the links the register already holds.

        Every source registered before the layered resolver existed was written straight
        to ``verified``. Some of them are dead. A dead link that still says ``verified``
        is worse than no link, because the research pipeline hands it to a reviewer as
        evidence. This is the only place that withdraws one, and it does so only on a
        settled answer — never on a timeout.

        On an ordinary sweep only rows that are currently trusted are touched. A row
        still sitting at ``candidate`` or ``unreachable`` was never evidence, so
        re-proving it here would report it as *withdrawn* every sweep — and an asset
        that looked like it had just lost a source would keep its human task open for
        ever. Those rows are retried by the layer walk instead, which is where they
        belong. A forced re-check proves them too, because the operator asked for every
        link; it still only reports a **withdrawal** for a row that was trusted before,
        which is what keeps that rule intact.
        """

        for row in rows.values():
            trusted = row.verification_state == VERIFIED
            if not self.force_recheck and not trusted:
                continue
            if not row.is_active or not self._due_for_recheck(row):
                continue
            settled.add(row.normalized_url)
            proof = await self._prove(row, row.category)
            if proof.usable:
                base = row.confidence or LAYER_CONFIDENCE.get(
                    _layer_of(row), CONFIDENCE_FLOOR
                )
                self._record(row, proof, score_candidate(base, proof), layer=row.discovery_layer)
                continue
            if proof.definitively_dead:
                self._record(row, proof, 0.0, layer=row.discovery_layer)
                if trusted:
                    row.is_active = False
                    outcome.withdrawn.append(
                        f"{category_label(row.category)}: {row.source_url}"
                    )
                continue
            if trusted:
                # Could not be checked. Say so, change nothing else.
                row.last_checked_at = self._clock()
                row.check_detail = {
                    "outcome": "not_checked",
                    "reason": _capped(proof.error_code or "unavailable"),
                }
            else:
                self._record(row, proof, 0.0, layer=row.discovery_layer)

    async def _candidates(
        self,
        layer: DiscoveryLayer,
        *,
        asset: CanonicalAsset,
        tried: tuple[str, ...] = (),
    ) -> tuple[SourceCandidate, ...]:
        """One layer's proposals, including the three that have to go and look."""

        channel_links: tuple[str, ...] = ()
        search_results: tuple[SearchResult, ...] = ()
        provider_links: dict[str, tuple[str, ...]] | None = None
        # Which site this layer measures "is that the same project?" against. Normally
        # the approved one; see ``_site_to_read`` for the coins that have none.
        site = (asset.official_website or "").strip()
        if layer is DiscoveryLayer.PROVIDER:
            provider_links = await self._provider_links(asset.symbol)
        elif layer is DiscoveryLayer.SOCIAL:
            site = await self._site_to_read(asset)
            channel_links = await self.discovery.channel_links(site)
        elif layer is DiscoveryLayer.SEARCH:
            search_results = await self.discovery.search(
                asset_name=asset.name, symbol=asset.symbol
            )
        elif layer is DiscoveryLayer.ASSISTED:
            search_results = await self.ai_discovery.suggest(
                asset_name=asset.name,
                symbol=asset.symbol,
                official_website=asset.official_website,
                already_tried=tried,
            )
        return candidates_for(
            layer,
            symbol=asset.symbol,
            asset_name=asset.name,
            official_website=site or asset.official_website,
            official_documentation=asset.official_documentation,
            channel_links=channel_links,
            search_results=search_results,
            provider_links=provider_links,
        )

    async def _site_to_read(self, asset: CanonicalAsset) -> str:
        """The homepage whose own links are worth harvesting.

        The approved website when there is one — a person checked it, and nothing beats
        that. When there is not, the market-data provider's ``website`` field, which is
        that provider's core record and the one address it is most reliable about.

        Without this fallback a coin with no approved website skipped the harvest
        completely, and its review case said so: "this coin has no approved official
        website, so the pages a project links to cannot be tried". That sentence was
        true about our own records and false about the world — the address was sitting
        in the provider record the layer above had *already fetched*. A project's
        homepage footer is where its Telegram, its X account and its blog are listed, so
        skipping it threw away the richest free source of channels there is, and then
        the coin fell through to the paid model layer for want of a link the provider
        had handed over a moment earlier.

        Costs no extra provider credit: the PROVIDER layer runs first in ``LAYER_ORDER``
        and leaves the record in ``_provider_cache``.
        """

        approved = (asset.official_website or "").strip()
        if approved:
            return approved
        await self._provider_links(asset.symbol)
        return self._provider_website(asset.symbol)

    def _provider_website(self, symbol: str) -> str:
        """The provider's homepage for this coin, from the cache only. Never fetches.

        Split from :meth:`_site_to_read` so the reporting side — which is not async and
        must never make a provider call just to word a sentence — can ask the same
        question and get the same answer. One owner, so the layer that reads a site and
        the case that says whether a site was available can never disagree.
        """

        for url in (self._provider_cache.get(symbol.strip().upper()) or {}).get(
            "website"
        ) or ():
            candidate = (url or "").strip()
            # Same test every other provider address gets. An aggregator's own profile
            # page is not a project homepage, whatever field it arrived in.
            if candidate and is_official_url(candidate) and not is_aggregator_url(candidate):
                return candidate
        return ""

    async def _provider_links(self, symbol: str) -> dict[str, tuple[str, ...]]:
        """What the market-data provider holds for this coin.

        Answers from a per-sweep cache, because one provider call carries up to a
        hundred coins: a sweep that pre-loads its symbols spends a few credits for the
        whole run instead of one per coin. A coin the cache does not hold is fetched on
        its own, which is correct but expensive, so ``preload_provider_links`` exists
        for callers that know their list up front.

        Never raises. The provider being down, switched off, or not carrying the
        endpoint all mean the same thing here: this layer offers nothing and the layers
        below it run exactly as they did before this integration existed.
        """

        key = symbol.strip().upper()
        if key in self._provider_cache:
            return self._provider_cache[key]
        if self.coinmarketcap is None or not self.coinmarketcap.enabled:
            return {}
        try:
            found = await self.coinmarketcap.coin_links([key])
        except CoinMarketCapError as exc:
            logger.info("provider_links_unavailable", extra={"symbol": key, "reason": exc.code})
            self._provider_cache[key] = {}
            return {}
        for held_symbol, record in found.items():
            self._provider_cache[held_symbol] = _provider_link_fields(record)
        self._provider_cache.setdefault(key, {})
        return self._provider_cache[key]

    async def preload_provider_links(self, symbols: Sequence[str]) -> int:
        """Fetch many coins' provider records in one call, before a sweep starts.

        Returns how many coins the provider answered for. A sweep that calls this pays
        single-figure credits for hundreds of coins; one that does not still works, one
        credit at a time.
        """

        if self.coinmarketcap is None or not self.coinmarketcap.enabled:
            return 0
        wanted = [
            s.strip().upper()
            for s in symbols
            if s and s.strip() and s.strip().upper() not in self._provider_cache
        ]
        if not wanted:
            return 0
        try:
            found = await self.coinmarketcap.coin_links(wanted)
        except CoinMarketCapError as exc:
            logger.info("provider_preload_unavailable", extra={"reason": exc.code})
            return 0
        for symbol, record in found.items():
            self._provider_cache[symbol] = _provider_link_fields(record)
        for symbol in wanted:
            self._provider_cache.setdefault(symbol, {})
        return len(found)

    async def _run_layer(
        self,
        layer: DiscoveryLayer,
        *,
        asset: CanonicalAsset,
        rows: dict[str, OfficialSource],
        lively: Counter[str],
        outcome: AssetSourceOutcome,
        settled: set[str],
    ) -> None:
        # A person writing links down is worth following to the end. Every other layer
        # stops offering a category once it holds as many links as the product wants.
        # ``found`` is what this layer has added so far, counted only so the layer knows
        # when to stop; the asset's real coverage is re-read from its rows afterwards.
        stop_when_full = layer is not DiscoveryLayer.CURATED
        found: Counter[str] = Counter()
        # The addresses the free layers have just proved do not work, so the paid layer
        # does not spend its one answer offering them back.
        candidates = await self._candidates(
            layer, asset=asset, tried=tuple(outcome.rejected[-12:])
        )
        for candidate in candidates:
            held = lively.get(candidate.category, 0) + found.get(candidate.category, 0)
            if stop_when_full and held >= self.wanted_per_category:
                continue
            if candidate.normalized_url in settled:
                continue  # already fetched and judged earlier in this same run
            row = rows.get(candidate.normalized_url)
            if row is not None and not self._due_for_recheck(row):
                continue
            row = await self._register(asset, candidate, row)
            rows[candidate.normalized_url] = row
            settled.add(candidate.normalized_url)
            proof = await self._prove(row, candidate.category)
            confidence = score_candidate(candidate.confidence, proof)
            self._record(row, proof, confidence, layer=str(layer))
            label = f"{category_label(candidate.category)}: {candidate.url}"
            if row.verification_state != VERIFIED:
                # Say *why* it did not work. "The system tried these and none of them
                # proved usable" told a reviewer nothing they could act on: a page that
                # does not exist needs a different address, a page that forbids us needs
                # none, and a page the browser could not read is the machine's problem
                # and not theirs.
                outcome.rejected.append(f"{label} — {_why_not_usable(proof)}")
                if (proof.error_code or "") in UNFINISHED_CHECK_CODES:
                    outcome.unfinished += 1
                continue
            outcome.proved.append(label)
            if proof.activity_score >= self.activity_floor:
                found[candidate.category] += 1

    async def _register(
        self,
        asset: CanonicalAsset,
        candidate: SourceCandidate,
        existing: OfficialSource | None,
    ) -> OfficialSource:
        if existing is not None:
            if not existing.is_active:
                # A layer is offering this address again. Switch it back on so the proof
                # below decides, rather than leaving a working page permanently off
                # because it failed once.
                existing.is_active = True
            return existing
        row = OfficialSource(
            canonical_asset_id=asset.id,
            category=candidate.category,
            title=candidate.title,
            source_url=candidate.url,
            normalized_url=candidate.normalized_url,
            priority=CATEGORY_PRIORITY.get(candidate.category, 100),
            verification_state=CANDIDATE,
            is_active=True,
            confidence=0.0,
            discovery_layer=str(candidate.layer),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    def _record(
        self,
        row: OfficialSource,
        proof: SourceProof,
        confidence: float,
        *,
        layer: str | None,
    ) -> None:
        row.confidence = confidence
        row.last_checked_at = self._clock()
        row.content_published_at = proof.published_at
        if layer:
            row.discovery_layer = layer
        detail: dict[str, Any] = {
            "reachable": proof.reachable,
            "robots_allowed": proof.allowed,
            "readable": proof.readable,
            "recent": proof.fresh,
            "http_status": proof.status,
            "error": _capped(proof.error_code) if proof.error_code else None,
            **{key: _capped(value) for key, value in proof.detail.items()},
        }
        if proof.activity is not None:
            detail["activity"] = proof.activity.as_detail()
            detail["activity_note"] = _capped(proof.activity.sentence(), 400)
        row.check_detail = detail
        if proof.usable and confidence >= CONFIDENCE_FLOOR:
            row.verification_state = VERIFIED
            row.verified_at = self._clock()
        elif proof.forbidden:
            row.verification_state = NOT_PERMITTED
        elif proof.definitively_dead:
            row.verification_state = UNREACHABLE
        else:
            row.verification_state = CANDIDATE

    async def _prove(self, row: OfficialSource, category: str) -> SourceProof:
        proof = await self._prove_once(row, category)
        if proof.usable or not self._browser_could_help(proof):
            return proof
        # The plain request could not read this page. A growing share of project blogs and
        # forums only exist after JavaScript has run, so the same address is asked for a
        # second time through a real browser. Robots has already decided — a disallowed
        # address never reaches here — and whatever comes back is proved by exactly the
        # same rules. Rendering makes a page legible; it never makes one trusted.
        rendered = await self.renderer.render(row.source_url)
        if not rendered.ok:
            if rendered.unavailable_reason:
                return replace(
                    proof,
                    detail={**proof.detail, "browser": _capped(rendered.unavailable_reason)},
                )
            return proof
        second = self._judge_document(
            rendered.html,
            row=row,
            category=category,
            content_type="text/html",
            status=proof.status,
        )
        return replace(
            second,
            detail={**second.detail, "read_with": rendered.engine or "browser"},
        )

    @staticmethod
    def _browser_could_help(proof: SourceProof) -> bool:
        """Whether a browser might read what a plain request could not.

        A settled "no" — robots said no, the address is gone, the site forbids us — is an
        answer, and asking a second time with a browser is only a second way of hearing
        it. Those are excluded here rather than at the call site so there is one place
        that decides what is worth a retry.
        """

        if proof.forbidden or proof.definitively_dead:
            return False
        if proof.reachable and proof.readable:
            # Read fine; it is only stale. A browser cannot make a page publish.
            return False
        return (proof.error_code or "too_short") in RETRYABLE_WITH_BROWSER

    async def _prove_once(self, row: OfficialSource, category: str) -> SourceProof:
        try:
            body, headers, status = await self.fetcher.fetch(row)
        except ShariaResearchError as exc:
            return SourceProof(
                reachable=False,
                allowed=exc.code != "robots_disallowed",
                readable=False,
                fresh=False,
                status=_status_from_error(str(exc)),
                error_code=exc.code,
                detail={"message": str(exc)},
            )
        return self._judge_document(
            body,
            row=row,
            category=category,
            content_type=headers.get("content-type"),
            status=status,
        )

    def _judge_document(
        self,
        body: str | bytes,
        *,
        row: OfficialSource,
        category: str,
        content_type: str | None,
        status: int | None,
    ) -> SourceProof:
        """Read one fetched document and say what it proves.

        One owner, used by both the plain fetch and the browser render, so a page cannot
        be judged readable by one route and unreadable by the other.
        """

        try:
            _title, headings, text = extract_document(
                body, row.source_url, content_type=content_type
            )
        except Exception as exc:  # noqa: BLE001 - an unreadable page is an answer
            return SourceProof(
                reachable=True,
                allowed=True,
                readable=False,
                fresh=False,
                status=status,
                error_code="unreadable_document",
                detail={"message": _capped(exc)},
            )
        readable = len(text.strip()) >= MINIMUM_READABLE_CHARACTERS
        now = self._clock()
        # The dates a page states in markup are added to the words it prints, and both
        # are read by the one date parser. Without the markup half, a Telegram channel
        # or any blog that renders "20 August" with the year only in a ``<time>`` tag
        # scored as having published nothing since it began.
        page = "\n".join([*headings, text, *extract_dates(body, row.source_url)])
        activity = measure(page, category=category, now=now)
        published = activity.newest_published_at
        if category == NEWS and published is not None:
            fresh = now - published <= timedelta(days=NEWS_MAXIMUM_AGE_DAYS)
        else:
            # Two cases, and neither is a failure.
            #
            # A forum or a subreddit has no publication date of its own, and demanding
            # one would reject every community page there is.
            #
            # A news page that shows **no date this reader could find** is the case that
            # made this rule wrong until 1 September 2026. "I could not find a date" was
            # being treated as "the page is stale": `fresh` went false, `score_candidate`
            # halved the confidence, every layer fell under `CONFIDENCE_FLOOR`, and the
            # page was refused. A real, live project blog whose dates are drawn by
            # JavaScript, or written as "3 days ago", or printed as an image, was thrown
            # away exactly like a newsroom that stopped publishing in 2019 — and the coin
            # was then reported to a person as having no news page at all.
            #
            # Not knowing is not evidence of staleness. The page is accepted, and the
            # thing that actually knows how alive it is — the activity score, which gets
            # nothing for recency when there is no date — puts it below the activity
            # floor. That is the designed answer: the link counts as coverage so nobody
            # is asked to find a page that already exists, and the layers keep looking
            # for livelier company for it. Only a date we *did* read, and that really is
            # too old, still refuses a page.
            fresh = True
        return SourceProof(
            reachable=True,
            allowed=True,
            readable=readable,
            fresh=fresh,
            status=status,
            published_at=published,
            # "too_short" is a real, separate answer from "we could not parse it": it is
            # the exact shape a JavaScript-only page has, and it is what tells the browser
            # fallback this address is worth a second look.
            error_code=None if readable else "too_short",
            detail={"characters": len(text.strip())},
            activity=activity,
        )

    async def _escalate(self, asset: CanonicalAsset, outcome: AssetSourceOutcome) -> None:
        """Ask a person. The machine has run out of layers.

        This opens a review case, which is what the System Brain shows under "Needs
        attention". It is a request for somebody to find an address — it decides
        nothing and it publishes nothing.
        """

        key = f"official-source-gap:{asset.id}"
        existing = await self.session.scalar(
            select(ReviewCase).where(ReviewCase.idempotency_key == key)
        )
        wanted = [
            f"An official {category_label(item)} page for {asset.name} ({asset.symbol})."
            for item in outcome.missing
        ]
        wanted.extend(
            f"A working replacement for this link, which is gone: {item}"
            for item in outcome.withdrawn
        )
        reason = self._gap_reason(asset, outcome)
        if existing is not None:
            if existing.done_at is not None:
                # It was closed and the gap came back. Reopen rather than open a second.
                existing.done_at = None
                existing.state = "needs_evidence"
            existing.human_review_reason = reason
            existing.requested_evidence = wanted
            outcome.escalated = True
            outcome.case_reference = existing.case_reference
            return
        reference = f"SRC-{str(asset.id).replace('-', '')[:12].upper()}"
        case = ReviewCase(
            case_reference=reference,
            case_type=ReviewCaseType.OFFICIAL_SOURCE_GAP,
            state="needs_evidence",
            publication_state="unpublished",
            canonical_asset_id=asset.id,
            title=f"Find an official news page for {asset.name}",
            priority="normal",
            risk_severity="low",
            human_review_reason=reason,
            requested_evidence=wanted,
            idempotency_key=key,
        )
        self.session.add(case)
        await self.session.flush()
        outcome.escalated = True
        outcome.case_reference = reference

    def _gap_reason(self, asset: CanonicalAsset, outcome: AssetSourceOutcome) -> str:
        """Why this coin still has no working page, in words that name the real cause.

        The old sentence ended "Please add the correct address on the asset, or say there
        is not one." on **every** gap, including the ones the machine had simply not been
        allowed to look for yet — no web search key, no official website to read, no
        browser to render a page that needs one. A reviewer was being asked to do the
        machine's job, two hundred times, for a reason nobody had told them.

        This says four things instead, and only the ones that are true:

        * what is missing — the category, not "every official page";
        * **where the product looked**, by name;
        * what was already tried, and what it did;
        * what is stopping the machine from going further, when something is.

        Naming the places is new on 4 September 2026 and it is the half that was missing.
        Until the guessing layer was removed, every address in the "tried" list was one the
        product had invented from the coin's domain, so a reviewer read eight plausible
        URLs and had no way to know that nobody had ever published any of them. Now every
        address in that list is one somebody stated — so saying who stated it is what makes
        the list mean anything.
        """

        missing = ", ".join(category_label(item) for item in outcome.missing)
        parts: list[str] = []
        if missing:
            parts.append(
                f"{asset.name} ({asset.symbol}) still has no working {missing} page."
            )
            parts.append(
                "The system looks in two places: the CoinMarketCap record for this coin, "
                "and the links on the project's own website. Neither gave a page that "
                "works. It never guesses an address."
            )
        if outcome.withdrawn:
            parts.append(
                f"{len(outcome.withdrawn)} link(s) that used to work are gone: "
                + "; ".join(outcome.withdrawn[:5])
                + "."
            )
        # Whether a single one of these addresses was actually looked at. It changes the
        # verb, not just an extra sentence: "tried and none proved usable" is a claim
        # about the pages, and it must not be made about pages nobody opened.
        nothing_checked = bool(outcome.rejected) and outcome.unfinished == len(
            outcome.rejected
        )
        tried = outcome.rejected[:5]
        if tried:
            more = len(outcome.rejected) - len(tried)
            tail = f" and {more} more" if more > 0 else ""
            opening = (
                "The system could not reach these, so nothing is known about them yet: "
                if nothing_checked
                else "The system tried these and none of them proved usable: "
            )
            parts.append(opening + "; ".join(tried) + f"{tail}.")
        # When nothing was actually checked, the switched-off-layer list below is worse
        # than unhelpful: it tells a reviewer to go and buy a search key for a coin whose
        # pages were never asked a single question.
        if nothing_checked:
            parts.append(
                "The system tries again on the next sweep. Nothing needs to be done "
                "here unless it keeps saying this."
            )
            return " ".join(parts)
        blocked = self._what_stops_looking(asset)
        if blocked:
            parts.append(
                "Separately, some ways of looking are switched off, which may or may not "
                "be why this one failed: " + " ".join(blocked)
            )
        else:
            parts.append(
                "The system keeps looking on every sweep. Open this only if you already "
                "know the right address."
            )
        return " ".join(parts)

    def _what_stops_looking(self, asset: CanonicalAsset) -> list[str]:
        """The reasons a layer could not run at all, said plainly. Empty when none apply.

        Each of these is a **configuration** fact, not a fact about the coin, and each one
        silently removed a whole discovery layer. Naming them here is what turns "please
        add the address" into "switch this on and the machine will find it".
        """

        blocked: list[str] = []
        site = (asset.official_website or "").strip()
        # The provider's homepage counts. Saying "no website" while the market-data
        # record holds one, and while the harvest has just read it, is the report
        # describing our own paperwork instead of what actually happened.
        if not site or not is_official_url(site):
            site = self._provider_website(asset.symbol)
        if not site or not is_official_url(site):
            blocked.append(
                "this coin has no approved official website, so the links a project "
                "puts on its own site cannot be read;"
            )
        if not self.discovery.search_configured:
            blocked.append(f"web search is not usable: {self.discovery.search_requirement()}")
        browser = self.renderer.why_unavailable()
        if browser:
            blocked.append(
                f"pages that only appear after JavaScript runs cannot be read: {browser}"
            )
        assisted = self.ai_discovery.requirement()
        if assisted:
            blocked.append(assisted)
        return blocked

    async def _close_gap_case(self, asset: CanonicalAsset) -> None:
        """The gap was filled, so the task stops asking.

        Without this the queue only ever grows: a person would keep seeing a task for
        an asset the machine had already sorted out on a later sweep.
        """

        case = await self.session.scalar(
            select(ReviewCase).where(
                ReviewCase.idempotency_key == f"official-source-gap:{asset.id}"
            )
        )
        if case is None or case.done_at is not None:
            return
        case.state = "resolved"
        case.done_at = self._clock()


#: Why a fetched page did not become a usable source, in words a non-engineer can act on.
#: Keyed by the error code the fetcher and the document reader produce. One owner, so the
#: case a reviewer reads and the row a report prints cannot describe the same failure two
#: different ways.
_FAILURE_WORDS: dict[str, str] = {
    "robots_disallowed": "the site's own rules say we may not read it",
    "robots_unavailable": "the site would not say what it allows, so nothing on it was read",
    # Ours, not the site's. Worded so nobody reads it as a fact about the project.
    "robots_not_asked": "our own checker was paused, so this address was never tried",
    "too_short": "the page loaded but showed no readable text",
    "unreadable_document": "the page could not be read as text",
    "official_source_unavailable": "nothing answered at that address",
    "official_source_fetch_failed": "the address could not be reached",
}


def _why_not_usable(proof: SourceProof) -> str:
    """One short phrase saying what stopped this address counting.

    A diagnostic must never become the failure: every branch returns a string, and an
    error code nobody has words for falls back to the code itself rather than raising.
    """

    if proof.forbidden:
        return "the site does not allow us to read it"
    if proof.status in DEFINITIVE_FAILURE_STATUSES:
        return f"the address is gone (HTTP {proof.status})"
    if proof.reachable and proof.readable and not proof.fresh:
        newest = proof.published_at.date().isoformat() if proof.published_at else "long ago"
        return f"it works, but its newest item is from {newest}"
    code = proof.error_code or ""
    if code in _FAILURE_WORDS:
        return _FAILURE_WORDS[code]
    if proof.status is not None:
        return f"it answered HTTP {proof.status}"
    return _capped(code or "it could not be checked", 80)


def _layer_of(row: OfficialSource) -> DiscoveryLayer:
    """Which layer a stored row came from, defaulting to the weakest reading.

    An unknown or missing layer name is read as the guessing layer on purpose: it is
    the lowest starting confidence there is, so a row whose provenance was lost can
    never be scored higher than one whose provenance is known.
    """

    try:
        return DiscoveryLayer(str(row.discovery_layer or ""))
    except ValueError:
        return DiscoveryLayer.CONVENTION


def _status_from_error(message: str) -> int | None:
    """Recover the HTTP status the fetcher reported inside its message.

    The fetcher raises one error type for every failure and puts the status in the
    text. Without reading it back, a 404 and a timeout look identical here — and one
    of them is allowed to withdraw a source while the other must never be.
    """

    found = re.search(r"HTTP (\d{3})", message)
    return int(found.group(1)) if found else None

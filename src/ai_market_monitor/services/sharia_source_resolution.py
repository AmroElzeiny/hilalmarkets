"""Turn proposed links into proved ones, layer by layer, and ask a person when they run out.

``sharia_source_catalog`` knows where a link can come from. This module is what decides
whether a link is real. Nothing else is allowed to write ``verification_state``
``verified`` onto an official source, because "verified" is the word the Sharia research
pipeline reads to choose what counts as evidence — it selects *only* verified sources.

The order matters and is the whole idea:

1. Ask the highest layer that has not answered yet for candidates.
2. Fetch each candidate and prove it: it must be reachable, permitted by the site's own
   robots policy, readable as text, and — for a news page — recently updated.
3. Score it. Proof adds; failure removes. A candidate that cannot be proved scores zero
   however confident the layer that proposed it was.
4. If a required category is still unanswered, drop to the next layer and repeat.
5. When the layers are exhausted, raise a task for a person.

Because proof can only ever *lower* a guess, the cheap guessing layer is safe to have.
A wrong ``https://example.com/blog`` fails step 2 and disappears; it can never talk its
way into being evidence. This is the same fail-closed rule the compiler follows: a
refused reading stays visible, an invented one silently measures the wrong thing.

Two things this module deliberately does **not** do. It never writes, infers or implies
a Sharia status — a confidence number here ranks links and nothing else. And it never
decides a case: when it gives up it opens a review case for a human, which is a request
for attention, not an answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import CanonicalAsset, OfficialSource, ReviewCase
from ai_market_monitor.db.models.enums import ReviewCaseType

# One owner for turning a fetched body into text. Re-implementing it here is how the
# two-parsers-that-disagree problem starts: this module would decide a page was
# unreadable while the research pipeline read it fine, or the reverse.
from ai_market_monitor.services.sharia_research import (
    OfficialEvidenceFetcher,
    ShariaResearchError,
    _extract_document,
)
from ai_market_monitor.services.sharia_source_catalog import (
    CATEGORY_PRIORITY,
    CONFIDENCE_FLOOR,
    CURATED_CONFIDENT,
    LAYER_ORDER,
    NEWS,
    NEWS_MAXIMUM_AGE_DAYS,
    PROOF_BONUS,
    REQUIRED_CATEGORIES,
    DiscoveryLayer,
    SourceCandidate,
    candidates_for,
    category_label,
    normalized_url,
)

logger = logging.getLogger(__name__)

#: A page that renders less than this much text is not something a reviewer or a model
#: can read. Usually it means the content only exists after JavaScript runs, which the
#: evidence fetcher does not do — so the page is real but not a usable source.
MINIMUM_READABLE_CHARACTERS = 200

#: HTTP answers that mean "this address is gone", as opposed to "try again later". Only
#: these are allowed to withdraw a source that was previously trusted.
DEFINITIVE_FAILURE_STATUSES = frozenset({400, 401, 403, 404, 405, 410, 451})

#: Error codes the fetcher raises that are settled answers rather than bad luck.
DEFINITIVE_FAILURE_CODES = frozenset({"robots_disallowed"})

#: What a source row's ``verification_state`` may say.
VERIFIED = "verified"
CANDIDATE = "candidate"
UNREACHABLE = "unreachable"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d{2})\b",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.IGNORECASE,
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
        return self.status in DEFINITIVE_FAILURE_STATUSES


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


def newest_published_at(text: str, *, now: datetime) -> datetime | None:
    """The most recent date the page itself claims, if it claims one.

    Dates in the future are ignored. A newsroom listing next month's conference is not
    evidence that the newsroom is still being written.
    """

    best: datetime | None = None
    candidates: list[tuple[int, int, int]] = []
    for year, month, day in _ISO_DATE.findall(text):
        candidates.append((int(year), int(month), int(day)))
    for day, month, year in _DAY_MONTH_YEAR.findall(text):
        candidates.append((int(year), _MONTHS[month.lower()[:3]], int(day)))
    for month, day, year in _MONTH_DAY_YEAR.findall(text):
        candidates.append((int(year), _MONTHS[month.lower()[:3]], int(day)))
    for year, month, day in candidates:
        try:
            moment = datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            continue  # 31 February on a badly built page is not a date.
        if moment > now:
            continue
        if best is None or moment > best:
            best = moment
    return best


@dataclass(slots=True)
class AssetSourceOutcome:
    """What the resolver managed to settle for one asset."""

    asset_id: UUID
    symbol: str
    proved: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)
    missing: tuple[str, ...] = ()
    escalated: bool = False
    case_reference: str | None = None

    @property
    def complete(self) -> bool:
        return not self.missing and not self.withdrawn


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

    def message(self) -> str:
        if not self.assets:
            return "No asset needed its official links looked at."
        done = sum(item.complete for item in self.assets)
        return (
            f"{len(self.assets)} asset(s) checked. {self.proved} link(s) proved. "
            f"{done} now have both a news page and a community page. "
            f"{self.escalated} sent to a person."
        )


class SourceResolutionService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        fetcher: OfficialEvidenceFetcher | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.fetcher = fetcher or OfficialEvidenceFetcher(settings)
        self._now = now

    def _clock(self) -> datetime:
        return self._now or datetime.now(UTC)

    async def resolve_pending(self, *, limit: int | None = None) -> ResolutionSweep:
        """Work through assets whose official links have not been settled.

        Verified identities only. An asset whose identity a reviewer has not approved
        has no address worth trusting to derive anything from.
        """

        sweep = ResolutionSweep()
        if not self.settings.sharia_source_resolution_enabled:
            return sweep
        batch = limit or self.settings.sharia_source_resolution_batch_size
        assets = list(
            (
                await self.session.scalars(
                    select(CanonicalAsset)
                    .where(CanonicalAsset.mapping_state == "verified")
                    .order_by(CanonicalAsset.created_at.asc())
                    .limit(batch)
                )
            ).all()
        )
        for asset in assets:
            sweep.assets.append(await self.resolve_asset(asset))
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

    async def resolve_asset(self, asset: CanonicalAsset) -> AssetSourceOutcome:
        outcome = AssetSourceOutcome(asset_id=asset.id, symbol=asset.symbol)
        rows = await self._existing_rows(asset.id)
        await self._recheck_trusted(asset, rows, outcome)
        satisfied = {
            row.category
            for row in rows.values()
            if row.verification_state == VERIFIED
            and row.is_active
            and row.confidence >= CONFIDENCE_FLOOR
            and not self._due_for_recheck(row)
        }
        for layer in LAYER_ORDER:
            # The curated layer always runs. Its links were written down by a person and
            # the product wants more than one good link per coin, so it is not skipped
            # just because a category is already answered. The guessing layers are
            # skipped, because guessing costs somebody else's server a request.
            if layer is not DiscoveryLayer.CURATED and not self._still_missing(satisfied):
                break
            await self._run_layer(
                layer, asset=asset, rows=rows, satisfied=satisfied, outcome=outcome
            )
        outcome.missing = self._still_missing(satisfied)
        if outcome.missing or outcome.withdrawn:
            await self._escalate(asset, outcome)
        else:
            await self._close_gap_case(asset)
        return outcome

    @staticmethod
    def _still_missing(satisfied: set[str]) -> tuple[str, ...]:
        return tuple(item for item in REQUIRED_CATEGORIES if item not in satisfied)

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

    def _due_for_recheck(self, row: OfficialSource) -> bool:
        if row.last_checked_at is None:
            return True
        checked = row.last_checked_at
        if checked.tzinfo is None:
            # SQLite hands back naive values; treating one as server-local would make
            # a freshly checked link look weeks old, or the reverse.
            checked = checked.replace(tzinfo=UTC)
        age = self._clock() - checked
        return age > timedelta(days=self.settings.sharia_source_recheck_days)

    async def _recheck_trusted(
        self,
        asset: CanonicalAsset,
        rows: dict[str, OfficialSource],
        outcome: AssetSourceOutcome,
    ) -> None:
        """Prove the links that were trusted without ever being fetched.

        Every source registered before this module existed was written straight to
        ``verified``. Some of them are dead. A dead link that still says ``verified``
        is worse than no link, because the research pipeline hands it to a reviewer as
        evidence. This is the only place that withdraws one, and it does so only on a
        settled answer — never on a timeout.
        """

        for row in rows.values():
            # Only links that are currently trusted. A row still sitting at
            # ``candidate`` or ``unreachable`` was never evidence, so re-proving it here
            # would report it as *withdrawn* every sweep — and an asset that looked like
            # it had just lost a source would keep its human task open for ever. Those
            # rows are retried by the layer walk instead, which is where they belong.
            if row.verification_state != VERIFIED:
                continue
            if not row.is_active or not self._due_for_recheck(row):
                continue
            proof = await self._prove(row, row.category)
            if proof.usable:
                base = row.confidence or CONFIDENCE_FLOOR
                self._record(row, proof, score_candidate(base, proof), layer=row.discovery_layer)
                continue
            if proof.definitively_dead:
                self._record(row, proof, 0.0, layer=row.discovery_layer)
                row.verification_state = UNREACHABLE
                row.is_active = False
                outcome.withdrawn.append(f"{category_label(row.category)}: {row.source_url}")
                continue
            # Could not be checked. Say so, change nothing else.
            row.last_checked_at = self._clock()
            row.check_detail = {
                "outcome": "not_checked",
                "reason": _capped(proof.error_code or "unavailable"),
            }

    async def _run_layer(
        self,
        layer: DiscoveryLayer,
        *,
        asset: CanonicalAsset,
        rows: dict[str, OfficialSource],
        satisfied: set[str],
        outcome: AssetSourceOutcome,
    ) -> None:
        # A person writing links down is worth following to the end: the product wants
        # more than one good link per coin. A rule guessing addresses is not, so a
        # guessing layer stops as soon as it has answered a category.
        stop_when_answered = layer is not DiscoveryLayer.CURATED
        candidates = candidates_for(
            layer,
            symbol=asset.symbol,
            asset_name=asset.name,
            official_website=asset.official_website,
            official_documentation=asset.official_documentation,
        )
        for candidate in candidates:
            if stop_when_answered and candidate.category in satisfied:
                continue
            row = rows.get(candidate.normalized_url)
            if row is not None and not self._due_for_recheck(row):
                if row.verification_state == VERIFIED and row.is_active:
                    satisfied.add(row.category)
                continue
            row = await self._register(asset, candidate, row)
            rows[candidate.normalized_url] = row
            proof = await self._prove(row, candidate.category)
            confidence = score_candidate(candidate.confidence, proof)
            self._record(row, proof, confidence, layer=str(layer))
            label = f"{category_label(candidate.category)}: {candidate.url}"
            if row.verification_state == VERIFIED:
                satisfied.add(candidate.category)
                outcome.proved.append(label)
            else:
                outcome.rejected.append(label)

    async def _register(
        self,
        asset: CanonicalAsset,
        candidate: SourceCandidate,
        existing: OfficialSource | None,
    ) -> OfficialSource:
        if existing is not None:
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
        row.check_detail = {
            "reachable": proof.reachable,
            "robots_allowed": proof.allowed,
            "readable": proof.readable,
            "recent": proof.fresh,
            "http_status": proof.status,
            "error": _capped(proof.error_code) if proof.error_code else None,
            **{key: _capped(value) for key, value in proof.detail.items()},
        }
        if proof.usable and confidence >= CONFIDENCE_FLOOR:
            row.verification_state = VERIFIED
            row.verified_at = self._clock()
        elif proof.definitively_dead:
            row.verification_state = UNREACHABLE
        else:
            row.verification_state = CANDIDATE

    async def _prove(self, row: OfficialSource, category: str) -> SourceProof:
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
        try:
            _title, headings, text = _extract_document(
                body, row.source_url, content_type=headers.get("content-type")
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
        published = newest_published_at(
            "\n".join([*headings, text]), now=now
        )
        if category == NEWS:
            fresh = published is not None and (
                now - published <= timedelta(days=NEWS_MAXIMUM_AGE_DAYS)
            )
        else:
            # A forum or a subreddit has no publication date of its own, and demanding
            # one would reject every community page there is.
            fresh = True
        return SourceProof(
            reachable=True,
            allowed=True,
            readable=readable,
            fresh=fresh,
            status=status,
            published_at=published,
            detail={"characters": len(text.strip())},
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
        tried = outcome.rejected[:10]
        reason = " ".join(
            [
                f"The system could not find every official page for {asset.name}.",
                (
                    "It tried these and none worked: " + "; ".join(tried) + "."
                    if tried
                    else "It had no address to try."
                ),
                "Please add the correct address on the asset, or say there is not one.",
            ]
        )
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
            title=f"Find the official news and community pages for {asset.name}",
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


def _status_from_error(message: str) -> int | None:
    """Recover the HTTP status the fetcher reported inside its message.

    The fetcher raises one error type for every failure and puts the status in the
    text. Without reading it back, a 404 and a timeout look identical here — and one
    of them is allowed to withdraw a source while the other must never be.
    """

    found = re.search(r"HTTP (\d{3})", message)
    return int(found.group(1)) if found else None

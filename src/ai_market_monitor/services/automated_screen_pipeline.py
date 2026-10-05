"""One coin, end to end: find its pages, read them, decide, and file a Passport.

The four steps each already have an owner, and this is the only place that runs them in
order:

    ``coinmarketcap``               where the project publishes
    ``coin_evidence_crawler``       what those pages say
    ``sharia_evidence_screen``      what that means under the automated rule
    this module                     what is written down, and what is not

**What is written down.** An :class:`AutomatedScreenRun` holding the verdict and the
sentence behind each reason, a receipt for every page read, and the factual half of a
Passport. What is **not** written is a Shariah status: no ``AssetShariaAssessment`` is
created, no ``ExternalAssessment`` is touched, and ``published`` stays false. A person
using the product sees this only where it is labelled as an automated proposal that no
scholar has reviewed.

**The Passport is built alongside the decision, not after it.** They come from the same
reading, so a Passport can never describe one set of facts while the verdict beside it
rests on another. That is the failure this ordering exists to make impossible.

**A coin with nothing to read is not a refused coin.** It is filed under *Not enough
data* and it stays there until somebody finds it a source. Silence never becomes a "no".
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.asset_logos import PROVIDER_LOGO_FIELD
from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AutomatedScreenRun,
    CanonicalAsset,
    CoinEvidenceDocument,
    ProviderCoinProfile,
    ReviewCase,
)
from ai_market_monitor.db.models.enums import ReviewCaseType
from ai_market_monitor.services.coin_evidence_crawler import (
    CoinEvidenceCrawler,
    EvidenceFolder,
)
from ai_market_monitor.services.coin_terms_ai_review import (
    COUNTED_AI_STATES,
    MAX_AI_ATTEMPTS,
    RETRY_AFTER,
    RETRY_AI_STATES,
    AIReview,
    CoinReport,
    CoinTermsAIReviewer,
    build_report,
)
from ai_market_monitor.services.coinmarketcap import (
    CoinLinks,
    CoinMarketCapClient,
    CoinMarketCapError,
)
from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
    METHODOLOGY_DISPLAY_NAME,
    METHODOLOGY_SYSTEM_CODE,
)
from ai_market_monitor.services.sharia_evidence_screen import (
    EvidenceDecision,
    EvidenceVerdict,
    decide,
)
from ai_market_monitor.services.unscreened_coin_research import apply_provider_record

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineResult:
    """What one sweep did. Counted by outcome, because that is what a reader asks."""

    eligible: int = 0
    not_eligible: int = 0
    not_enough_data: int = 0
    pages_read: int = 0
    provider_silent: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    credits_spent: int = 0
    #: Coins a term against the methodology was found for. Not a status: each one is
    #: in the reviewers' task list waiting for a person.
    held_back: int = 0
    #: Coins the AI reviewer read and whose answer passed the grounding check.
    ai_reviewed: int = 0
    #: Coins left for the next sweep because this one ran out of time.
    deferred: list[str] = field(default_factory=list)

    @property
    def decided(self) -> int:
        return self.eligible + self.not_eligible

    @property
    def stored(self) -> int:
        """Coins this sweep finished and filed a report for, whatever the report says."""

        return self.eligible + self.not_eligible + self.not_enough_data

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "not_eligible": self.not_eligible,
            "not_enough_data": self.not_enough_data,
            "decided": self.decided,
            "stored": self.stored,
            "pages_read": self.pages_read,
            "provider_silent": list(self.provider_silent),
            "failed": dict(self.failed),
            "credits_spent": self.credits_spent,
            "held_back": self.held_back,
            "ai_reviewed": self.ai_reviewed,
            "deferred": list(self.deferred),
        }


def passport_payload(
    decision: EvidenceDecision,
    record: CoinLinks | None,
    folder: EvidenceFolder,
) -> dict[str, Any]:
    """The Passport for a coin nobody has ruled on, built from the same reading.

    Shaped like the published Passport a reviewer already knows, with one difference
    that is stated in the payload rather than left to a template: ``human_reviewed`` is
    false and ``methodology`` names the automated screen. Every surface that shows this
    is required to say so.
    """

    return {
        "symbol": decision.symbol,
        "name": decision.name or (record.name if record else decision.symbol),
        "methodology": METHODOLOGY_SYSTEM_CODE,
        "methodology_name": METHODOLOGY_DISPLAY_NAME,
        "human_reviewed": False,
        "disclosure": AUTOMATED_DISCLOSURE,
        "identity": {
            "slug": record.slug if record else "",
            "category": record.category if record else None,
            "tags": list(record.tags) if record else [],
            "platform": record.platform if record else None,
            "contract_address": record.contract_address if record else None,
            "date_added": record.date_added.isoformat()
            if record and record.date_added
            else None,
            "logo_url": record.logo if record else None,
        },
        "official_sources": {
            "website": list(record.website) if record else [],
            "whitepaper": list(record.whitepaper) if record else [],
            "source_code": list(record.source_code) if record else [],
            "explorer": list(record.explorer) if record else [],
        },
        "description": record.description if record else None,
        "what_it_does": [item.text for item in decision.reasons]
        if decision.verdict is EvidenceVerdict.ELIGIBLE
        else [],
        "evidence_read": folder.as_dict(),
        "automated_result": decision.as_dict(),
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def coins_to_screen(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Every researched coin still owed a reading, in the order the sweep takes them.

    Two groups, and the first always goes first:

    1. **Never read.** Every coin the researcher gathered links for that has no report.
       Biggest first, by the market size the daily numbers refresh stores, so the effort
       lands where users actually are; then by symbol, so the order is the same on every
       run instead of whatever the database happens to return.
    2. **Owed another try.** A report whose reading did not happen — the AI failed, or
       nothing could be read — is tried again, up to :data:`MAX_AI_ATTEMPTS` times and
       no sooner than :data:`RETRY_AFTER` after the last try. Retries go last so a site
       that is down for good can never hold a new coin back.

    Everything else already has its report and is not read again.
    """

    now = now or datetime.now(UTC)
    runs = {
        row.symbol: row
        for row in (
            await session.execute(
                select(
                    AutomatedScreenRun.symbol,
                    AutomatedScreenRun.ai_review_state,
                    AutomatedScreenRun.ai_review_attempts,
                    AutomatedScreenRun.decided_at,
                )
            )
        ).all()
    }
    candidates = (
        await session.scalars(
            select(ProviderCoinProfile.symbol)
            .where(
                ProviderCoinProfile.provider == "coinmarketcap",
                ProviderCoinProfile.research_state == "researched",
            )
            .order_by(
                ProviderCoinProfile.market_cap_usd.desc().nullslast(),
                ProviderCoinProfile.symbol,
            )
        )
    ).all()

    def owed_retry(symbol: str) -> bool:
        run = runs[symbol]
        return (
            run.ai_review_state in RETRY_AI_STATES
            and (run.ai_review_attempts or 0) < MAX_AI_ATTEMPTS
            and (run.decided_at is None or _aware(run.decided_at) <= now - RETRY_AFTER)
        )

    fresh = [symbol for symbol in candidates if symbol not in runs]
    retry = [symbol for symbol in candidates if symbol in runs and owed_retry(symbol)]
    return list(dict.fromkeys([*fresh, *retry]))


class AutomatedScreenPipeline:
    """Runs the four steps for a list of coins and files what they produce."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        coinmarketcap: CoinMarketCapClient | None = None,
        crawler: CoinEvidenceCrawler | None = None,
        ai_reviewer: CoinTermsAIReviewer | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.coinmarketcap = coinmarketcap or CoinMarketCapClient(settings)
        self.crawler = crawler or CoinEvidenceCrawler(settings)
        self.ai_reviewer = ai_reviewer or CoinTermsAIReviewer(settings)

    async def run(
        self,
        symbols: Sequence[str],
        *,
        limit: int | None = None,
        time_budget_seconds: float | None = None,
    ) -> PipelineResult:
        """Screen each symbol. One provider call covers up to a hundred of them.

        ``time_budget_seconds`` stops the sweep *starting* another coin once it has run
        that long. The coins left over are simply not screened yet, so the next sweep
        takes them; nothing is half-written.
        """

        started = time.monotonic()
        result = PipelineResult()
        wanted = [s.strip().upper() for s in symbols if s and s.strip()]
        if limit is not None:
            wanted = wanted[: max(0, limit)]
        if not wanted:
            return result

        try:
            records = await self.coinmarketcap.coin_links(wanted)
        except CoinMarketCapError as exc:
            logger.info("automated_screen_provider_unavailable", extra={"reason": exc.code})
            result.failed = dict.fromkeys(wanted, exc.code)
            return result
        result.credits_spent = self.coinmarketcap.usage.credits
        result.provider_silent = sorted(set(wanted) - set(records))

        for index, symbol in enumerate(wanted):
            if (
                time_budget_seconds is not None
                and time.monotonic() - started >= time_budget_seconds
            ):
                result.deferred = wanted[index:]
                break
            record = records.get(symbol)
            try:
                decision, folder = await self.screen_one(symbol, record)
                review = await self.ai_reviewer.review(
                    symbol=decision.symbol,
                    name=decision.name,
                    folder=folder,
                    record=record,
                )
            except Exception as exc:  # noqa: BLE001 - one coin must not end the sweep
                logger.warning(
                    "automated_screen_failed",
                    extra={"symbol": symbol, "error": type(exc).__name__},
                )
                result.failed[symbol] = type(exc).__name__
                continue

            result.pages_read += decision.documents_read
            if decision.verdict is EvidenceVerdict.ELIGIBLE:
                result.eligible += 1
            elif decision.verdict is EvidenceVerdict.NOT_ELIGIBLE:
                result.not_eligible += 1
            else:
                result.not_enough_data += 1

            report = build_report(decision, review, folder, record)
            if report.held_back:
                result.held_back += 1
            if review.completed:
                result.ai_reviewed += 1
            await self.store(decision, record, folder, review=review, report=report)

        await self.session.flush()
        await self.crawler.aclose()
        return result

    async def screen_one(
        self,
        symbol: str,
        record: CoinLinks | None,
    ) -> tuple[EvidenceDecision, EvidenceFolder]:
        """Gather and judge one coin. Touches the network; writes nothing."""

        website = record.website[0] if record and record.website else None
        folder = await self.crawler.gather(
            symbol,
            website=website,
            provider_links=_link_fields(record),
        )
        name = record.name if record else symbol
        decision = decide(
            symbol,
            name,
            folder,
            also_known_as=_other_names(record),
            # Read only for whether the provider calls it a meme coin — the one thing
            # its labels may decide. See `services/meme_coins.py`.
            provider_tags=record.tags if record else (),
            provider_category=record.category if record else None,
            provider_slug=record.slug if record else None,
        )
        return decision, folder

    async def store(
        self,
        decision: EvidenceDecision,
        record: CoinLinks | None,
        folder: EvidenceFolder,
        *,
        review: AIReview,
        report: CoinReport,
    ) -> None:
        run = await self.session.scalar(
            select(AutomatedScreenRun).where(AutomatedScreenRun.symbol == decision.symbol)
        )
        if run is None:
            run = AutomatedScreenRun(symbol=decision.symbol)
            self.session.add(run)
        run.asset_name = decision.name[:180]
        run.verdict = decision.verdict.value
        run.reasons = [item.as_dict() for item in decision.reasons]
        run.activities = [item.value for item in decision.activities]
        run.blocking_activities = [item.value for item in decision.blocking_activities]
        run.evidence = [item.as_dict() for item in decision.findings]
        run.holder_return = decision.holder_return.value if decision.holder_return else None
        run.holder_return_basis = decision.holder_return_basis or None
        run.open_questions = list(decision.open_questions)
        run.matched_conditions = list(decision.matched_conditions)
        run.proposed_matches = list(decision.proposed_matches)
        run.documents_read = decision.documents_read
        run.primary_documents_read = decision.primary_documents_read
        run.decided_at = datetime.now(UTC)
        run.review_report = report.body
        run.hold_state = report.hold_state
        run.ai_review_state = review.state
        if review.state in COUNTED_AI_STATES:
            run.ai_review_attempts = (run.ai_review_attempts or 0) + 1
        # `published` is deliberately never assigned here. Only the application's own
        # approval route may set it, and only after a person has decided.
        await self.session.flush()

        await self._store_documents(run, decision.symbol, folder)
        await self._store_passport(decision, record, folder)
        await self._file_review_task(
            run, symbol=decision.symbol, name=decision.name, report=report
        )
        self._record_ai_usage(review)

    async def _file_review_task(
        self,
        run: AutomatedScreenRun,
        *,
        symbol: str,
        name: str,
        report: CoinReport,
    ) -> None:
        """Put the report in the reviewers' task list. One task per coin.

        A task a person has already decided is left alone: a later reading adds to the
        run it describes, and never reopens a decision behind the reviewer's back. An
        open task is brought up to date, so a reviewer never reads a report older than
        the one on the run.

        The task carries no verdict and cannot publish one. Only a reviewer's decision on
        it does — "Approve & publish" or "Reject & publish" — and that writes the coin's
        Passport under the Hilal Markets Methodology (``reviewer_passports``).
        """

        case = (
            await self.session.get(ReviewCase, run.review_case_id)
            if run.review_case_id is not None
            else None
        )
        key = f"automated-coin-review:{symbol}"
        if case is None:
            case = await self.session.scalar(
                select(ReviewCase).where(ReviewCase.idempotency_key == key)
            )
        if case is not None and case.done_at is not None:
            run.review_case_id = case.id
            return

        held = report.held_back
        body = report.body
        now = datetime.now(UTC)
        name = (name or symbol)[:180]
        if case is None:
            asset = await self.session.scalar(
                select(CanonicalAsset).where(CanonicalAsset.symbol == symbol)
            )
            case = ReviewCase(
                case_reference=f"NEW-{symbol}"[:40],
                case_type=ReviewCaseType.AUTOMATED_COIN_REVIEW,
                state="ready_for_review",
                publication_state="unpublished",
                canonical_asset_id=asset.id if asset is not None else None,
                idempotency_key=key[:128],
                title=f"New coin check: {name} ({symbol})"[:300],
                human_review_reason="",
                requested_evidence=[],
                due_at=now
                + timedelta(hours=self.settings.sharia_review_sla_hours),
                next_reminder_at=now,
            )
            self.session.add(case)
        flagged = report.red_flag_count > 0
        case.priority = (
            "high"
            if held or flagged
            else "low"
            if report.hold_state == "not_enough_data"
            else "normal"
        )
        # ``medium`` is the research vocabulary's word for "read this first, nothing is
        # proven" — exactly a red flag. ``sharia_case_tags`` reads it; never the text.
        case.risk_severity = "high" if held else "medium" if flagged else "none"
        case.human_review_reason = report.summary
        case.requested_evidence = [
            *(f"Red flag: {item.get('text')}" for item in body.get("red_flags") or []),
            *(body.get("doubts") or []),
        ]
        await self.session.flush()
        run.review_case_id = case.id

    async def send_to_review(self, symbols: Sequence[str]) -> dict[str, str]:
        """Put coins in front of a reviewer, from the report already stored for each.

        For a coin whose machine result was taken down because no person decided it
        (5 October 2026: the owner's rule that nothing is shown until a reviewer decides).
        Nothing is read again and nothing is decided. Returns, per coin, what happened:

        * ``filed`` — a task is open for it now (a new one, or the open one brought up
          to date);
        * ``already_decided`` — a person already decided it; that decision stands;
        * ``queued`` — its stored reading has no report yet, so the next sweep reads it
          again and files the task then;
        * ``never_read`` — the machine never read it; nothing to file.
        """

        outcome: dict[str, str] = {}
        for raw in symbols:
            symbol = raw.strip().upper()
            run = await self.session.scalar(
                select(AutomatedScreenRun).where(AutomatedScreenRun.symbol == symbol)
            )
            if run is None:
                outcome[symbol] = "never_read"
                continue
            existing = (
                await self.session.get(ReviewCase, run.review_case_id)
                if run.review_case_id is not None
                else None
            )
            if existing is not None and existing.done_at is not None:
                outcome[symbol] = "already_decided"
                continue
            body = dict(run.review_report or {})
            if not body.get("hold_state"):
                # A reading from before reports existed. Owed a fresh read: the sweep
                # takes it on its next run, and files the task from that report.
                run.ai_review_state = "pending"
                run.ai_review_attempts = 0
                run.decided_at = None
                outcome[symbol] = "queued"
                continue
            report = CoinReport(
                hold_state=body["hold_state"], summary=str(body.get("summary") or ""), body=body
            )
            await self._file_review_task(
                run, symbol=symbol, name=run.asset_name or symbol, report=report
            )
            outcome[symbol] = "filed"
        await self.session.flush()
        return outcome

    def _record_ai_usage(self, review: AIReview) -> None:
        """One row in the AI spending ledger for each answer the model gave.

        The row is built by the reviewer, which owns the model call; this pipeline only
        stores it, so it never needs to know which provider answered.
        """

        event = self.ai_reviewer.usage_event(review)
        if event is not None:
            self.session.add(event)

    async def _store_documents(
        self,
        run: AutomatedScreenRun,
        symbol: str,
        folder: EvidenceFolder,
    ) -> None:
        existing = {
            row.url: row
            for row in await self.session.scalars(
                select(CoinEvidenceDocument).where(CoinEvidenceDocument.symbol == symbol)
            )
        }
        for document in folder.documents:
            row = existing.get(document.url)
            if row is None:
                row = CoinEvidenceDocument(symbol=symbol, url=document.url)
                self.session.add(row)
            row.run_id = run.id
            row.category = document.category[:40]
            row.title = document.title[:500]
            row.characters = len(document.text)
            row.seeded = document.seeded
            row.is_primary = document.is_primary
            row.fetched_at = document.fetched_at
            row.failure_code = None
        for url, code in folder.failures.items():
            row = existing.get(url)
            if row is None:
                row = CoinEvidenceDocument(symbol=symbol, url=url)
                self.session.add(row)
            row.run_id = run.id
            row.failure_code = code[:60]
            row.characters = 0

    async def _store_passport(
        self,
        decision: EvidenceDecision,
        record: CoinLinks | None,
        folder: EvidenceFolder,
    ) -> None:
        """File the factual profile beside the provider record, and the logo with it."""

        profile = await self.session.scalar(
            select(ProviderCoinProfile).where(
                ProviderCoinProfile.symbol == decision.symbol,
                ProviderCoinProfile.provider == "coinmarketcap",
            )
        )
        if profile is None:
            profile = ProviderCoinProfile(
                provider="coinmarketcap", symbol=decision.symbol
            )
            self.session.add(profile)
        now = datetime.now(UTC)
        if record is not None:
            # The researcher's writer, not a copy of it: two copies had already drifted.
            apply_provider_record(profile, record, now)
        profile.links = {
            **(profile.links or {}),
            "passport": passport_payload(decision, record, folder),
        }
        profile.research_state = "researched"
        profile.refreshed_at = now

        await self._attach_logo(decision.symbol, record)

    async def _attach_logo(self, symbol: str, record: CoinLinks | None) -> None:
        """Give an approved asset the provider's picture, under the provider's own key.

        Written to :data:`PROVIDER_LOGO_FIELD`, never to the identity picture's key. Two
        different jobs write a coin's picture and whichever ran last would replace the
        other's answer if they shared a field. Which one is *shown* is decided once, in
        ``core/asset_logos``, and not by whoever happened to run second.
        """

        if record is None or not record.logo:
            return
        asset = await self.session.scalar(
            select(CanonicalAsset).where(CanonicalAsset.symbol == symbol)
        )
        if asset is None:
            return
        provider_ids = dict(asset.provider_ids or {})
        if provider_ids.get(PROVIDER_LOGO_FIELD) == record.logo:
            return
        provider_ids[PROVIDER_LOGO_FIELD] = record.logo
        if record.cmc_id:
            # Stored as text, because every other id in this mapping is text and a
            # mapping whose values are sometimes numbers is one a reader has to guess at.
            provider_ids.setdefault("coinmarketcap_id", str(record.cmc_id))
        asset.provider_ids = provider_ids


def _other_names(record: CoinLinks | None) -> list[str]:
    """Every other way this project refers to itself, for the attribution test.

    The provider's slug and the host it publishes under are both the project's own name
    in another spelling — ``eigenlayer``, ``rocketpool``. Handing them over can only keep
    a refusal that would otherwise be dropped as belonging to somebody else, so more
    names here is strictly the safer direction.
    """

    if record is None:
        return []
    names = [record.slug, record.name]
    for url in record.website[:2]:
        host = urlsplit(str(url)).netloc.casefold()
        names.extend(part for part in host.split(".") if part not in _HOST_NOISE)
    return [value for value in names if value]


#: Parts of a domain that name no project.
_HOST_NOISE = frozenset(
    {"www", "com", "org", "io", "net", "xyz", "fi", "ai", "co", "app", "dev", "foundation"}
)


def _link_fields(record: CoinLinks | None) -> Mapping[str, Sequence[str]]:
    if record is None:
        return {}
    return {
        "website": record.website,
        "whitepaper": record.whitepaper,
        "announcement": record.announcement,
        "source_code": record.source_code,
    }


__all__ = [
    "AutomatedScreenPipeline",
    "PipelineResult",
    "coins_to_screen",
    "passport_payload",
]

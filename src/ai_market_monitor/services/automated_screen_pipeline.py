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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
)
from ai_market_monitor.services.coin_evidence_crawler import (
    CoinEvidenceCrawler,
    EvidenceFolder,
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

    @property
    def decided(self) -> int:
        return self.eligible + self.not_eligible

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "not_eligible": self.not_eligible,
            "not_enough_data": self.not_enough_data,
            "decided": self.decided,
            "pages_read": self.pages_read,
            "provider_silent": list(self.provider_silent),
            "failed": dict(self.failed),
            "credits_spent": self.credits_spent,
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


class AutomatedScreenPipeline:
    """Runs the four steps for a list of coins and files what they produce."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        coinmarketcap: CoinMarketCapClient | None = None,
        crawler: CoinEvidenceCrawler | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.coinmarketcap = coinmarketcap or CoinMarketCapClient(settings)
        self.crawler = crawler or CoinEvidenceCrawler(settings)

    async def run(
        self,
        symbols: Sequence[str],
        *,
        limit: int | None = None,
    ) -> PipelineResult:
        """Screen each symbol. One provider call covers up to a hundred of them."""

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

        for symbol in wanted:
            record = records.get(symbol)
            try:
                decision, folder = await self.screen_one(symbol, record)
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

            await self.store(decision, record, folder)

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
        return decide(symbol, name, folder, also_known_as=_other_names(record)), folder

    async def store(
        self,
        decision: EvidenceDecision,
        record: CoinLinks | None,
        folder: EvidenceFolder,
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
        # `published` is deliberately never assigned here. Only the application's own
        # approval route may set it, and only after a person has decided.
        await self.session.flush()

        await self._store_documents(run, decision.symbol, folder)
        await self._store_passport(decision, record, folder)

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
        if record is not None:
            profile.provider_id = record.cmc_id or None
            profile.name = (record.name or profile.name)[:180]
            profile.slug = (record.slug or profile.slug)[:180]
            profile.official_website = record.website[0] if record.website else None
            profile.whitepaper_url = record.whitepaper[0] if record.whitepaper else None
            profile.source_code_url = record.source_code[0] if record.source_code else None
            profile.logo_url = record.logo
            profile.category = record.category
            profile.tags = list(record.tags)
            profile.platform = record.platform
            profile.description = record.description
            profile.date_added = record.date_added
        profile.links = {
            **(profile.links or {}),
            "passport": passport_payload(decision, record, folder),
        }
        profile.research_state = "researched"
        profile.refreshed_at = datetime.now(UTC)

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


__all__ = ["AutomatedScreenPipeline", "PipelineResult", "passport_payload"]

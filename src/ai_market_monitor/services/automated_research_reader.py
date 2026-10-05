"""What to show on the page about coins a Hilal Markets reviewer decided.

One reader, so the page, the counters and the coin detail can never disagree about what
a verdict means or how many of each there are.

**Only a person's decision is shown.** The machine reads every new coin, and that
reading is research for the reviewer in System Brain. Customers see a coin here only
after a Hilal Markets reviewer approved or rejected it there, and they see the reviewer's
decision and reasons — never the machine's verdict.

**The list never loads the machine's evidence.** A run carries its quotations and page
receipts, a few kilobytes each; five list views once loaded evidence JSON like that and
turned a twelve-row page into 1.6 GB of reads. The list selects the reviewer record's
small columns. The detail loads the page list for one coin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.dashboard_paths import passport_path
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    AutomatedScreenRun,
    CoinEvidenceDocument,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.services.coin_logos import provider_logos
from ai_market_monitor.services.hilal_methodology import is_reviewer_checked
from ai_market_monitor.services.reviewer_passports import (
    REVIEWER_QUALIFICATION,
    STATUS_FOR,
)
from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
    METHODOLOGY_DISPLAY_NAME,
    METHODOLOGY_SYSTEM_CODE,
)

#: How each reviewer decision is said to somebody who has never read a screening report,
#: and the colour the shipped dashboard already uses for that kind of answer.
#:
#: Only two answers. The machine's own three ("looks clean", "has a problem", "not
#: enough data") are no longer shown to customers at all: the owner's rule from
#: 5 October 2026 is that no decision reaches this page until a person made it.
VERDICT_PRESENTATION: dict[str, dict[str, str]] = {
    "not_approved": {
        "label": "Not approved",
        "tone": "warning",
        "meaning": (
            "A Hilal Markets reviewer read this coin's pages and did not approve it. "
            "The reasons are on the coin's page."
        ),
    },
    "approved": {
        "label": "Approved by our reviewer",
        "tone": "success",
        "meaning": (
            "A Hilal Markets reviewer read this coin's pages and approved it under the "
            "Hilal Markets Methodology. It is not an outside Shariah authority's ruling."
        ),
    },
}

#: The order the page shows them in. Problems first is deliberate: a reader scanning
#: this list is looking for what to be careful about, not for reassurance.
VERDICT_ORDER: tuple[str, ...] = ("not_approved", "approved")

#: How many rows one page shows.
PAGE_SIZE = 200


def _verdict(status: ShariaAssetStatus | str) -> str:
    """The reviewer's answer, from the status ``reviewer_passports.STATUS_FOR`` wrote."""

    value = str(getattr(status, "value", status))
    return "approved" if value == STATUS_FOR[True].value else "not_approved"


class AutomatedResearchReader:
    """Reads the coins a reviewer decided, for the page that shows them."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, *, verdict: str = "all") -> dict[str, Any]:
        decided = await self._decided()
        if verdict not in VERDICT_PRESENTATION:
            verdict = "all"
        counts = {value: 0 for value in VERDICT_ORDER}
        for row in decided:
            counts[row["verdict"]] += 1
        counts["all"] = len(decided)
        rows = [row for row in decided if verdict == "all" or row["verdict"] == verdict]
        logos = await provider_logos(self.session, [row["symbol"] for row in rows])
        for row in rows:
            row["logo_url"] = logos.get(row["symbol"])
        return {
            "research_rows": rows[:PAGE_SIZE],
            "research_counts": counts,
            "research_verdict": verdict,
            "research_verdicts": VERDICT_PRESENTATION,
            "research_verdict_order": VERDICT_ORDER,
            "automated_methodology_name": METHODOLOGY_DISPLAY_NAME,
            "automated_methodology_disclosure": AUTOMATED_DISCLOSURE,
        }

    async def _decided(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Every coin's current reviewer decision, newest first.

        Only rows a person decided (:func:`hilal_methodology.is_reviewer_checked`). The
        machine's own readings stay in the database for System Brain, where reviewers
        read them; they are never a row here.
        """

        query = (
            select(
                AssetShariaAssessment.canonical_asset,
                AssetShariaAssessment.asset_name,
                AssetShariaAssessment.status,
                AssetShariaAssessment.reviewed_at,
                AssetShariaAssessment.evidence_snapshot,
            )
            .join(ShariaMethodology, ShariaMethodology.id == AssetShariaAssessment.methodology_id)
            .where(
                ShariaMethodology.code == METHODOLOGY_SYSTEM_CODE,
                AssetShariaAssessment.valid_until.is_(None),
                AssetShariaAssessment.reviewed_by_user_id.is_not(None),
            )
            .order_by(AssetShariaAssessment.reviewed_at.desc())
        )
        if symbol is not None:
            query = query.where(AssetShariaAssessment.canonical_asset == symbol)
        decided: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in await self.session.execute(query):
            snapshot = row.evidence_snapshot or {}
            if row.canonical_asset in seen or not is_reviewer_checked(snapshot):
                continue
            seen.add(row.canonical_asset)
            verdict = _verdict(row.status)
            decided.append(
                {
                    "symbol": row.canonical_asset,
                    "name": row.asset_name or row.canonical_asset,
                    "verdict": verdict,
                    "presentation": VERDICT_PRESENTATION[verdict],
                    "reasons": [str(item) for item in snapshot.get("reasons") or []],
                    "documents_read": int(snapshot.get("pages_read") or 0),
                    "primary_documents_read": int(snapshot.get("primary_pages_read") or 0),
                    "decided_at": row.reviewed_at,
                    "passport_url": passport_path(row.canonical_asset),
                }
            )
        return decided

    async def detail(self, symbol: str) -> dict[str, Any] | None:
        """One decided coin: the reviewer's decision, its reasons, and the pages read.

        ``None`` for a coin no reviewer has decided — even one the machine has read. Its
        reading is for the reviewer, in System Brain, not for this page.
        """

        wanted = symbol.strip().upper()
        found = await self._decided(wanted)
        if not found:
            return None
        coin = found[0]
        run_name = await self.session.scalar(
            select(AutomatedScreenRun.asset_name).where(AutomatedScreenRun.symbol == wanted)
        )
        documents = await self.session.scalars(
            select(CoinEvidenceDocument)
            .where(
                CoinEvidenceDocument.symbol == wanted,
                CoinEvidenceDocument.failure_code.is_(None),
            )
            .order_by(CoinEvidenceDocument.is_primary.desc(), CoinEvidenceDocument.url)
        )
        logos = await provider_logos(self.session, [wanted])
        decided_at: datetime | None = coin["decided_at"]
        return {
            **coin,
            "name": coin["name"] if coin["name"] != wanted else (run_name or wanted),
            "logo_url": logos.get(wanted),
            "documents": [
                {
                    "url": document.url,
                    "category": document.category,
                    "title": document.title,
                    "characters": document.characters,
                    "is_primary": document.is_primary,
                    "failure_code": document.failure_code,
                }
                for document in documents
            ],
            "decided_at": decided_at,
            "qualification": REVIEWER_QUALIFICATION,
            "disclosure": AUTOMATED_DISCLOSURE,
        }


__all__ = [
    "PAGE_SIZE",
    "VERDICT_ORDER",
    "VERDICT_PRESENTATION",
    "AutomatedResearchReader",
]

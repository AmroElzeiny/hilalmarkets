"""What to show on the page about coins the machine researched.

One reader, so the page, the counters and the coin detail can never disagree about what
a verdict means or how many of each there are.

**The list never loads the evidence.** Every run carries its quotations and its page
receipts, which is a few kilobytes each and the whole point of the design — but a
hundred rows of it is megabytes nobody reading a list will look at. This product has
already paid for that mistake once: five list views loaded full evidence JSON and turned
a twelve-row page into 1.6 GB of reads, which made every other page slow whenever one
person opened it. The list selects the columns it draws. The detail selects the rest.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import AutomatedScreenRun, CoinEvidenceDocument
from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
    METHODOLOGY_DISPLAY_NAME,
)
from ai_market_monitor.services.sharia_evidence_screen import EvidenceVerdict

#: How each verdict is said to somebody who has never read a screening report, and the
#: colour the shipped dashboard already uses for that kind of answer.
VERDICT_PRESENTATION: dict[str, dict[str, str]] = {
    EvidenceVerdict.ELIGIBLE.value: {
        "label": "Looks clean",
        "tone": "success",
        "meaning": (
            "We read this project's own pages and found nothing that breaks the rules "
            "we check. No scholar has looked at it."
        ),
    },
    EvidenceVerdict.NOT_ELIGIBLE.value: {
        "label": "Has a problem",
        "tone": "warning",
        "meaning": (
            "The project's own pages describe something the rules we check do not "
            "allow. The exact words are shown with each reason."
        ),
    },
    EvidenceVerdict.NOT_ENOUGH_DATA.value: {
        "label": "Not enough data",
        "tone": "neutral",
        "meaning": (
            "We could not find enough written about this project to say anything. "
            "This is not a 'no' — nobody has judged it either way."
        ),
    },
}

#: The order the page shows them in. Problems first is deliberate: a reader scanning
#: this list is looking for what to be careful about, not for reassurance.
VERDICT_ORDER: tuple[str, ...] = (
    EvidenceVerdict.NOT_ELIGIBLE.value,
    EvidenceVerdict.ELIGIBLE.value,
    EvidenceVerdict.NOT_ENOUGH_DATA.value,
)

#: How many rows one page shows.
PAGE_SIZE = 200


class AutomatedResearchReader:
    """Reads automated screen runs for the page that shows them."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, *, verdict: str = "all") -> dict[str, Any]:
        return {
            "research_rows": await self.rows(verdict=verdict),
            "research_counts": await self.counts(),
            "research_verdict": verdict,
            "research_verdicts": VERDICT_PRESENTATION,
            "research_verdict_order": VERDICT_ORDER,
            "automated_methodology_name": METHODOLOGY_DISPLAY_NAME,
            "automated_methodology_disclosure": AUTOMATED_DISCLOSURE,
        }

    async def counts(self) -> dict[str, int]:
        """How many coins landed in each answer. One query, three numbers."""

        rows = await self.session.execute(
            select(AutomatedScreenRun.verdict, func.count(AutomatedScreenRun.id)).group_by(
                AutomatedScreenRun.verdict
            )
        )
        counts = {value: 0 for value in VERDICT_ORDER}
        for value, count in rows:
            counts[str(value)] = int(count)
        counts["all"] = sum(counts[value] for value in VERDICT_ORDER)
        return counts

    async def rows(self, *, verdict: str = "all") -> list[dict[str, Any]]:
        """One row per coin, carrying only what the list actually draws.

        The columns are named explicitly. ``select(AutomatedScreenRun)`` would load the
        reasons, the evidence and the activity lists for every row — the exact shape of
        read that made an unrelated list view in this product read 1.6 GB to draw twelve
        coins.
        """

        query = select(
            AutomatedScreenRun.symbol,
            AutomatedScreenRun.asset_name,
            AutomatedScreenRun.verdict,
            AutomatedScreenRun.documents_read,
            AutomatedScreenRun.primary_documents_read,
            AutomatedScreenRun.decided_at,
        )
        if verdict in VERDICT_PRESENTATION:
            query = query.where(AutomatedScreenRun.verdict == verdict)
        query = query.order_by(
            AutomatedScreenRun.documents_read.desc(), AutomatedScreenRun.symbol
        ).limit(PAGE_SIZE)

        return [
            {
                "symbol": row.symbol,
                "name": row.asset_name or row.symbol,
                "verdict": row.verdict,
                "presentation": VERDICT_PRESENTATION.get(
                    row.verdict, VERDICT_PRESENTATION[EvidenceVerdict.NOT_ENOUGH_DATA.value]
                ),
                "documents_read": row.documents_read,
                "primary_documents_read": row.primary_documents_read,
                "decided_at": row.decided_at,
            }
            for row in await self.session.execute(query)
        ]

    async def detail(self, symbol: str) -> dict[str, Any] | None:
        """One coin in full: every reason, its quotation, and every page read.

        This is the only place the heavy columns are loaded, and it loads them for one
        coin at a time.
        """

        run = await self.session.scalar(
            select(AutomatedScreenRun).where(
                AutomatedScreenRun.symbol == symbol.strip().upper()
            )
        )
        if run is None:
            return None
        documents = await self.session.scalars(
            select(CoinEvidenceDocument)
            .where(CoinEvidenceDocument.symbol == run.symbol)
            .order_by(
                CoinEvidenceDocument.is_primary.desc(), CoinEvidenceDocument.url
            )
        )
        return {
            "symbol": run.symbol,
            "name": run.asset_name or run.symbol,
            "verdict": run.verdict,
            "presentation": VERDICT_PRESENTATION.get(
                run.verdict, VERDICT_PRESENTATION[EvidenceVerdict.NOT_ENOUGH_DATA.value]
            ),
            "reasons": list(run.reasons or []),
            "open_questions": list(run.open_questions or []),
            "holder_return_basis": run.holder_return_basis,
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
            "decided_at": run.decided_at,
            "disclosure": AUTOMATED_DISCLOSURE,
        }


__all__ = [
    "PAGE_SIZE",
    "VERDICT_ORDER",
    "VERDICT_PRESENTATION",
    "AutomatedResearchReader",
]

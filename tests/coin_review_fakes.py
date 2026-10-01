"""Stand-ins for the network in the new-coin review pipeline: provider, crawler, model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.coin_evidence_crawler import (
    EvidenceDocument,
    EvidenceFolder,
)
from ai_market_monitor.services.coin_terms_ai_review import (
    AIReview,
    CoinTermsAIReviewer,
    GroundedClaim,
)
from ai_market_monitor.services.coinmarketcap import CoinLinks
from ai_market_monitor.services.sharia_conditions import blocking_activities
from ai_market_monitor.services.sharia_source_catalog import WEBSITE

SITE = "https://newx.example"
TEXT = "New X runs its own blockchain network where validators secure every block."
BLOCKED = sorted(blocking_activities(), key=lambda a: a.value)[0]


@dataclass
class _Usage:
    credits: int = 1


class FakeProvider:
    def __init__(self) -> None:
        self.usage = _Usage()

    async def coin_links(self, symbols):
        return {
            s: CoinLinks(symbol=s, cmc_id=7, name=f"Coin {s}", website=(SITE,))
            for s in symbols
        }


class FakeCrawler:
    async def gather(self, symbol, *, website=None, provider_links=None):
        now = datetime.now(UTC)
        return EvidenceFolder(
            symbol=symbol,
            documents=[
                EvidenceDocument(
                    url=f"{SITE}/{n}", category=WEBSITE, title="About", text=TEXT, fetched_at=now
                )
                for n in ("about", "network")
            ],
        )

    async def aclose(self):
        return None


class FakeAI(CoinTermsAIReviewer):
    """Answers with a fixed review instead of asking a model.

    Everything except the model call is the real reviewer's — the spending ledger row
    in particular — so a test of the pipeline also tests what it records.
    """

    def __init__(self, review: AIReview, settings: Settings) -> None:
        super().__init__(settings)
        self.result = review
        self.calls = 0

    async def review(self, *, symbol, name, folder, record):
        self.calls += 1
        return self.result


def held_review() -> AIReview:
    return AIReview(
        state="completed",
        model="muse-spark-1.3-contributor",
        reasoning_effort="high",
        claims=[GroundedClaim(BLOCKED, TEXT, f"{SITE}/about", True)],
        doubts=["The team is not named anywhere."],
        trust_points=["The code is open source."],
        usage={"input_tokens": 1000, "output_tokens": 300},
    )

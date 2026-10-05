"""A Passport for every coin a Hilal Markets reviewer decides, approved or not.

Two kinds of review end in System Brain, and before this module only one of them could
leave anything public:

* a **full review** against an outside authority's ruling. Approving it publishes the
  authority-backed Passport through ``ShariaGovernanceService.publish_approved``, and
  nothing here touches that path. Rejecting it used to leave nothing at all;
* a **new-coin review** of a coin no authority has ruled on. Its "release" left the coin
  with no status and no page.

The owner asked for one rule: every coin a reviewer decides has a Passport that says what
the reviewer decided and why. A Hilal Markets reviewer is not an outside Shariah
authority, so the result is written under the standard that is Hilal Markets' own — the
Hilal Markets Methodology (``services/hilal_methodology.py``) — and it says, on the row
itself, that a Hilal Markets reviewer checked it and that it is not an outside authority's
ruling.

**What it writes.** One :class:`AssetShariaAssessment` under that standard, superseding the
coin's current one, with:

* the status from :data:`STATUS_FOR` — the same two words the standard already uses for
  a coin it admits or refuses. An approval is never plain *Eligible*;
* the reviewer's confirmed public reasons, and the decision's id, so the Passport can
  show the decision record (``sharia_passports._reviewer_decision``);
* the pages the decision rested on, as evidence sources.

**What it refuses.** It fails closed rather than writing a weaker claim:

* an approval with no page to cite is refused — a status with no source is an assertion;
* an approval of a meme coin is refused — this standard does not cover meme coins;
* a refusal of a coin the Malaysian regulator publishes as compliant is not written —
  this standard's own rule is that the regulator is a floor it may not fall below. The
  rejection itself is still stored; only the Passport is left alone, and the caller says
  so.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    CoinEvidenceDocument,
    ReviewDecision,
    ShariaEvidenceSource,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.services.hilal_methodology import (
    METHODOLOGY_SYSTEM_CODE,
    METHODOLOGY_VERSION,
    Admission,
    admitted_by,
    ensure_methodology,
    is_reviewer_checked,
)
from ai_market_monitor.services.meme_coins import meme_finding

#: Said on every reviewer-checked row, so no surface can drop it.
REVIEWER_QUALIFICATION = (
    "Checked by a Hilal Markets reviewer under the Hilal Markets Methodology. This is "
    "not a ruling from an outside Shariah authority."
)

#: The two answers a reviewer gives, as the status words this standard already uses.
STATUS_FOR: dict[bool, ShariaAssetStatus] = {
    True: ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
    False: ShariaAssetStatus.EXCLUDED,
}

#: How many pages are cited on one Passport. The project's own pages come first.
MAX_SOURCES = 12


class ReviewerPassportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CitedPage:
    url: str
    title: str
    category: str
    own: bool
    read_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewerPassportResult:
    assessment: AssetShariaAssessment | None
    #: Why no Passport was written, in one plain sentence, when none was.
    skipped_reason: str = ""


async def coin_pages(session: AsyncSession, symbol: str) -> list[CitedPage]:
    """The pages read for this coin that actually loaded, its own pages first."""

    rows = (
        await session.scalars(
            select(CoinEvidenceDocument)
            .where(
                CoinEvidenceDocument.symbol == symbol,
                CoinEvidenceDocument.failure_code.is_(None),
            )
            .order_by(CoinEvidenceDocument.is_primary.desc(), CoinEvidenceDocument.url)
            .limit(MAX_SOURCES)
        )
    ).all()
    return [
        CitedPage(
            url=row.url,
            title=row.title or row.url,
            category=row.category or "website",
            own=row.is_primary,
            read_at=row.fetched_at,
        )
        for row in rows
    ]


def _summary(name: str, symbol: str, approved: bool, reasons: list[str]) -> str:
    lead = (
        f"A Hilal Markets reviewer checked {name} ({symbol}) and approved it under the "
        "Hilal Markets Methodology."
        if approved
        else f"A Hilal Markets reviewer checked {name} ({symbol}) and did not approve it "
        "under the Hilal Markets Methodology."
    )
    # The qualification is not repeated here: it is on the row's own qualifications,
    # which every Passport prints in its own box.
    because = f" Reasons: {' '.join(reasons)}" if reasons else ""
    return f"{lead}{because}"


async def write_reviewer_passport(
    session: AsyncSession,
    *,
    symbol: str,
    name: str,
    approved: bool,
    decision: ReviewDecision,
    reviewer_label: str,
    pages: list[CitedPage],
) -> ReviewerPassportResult:
    """Write the coin's Passport for one reviewer decision. See the module note."""

    symbol = (symbol or "").strip().upper()
    name = (name or symbol).strip() or symbol
    if not symbol:
        raise ReviewerPassportError(
            "reviewer_passport_symbol_missing",
            "This case is not linked to a coin, so no Passport can be written for it.",
        )
    if approved and meme_finding(symbol) is not None:
        raise ReviewerPassportError(
            "reviewer_passport_meme_coin",
            f"{symbol} is a meme coin. The Hilal Markets Methodology does not cover meme "
            "coins, so it cannot be approved.",
        )
    if approved and not pages:
        raise ReviewerPassportError(
            "reviewer_passport_no_source",
            f"No page about {symbol} could be read, so an approval would cite nothing. "
            "Ask for evidence first.",
        )
    if not approved and symbol in {item.symbol for item in admitted_by(Admission.REGULATOR_FLOOR)}:
        return ReviewerPassportResult(
            assessment=None,
            skipped_reason=(
                f"No Passport was written: the Malaysian regulator publishes {symbol} as "
                "Shariah-compliant, and the Hilal Markets Methodology may not refuse it."
            ),
        )
    if not pages:
        return ReviewerPassportResult(
            assessment=None,
            skipped_reason=(
                f"No Passport was written: no page about {symbol} could be read, so it "
                "would cite nothing."
            ),
        )

    methodology, _created = await ensure_methodology(session)
    now = datetime.now(UTC)
    current = await session.scalar(
        select(AssetShariaAssessment)
        .where(
            AssetShariaAssessment.methodology_id == methodology.id,
            AssetShariaAssessment.canonical_asset == symbol,
            AssetShariaAssessment.valid_until.is_(None),
        )
        .order_by(AssetShariaAssessment.valid_from.desc())
        .limit(1)
    )
    if current is not None:
        current.valid_until = now

    reasons = list(decision.public_reasons or [])
    status = STATUS_FOR[approved]
    assessment = AssetShariaAssessment(
        canonical_asset=symbol,
        asset_name=name[:160],
        methodology_id=methodology.id,
        status=status,
        summary=_summary(name, symbol, approved, reasons),
        qualifications=[REVIEWER_QUALIFICATION],
        exclusion_reasons=(
            [] if approved else [{"code": "reviewer_decision", "reason": item} for item in reasons]
        ),
        evidence_snapshot={
            "methodology": METHODOLOGY_SYSTEM_CODE,
            "methodology_version": METHODOLOGY_VERSION,
            "human_reviewed": True,
            "review_decision_id": str(decision.id),
            "review_case_id": str(decision.review_case_id),
            "decision": "approved" if approved else "not_approved",
            "reasons": reasons,
            "pages_read": len(pages),
            "primary_pages_read": sum(1 for page in pages if page.own),
            # Never equal to a file admission's fingerprint, so the file's publisher can
            # tell this row from its own and leaves it alone.
            "fingerprint": f"reviewer:{decision.id}",
        },
        reviewed_by=reviewer_label[:240],
        reviewed_by_user_id=decision.admin_user_id,
        reviewed_at=now,
        valid_from=now,
        valid_until=None,
        supersedes_assessment_id=current.id if current is not None else None,
    )
    session.add(assessment)
    await session.flush()
    for page in pages[:MAX_SOURCES]:
        session.add(
            ShariaEvidenceSource(
                assessment_id=assessment.id,
                source_type="project_page" if page.own else "official_external_reference",
                title=page.title[:300],
                publisher=(name if page.own else urlsplit(page.url).hostname or "Web page")[:200],
                source_url=page.url[:1000],
                published_at=None,
                retrieved_at=page.read_at or now,
                evidence_category="project_own_pages" if page.own else page.category[:80],
                evidence_summary=(
                    f"Read while reviewing {symbol}. The reviewer's decision rests on the "
                    "pages listed here."
                ),
                source_hash=hashlib.sha256(f"{assessment.id}:{page.url}".encode()).hexdigest(),
            )
        )
    await session.flush()
    return ReviewerPassportResult(assessment=assessment)


async def reviewer_assessment_for(
    session: AsyncSession, decision: ReviewDecision
) -> AssetShariaAssessment | None:
    """The live Passport row one reviewer decision wrote, if it is still live."""

    rows = (
        await session.scalars(
            select(AssetShariaAssessment).where(
                AssetShariaAssessment.reviewed_by_user_id == decision.admin_user_id,
                AssetShariaAssessment.valid_until.is_(None),
            )
        )
    ).all()
    wanted = str(decision.id)
    return next(
        (
            row
            for row in rows
            if str((row.evidence_snapshot or {}).get("review_decision_id") or "") == wanted
        ),
        None,
    )


async def retract_reviewer_passport(
    session: AsyncSession, decision: ReviewDecision
) -> bool:
    """Take down the Passport one decision wrote, and bring back the one it replaced.

    The way back from a decision that was undone. Nothing is deleted: the retracted row
    keeps its dates, so the history still shows it was live and for how long.
    """

    row = await reviewer_assessment_for(session, decision)
    if row is None:
        return False
    now = datetime.now(UTC)
    row.valid_until = now
    if row.supersedes_assessment_id is not None:
        previous = await session.get(AssetShariaAssessment, row.supersedes_assessment_id)
        if previous is not None and previous.valid_until is not None:
            previous.valid_until = None
    await session.flush()
    return True


def audit_payload(result: ReviewerPassportResult) -> dict[str, Any]:
    return {
        "public_assessment_created": result.assessment is not None,
        "public_assessment_id": str(result.assessment.id) if result.assessment else None,
        "passport_skipped_reason": result.skipped_reason or None,
    }


__all__ = [
    "REVIEWER_QUALIFICATION",
    "STATUS_FOR",
    "CitedPage",
    "ReviewerPassportError",
    "ReviewerPassportResult",
    "audit_payload",
    "coin_pages",
    "is_reviewer_checked",
    "retract_reviewer_passport",
    "reviewer_assessment_for",
    "write_reviewer_passport",
]

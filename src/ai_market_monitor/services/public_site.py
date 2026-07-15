from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.site_content import SHARIA_STATUS_PRESENTATION
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ShariaEvidenceSource,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus


class PublicSiteReadService:
    """Build bounded, public-safe read models from approved screening records."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def active_methodology(self) -> ShariaMethodology | None:
        query = select(ShariaMethodology).where(
            ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE
        )
        if self.settings.sharia_default_methodology_code:
            query = query.where(
                ShariaMethodology.code == self.settings.sharia_default_methodology_code
            )
        return await self.session.scalar(
            query.order_by(
                ShariaMethodology.effective_from.desc(),
                ShariaMethodology.published_at.desc(),
                ShariaMethodology.created_at.desc(),
            ).limit(1)
        )

    async def screened_market_preview(self, *, limit: int = 3) -> dict[str, Any]:
        limit = max(1, min(limit, 6))
        methodology = await self.active_methodology()
        if methodology is None:
            return {
                "status": "unavailable",
                "reason": "No active screening methodology is published.",
                "methodology": None,
                "assets": [],
                "eligible_count": 0,
            }

        now = datetime.now(UTC)
        allowed_statuses = (
            ShariaAssetStatus.ELIGIBLE,
            ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
        )
        assessments = (
            await self.session.scalars(
                select(AssetShariaAssessment)
                .where(
                    AssetShariaAssessment.methodology_id == methodology.id,
                    AssetShariaAssessment.status.in_(allowed_statuses),
                    AssetShariaAssessment.valid_from <= now,
                    or_(
                        AssetShariaAssessment.valid_until.is_(None),
                        AssetShariaAssessment.valid_until > now,
                    ),
                )
                .order_by(
                    AssetShariaAssessment.canonical_asset,
                    AssetShariaAssessment.reviewed_at.desc(),
                )
                .limit(200)
            )
        ).all()

        current_by_asset: dict[str, AssetShariaAssessment] = {}
        for assessment in assessments:
            current_by_asset.setdefault(assessment.canonical_asset, assessment)
        current = list(current_by_asset.values())
        current.sort(key=lambda item: item.reviewed_at, reverse=True)
        selected = current[:limit]

        evidence_counts: dict[Any, int] = {}
        if selected:
            evidence_counts = {
                assessment_id: int(count)
                for assessment_id, count in (
                    await self.session.execute(
                        select(
                            ShariaEvidenceSource.assessment_id,
                            func.count(ShariaEvidenceSource.id),
                        )
                        .where(
                            ShariaEvidenceSource.assessment_id.in_(
                                [item.id for item in selected]
                            )
                        )
                        .group_by(ShariaEvidenceSource.assessment_id)
                    )
                ).all()
            }

        assets = []
        for assessment in selected:
            status = SHARIA_STATUS_PRESENTATION[assessment.status.value]
            assets.append(
                {
                    "assessment_id": assessment.id,
                    "symbol": assessment.canonical_asset,
                    "name": assessment.asset_name or assessment.canonical_asset,
                    "status": assessment.status.value,
                    "status_label": status["label"],
                    "status_badge": status["badge"],
                    "summary": assessment.summary,
                    "qualifications": tuple(assessment.qualifications[:3]),
                    "reviewed_at": assessment.reviewed_at,
                    "methodology_name": methodology.name,
                    "methodology_code": methodology.code,
                    "methodology_version": methodology.version,
                    "evidence_count": evidence_counts.get(assessment.id, 0),
                    "readiness": None,
                    "direction": None,
                    "opportunity_type": None,
                    "present_conditions": (),
                    "missing_requirement": None,
                }
            )

        return {
            "status": "ready" if assets else "empty",
            "reason": None if assets else "No current eligible assessments are published.",
            "methodology": self.methodology_view(methodology),
            "assets": assets,
            "eligible_count": len(current),
        }

    @staticmethod
    def methodology_view(methodology: ShariaMethodology) -> dict[str, Any]:
        return {
            "id": methodology.id,
            "code": methodology.code,
            "name": methodology.name,
            "version": methodology.version,
            "description": methodology.description,
            "governing_body": methodology.governing_body,
            "reviewer_group": methodology.reviewer_group,
            "published_at": methodology.published_at,
            "effective_from": methodology.effective_from,
            "screening_areas": tuple(
                str(item)
                for item in methodology.rules_json.get("screening_areas", [])[:8]
            ),
        }

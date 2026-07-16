import asyncio
import difflib
import hashlib
import json
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from scrapling.parser import Selector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AIAnalysisSnapshot,
    AIUsageEvent,
    AssetEvidenceRecord,
    AssetResearchDossier,
    CanonicalAsset,
    ExternalAssessment,
    OfficialSource,
    ReviewCase,
    ShariaMonitoringRun,
    SourceSnapshot,
)
from ai_market_monitor.services.system_brain import estimate_usage_cost

SCRAPER_VERSION = "scrapling-evidence-v1"
AI_PROMPT_VERSION = "sharia-factual-dossier-v1"


class ShariaResearchError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=2, max_length=80)
    finding: str = Field(min_length=2, max_length=1000)


class FactualInformationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_identity: str = Field(min_length=2, max_length=2000)
    primary_activity: str = Field(min_length=2, max_length=3000)
    token_role: str = Field(min_length=2, max_length=3000)
    staking: str = Field(min_length=2, max_length=2000)
    lending_and_yield: str = Field(min_length=2, max_length=2000)
    derivatives: str = Field(min_length=2, max_length=2000)
    treasury_and_governance: str = Field(min_length=2, max_length=2000)
    tokenomics_and_backing: str = Field(min_length=2, max_length=2000)


class ShariaFactualAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_identity_conclusion: Literal["confirmed", "uncertain", "conflict"]
    profile: FactualInformationProfile
    relevant_activity_categories: list[str] = Field(max_length=40)
    evidence_references: list[EvidenceReference] = Field(max_length=80)
    missing_evidence: list[str] = Field(max_length=40)
    contradictions: list[str] = Field(max_length=40)
    change_type: Literal[
        "initial_research", "no_material_change", "information_update", "potential_material_change"
    ]
    potential_impact_severity: Literal["none", "low", "medium", "high", "critical"]
    potentially_affected_methodology_areas: list[str] = Field(max_length=30)
    human_review_required: bool
    human_review_reason: str = Field(min_length=2, max_length=3000)
    recommended_next_action: Literal[
        "human_review", "request_more_evidence", "record_information_update", "no_action"
    ]
    confidence: float = Field(ge=0, le=1)
    explicit_limitations: list[str] = Field(max_length=40)


@dataclass(frozen=True, slots=True)
class AIAnalysisResult:
    analysis: ShariaFactualAnalysis
    usage: dict[str, Any]
    returned_service_tier: str | None
    retry_count: int


@dataclass(frozen=True, slots=True)
class ResearchResult:
    dossier_id: str
    case_id: str | None
    source_count: int
    failed_source_count: int
    ai_status: str
    idempotent_replay: bool


class OfficialEvidenceFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._robots: dict[str, RobotFileParser | None] = {}

    async def fetch(self, source: OfficialSource) -> tuple[str, dict[str, str], int]:
        await self._assert_robots(source.source_url)
        await asyncio.sleep(self.settings.sharia_scraper_download_delay_seconds)
        async with httpx.AsyncClient(
            timeout=90,
            follow_redirects=True,
            transport=self.transport,
            headers={"User-Agent": "HilalMarketsEvidenceBot/1.0 (+compliance research)"},
        ) as client:
            response = await client.get(source.source_url)
        if response.status_code >= 400:
            raise ShariaResearchError(
                "official_source_fetch_failed",
                f"Official source returned HTTP {response.status_code}.",
                retryable=response.status_code in {408, 429} or response.status_code >= 500,
            )
        return (
            response.text,
            {key.casefold(): value for key, value in response.headers.items()},
            response.status_code,
        )

    async def _assert_robots(self, url: str) -> None:
        if not self.settings.sharia_scraper_obey_robots:
            return
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            robots_url = f"{origin}/robots.txt"
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                transport=self.transport,
                headers={"User-Agent": "HilalMarketsEvidenceBot/1.0 (+compliance research)"},
            ) as client:
                response = await client.get(robots_url)
            if response.status_code == 404:
                self._robots[origin] = None
            elif response.status_code >= 400:
                raise ShariaResearchError(
                    "robots_unavailable",
                    "The official source robots policy could not be verified.",
                )
            else:
                parser = RobotFileParser(robots_url)
                parser.parse(response.text.splitlines())
                self._robots[origin] = parser
        policy = self._robots[origin]
        if policy is not None and not policy.can_fetch("HilalMarketsEvidenceBot", url):
            raise ShariaResearchError(
                "robots_disallowed", "The official source does not permit automated retrieval."
            )


class ShariaAIResearchClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def analyze(self, evidence_package: dict[str, Any]) -> AIAnalysisResult:
        if self.settings.openai_api_key is None:
            raise ShariaResearchError(
                "openai_key_missing", "Sharia research AI is not configured."
            )
        invalid_output: str | None = None
        total_retries = 0
        for repair in (False, True):
            payload = self._payload(
                evidence_package,
                repair=repair,
                invalid_output=invalid_output,
                service_tier=self.settings.sharia_ai_service_tier,
            )
            response, retries = await self._post(payload)
            total_retries += retries
            output_text = _extract_output_text(response)
            try:
                analysis = ShariaFactualAnalysis.model_validate_json(output_text)
                return AIAnalysisResult(
                    analysis=analysis,
                    usage=dict(response.get("usage") or {}),
                    returned_service_tier=response.get("service_tier"),
                    retry_count=total_retries,
                )
            except (ValidationError, ValueError) as exc:
                if repair:
                    raise ShariaResearchError(
                        "invalid_ai_analysis",
                        "The AI result failed the factual-dossier schema twice.",
                    ) from exc
                invalid_output = output_text[:4000]
        raise AssertionError("unreachable")

    async def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        api_key = self.settings.openai_api_key
        if api_key is None:
            raise ShariaResearchError(
                "openai_key_missing", "Sharia research AI is not configured."
            )
        headers = {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        retry_count = 0
        service_tier = str(payload.get("service_tier") or "default")
        while True:
            try:
                async with httpx.AsyncClient(
                    base_url=str(self.settings.openai_base_url).rstrip("/"),
                    timeout=self.settings.sharia_ai_timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = await client.post("/responses", headers=headers, json=payload)
                if response.status_code < 400:
                    return response.json(), retry_count
                retryable = response.status_code in {408, 429} or response.status_code >= 500
                if not retryable:
                    raise ShariaResearchError(
                        "openai_non_retryable",
                        f"OpenAI returned HTTP {response.status_code}.",
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                retryable = True
                last_exc: Exception = exc
            else:
                status_code = response.status_code
                last_exc = ShariaResearchError(
                    "openai_retryable",
                    f"OpenAI returned HTTP {status_code}.",
                    retryable=True,
                )
            if retry_count >= self.settings.sharia_ai_max_retries:
                if (
                    service_tier == "flex"
                    and self.settings.sharia_ai_allow_standard_fallback
                ):
                    payload = {**payload, "service_tier": "default"}
                    service_tier = "default"
                    retry_count = 0
                    continue
                raise ShariaResearchError(
                    "openai_retry_exhausted",
                    "The queued AI assessment remains unavailable after bounded retries.",
                    retryable=True,
                ) from last_exc
            retry_count += 1
            await asyncio.sleep(min(60, (2**retry_count) + random.random()))

    def _payload(
        self,
        evidence_package: dict[str, Any],
        *,
        repair: bool,
        invalid_output: str | None,
        service_tier: str,
    ) -> dict[str, Any]:
        instructions = (
            "You are HilalMarkets' bounded factual research analyst. Analyze only the supplied "
            "official-source evidence for a crypto spot asset. You do not issue halal or haram "
            "rulings, reconstruct unpublished SC Malaysia reasoning, change an official status, "
            "publish or reject assets, or treat third-party products as part of an asset-level "
            "decision. Cite only supplied snapshot IDs. State missing evidence rather than guess. "
            "Separate native staking, third-party lending or yield, derivatives, and wrapped uses."
        )
        if repair:
            instructions += (
                " Your prior output was schema-invalid. Return one corrected object only; preserve "
                "the evidence and do not add unsupported facts."
            )
        input_value: dict[str, Any] = {"evidence_package": evidence_package}
        if repair and invalid_output:
            input_value["invalid_output_excerpt"] = invalid_output
        return {
            "model": self.settings.sharia_ai_model,
            "store": False,
            "service_tier": service_tier,
            "reasoning": {"effort": self.settings.sharia_ai_reasoning_effort},
            "max_output_tokens": 5000,
            "instructions": instructions,
            "input": json.dumps(input_value, sort_keys=True, default=str),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "sharia_factual_analysis",
                    "strict": True,
                    "schema": ShariaFactualAnalysis.model_json_schema(),
                }
            },
        }


class ShariaResearchPipeline:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        fetcher: OfficialEvidenceFetcher | None = None,
        ai_client: ShariaAIResearchClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.fetcher = fetcher or OfficialEvidenceFetcher(settings)
        self.ai = ai_client or ShariaAIResearchClient(settings)

    async def research_initial_asset(self, external_id) -> ResearchResult:
        external = await self.session.get(ExternalAssessment, external_id)
        if external is None:
            raise ShariaResearchError("external_assessment_missing", "Import draft not found.")
        if external.canonical_asset_id is None or external.mapping_state != "mapped":
            raise ShariaResearchError(
                "canonical_mapping_required", "Verified canonical mapping is required first."
            )
        asset = await self.session.get(CanonicalAsset, external.canonical_asset_id)
        if asset is None or asset.mapping_state != "verified":
            raise ShariaResearchError(
                "canonical_mapping_unverified", "Canonical identity is not verified."
            )
        sources = list(
            (
                await self.session.scalars(
                    select(OfficialSource)
                    .where(
                        OfficialSource.canonical_asset_id == asset.id,
                        OfficialSource.verification_state == "verified",
                        OfficialSource.is_active.is_(True),
                    )
                    .order_by(OfficialSource.priority.asc(), OfficialSource.source_url.asc())
                )
            ).all()
        )
        if not sources:
            raise ShariaResearchError(
                "verified_sources_missing", "No verified official source is registered."
            )
        run_key = f"initial-research:{external.id}:{_source_registry_hash(sources)}"
        existing = await self.session.scalar(
            select(AssetResearchDossier).where(AssetResearchDossier.run_key == run_key)
        )
        if existing is not None:
            case = await self.session.scalar(
                select(ReviewCase).where(ReviewCase.dossier_id == existing.id)
            )
            return ResearchResult(
                dossier_id=str(existing.id),
                case_id=str(case.id) if case else None,
                source_count=len(existing.source_snapshot_ids),
                failed_source_count=0,
                ai_status="completed" if existing.state == "ready" else existing.state,
                idempotent_replay=True,
            )

        now = datetime.now(UTC)
        run = ShariaMonitoringRun(
            run_kind="initial_research",
            canonical_asset_id=asset.id,
            idempotency_key=run_key,
            status="running",
            started_at=now,
            items_attempted=len(sources),
        )
        self.session.add(run)
        await self.session.flush()
        snapshots: list[SourceSnapshot] = []
        failures = 0
        for source in sources:  # Deliberately sequential to bound load and preserve order.
            snapshot = await self._fetch_source(run, source)
            snapshots.append(snapshot)
            if snapshot.fetch_status != "success":
                failures += 1

        successful = [row for row in snapshots if row.fetch_status == "success"]
        evidence_hash = _hash_json(
            [{"id": str(row.id), "hash": row.content_hash} for row in successful]
        )
        dossier = AssetResearchDossier(
            canonical_asset_id=asset.id,
            external_assessment_id=external.id,
            monitoring_run_id=run.id,
            run_key=run_key,
            state="researching",
            source_snapshot_ids=[str(row.id) for row in snapshots],
            evidence_completeness=(len(successful) / len(sources) if sources else 0),
            evidence_package_hash=evidence_hash,
            factual_profile={},
            limitations=[],
        )
        self.session.add(dossier)
        await self.session.flush()
        ai_row = AIAnalysisSnapshot(
            dossier_id=dossier.id,
            analysis_version=1,
            model=self.settings.sharia_ai_model,
            reasoning_effort=self.settings.sharia_ai_reasoning_effort,
            requested_service_tier=self.settings.sharia_ai_service_tier,
            prompt_version=AI_PROMPT_VERSION,
            input_snapshot_ids=[str(row.id) for row in successful],
            input_hash=evidence_hash,
            output={},
            usage={},
            status="running",
            retry_count=0,
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        self.session.add(ai_row)
        await self.session.flush()
        try:
            result = await self.ai.analyze(
                _evidence_package(asset, external, successful)
            )
        except ShariaResearchError as exc:
            ai_row.status = "failed"
            ai_row.error_code = exc.code
            ai_row.error_detail = str(exc)[:2000]
            ai_row.completed_at = datetime.now(UTC)
            dossier.state = "needs_evidence"
            dossier.limitations = [str(exc)]
            run.status = "failed"
            run.items_succeeded = len(successful)
            run.items_failed = failures
            run.last_error_code = exc.code
            run.last_error_detail = str(exc)[:2000]
            run.completed_at = datetime.now(UTC)
            await self.session.flush()
            return ResearchResult(
                dossier_id=str(dossier.id),
                case_id=None,
                source_count=len(successful),
                failed_source_count=failures,
                ai_status="failed",
                idempotent_replay=False,
            )

        analysis = result.analysis
        ai_row.output = analysis.model_dump(mode="json")
        ai_row.usage = result.usage
        ai_row.returned_service_tier = result.returned_service_tier
        ai_row.retry_count = result.retry_count
        ai_row.status = "completed"
        ai_row.completed_at = datetime.now(UTC)
        dossier.state = "ready"
        dossier.factual_profile = analysis.profile.model_dump(mode="json")
        dossier.missing_information_count = len(analysis.missing_evidence)
        dossier.contradiction_count = len(analysis.contradictions)
        dossier.limitations = analysis.explicit_limitations
        dossier.completed_at = datetime.now(UTC)
        await self._store_evidence(dossier, analysis, successful)
        case = await self._create_initial_case(dossier, asset, external, analysis)
        run.status = "completed"
        run.items_succeeded = len(successful)
        run.items_failed = failures
        run.completed_at = datetime.now(UTC)
        run.result_summary = {
            "dossier_id": str(dossier.id),
            "case_id": str(case.id),
            "source_count": len(successful),
            "failed_source_count": failures,
            "one_aggregated_ai_call": True,
        }
        self._record_usage(result)
        await self.session.flush()
        return ResearchResult(
            dossier_id=str(dossier.id),
            case_id=str(case.id),
            source_count=len(successful),
            failed_source_count=failures,
            ai_status="completed",
            idempotent_replay=False,
        )

    async def _fetch_source(
        self, run: ShariaMonitoringRun, source: OfficialSource
    ) -> SourceSnapshot:
        retrieved = datetime.now(UTC)
        previous = await self.session.scalar(
            select(SourceSnapshot)
            .where(
                SourceSnapshot.official_source_id == source.id,
                SourceSnapshot.fetch_status == "success",
            )
            .order_by(SourceSnapshot.retrieved_at.desc())
            .limit(1)
        )
        try:
            html, headers, status = await self.fetcher.fetch(source)
            title, headings, clean_text = _extract_document(html, source.source_url)
            content_hash = _sha256(_material_text(clean_text))
            diff = _meaningful_diff(previous.normalized_text if previous else "", clean_text)
            snapshot = SourceSnapshot(
                monitoring_run_id=run.id,
                official_source_id=source.id,
                previous_snapshot_id=previous.id if previous else None,
                source_url=source.source_url,
                retrieved_at=retrieved,
                http_status=status,
                etag=headers.get("etag"),
                last_modified=headers.get("last-modified"),
                response_headers={
                    key: value
                    for key, value in headers.items()
                    if key in {"content-type", "content-length", "etag", "last-modified"}
                },
                raw_content=html,
                normalized_text=clean_text,
                title=title,
                headings=headings,
                content_hash=content_hash,
                meaningful_diff=diff,
                is_material_change=bool(previous and diff.get("material")),
                fetch_status="success",
                scraper_version=SCRAPER_VERSION,
                parser_result={"adaptive_selector": True, "text_length": len(clean_text)},
            )
        except ShariaResearchError as exc:
            snapshot = SourceSnapshot(
                monitoring_run_id=run.id,
                official_source_id=source.id,
                previous_snapshot_id=previous.id if previous else None,
                source_url=source.source_url,
                retrieved_at=retrieved,
                normalized_text="",
                content_hash=_sha256(f"failure:{source.id}:{retrieved.isoformat()}"),
                meaningful_diff={},
                is_material_change=False,
                fetch_status="failed",
                error_code=exc.code,
                error_detail=str(exc)[:2000],
                scraper_version=SCRAPER_VERSION,
                parser_result={},
            )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def _store_evidence(
        self,
        dossier: AssetResearchDossier,
        analysis: ShariaFactualAnalysis,
        snapshots: list[SourceSnapshot],
    ) -> None:
        allowed = {str(row.id): row for row in snapshots}
        for reference in analysis.evidence_references:
            snapshot = allowed.get(reference.snapshot_id)
            if snapshot is None:
                continue
            evidence_hash = _hash_json(reference.model_dump(mode="json"))
            self.session.add(
                AssetEvidenceRecord(
                    canonical_asset_id=dossier.canonical_asset_id,
                    dossier_id=dossier.id,
                    source_snapshot_id=snapshot.id,
                    category=reference.category,
                    claim_summary=reference.finding,
                    evidence_excerpt=reference.finding,
                    source_locator=snapshot.source_url,
                    evidence_hash=evidence_hash,
                    verified=False,
                )
            )

    async def _create_initial_case(
        self,
        dossier: AssetResearchDossier,
        asset: CanonicalAsset,
        external: ExternalAssessment,
        analysis: ShariaFactualAnalysis,
    ) -> ReviewCase:
        key = f"initial-review:{external.id}:{dossier.evidence_package_hash}"
        existing = await self.session.scalar(
            select(ReviewCase).where(ReviewCase.idempotency_key == key)
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        case = ReviewCase(
            case_reference=f"SC-{asset.symbol}-{str(dossier.id)[:8].upper()}",
            case_type="initial_asset_review",
            state="ready_for_review",
            publication_state="unpublished",
            canonical_asset_id=asset.id,
            external_assessment_id=external.id,
            dossier_id=dossier.id,
            title=f"Initial SC Malaysia review: {asset.name} ({asset.symbol})",
            priority="high" if analysis.human_review_required else "normal",
            risk_severity=analysis.potential_impact_severity,
            human_review_reason=analysis.human_review_reason,
            requested_evidence=analysis.missing_evidence,
            idempotency_key=key,
            due_at=now + timedelta(hours=self.settings.sharia_review_sla_hours),
            source_freshness_deadline=now
            + timedelta(hours=self.settings.sharia_source_scan_interval_hours),
            next_reminder_at=now,
        )
        self.session.add(case)
        await self.session.flush()
        return case

    def _record_usage(self, result: AIAnalysisResult) -> None:
        usage = result.usage
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        self.session.add(
            AIUsageEvent(
                user_id=None,
                chat_session_id=None,
                operation="sharia_factual_dossier",
                provider="openai",
                model=self.settings.sharia_ai_model,
                reasoning_effort=self.settings.sharia_ai_reasoning_effort,
                input_tokens=int(usage.get("input_tokens") or 0),
                cached_input_tokens=int(input_details.get("cached_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
                estimated_cost_usd=estimate_usage_cost(
                    self.settings,
                    model=self.settings.sharia_ai_model,
                    usage=usage,
                    service_tier=result.returned_service_tier
                    or self.settings.sharia_ai_service_tier,
                ),
                pricing_source="configured_from_openai_pricing",
                raw_usage=usage,
                created_at=datetime.now(UTC),
            )
        )


def _extract_document(html: str, url: str) -> tuple[str, list[str], str]:
    document = Selector(html, url=url, adaptive=True)
    title = str(document.css("title::text").get() or "").strip()
    headings = [
        _clean_text(value)
        for value in document.css("h1::text, h2::text, h3::text").getall()
        if _clean_text(value)
    ][:100]
    text = _clean_text(document.get_all_text(separator="\n", strip=True))
    if len(text) < 80:
        raise ShariaResearchError(
            "official_source_text_insufficient",
            "The official page did not expose enough accessible text for evidence review.",
        )
    return title[:500] or url, headings, text[:300_000]


def _material_text(value: str) -> str:
    lines: list[str] = []
    for raw in value.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        normalized = line.casefold()
        if not line or normalized in {"accept cookies", "privacy", "menu", "close"}:
            continue
        if re.fullmatch(r"(?:updated|last updated)?\s*\d{1,2}[:/-]\d{1,2}[:/-]\d{2,4}", normalized):
            continue
        lines.append(line)
    return "\n".join(lines)


def _meaningful_diff(previous: str, current: str) -> dict[str, Any]:
    if not previous:
        return {"material": False, "reason": "initial_snapshot", "added": [], "removed": []}
    old_lines = _material_text(previous).splitlines()
    new_lines = _material_text(current).splitlines()
    if old_lines == new_lines:
        return {"material": False, "reason": "no_substantive_change", "added": [], "removed": []}
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"insert", "replace"}:
            added.extend(new_lines[j1:j2])
        if tag in {"delete", "replace"}:
            removed.extend(old_lines[i1:i2])
    substantive = [line for line in [*added, *removed] if len(line) >= 20]
    return {
        "material": bool(substantive),
        "similarity": round(matcher.ratio(), 5),
        "added": added[:40],
        "removed": removed[:40],
    }


def _evidence_package(
    asset: CanonicalAsset,
    external: ExternalAssessment,
    snapshots: list[SourceSnapshot],
) -> dict[str, Any]:
    return {
        "asset": {
            "canonical_id": str(asset.id),
            "name": asset.name,
            "symbol": asset.symbol,
            "asset_type": asset.asset_type,
            "native_chain": asset.native_chain,
            "contract_addresses": asset.contract_addresses,
            "official_website": asset.official_website,
        },
        "official_sc_reference": {
            "authority": external.source_authority,
            "exact_status_wording": external.exact_status_wording,
            "meeting_number": external.sac_meeting_number,
            "decision_date": external.decision_date.isoformat(),
            "regulatory_scope": external.regulatory_scope,
            "source_url": external.source_url,
            "limitation": (
                "Coin-specific detailed reasoning was not publicly provided by this source."
            ),
        },
        "official_sources": [
            {
                "snapshot_id": str(row.id),
                "url": row.source_url,
                "title": row.title,
                "retrieved_at": row.retrieved_at.isoformat(),
                "content_hash": row.content_hash,
                "text": row.normalized_text[:80_000],
            }
            for row in snapshots
        ],
        "required_boundary": (
            "This is factual information research, not SC Malaysia's unpublished reasoning and "
            "not an independent religious ruling."
        ),
    }


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                return str(content.get("text") or "")
    raise ShariaResearchError(
        "openai_output_missing", "OpenAI did not return a structured analysis."
    )


def _source_registry_hash(sources: list[OfficialSource]) -> str:
    return _hash_json(
        [
            {"id": str(row.id), "url": row.normalized_url, "priority": row.priority}
            for row in sources
        ]
    )


def _clean_text(value: object) -> str:
    return "\n".join(
        line.strip()
        for line in re.sub(r"[ \t\r\f\v]+", " ", str(value or "")).split("\n")
        if line.strip()
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: object) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))

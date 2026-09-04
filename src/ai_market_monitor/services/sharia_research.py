import asyncio
import difflib
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader
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
    ShariaMethodology,
    ShariaMonitoringRun,
    SourceSnapshot,
)
from ai_market_monitor.db.models.enums import ReviewCaseType, ShariaMethodologyStatus
from ai_market_monitor.services import sharia_dossier_state as dossier_state
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_call, provider_request

# The one word that decides what becomes evidence. It is imported rather than typed,
# because a second spelling of it anywhere silently selects nothing.
from ai_market_monitor.services.sharia_source_catalog import VERIFIED
from ai_market_monitor.services.system_brain import estimate_usage_cost

SCRAPER_VERSION = "scrapling-evidence-v1"
AI_PROMPT_VERSION = "sharia-factual-dossier-v2"
_SOURCE_METHODOLOGY_CODES = {
    "sc_malaysia_sac": "SC_MALAYSIA_SAC_REFERENCE",
    "shariah_review_bureau": "SHARIAH_REVIEW_BUREAU",
    "fasset_shariah_reports": "FASSET_SHARIAH_REPORTS",
}


def _circuit_key(url: str) -> str:
    """Which breaker bucket an open-web fetch belongs in: one per host.

    ``official_source`` is not an upstream service. It is whatever website a coin happens
    to publish on, so every call under that name reaches a different company. Counting
    their failures together made the breaker say "the provider is down" when what was
    really true was "five unrelated domains are dead", and it then refused the live sites
    in the same sweep. One owner for this key so the two call sites — the page and its
    ``robots.txt`` — always land in the same bucket for the same host.
    """

    host = urlparse(url).netloc.casefold()
    return f"official_source:{host}" if host else "official_source"


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


class PassportEnrichmentProfile(BaseModel):
    """Strict package-compatible Hilal Markets factual profile."""

    model_config = ConfigDict(extra="forbid")

    canonical_asset_identity: dict[str, Any]
    official_website: str | None = None
    official_documentation: str | None = None
    primary_activity: str | None
    token_role_and_utility: str | None
    asset_type: str | None = None
    native_chain_or_contract: dict[str, Any] | str | None = None
    data_structure: str | None = None
    smart_contract_capability: str | None = None
    transaction_validation: str | None = None
    consensus_mechanism: str | None = None
    governance_model: str | None = None
    tokenomics: str | None = None
    staking_and_rewards: dict[str, Any] | str | None = None
    lending_borrowing: dict[str, Any] | str | None = None
    interest_or_yield: dict[str, Any] | str | None = None
    derivatives_and_prediction_products: dict[str, Any] | str | None = None
    treasury_and_revenue: dict[str, Any] | str | None = None
    backing_redemption_or_collateral: dict[str, Any] | str | None = None
    official_source_registry: list[dict[str, Any]]
    missing_information: list[str]
    contradictions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    plain_language_profile: str
    provenance: Literal["HILALMARKETS_AI_ENRICHMENT_UNVERIFIED"]
    manual_verification_required: Literal[True]


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


@dataclass(frozen=True, slots=True)
class FetchTarget:
    """Any address the evidence fetcher may read.

    The fetcher only ever needed the address, but it was typed to the database row, so
    reading a page that is not yet a registered source meant either inventing a throwaway
    row or writing a second fetcher — and a second fetcher would have its own idea about
    robots, timeouts and PDFs. This is the address on its own.
    """

    source_url: str


class OfficialEvidenceFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        #: One origin's rules: a parser, or ``None`` meaning "no rules, everything is
        #: allowed". Asked once per sweep.
        self._robots: dict[str, RobotFileParser | None] = {}
        #: Origins whose rules could not be read at all this run — the site was down or
        #: rate-limiting. Kept apart from ``_robots`` on purpose: "there are no rules" and
        #: "we could not find out what the rules are" are opposite answers, and storing
        #: them in one place is how the second quietly became the first.
        self._unreadable_robots: set[str] = set()
        self._response_cache: dict[
            str,
            tuple[str | bytes, dict[str, str], int],
        ] = {}

    async def fetch(
        self,
        source: OfficialSource | FetchTarget,
    ) -> tuple[str | bytes, dict[str, str], int]:
        cache_key = source.source_url.strip()
        cached = self._response_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            await self._assert_robots(source.source_url)
            await asyncio.sleep(
                self.settings.sharia_scraper_download_delay_seconds
            )
            response = await provider_request(
                self.settings,
                "GET",
                source.source_url,
                provider="official_source",
                operation="fetch_evidence",
                timeout=90,
                # One breaker bucket per site. "official_source" is not an upstream, it is
                # every project's website in turn, and counting them together let a handful
                # of dead domains stop the sweep reading live ones.
                circuit_key=_circuit_key(source.source_url),
                # Reading a public document changes nothing on the far side.
                mutation_committed=False,
                transport=self.transport,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "HilalMarketsEvidenceBot/1.0 (+compliance research)"
                    )
                },
            )
        except ShariaResearchError:
            raise
        except (httpx.HTTPError, ProviderCallError) as exc:
            raise ShariaResearchError(
                "official_source_unavailable",
                "The official source could not be reached securely.",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise ShariaResearchError(
                "official_source_fetch_failed",
                f"Official source returned HTTP {response.status_code}.",
                retryable=response.status_code in {408, 429} or response.status_code >= 500,
            )
        response_headers = {
            key.casefold(): value for key, value in response.headers.items()
        }
        content_type = response_headers.get("content-type", "").casefold()
        body: str | bytes = (
            response.content
            if "application/pdf" in content_type
            or response.content.lstrip().startswith(b"%PDF")
            else response.text
        )
        result = (body, response_headers, response.status_code)
        self._response_cache[cache_key] = result
        return result

    async def _assert_robots(self, url: str) -> None:
        """Refuse an address the site's own rules say we may not read.

        **What each answer from ``robots.txt`` means** is the robots standard's own
        rule (RFC 9309, section 2.3.1), not this product's invention, and it is written
        out here because getting it wrong silently removes a whole website:

        =====================  ====================================================
        ``robots.txt`` answers  What it means
        =====================  ====================================================
        200                    Read the rules and obey them.
        any other 4xx          There are no rules. Everything on the site is allowed.
        429 or 5xx             We could not find out. Do not read anything this run.
        could not be reached   The same: we could not find out.
        =====================  ====================================================

        The middle row is the one that was wrong until 1 September 2026. Every 4xx
        except 404 was treated as "we could not verify the policy", which refused every
        address on that origin. A site behind a bot filter — which answers **403** to a
        plain ``robots.txt`` request from a datacentre address, and Cloudflare does this
        by default — therefore had every one of its pages refused, for ever, while the
        pages themselves were perfectly readable. The review case then said the project
        had no news page, and a person was asked to find one that already existed.

        Refusing on a 403 is not the cautious reading of the standard; it is a different
        rule. A 403 on ``robots.txt`` says nothing whatever about what a site permits.

        **A temporary failure is cached for this run only.** Both the answer and the
        "could not find out" are remembered per origin, so a sweep asks a site for its
        rules once instead of once per address. The cache lives as long as the fetcher,
        which is one sweep, so a site having a bad hour is retried on the next one.

        **Our own outage is never written down as the site's answer.** A request the
        breaker refused to send was never made, so it says nothing at all about that
        origin — and caching it would then refuse every address on that host for the rest
        of the sweep on the strength of a failure somewhere else entirely. That is how
        ``github.com``, which has served ``robots.txt`` to everyone for fifteen years,
        came to be reported as a site that "would not say what it allows". It is raised
        under its own code so the sentence a reviewer reads names the real cause.
        """

        if not self.settings.sharia_scraper_obey_robots:
            return
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._unreadable_robots:
            raise ShariaResearchError(
                "robots_unavailable",
                "The official source robots policy could not be read.",
                retryable=True,
            )
        if origin not in self._robots:
            robots_url = f"{origin}/robots.txt"
            try:
                response = await provider_request(
                    self.settings,
                    "GET",
                    robots_url,
                    provider="official_source",
                    operation="robots",
                    timeout=20,
                    circuit_key=_circuit_key(robots_url),
                    mutation_committed=False,
                    transport=self.transport,
                    follow_redirects=True,
                    headers={
                        "User-Agent": "HilalMarketsEvidenceBot/1.0 (+compliance research)"
                    },
                )
            except ProviderCallError as exc:
                if exc.circuit_open:
                    # We never sent it. Not an answer, not this origin's fault, and not
                    # cached: the next address on this host asks again.
                    raise ShariaResearchError(
                        "robots_not_asked",
                        "This site was not asked for its rules on this run.",
                        retryable=True,
                    ) from exc
                self._unreadable_robots.add(origin)
                raise ShariaResearchError(
                    "robots_unavailable",
                    "The official source robots policy could not be read.",
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                # Could not ask. That is not permission, so nothing on this origin is
                # read this run — but it is remembered so the whole sweep asks once.
                self._unreadable_robots.add(origin)
                raise ShariaResearchError(
                    "robots_unavailable",
                    "The official source robots policy could not be read.",
                    retryable=True,
                ) from exc
            status = response.status_code
            if status == 429 or status >= 500:
                self._unreadable_robots.add(origin)
                raise ShariaResearchError(
                    "robots_unavailable",
                    "The official source robots policy could not be read.",
                    retryable=True,
                )
            if status >= 400:
                # No rules published, or none this client is allowed to see. Either way
                # the standard says the site is open. See the table above.
                self._robots[origin] = None
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
        # Retrying an HTTP call is not this module's decision. It used to keep its own
        # status list, its own backoff and its own Retry-After reading, all of which had
        # already drifted from the shared matrix. What is genuinely local is the *service
        # tier* fallback below: dropping from "flex" to "default" is a different request,
        # not another attempt at the same one.
        attempts_used = 0
        service_tier = str(payload.get("service_tier") or "default")
        while True:
            try:
                outcome = await provider_call(
                    self.settings,
                    "POST",
                    f"{str(self.settings.openai_base_url).rstrip('/')}/responses",
                    provider="openai",
                    operation="sharia_research",
                    model=str(payload.get("model") or ""),
                    timeout=self.settings.sharia_ai_timeout_seconds,
                    mutation_committed=False,
                    transport=self.transport,
                    headers=headers,
                    json=payload,
                )
            except ProviderCallError as exc:
                attempts_used += max(0, len(exc.attempts) - 1)
                status_code = exc.status
                if status_code is not None and not (
                    status_code in {408, 429} or status_code >= 500
                ):
                    raise ShariaResearchError(
                        "openai_non_retryable",
                        f"OpenAI returned HTTP {status_code}.",
                    ) from exc
                last_exc: Exception = exc
            else:
                response = outcome.response
                if response is not None and response.status_code < 400:
                    return response.json(), attempts_used + outcome.attempt_count - 1
                status_code = response.status_code if response is not None else 0
                attempts_used += max(0, outcome.attempt_count - 1)
                last_exc = ShariaResearchError(
                    "openai_retryable",
                    f"OpenAI returned HTTP {status_code}.",
                    retryable=True,
                )
            if service_tier == "flex" and self.settings.sharia_ai_allow_standard_fallback:
                payload = {**payload, "service_tier": "default"}
                service_tier = "default"
                continue
            raise ShariaResearchError(
                "openai_retry_exhausted",
                "The queued AI assessment remains unavailable after bounded retries.",
                retryable=True,
            ) from last_exc

    def _payload(
        self,
        evidence_package: dict[str, Any],
        *,
        repair: bool,
        invalid_output: str | None,
        service_tier: str,
    ) -> dict[str, Any]:
        instructions = (
            "You are Hilal Markets' bounded factual research analyst. Analyze only the supplied "
            "official-source evidence for a crypto spot asset. You do not issue halal or haram "
            "rulings, reconstruct unpublished authority reasoning, change an external verdict, "
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
                        OfficialSource.verification_state == VERIFIED,
                        OfficialSource.is_active.is_(True),
                    )
                    .order_by(OfficialSource.priority.asc(), OfficialSource.source_url.asc())
                )
            ).all()
        )
        external_snapshot = await self.session.get(
            SourceSnapshot, external.source_snapshot_id
        )
        if external_snapshot is None or external_snapshot.fetch_status != "success":
            raise ShariaResearchError(
                "external_source_snapshot_missing",
                "The retained authority snapshot is unavailable.",
            )
        if not sources and not (
            external.source_family == "fasset_shariah_reports"
            and external.structured_facts
        ):
            raise ShariaResearchError(
                "verified_sources_missing", "No verified official source is registered."
            )
        run_key = _initial_research_run_key(
            external.id,
            external_snapshot.content_hash,
            sources,
        )
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
                ai_status=(
                    "completed"
                    if dossier_state.is_complete(existing.state)
                    else existing.state
                ),
                idempotent_replay=True,
            )

        now = datetime.now(UTC)
        run = await self.session.scalar(
            select(ShariaMonitoringRun).where(
                ShariaMonitoringRun.idempotency_key == run_key
            )
        )
        if run is None:
            run = ShariaMonitoringRun(
                run_kind="initial_research",
                canonical_asset_id=asset.id,
                idempotency_key=run_key,
                status="running",
                started_at=now,
                items_attempted=len(sources) + 1,
            )
            self.session.add(run)
        else:
            run.run_kind = "initial_research"
            run.canonical_asset_id = asset.id
            run.status = "running"
            run.started_at = now
            run.completed_at = None
            run.items_attempted = len(sources) + 1
            run.items_succeeded = 0
            run.items_failed = 0
            run.result_summary = {}
            run.last_error_code = None
            run.last_error_detail = None
        await self.session.flush()
        snapshots: list[SourceSnapshot] = [external_snapshot]
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
            state=dossier_state.RESEARCHING,
            source_snapshot_ids=[str(row.id) for row in snapshots],
            evidence_completeness=len(successful) / (len(sources) + 1),
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
            dossier.state = dossier_state.NEEDS_EVIDENCE
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
        dossier.state = dossier_state.COMPLETE
        dossier.factual_profile = (
            _passport_enrichment_profile(asset, analysis, successful).model_dump(
                mode="json"
            )
            if external.enrichment_task_id
            else analysis.profile.model_dump(mode="json")
        )
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
            body, headers, status = await self.fetcher.fetch(source)
            title, headings, clean_text = extract_document(
                body,
                source.source_url,
                content_type=headers.get("content-type"),
            )
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
                raw_content=(
                    _database_safe_text(body)
                    if isinstance(body, str)
                    else clean_text
                ),
                normalized_text=clean_text,
                title=title,
                headings=headings,
                content_hash=content_hash,
                meaningful_diff=diff,
                is_material_change=bool(previous and diff.get("material")),
                fetch_status="success",
                scraper_version=SCRAPER_VERSION,
                parser_result={
                    "document_type": (
                        "pdf" if isinstance(body, bytes) else "html"
                    ),
                    "adaptive_selector": isinstance(body, str),
                    "text_length": len(clean_text),
                },
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
        open_case = await self.session.scalar(
            select(ReviewCase)
            .where(
                ReviewCase.external_assessment_id == external.id,
                ReviewCase.done_at.is_(None),
            )
            .order_by(ReviewCase.created_at.desc())
            .limit(1)
        )
        if open_case is not None:
            open_case.canonical_asset_id = asset.id
            open_case.dossier_id = dossier.id
            open_case.methodology_id = (
                external.methodology_id
                or await self._methodology_id_for_external(external)
            )
            open_case.state = "ready_for_review"
            open_case.priority = "high" if analysis.human_review_required else "normal"
            open_case.risk_severity = analysis.potential_impact_severity
            open_case.human_review_reason = analysis.human_review_reason
            open_case.requested_evidence = analysis.missing_evidence
            open_case.source_freshness_deadline = datetime.now(UTC) + timedelta(
                hours=self.settings.sharia_source_scan_interval_hours
            )
            await self.session.flush()
            await self._settle_review_readiness(open_case)
            return open_case

        key = f"initial-review:{external.id}:{dossier.evidence_package_hash}"
        existing = await self.session.scalar(
            select(ReviewCase).where(ReviewCase.idempotency_key == key)
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        source_name = {
            "fasset_shariah_reports": "Fasset",
            "shariah_review_bureau": "Shariah Review Bureau",
            "sc_malaysia_sac": "SC Malaysia",
        }.get(external.source_family, "external methodology")
        case = ReviewCase(
            case_reference=(
                f"{'FAS' if external.source_family == 'fasset_shariah_reports' else 'SC'}-"
                f"{asset.symbol}-{str(dossier.id)[:8].upper()}"
            ),
            case_type=ReviewCaseType.INITIAL_ASSET_REVIEW,
            state="ready_for_review",
            publication_state="unpublished",
            canonical_asset_id=asset.id,
            external_assessment_id=external.id,
            dossier_id=dossier.id,
            methodology_id=await self._methodology_id_for_external(external),
            title=f"Initial {source_name} review: {asset.name} ({asset.symbol})",
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
        await self._settle_review_readiness(case)
        return case

    async def _settle_review_readiness(self, case: ReviewCase) -> None:
        """Only call a case ready for review when the approval would accept it.

        Research finishing is not the same as the evidence being good enough to decide
        on. An official page that would not load, or evidence older than the methodology
        allows, leaves a dossier that is finished but not approvable — and a case
        presented as "ready" in that state gave the reviewer a button that always failed.

        The question is asked of the approval path itself, never re-listed here, so the
        queue and the decision can never hold different ideas of ready.
        """

        from ai_market_monitor.services.sharia_governance import ShariaGovernanceService

        blocker = await ShariaGovernanceService(
            self.session, self.settings
        ).review_blocker(case)
        if blocker is None:
            return
        case.state = "needs_evidence"
        case.requested_evidence = sorted(
            {*(case.requested_evidence or []), str(blocker)}
        )
        case.next_reminder_at = datetime.now(UTC)
        await self.session.flush()

    async def _methodology_id_for_external(self, external: ExternalAssessment):
        if external.methodology_id is not None:
            methodology = await self.session.get(
                ShariaMethodology,
                external.methodology_id,
            )
            if (
                methodology is None
                or methodology.status != ShariaMethodologyStatus.ACTIVE
            ):
                raise ShariaResearchError(
                    "source_methodology_unavailable",
                    "The imported authority methodology is not active.",
                )
            return methodology.id
        code = _SOURCE_METHODOLOGY_CODES.get(external.source_family)
        if code is None:
            raise ShariaResearchError(
                "source_methodology_unconfigured",
                "No active methodology is configured for this authority source.",
            )
        methodology_id = await self.session.scalar(
            select(ShariaMethodology.id).where(
                ShariaMethodology.code == code,
                ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE,
            )
        )
        if methodology_id is None:
            raise ShariaResearchError(
                "source_methodology_unavailable",
                "The authority methodology is not active.",
            )
        return methodology_id

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


def extract_document(
    body: str | bytes,
    url: str,
    *,
    content_type: str | None = None,
) -> tuple[str, list[str], str]:
    if isinstance(body, bytes) or "application/pdf" in str(content_type).casefold():
        try:
            raw = body if isinstance(body, bytes) else body.encode()
            reader = PdfReader(io.BytesIO(raw))
            text = _clean_text(
                "\n".join((page.extract_text() or "") for page in reader.pages)
            )
            metadata_title = (
                str(reader.metadata.title or "").strip()
                if reader.metadata is not None
                else ""
            )
        except Exception as exc:
            raise ShariaResearchError(
                "official_pdf_parse_failed",
                "The official PDF could not be converted into reviewable text.",
            ) from exc
        if len(text) < 80:
            raise ShariaResearchError(
                "official_source_text_insufficient",
                "The official PDF did not expose enough accessible text for evidence review.",
            )
        return metadata_title[:500] or url, [], text[:300_000]

    html = body.replace("\x00", " ")
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


def extract_links(body: str | bytes, url: str) -> tuple[str, ...]:
    """Every address a fetched page points at, made absolute and de-duplicated.

    This lives beside ``extract_document`` because both answer "what is in this body",
    and keeping them together is what stops a second HTML parser appearing with its own
    idea of what a page contains. ``extract_document`` reads the words; this reads the
    addresses, which is what finds a project's Telegram channel and its X account
    without anybody guessing at either.

    A body that cannot be parsed returns nothing. Failing to find links is an ordinary
    answer, not an error: the caller simply moves on to the next layer.
    """

    if isinstance(body, bytes):
        return ()  # a PDF has no navigation to read
    try:
        document = Selector(body.replace("\x00", " "), url=url, adaptive=True)
        hrefs = document.css("a::attr(href)").getall()
    except Exception:  # noqa: BLE001 - an unparsable page simply has no links
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for raw in hrefs:
        text = str(raw or "").strip()
        if not text or text.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(url, text)
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= 1000:
            break
    return tuple(found)


#: Where a page states a date for a machine rather than for a reader.
_DATE_META_NAMES = (
    "article:published_time",
    "article:modified_time",
    "og:updated_time",
    "date",
    "dc.date",
    "dcterms.modified",
    "last-modified",
    "pubdate",
)


def extract_dates(body: str | bytes, url: str) -> tuple[str, ...]:
    """Every date a page states in markup rather than in words.

    ``extract_document`` strips the tags, which throws these away — and on a growing
    number of real pages they are the *only* dates there are. A Telegram channel's
    public web view prints "August 20" with no year and carries the real timestamp in a
    ``<time datetime="…">`` attribute; the same is true of most modern blogs. Reading
    only the visible words scored those pages as having published nothing, ever, so a
    live announcement channel could never clear the freshness proof.

    The strings are returned raw. They are appended to the page text and read by the one
    date parser in ``sharia_source_activity``, so a second date format cannot appear
    here and be understood differently there.
    """

    if isinstance(body, bytes):
        return ()
    try:
        document = Selector(body.replace("\x00", " "), url=url, adaptive=True)
        found = list(document.css("time::attr(datetime)").getall())
        found.extend(document.css("*::attr(data-timestamp)").getall())
        for name in _DATE_META_NAMES:
            found.extend(document.css(f'meta[property="{name}"]::attr(content)').getall())
            found.extend(document.css(f'meta[name="{name}"]::attr(content)').getall())
    except Exception:  # noqa: BLE001 - a page with no readable markup simply has no dates
        return ()
    return tuple(str(value).strip() for value in found if str(value).strip())[:400]


def _database_safe_text(value: str) -> str:
    return value.replace("\x00", "")


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
        "external_authority_reference": {
            "source_family": external.source_family,
            "authority": external.source_authority,
            "exact_status_wording": external.exact_status_wording,
            "meeting_number": external.sac_meeting_number,
            "decision_date": (
                external.decision_date.isoformat() if external.decision_date else None
            ),
            "regulatory_scope": external.regulatory_scope,
            "source_url": external.source_url,
            "source_reference": external.source_reference,
            "published_profile_facts": (
                {}
                if external.source_row_id is not None
                else external.structured_facts
            ),
            "rights_state": external.rights_state,
            "package_authority_content_withheld_from_ai": (
                external.source_row_id is not None
            ),
            "limitation": (
                "The external status is context only. Do not use it to create "
                "project facts or reconstruct authority reasoning. Package "
                "authority content is withheld; missing fields remain missing."
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
            "This is factual information research, not unpublished authority reasoning and not "
            "an independent religious ruling."
        ),
    }


def _passport_enrichment_profile(
    asset: CanonicalAsset,
    analysis: ShariaFactualAnalysis,
    snapshots: list[SourceSnapshot],
) -> PassportEnrichmentProfile:
    official_sources = [
        {
            "snapshot_id": str(snapshot.id),
            "source_url": snapshot.source_url,
            "retrieved_at": snapshot.retrieved_at.isoformat(),
            "content_hash": snapshot.content_hash,
        }
        for snapshot in snapshots
        if snapshot.official_source_id is not None and snapshot.fetch_status == "success"
    ]
    primary = analysis.profile.primary_activity
    token_role = analysis.profile.token_role
    return PassportEnrichmentProfile(
        canonical_asset_identity={
            "canonical_asset_id": str(asset.id),
            "name": asset.name,
            "symbol": asset.symbol,
            "asset_type": asset.asset_type,
            "native_chain": asset.native_chain,
            "contract_addresses": dict(asset.contract_addresses or {}),
            "identity_hash": asset.identity_hash,
            "provider_ids": dict(asset.provider_ids or {}),
        },
        official_website=asset.official_website,
        official_documentation=asset.official_documentation,
        primary_activity=primary,
        token_role_and_utility=token_role,
        asset_type=asset.asset_type,
        native_chain_or_contract={
            "native_chain": asset.native_chain,
            "contract_addresses": dict(asset.contract_addresses or {}),
        },
        governance_model=analysis.profile.treasury_and_governance,
        tokenomics=analysis.profile.tokenomics_and_backing,
        staking_and_rewards=analysis.profile.staking,
        lending_borrowing=analysis.profile.lending_and_yield,
        interest_or_yield=analysis.profile.lending_and_yield,
        derivatives_and_prediction_products=analysis.profile.derivatives,
        treasury_and_revenue=analysis.profile.treasury_and_governance,
        backing_redemption_or_collateral=analysis.profile.tokenomics_and_backing,
        official_source_registry=official_sources,
        missing_information=list(analysis.missing_evidence),
        contradictions=list(analysis.contradictions),
        risk_flags=list(analysis.explicit_limitations),
        plain_language_profile=f"{primary} {token_role}".strip(),
        provenance="HILALMARKETS_AI_ENRICHMENT_UNVERIFIED",
        manual_verification_required=True,
    )


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


def _initial_research_run_key(
    external_assessment_id: object,
    external_snapshot_hash: str,
    sources: list[OfficialSource],
) -> str:
    fingerprint = _hash_json(
        {
            "external_snapshot_hash": external_snapshot_hash,
            "source_registry_hash": _source_registry_hash(sources),
        }
    )
    return f"initial-research:{external_assessment_id}:{fingerprint}"


def _clean_text(value: object) -> str:
    return "\n".join(
        line.strip()
        for line in re.sub(
            r"[ \t\r\f\v]+",
            " ",
            str(value or "").replace("\x00", " "),
        ).split("\n")
        if line.strip()
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: object) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))

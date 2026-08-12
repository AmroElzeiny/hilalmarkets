from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from curl_cffi import requests as curl_requests
from scrapling.parser import Selector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AuditEvent,
    ExternalAssessment,
    ShariaMonitoringRun,
    SourceSnapshot,
)
from ai_market_monitor.services.provider_runtime import provider_request

FASSET_AUTHORITY = "Fasset published Shariah Reports"
FASSET_SCOPE = (
    "Asset-level verdict and factual profile published on Fasset's Shariah Reports page"
)
IMPORTER_VERSION = "fasset-v1"
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,23}$")
_CHALLENGE_MARKERS = (
    "<title>just a moment",
    "cf-chl-",
    "/cdn-cgi/challenge-platform/",
)
_PROFILE_LABELS = (
    "Platform Purpose",
    "Token Utility",
    "Data Structure",
    "Smart Contract Capability",
    "Transaction Validation",
    "Consensus Mechanism",
    "Governance Model",
    "Tokenomics",
)


class FassetImportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FassetFetchedSource:
    url: str
    status_code: int
    content: str
    headers: dict[str, str]
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class FassetCompliantProfile:
    profile_number: int
    name: str
    symbol: str
    exact_status_wording: str
    facts: dict[str, str]
    exact_profile_text: str


@dataclass(frozen=True, slots=True)
class FassetParseResult:
    profiles: list[FassetCompliantProfile]
    excluded_profiles: list[dict[str, str]]
    normalized_text: str
    total_profiles: int


@dataclass(frozen=True, slots=True)
class FassetImportResult:
    run_id: str
    snapshot_id: str
    created_assessments: int
    updated_package_assessments: int
    conflicted_package_assessments: int
    explicit_compliant_profiles: int
    excluded_profiles: int
    idempotent_replay: bool


class FassetSourceFetcher:
    """Fetch the configured official page directly with a browser TLS fingerprint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch(self, url: str) -> FassetFetchedSource:
        if self.settings.sharia_scraper_obey_robots:
            await self._assert_robots_allowed(url)
        await asyncio.sleep(self.settings.sharia_scraper_download_delay_seconds)
        response = await asyncio.to_thread(self._fetch_sync, url)
        if response.status_code >= 400:
            raise FassetImportError(
                "fasset_source_fetch_failed",
                f"Fasset source returned HTTP {response.status_code}.",
            )
        content = str(response.text or "")
        lowered = content[:100_000].casefold()
        if any(marker in lowered for marker in _CHALLENGE_MARKERS):
            raise FassetImportError(
                "fasset_source_challenged",
                "Fasset returned an anti-bot challenge instead of the published reports.",
            )
        final_url = str(response.url)
        expected_host = urlparse(url).hostname
        final_host = urlparse(final_url).hostname
        if not expected_host or final_host != expected_host:
            raise FassetImportError(
                "fasset_source_redirected",
                "Fasset redirected the importer outside the configured official host.",
            )
        return FassetFetchedSource(
            url=final_url,
            status_code=int(response.status_code),
            content=content,
            headers={
                str(key).casefold(): str(value)
                for key, value in dict(response.headers).items()
            },
            retrieved_at=datetime.now(UTC),
        )

    @staticmethod
    def _fetch_sync(url: str):
        return curl_requests.get(
            url,
            impersonate="chrome",
            timeout=60,
            allow_redirects=True,
        )

    async def _assert_robots_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        response = await provider_request(
            self.settings,
            "GET",
            robots_url,
            provider="fasset",
            operation="robots",
            timeout=20,
            mutation_committed=False,
            follow_redirects=True,
            headers={"User-Agent": "HilalMarketsEvidenceBot/1.0 (+compliance research)"},
        )
        if response.status_code == 404:
            return
        if response.status_code >= 400:
            raise FassetImportError(
                "robots_unavailable",
                "The Fasset robots policy could not be verified; import was stopped.",
            )
        parser = RobotFileParser(robots_url)
        parser.parse(response.text.splitlines())
        if not parser.can_fetch("HilalMarketsEvidenceBot", url):
            raise FassetImportError(
                "robots_disallowed",
                "The Fasset robots policy does not permit this import.",
            )


class FassetParser:
    def __init__(self, *, minimum_profile_count: int = 100) -> None:
        self.minimum_profile_count = minimum_profile_count

    def parse(self, html: str, *, url: str) -> FassetParseResult:
        document = Selector(html, url=url, adaptive=True)
        lines = [
            cleaned
            for value in document.get_all_text(separator="\n", strip=True).splitlines()
            if (cleaned := _clean_text(value))
        ]
        detailed_starts = [
            index
            for index, value in enumerate(lines)
            if value.isdigit()
            and index + 5 < len(lines)
            and "Platform Purpose" in lines[index + 1 : index + 12]
        ]
        detailed = len(detailed_starts) >= self.minimum_profile_count
        if detailed:
            blocks = [
                lines[
                    start : (
                        detailed_starts[position + 1]
                        if position + 1 < len(detailed_starts)
                        else len(lines)
                    )
                ]
                for position, start in enumerate(detailed_starts)
            ]
        else:
            blocks = self._compact_blocks(lines)
        if len(blocks) < self.minimum_profile_count:
            raise FassetImportError(
                "fasset_profile_count_below_minimum",
                "The Fasset page exposed fewer complete profiles than the configured "
                "source-shape safety threshold.",
            )

        profiles_by_symbol: dict[str, FassetCompliantProfile] = {}
        conflicted_symbols: set[str] = set()
        excluded: list[dict[str, str]] = []
        for block in blocks:
            parsed = self._parse_block(block, require_profile_facts=detailed)
            if isinstance(parsed, FassetCompliantProfile):
                if parsed.symbol in conflicted_symbols:
                    excluded.append(
                        {
                            "profile": str(parsed.profile_number),
                            "asset": f"{parsed.name} ({parsed.symbol})",
                            "reason": "duplicate_symbol_identity_conflict",
                        }
                    )
                    continue
                previous = profiles_by_symbol.get(parsed.symbol)
                if previous is not None:
                    if _identity_text(previous.name) != _identity_text(parsed.name):
                        profiles_by_symbol.pop(parsed.symbol)
                        conflicted_symbols.add(parsed.symbol)
                        excluded.extend(
                            [
                                {
                                    "profile": str(previous.profile_number),
                                    "asset": f"{previous.name} ({previous.symbol})",
                                    "reason": "duplicate_symbol_identity_conflict",
                                },
                                {
                                    "profile": str(parsed.profile_number),
                                    "asset": f"{parsed.name} ({parsed.symbol})",
                                    "reason": "duplicate_symbol_identity_conflict",
                                },
                            ]
                        )
                        continue
                    excluded.append(
                        {
                            "profile": str(previous.profile_number),
                            "asset": f"{previous.name} ({previous.symbol})",
                            "reason": "superseded_duplicate_profile",
                        }
                    )
                profiles_by_symbol[parsed.symbol] = parsed
            else:
                excluded.append(parsed)
        profiles = sorted(
            profiles_by_symbol.values(), key=lambda item: item.profile_number
        )
        if not profiles:
            raise FassetImportError(
                "fasset_explicit_verdicts_missing",
                "No explicit Fasset Shariah-compliant verdicts were found.",
            )
        return FassetParseResult(
            profiles=profiles,
            excluded_profiles=excluded,
            normalized_text="\n".join(lines),
            total_profiles=len(blocks),
        )

    @staticmethod
    def _compact_blocks(lines: list[str]) -> list[list[str]]:
        blocks: list[list[str]] = []
        allowed_verdicts = {"Shariah Compliant", "Not Compliant"}
        for index in range(len(lines) - 2):
            name, source_symbol, verdict = lines[index : index + 3]
            aliases = [item.strip() for item in source_symbol.upper().split("/") if item.strip()]
            symbol = aliases[-1] if aliases else source_symbol.upper()
            if (
                verdict not in allowed_verdicts
                or not name
                or not _SYMBOL_RE.fullmatch(symbol)
            ):
                continue
            blocks.append(
                [
                    str(len(blocks) + 1),
                    name,
                    source_symbol.upper(),
                    "Shariah Verdict",
                    verdict,
                ]
            )
        return blocks

    @staticmethod
    def _parse_block(
        block: list[str],
        *,
        require_profile_facts: bool = True,
    ) -> FassetCompliantProfile | dict[str, str]:
        profile_number = int(block[0])
        name = _clean_text(block[1] if len(block) > 1 else "")
        source_symbol = _clean_text(block[2] if len(block) > 2 else "").upper()
        symbol_aliases = [
            item.strip() for item in source_symbol.split("/") if item.strip()
        ]
        symbol = symbol_aliases[-1] if symbol_aliases else source_symbol
        if not name or not _SYMBOL_RE.fullmatch(symbol):
            return {
                "profile": str(profile_number),
                "asset": name or symbol,
                "reason": "asset_identity_unparseable",
            }
        try:
            verdict_index = block.index("Shariah Verdict")
            exact_verdict = block[verdict_index + 1]
        except (ValueError, IndexError):
            return {
                "profile": str(profile_number),
                "asset": f"{name} ({symbol})",
                "reason": "explicit_verdict_missing",
            }
        if exact_verdict != "Shariah Compliant":
            return {
                "profile": str(profile_number),
                "asset": f"{name} ({symbol})",
                "reason": "verdict_not_compliant",
            }

        facts: dict[str, str] = {}
        if source_symbol != symbol:
            facts["source_symbol"] = source_symbol
            facts["symbol_aliases"] = "; ".join(symbol_aliases)
        for label in _PROFILE_LABELS:
            try:
                label_index = block.index(label)
            except ValueError:
                continue
            if label_index + 1 >= len(block):
                continue
            value = block[label_index + 1]
            reserved_labels = {
                *_PROFILE_LABELS,
                "Blockchain Protocol & Operation",
                "Shariah Verdict",
            }
            if value not in reserved_labels:
                facts[_fact_key(label)] = value
        if require_profile_facts and (
            not facts.get("platform_purpose") or not facts.get("token_utility")
        ):
            return {
                "profile": str(profile_number),
                "asset": f"{name} ({symbol})",
                "reason": "required_profile_facts_missing",
            }
        exact_profile_text = "\n".join(
            [
                f"{name} ({symbol})",
                *[
                    f"{label}: {facts[key]}"
                    for label in _PROFILE_LABELS
                    if (key := _fact_key(label)) in facts
                ],
                f"Shariah Verdict: {exact_verdict}",
            ]
        )
        return FassetCompliantProfile(
            profile_number=profile_number,
            name=name,
            symbol=symbol,
            exact_status_wording=exact_verdict,
            facts=facts,
            exact_profile_text=exact_profile_text,
        )


class FassetImporter:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        fetcher: FassetSourceFetcher | None = None,
        parser: FassetParser | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.fetcher = fetcher or FassetSourceFetcher(settings)
        self.parser = parser or FassetParser(
            minimum_profile_count=settings.fasset_minimum_profile_count
        )

    async def import_latest(self, *, actor_user_id=None) -> FassetImportResult:
        source = await self.fetcher.fetch(str(self.settings.fasset_shariah_reports_url))
        source_hash = _sha256(source.content)
        cadence = timedelta(hours=self.settings.sharia_source_scan_interval_hours)
        cycle = int(source.retrieved_at.timestamp() // cadence.total_seconds())
        run_key = f"fasset-import:{cycle}:{source_hash[:64]}"
        existing_run = await self.session.scalar(
            select(ShariaMonitoringRun).where(
                ShariaMonitoringRun.idempotency_key == run_key
            )
        )
        if existing_run is not None:
            snapshot = await self.session.scalar(
                select(SourceSnapshot)
                .where(SourceSnapshot.monitoring_run_id == existing_run.id)
                .order_by(SourceSnapshot.retrieved_at.desc())
                .limit(1)
            )
            return FassetImportResult(
                run_id=str(existing_run.id),
                snapshot_id=str(snapshot.id) if snapshot else "",
                created_assessments=0,
                updated_package_assessments=int(
                    existing_run.result_summary.get(
                        "updated_package_assessments",
                        0,
                    )
                ),
                conflicted_package_assessments=int(
                    existing_run.result_summary.get(
                        "conflicted_package_assessments",
                        0,
                    )
                ),
                explicit_compliant_profiles=int(
                    existing_run.result_summary.get("explicit_compliant_profiles", 0)
                ),
                excluded_profiles=int(
                    existing_run.result_summary.get("excluded_profiles", 0)
                ),
                idempotent_replay=True,
            )

        run = ShariaMonitoringRun(
            run_kind="fasset_import",
            idempotency_key=run_key,
            status="running",
            source_url=source.url,
            started_at=source.retrieved_at,
            next_due_at=source.retrieved_at + cadence,
        )
        self.session.add(run)
        await self.session.flush()
        try:
            parsed = self.parser.parse(source.content, url=source.url)
        except FassetImportError as exc:
            run.status = "failed"
            run.last_error_code = exc.code
            run.last_error_detail = str(exc)
            run.completed_at = datetime.now(UTC)
            raise

        snapshot = SourceSnapshot(
            monitoring_run_id=run.id,
            source_url=source.url,
            retrieved_at=source.retrieved_at,
            http_status=source.status_code,
            etag=source.headers.get("etag"),
            last_modified=source.headers.get("last-modified"),
            response_headers={
                key: value
                for key, value in source.headers.items()
                if key in {"content-type", "content-length", "etag", "last-modified"}
            },
            raw_content=source.content,
            normalized_text=parsed.normalized_text,
            title="Fasset Shariah Reports",
            headings=["Shariah Reports", "Shariah Verdict"],
            content_hash=source_hash,
            meaningful_diff={},
            is_material_change=False,
            fetch_status="success",
            scraper_version=f"scrapling+curl-cffi:{IMPORTER_VERSION}",
            parser_result={
                "total_profiles": parsed.total_profiles,
                "explicit_compliant_profiles": len(parsed.profiles),
                "excluded_profiles": parsed.excluded_profiles,
            },
        )
        self.session.add(snapshot)
        await self.session.flush()

        created = 0
        updated_package_assessments = 0
        conflicted_package_assessments = 0
        for profile in parsed.profiles:
            package_matches = await self._package_assessment_matches(
                profile=profile,
                source_url=source.url,
            )
            if len(package_matches) == 1:
                package_assessment = package_matches[0]
                if profile.facts:
                    package_assessment.source_detail_extraction_state = (
                        "FETCHED_AND_VERIFIED"
                    )
                    package_assessment.source_detail_snapshot_id = snapshot.id
                    package_assessment.source_detail_fields = dict(profile.facts)
                    updated_package_assessments += 1
                continue
            if len(package_matches) > 1:
                note = (
                    "The live Fasset source matched more than one retained package "
                    "assessment by exact name, symbol, status and source URL. Manual "
                    "identity review is required before source details can be attached."
                )
                for package_assessment in package_matches:
                    package_assessment.mapping_state = "identity_conflict"
                    package_assessment.manual_verification_required = True
                    notes = list(package_assessment.mapping_notes or [])
                    if note not in notes:
                        notes.append(note)
                    package_assessment.mapping_notes = notes
                conflicted_package_assessments += len(package_matches)
                continue

            import_hash = _sha256(
                "|".join(
                    [
                        "fasset_shariah_reports",
                        profile.name,
                        profile.symbol,
                        profile.exact_status_wording,
                        _sha256(profile.exact_profile_text),
                    ]
                )
            )
            existing = await self.session.scalar(
                select(ExternalAssessment).where(
                    ExternalAssessment.import_hash == import_hash
                )
            )
            if existing is not None:
                continue
            self.session.add(
                ExternalAssessment(
                    source_snapshot_id=snapshot.id,
                    source_family="fasset_shariah_reports",
                    source_authority=FASSET_AUTHORITY,
                    source_url=source.url,
                    source_reference=f"profile:{profile.profile_number}",
                    asset_name=profile.name,
                    asset_symbol=profile.symbol,
                    exact_status_wording=profile.exact_status_wording,
                    sac_meeting_number=None,
                    decision_date=None,
                    regulatory_scope=FASSET_SCOPE,
                    retrieval_date=source.retrieved_at,
                    exact_row_text=profile.exact_profile_text,
                    structured_facts=profile.facts,
                    import_hash=import_hash,
                    mapping_state="unresolved",
                    mapping_notes=[],
                )
            )
            created += 1

        run.status = "completed"
        run.items_attempted = parsed.total_profiles
        run.items_succeeded = len(parsed.profiles)
        run.items_failed = len(parsed.excluded_profiles)
        run.completed_at = datetime.now(UTC)
        run.result_summary = {
            "total_profiles": parsed.total_profiles,
            "explicit_compliant_profiles": len(parsed.profiles),
            "created_assessments": created,
            "updated_package_assessments": updated_package_assessments,
            "conflicted_package_assessments": conflicted_package_assessments,
            "excluded_profiles": len(parsed.excluded_profiles),
            "source_family": "fasset_shariah_reports",
            "publication_boundary": "human_review_and_publication_required",
        }
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin" if actor_user_id else "worker",
                action="sharia.fasset_import_completed",
                target_type="sharia_monitoring_run",
                target_id=str(run.id),
                metadata_redacted={
                    "total_profiles": parsed.total_profiles,
                    "explicit_compliant_profiles": len(parsed.profiles),
                    "created_assessments": created,
                    "updated_package_assessments": updated_package_assessments,
                    "conflicted_package_assessments": (
                        conflicted_package_assessments
                    ),
                    "excluded_profiles": len(parsed.excluded_profiles),
                    "source_hash": source_hash,
                },
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return FassetImportResult(
            run_id=str(run.id),
            snapshot_id=str(snapshot.id),
            created_assessments=created,
            updated_package_assessments=updated_package_assessments,
            conflicted_package_assessments=conflicted_package_assessments,
            explicit_compliant_profiles=len(parsed.profiles),
            excluded_profiles=len(parsed.excluded_profiles),
            idempotent_replay=False,
        )

    async def _package_assessment_matches(
        self,
        *,
        profile: FassetCompliantProfile,
        source_url: str,
    ) -> list[ExternalAssessment]:
        candidates = list(
            (
                await self.session.scalars(
                    select(ExternalAssessment).where(
                        ExternalAssessment.source_family
                        == "fasset_shariah_reports",
                        ExternalAssessment.source_row_id.is_not(None),
                        ExternalAssessment.asset_name == profile.name,
                        ExternalAssessment.asset_symbol == profile.symbol,
                        ExternalAssessment.exact_status_wording
                        == profile.exact_status_wording,
                    )
                )
            ).all()
        )
        normalized_source_url = _normalized_source_url(source_url)
        return [
            assessment
            for assessment in candidates
            if _normalized_source_url(assessment.source_url)
            == normalized_source_url
        ]


def _fact_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


def _identity_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalized_source_url(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{path}"


def _clean_text(value: object) -> str:
    text = "".join(
        char for char in str(value or "") if unicodedata.category(char) != "Cf"
    )
    return re.sub(r"\s+", " ", text).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

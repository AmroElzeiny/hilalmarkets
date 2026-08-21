import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
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
from ai_market_monitor.services.sharia_source_catalog import normalized_url

SC_AUTHORITY = "Shariah Advisory Council of the Securities Commission Malaysia"
SC_SCOPE = "Securities Commission Malaysia regulated digital-assets framework"
IMPORTER_VERSION = "sc-malaysia-v1"
_ASSET_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<symbol>[A-Za-z0-9]{2,16})\)\s*$")
_MEETING_RE = re.compile(
    r"(?P<meeting>\d+(?:st|nd|rd|th))\s+SAC\s+Meeting\s*"
    r"\((?P<date>\d{1,2}\s+[A-Za-z]+\s+\d{4})\)",
    re.IGNORECASE,
)


class SCImportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchedSource:
    url: str
    status_code: int
    content: str
    headers: dict[str, str]
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class SCCompliantRow:
    name: str
    symbol: str
    exact_status_wording: str
    meeting_number: str
    decision_date: date
    exact_row_text: str


@dataclass(frozen=True, slots=True)
class SCParseResult:
    rows: list[SCCompliantRow]
    excluded_rows: list[dict[str, str]]
    normalized_text: str


@dataclass(frozen=True, slots=True)
class SCImportResult:
    run_id: str
    snapshot_id: str
    created_assessments: int
    verified_package_assessments: int
    conflicted_package_assessments: int
    explicit_rows: int
    excluded_notice_rows: int
    idempotent_replay: bool


class SCSourceFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def fetch(self, url: str) -> FetchedSource:
        if self.settings.sharia_scraper_obey_robots:
            await self._assert_robots_allowed(url)
        await asyncio.sleep(self.settings.sharia_scraper_download_delay_seconds)
        response = await provider_request(
            self.settings,
            "GET",
            url,
            provider="sc_malaysia",
            operation="fetch_source",
            timeout=60,
            mutation_committed=False,
            transport=self.transport,
            follow_redirects=True,
            headers={"User-Agent": "HilalMarketsEvidenceBot/1.0 (+compliance research)"},
        )
        if response.status_code >= 400:
            raise SCImportError(
                "sc_source_fetch_failed",
                f"SC Malaysia source returned HTTP {response.status_code}.",
            )
        expected_host = urlparse(url).hostname
        final_host = urlparse(str(response.url)).hostname
        if not expected_host or final_host != expected_host:
            raise SCImportError(
                "sc_source_redirected",
                "SC Malaysia redirected the importer outside the configured official host.",
            )
        return FetchedSource(
            url=str(response.url),
            status_code=response.status_code,
            content=response.text,
            headers={key.casefold(): value for key, value in response.headers.items()},
            retrieved_at=datetime.now(UTC),
        )

    async def _assert_robots_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        response = await provider_request(
            self.settings,
            "GET",
            robots_url,
            provider="sc_malaysia",
            operation="robots",
            timeout=20,
            mutation_committed=False,
            transport=self.transport,
            follow_redirects=True,
            headers={"User-Agent": "HilalMarketsEvidenceBot/1.0 (+compliance research)"},
        )
        if response.status_code == 404:
            return
        if response.status_code >= 400:
            raise SCImportError(
                "robots_unavailable",
                "The source robots policy could not be verified; import was stopped.",
            )
        parser = RobotFileParser(robots_url)
        parser.parse(response.text.splitlines())
        if not parser.can_fetch("HilalMarketsEvidenceBot", url):
            raise SCImportError(
                "robots_disallowed",
                "The source robots policy does not permit this import.",
            )


class SCMalaysiaParser:
    def parse(self, html: str, *, url: str) -> SCParseResult:
        document = Selector(html, url=url, adaptive=True)
        target = None
        for table in document.css("table"):
            headers = " ".join(
                _clean_text(node.get_all_text(separator=" ", strip=True))
                for node in table.css("th")
            )
            if "Tradeable Digital Asset" in headers and "Shariah Status" in headers:
                target = table
                break
        if target is None:
            raise SCImportError(
                "sc_table_not_found",
                "The SC Malaysia digital-asset status table could not be located.",
            )

        rows: list[SCCompliantRow] = []
        excluded: list[dict[str, str]] = []
        for row in target.css("tbody tr"):
            cells = row.css("td")
            if len(cells) < 2:
                continue
            asset_text = _clean_text(cells[1].get_all_text(separator=" ", strip=True))
            row_text = _clean_text(row.get_all_text(separator=" ", strip=True))
            status_text = (
                _clean_text(cells[2].get_all_text(separator=" ", strip=True))
                if len(cells) >= 3
                else ""
            )
            asset_match = _ASSET_RE.match(asset_text)
            if asset_match is None:
                excluded.append({"asset": asset_text, "reason": "asset_identity_unparseable"})
                continue
            if "Shariah-compliant" not in status_text:
                excluded.append(
                    {
                        "asset": asset_text,
                        "reason": "no_explicit_compliant_result_in_asset_row",
                    }
                )
                continue
            meeting = _MEETING_RE.search(status_text)
            if meeting is None:
                excluded.append(
                    {"asset": asset_text, "reason": "meeting_or_decision_date_missing"}
                )
                continue
            try:
                decision_date = datetime.strptime(
                    meeting.group("date"), "%d %B %Y"
                ).date()
            except ValueError:
                excluded.append({"asset": asset_text, "reason": "decision_date_invalid"})
                continue
            status_node = cells[2].css("strong::text").get()
            exact_wording = _clean_text(status_node or "Shariah-compliant")
            if exact_wording != "Shariah-compliant":
                excluded.append({"asset": asset_text, "reason": "status_wording_not_exact"})
                continue
            rows.append(
                SCCompliantRow(
                    name=_clean_text(asset_match.group("name")),
                    symbol=asset_match.group("symbol").upper(),
                    exact_status_wording=exact_wording,
                    meeting_number=meeting.group("meeting"),
                    decision_date=decision_date,
                    exact_row_text=row_text,
                )
            )
        if not rows:
            raise SCImportError(
                "sc_explicit_rows_missing",
                "No explicit SC Malaysia Shariah-compliant rows were found.",
            )
        normalized = _clean_text(document.get_all_text(separator=" ", strip=True))
        return SCParseResult(rows=rows, excluded_rows=excluded, normalized_text=normalized)


class SCMalaysiaImporter:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        fetcher: SCSourceFetcher | None = None,
        parser: SCMalaysiaParser | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.fetcher = fetcher or SCSourceFetcher(settings)
        self.parser = parser or SCMalaysiaParser()

    async def import_latest(self, *, actor_user_id=None) -> SCImportResult:
        source = await self.fetcher.fetch(str(self.settings.sc_malaysia_digital_assets_url))
        source_hash = _sha256(source.content)
        cadence = timedelta(hours=self.settings.sharia_source_scan_interval_hours)
        cycle = int(source.retrieved_at.timestamp() // cadence.total_seconds())
        run_key = f"sc-malaysia-import:{cycle}:{source_hash[:64]}"
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
            return SCImportResult(
                run_id=str(existing_run.id),
                snapshot_id=str(snapshot.id) if snapshot else "",
                created_assessments=0,
                verified_package_assessments=int(
                    existing_run.result_summary.get(
                        "verified_package_assessments",
                        0,
                    )
                ),
                conflicted_package_assessments=int(
                    existing_run.result_summary.get(
                        "conflicted_package_assessments",
                        0,
                    )
                ),
                explicit_rows=int(existing_run.result_summary.get("explicit_rows", 0)),
                excluded_notice_rows=int(
                    existing_run.result_summary.get("excluded_notice_rows", 0)
                ),
                idempotent_replay=True,
            )

        run = ShariaMonitoringRun(
            run_kind="sc_import",
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
        except SCImportError as exc:
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
            title="SC Malaysia Digital Assets",
            headings=["Tradeable Digital Asset", "Shariah Status"],
            content_hash=source_hash,
            meaningful_diff={},
            is_material_change=False,
            fetch_status="success",
            scraper_version=f"scrapling:{IMPORTER_VERSION}",
            parser_result={
                "explicit_rows": len(parsed.rows),
                "excluded_rows": parsed.excluded_rows,
            },
        )
        self.session.add(snapshot)
        await self.session.flush()

        created = 0
        verified_package_assessments = 0
        conflicted_package_assessments = 0
        for row in parsed.rows:
            package_matches = await self._package_assessment_matches(
                row=row,
                source_url=source.url,
            )
            if len(package_matches) == 1:
                package_assessment = package_matches[0]
                package_assessment.source_detail_extraction_state = (
                    "SOURCE_ROW_VERIFIED"
                )
                package_assessment.source_detail_snapshot_id = snapshot.id
                package_assessment.source_detail_fields = {
                    "exact_status_wording": row.exact_status_wording,
                    "sac_meeting_number": row.meeting_number,
                    "decision_date": row.decision_date.isoformat(),
                    "exact_row_text": row.exact_row_text,
                }
                verified_package_assessments += 1
                continue
            if len(package_matches) > 1:
                note = (
                    "The live SC Malaysia source matched more than one retained "
                    "package assessment by exact identity, decision, status and "
                    "source URL. Manual identity review is required."
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
                        source.url,
                        row.name,
                        row.symbol,
                        row.exact_status_wording,
                        row.meeting_number,
                        row.decision_date.isoformat(),
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
                    source_family="sc_malaysia_sac",
                    source_authority=SC_AUTHORITY,
                    source_url=source.url,
                    source_reference=(
                        f"{row.meeting_number} SAC Meeting ({row.decision_date.isoformat()})"
                    ),
                    asset_name=row.name,
                    asset_symbol=row.symbol,
                    exact_status_wording=row.exact_status_wording,
                    sac_meeting_number=row.meeting_number,
                    decision_date=row.decision_date,
                    regulatory_scope=SC_SCOPE,
                    retrieval_date=source.retrieved_at,
                    exact_row_text=row.exact_row_text,
                    structured_facts={
                        "sac_meeting_number": row.meeting_number,
                        "decision_date": row.decision_date.isoformat(),
                        "regulatory_scope": SC_SCOPE,
                    },
                    import_hash=import_hash,
                    mapping_state="unresolved",
                    mapping_notes=[],
                )
            )
            created += 1

        excluded_notice_rows = sum(
            item["reason"] == "no_explicit_compliant_result_in_asset_row"
            for item in parsed.excluded_rows
        )
        run.status = "completed"
        run.items_attempted = len(parsed.rows) + len(parsed.excluded_rows)
        run.items_succeeded = len(parsed.rows)
        run.items_failed = len(parsed.excluded_rows)
        run.completed_at = datetime.now(UTC)
        run.result_summary = {
            "explicit_rows": len(parsed.rows),
            "created_assessments": created,
            "verified_package_assessments": verified_package_assessments,
            "conflicted_package_assessments": conflicted_package_assessments,
            "excluded_notice_rows": excluded_notice_rows,
            "pilot_symbols": sorted(self.settings.sharia_pilot_symbol_set),
            "remaining_processing_enabled": self.settings.sharia_process_remaining_imports,
        }
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin" if actor_user_id else "worker",
                action="sharia.sc_malaysia_import_completed",
                target_type="sharia_monitoring_run",
                target_id=str(run.id),
                metadata_redacted={
                    "explicit_rows": len(parsed.rows),
                    "created_assessments": created,
                    "verified_package_assessments": verified_package_assessments,
                    "conflicted_package_assessments": (
                        conflicted_package_assessments
                    ),
                    "notice_only_rows_excluded": excluded_notice_rows,
                    "source_hash": source_hash,
                },
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return SCImportResult(
            run_id=str(run.id),
            snapshot_id=str(snapshot.id),
            created_assessments=created,
            verified_package_assessments=verified_package_assessments,
            conflicted_package_assessments=conflicted_package_assessments,
            explicit_rows=len(parsed.rows),
            excluded_notice_rows=excluded_notice_rows,
            idempotent_replay=False,
        )

    async def _package_assessment_matches(
        self,
        *,
        row: SCCompliantRow,
        source_url: str,
    ) -> list[ExternalAssessment]:
        candidates = list(
            (
                await self.session.scalars(
                    select(ExternalAssessment).where(
                        ExternalAssessment.source_family == "sc_malaysia_sac",
                        ExternalAssessment.source_row_id.is_not(None),
                        ExternalAssessment.asset_name == row.name,
                        ExternalAssessment.asset_symbol == row.symbol,
                        ExternalAssessment.exact_status_wording
                        == row.exact_status_wording,
                        ExternalAssessment.sac_meeting_number
                        == row.meeting_number,
                        ExternalAssessment.decision_date == row.decision_date,
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


def _clean_text(value: object) -> str:
    text = "".join(
        char for char in str(value or "") if unicodedata.category(char) != "Cf"
    )
    return re.sub(r"\s+", " ", text).strip()


#: One owner for "are these two addresses the same page". See
#: ``sharia_source_catalog.normalized_url`` for why four private copies of this was a
#: defect rather than a convenience.
_normalized_source_url = normalized_url


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

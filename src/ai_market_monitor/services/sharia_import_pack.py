from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AuditEvent,
    ExternalAssessment,
    ReviewCase,
    ShariaMethodology,
    ShariaMonitoringRun,
    SourceSnapshot,
    TelegramNotificationAttempt,
)
from ai_market_monitor.db.models.enums import ShariaMethodologyStatus
from ai_market_monitor.services.sharia_governance import (
    ShariaAdminTelegramService,
)
from ai_market_monitor.services.sharia_identity import (
    REVIEWED_ASSET_CANDIDATES,
    AssetIdentityError,
    CanonicalAssetMappingService,
)

PACK_VERSION = "1.0.0"
METHODOLOGY_VERSION = "2026.07-pack.1"
EXPECTED_COUNTS = {
    "SC_MALAYSIA_SAC_DIGITAL_ASSETS": 15,
    "SHARIAH_REVIEW_BUREAU": 31,
    "FASSET_SHARIAH_REPORTS": 188,
}
PACKAGE_TO_SYSTEM_CODE = {
    "SC_MALAYSIA_SAC_DIGITAL_ASSETS": "SC_MALAYSIA_SAC_REFERENCE",
    "SHARIAH_REVIEW_BUREAU": "SHARIAH_REVIEW_BUREAU",
    "FASSET_SHARIAH_REPORTS": "FASSET_SHARIAH_REPORTS",
}
DATASET_FILES = {
    "SC_MALAYSIA_SAC_DIGITAL_ASSETS": "sc_malaysia_compliant_assets.json",
    "SHARIAH_REVIEW_BUREAU": "shariah_review_bureau_compliant_assets.json",
    "FASSET_SHARIAH_REPORTS": "fasset_compliant_assets.json",
}
SOURCE_FAMILIES = {
    "SC_MALAYSIA_SAC_DIGITAL_ASSETS": "sc_malaysia_sac",
    "SHARIAH_REVIEW_BUREAU": "shariah_review_bureau",
    "FASSET_SHARIAH_REPORTS": "fasset_shariah_reports",
}
PUBLICATION_GATES = {
    "SC_MALAYSIA_SAC_DIGITAL_ASSETS": "ADMIN_APPROVAL_REQUIRED",
    "SHARIAH_REVIEW_BUREAU": (
        "ADMIN_APPROVAL_AND_RIGHTS_CLEARANCE_REQUIRED"
    ),
    "FASSET_SHARIAH_REPORTS": "ADMIN_APPROVAL_AND_RIGHTS_REVIEW_REQUIRED",
}
TERMINAL_CASE_STATES = {
    "approved",
    "published",
    "rejected",
    "stored",
    "superseded",
}


class ShariaImportPackError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ShariaImportPackBundle:
    root: Path
    manifest: dict[str, Any]
    methodology_definitions: dict[str, dict[str, Any]]
    rows: dict[str, list[dict[str, Any]]]
    fasset_guard: list[dict[str, Any]]
    passport_seeds: dict[str, dict[str, Any]]
    enrichment_tasks: dict[str, dict[str, Any]]
    duplicate_or_migrated_source_rows: frozenset[str]


@dataclass(slots=True)
class ShariaImportPackResult:
    methodology_counts: dict[str, int] = field(default_factory=dict)
    created_assessments: int = 0
    adopted_assessments: int = 0
    replayed_assessments: int = 0
    review_cases_created: int = 0
    enrichment_jobs_queued: int = 0
    mapped_assessments: int = 0
    unresolved_source_rows: list[str] = field(default_factory=list)
    duplicate_or_migrated_source_rows: list[str] = field(default_factory=list)
    rights_blocked_source_rows: list[str] = field(default_factory=list)
    telegram_notifications_queued: int = 0
    guard_rows_retained: int = 0
    auto_published: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "methodology_counts": dict(self.methodology_counts),
            "created_assessments": self.created_assessments,
            "adopted_assessments": self.adopted_assessments,
            "replayed_assessments": self.replayed_assessments,
            "review_cases_created": self.review_cases_created,
            "enrichment_jobs_queued": self.enrichment_jobs_queued,
            "mapped_assessments": self.mapped_assessments,
            "unresolved_source_rows": sorted(self.unresolved_source_rows),
            "duplicate_or_migrated_source_rows": sorted(
                self.duplicate_or_migrated_source_rows
            ),
            "rights_blocked_source_rows": sorted(self.rights_blocked_source_rows),
            "telegram_notifications_queued": self.telegram_notifications_queued,
            "guard_rows_retained": self.guard_rows_retained,
            "auto_published": self.auto_published,
        }


class ShariaMethodologyImportPackService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.telegram = ShariaAdminTelegramService(session, settings)

    async def import_bundle(
        self,
        *,
        actor_user_id=None,
    ) -> ShariaImportPackResult:
        initial_case_count = int(
            await self.session.scalar(select(func.count(ReviewCase.id))) or 0
        )
        bundle = load_import_pack(self.settings.sharia_import_pack_path)
        methodologies = await self._ensure_methodologies(bundle)
        result = ShariaImportPackResult(
            guard_rows_retained=len(bundle.fasset_guard),
            duplicate_or_migrated_source_rows=sorted(
                bundle.duplicate_or_migrated_source_rows
            ),
        )
        for package_methodology_id, rows in bundle.rows.items():
            methodology = methodologies[package_methodology_id]
            snapshot, run = await self._source_snapshot(
                bundle,
                package_methodology_id,
                rows,
            )
            result.methodology_counts[methodology.code] = len(rows)
            for row in rows:
                external, state = await self._upsert_assessment(
                    bundle=bundle,
                    methodology=methodology,
                    package_methodology_id=package_methodology_id,
                    snapshot=snapshot,
                    row=row,
                )
                if state == "created":
                    result.created_assessments += 1
                elif state == "adopted":
                    result.adopted_assessments += 1
                else:
                    result.replayed_assessments += 1

                source_row_id = _required_text(row, "source_row_id")
                conflict_reasons = self._identity_conflicts(
                    bundle,
                    source_row_id,
                    row,
                )
                mapped = False
                if external.mapping_state == "mapped":
                    mapped = True
                elif conflict_reasons:
                    external.mapping_state = "conflict"
                    external.mapping_notes = sorted(
                        {*external.mapping_notes, *conflict_reasons}
                    )
                else:
                    mapped = await self._map_registered_identity(external)
                if mapped:
                    result.mapped_assessments += 1
                else:
                    result.unresolved_source_rows.append(source_row_id)

                case, _ = await self._ensure_review_case(
                    methodology=methodology,
                    external=external,
                    source_row_id=source_row_id,
                    conflict_reasons=conflict_reasons,
                )
                if not self.settings.sharia_import_auto_publish:
                    notification_key = f"import-pack-review:{case.id}"
                    existing_notification = await self.session.scalar(
                        select(TelegramNotificationAttempt.id).where(
                            TelegramNotificationAttempt.idempotency_key
                            == notification_key
                        )
                    )
                    notification = await self.telegram.enqueue(
                        case,
                        notification_type="new_review_required",
                        idempotency_key=notification_key,
                    )
                    if notification is not None and existing_notification is None:
                        result.telegram_notifications_queued += 1
                if external.enrichment_state == "queued":
                    result.enrichment_jobs_queued += int(state != "replayed")
                if not external.commercial_display_allowed:
                    result.rights_blocked_source_rows.append(source_row_id)

            run.status = "completed"
            run.items_attempted = len(rows)
            run.items_succeeded = len(rows)
            run.items_failed = 0
            run.completed_at = datetime.now(UTC)
            run.result_summary = {
                "package_version": PACK_VERSION,
                "methodology_id": package_methodology_id,
                "validated_rows": len(rows),
                "guard_rows": (
                    len(bundle.fasset_guard)
                    if package_methodology_id == "FASSET_SHARIAH_REPORTS"
                    else 0
                ),
                "runtime_auto_publication_enabled": (
                    self.settings.sharia_import_auto_publish
                ),
                "package_rows_requested_auto_publish": False,
            }
        current_case_count = int(
            await self.session.scalar(select(func.count(ReviewCase.id))) or 0
        )
        result.review_cases_created = max(
            current_case_count - initial_case_count,
            0,
        )
        self.session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                actor_type="admin" if actor_user_id else "worker",
                action="sharia.methodology_import_pack_completed",
                target_type="sharia_import_pack",
                target_id=PACK_VERSION,
                metadata_redacted={
                    **result.as_dict(),
                    "unresolved_source_rows": len(result.unresolved_source_rows),
                    "duplicate_or_migrated_source_rows": len(
                        result.duplicate_or_migrated_source_rows
                    ),
                    "rights_blocked_source_rows": len(
                        result.rights_blocked_source_rows
                    ),
                },
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return result

    async def _ensure_methodologies(
        self,
        bundle: ShariaImportPackBundle,
    ) -> dict[str, ShariaMethodology]:
        now = datetime.now(UTC)
        result: dict[str, ShariaMethodology] = {}
        for package_id, source_definition in bundle.methodology_definitions.items():
            code = PACKAGE_TO_SYSTEM_CODE[package_id]
            methodology = await self.session.scalar(
                select(ShariaMethodology).where(
                    ShariaMethodology.code == code,
                    ShariaMethodology.version == METHODOLOGY_VERSION,
                )
            )
            if methodology is None:
                active_rows = list(
                    (
                        await self.session.scalars(
                            select(ShariaMethodology).where(
                                ShariaMethodology.code == code,
                                ShariaMethodology.status
                                == ShariaMethodologyStatus.ACTIVE,
                            )
                        )
                    ).all()
                )
                for active in active_rows:
                    active.status = ShariaMethodologyStatus.ARCHIVED
                    active.effective_to = now
                methodology = ShariaMethodology(
                    code=code,
                    name=_required_text(source_definition, "display_name"),
                    version=METHODOLOGY_VERSION,
                    description=_methodology_description(source_definition),
                    status=ShariaMethodologyStatus.ACTIVE,
                    governing_body=_required_text(source_definition, "authority"),
                    reviewer_group="Hilal Markets governance reviewers",
                    published_at=now,
                    effective_from=now,
                    rules_json=_methodology_rules(package_id),
                    evidence_requirements_json=_evidence_requirements(package_id),
                )
                self.session.add(methodology)
                await self.session.flush()
            elif methodology.status != ShariaMethodologyStatus.ACTIVE:
                methodology.status = ShariaMethodologyStatus.ACTIVE
                methodology.effective_to = None
            result[package_id] = methodology
        return result

    async def _source_snapshot(
        self,
        bundle: ShariaImportPackBundle,
        package_methodology_id: str,
        rows: list[dict[str, Any]],
    ) -> tuple[SourceSnapshot, ShariaMonitoringRun]:
        raw_payload: dict[str, Any] = {
            "methodology": bundle.methodology_definitions[package_methodology_id],
            "rows": rows,
        }
        if package_methodology_id == "FASSET_SHARIAH_REPORTS":
            raw_payload["noncompliant_guard"] = bundle.fasset_guard
        raw = json.dumps(raw_payload, sort_keys=True, ensure_ascii=False)
        digest = _sha256(raw)
        run_key = (
            f"methodology-pack:{PACK_VERSION}:{package_methodology_id}:{digest}"
        )
        run = await self.session.scalar(
            select(ShariaMonitoringRun).where(
                ShariaMonitoringRun.idempotency_key == run_key
            )
        )
        if run is not None:
            snapshot = await self.session.scalar(
                select(SourceSnapshot)
                .where(SourceSnapshot.monitoring_run_id == run.id)
                .order_by(SourceSnapshot.retrieved_at.desc())
                .limit(1)
            )
            if snapshot is None:
                raise ShariaImportPackError(
                    "pack_snapshot_missing",
                    "The prior package import has no retained source snapshot.",
                )
            return snapshot, run

        now = datetime.now(UTC)
        source_url = _required_text(
            bundle.methodology_definitions[package_methodology_id],
            "source_url",
        )
        run = ShariaMonitoringRun(
            run_kind="methodology_pack_import",
            idempotency_key=run_key,
            status="running",
            source_url=source_url,
            started_at=now,
            next_due_at=now
            + timedelta(hours=self.settings.sharia_source_scan_interval_hours),
        )
        self.session.add(run)
        await self.session.flush()
        snapshot = SourceSnapshot(
            monitoring_run_id=run.id,
            source_url=source_url,
            retrieved_at=now,
            normalized_text=raw,
            raw_content=raw,
            title=(
                f"{bundle.methodology_definitions[package_methodology_id]['display_name']} "
                f"import pack {PACK_VERSION}"
            ),
            headings=["External authority records", "Pending admin review"],
            content_hash=digest,
            meaningful_diff={},
            is_material_change=False,
            fetch_status="success",
            scraper_version=f"methodology-import-pack:{PACK_VERSION}",
            parser_result={
                "methodology_id": package_methodology_id,
                "row_count": len(rows),
                "validated": True,
                "auto_publish": False,
            },
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot, run

    async def _upsert_assessment(
        self,
        *,
        bundle: ShariaImportPackBundle,
        methodology: ShariaMethodology,
        package_methodology_id: str,
        snapshot: SourceSnapshot,
        row: dict[str, Any],
    ) -> tuple[ExternalAssessment, str]:
        source_row_id = _required_text(row, "source_row_id")
        existing = await self.session.scalar(
            select(ExternalAssessment).where(
                ExternalAssessment.methodology_id == methodology.id,
                ExternalAssessment.source_row_id == source_row_id,
            )
        )
        state = "replayed"
        if existing is None:
            legacy = await self._unique_legacy_source_match(
                methodology=methodology,
                package_methodology_id=package_methodology_id,
                row=row,
            )
            if legacy is not None:
                existing = legacy
                state = "adopted"
            else:
                existing = ExternalAssessment(
                    methodology_id=methodology.id,
                    source_snapshot_id=snapshot.id,
                    source_family=SOURCE_FAMILIES[package_methodology_id],
                    source_authority=_required_text(row, "authority_name"),
                    source_url=_required_text(row, "source_url"),
                    source_reference=_source_reference(package_methodology_id, row),
                    asset_name=_required_text(row, "asset_name_source"),
                    asset_symbol=_canonical_symbol(row),
                    exact_status_wording=_required_text(
                        row,
                        "external_status_source",
                    ),
                    sac_meeting_number=_optional_text(row.get("sac_meeting_number")),
                    decision_date=_decision_date(row),
                    regulatory_scope=_scope(bundle, package_methodology_id, row),
                    retrieval_date=_retrieval_datetime(row),
                    exact_row_text=json.dumps(
                        row,
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                    structured_facts=_external_structured_facts(
                        package_methodology_id,
                        row,
                    ),
                    import_hash=_sha256(
                        json.dumps(
                            {
                                "pack_version": PACK_VERSION,
                                "methodology": package_methodology_id,
                                "source_row_id": source_row_id,
                                "row": row,
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                        )
                    ),
                    mapping_state="unresolved",
                    mapping_notes=[],
                )
                self.session.add(existing)
                await self.session.flush()
                state = "created"

        seed = _seed_for_source_row(bundle, source_row_id)
        task = bundle.enrichment_tasks.get(_required_text(seed, "passport_seed_id"))
        if task is None:
            raise ShariaImportPackError(
                "enrichment_task_missing",
                f"No enrichment task exists for {source_row_id}.",
            )
        existing.methodology_id = methodology.id
        existing.source_row_id = source_row_id
        existing.source_snapshot_id = snapshot.id
        existing.structured_facts = _external_structured_facts(
            package_methodology_id,
            row,
        )
        existing.normalized_status = _required_text(row, "normalized_status")
        existing.publication_gate = PUBLICATION_GATES[package_methodology_id]
        existing.rights_state = _rights_state(package_methodology_id, row)
        existing.commercial_display_allowed = _commercial_display_allowed(
            package_methodology_id,
            row,
        )
        existing.manual_verification_required = True
        existing.source_detail_extraction_state = _optional_text(
            row.get("source_detail_extraction_state")
        )
        existing.source_detail_fields = dict(row.get("source_detail_fields") or {})
        existing.passport_seed_id = _required_text(seed, "passport_seed_id")
        existing.passport_seed_snapshot = seed
        existing.enrichment_task_id = _required_text(task, "task_id")
        if existing.enrichment_state not in {"completed", "running"}:
            existing.enrichment_state = "queued"
        await self.session.flush()
        return existing, state

    async def _unique_legacy_source_match(
        self,
        *,
        methodology: ShariaMethodology,
        package_methodology_id: str,
        row: dict[str, Any],
    ) -> ExternalAssessment | None:
        matches = list(
            (
                await self.session.scalars(
                    select(ExternalAssessment).where(
                        ExternalAssessment.methodology_id.is_(None),
                        ExternalAssessment.source_row_id.is_(None),
                        ExternalAssessment.source_family
                        == SOURCE_FAMILIES[package_methodology_id],
                        ExternalAssessment.asset_name
                        == _required_text(row, "asset_name_source"),
                        ExternalAssessment.asset_symbol == _canonical_symbol(row),
                        ExternalAssessment.exact_status_wording
                        == _required_text(row, "external_status_source"),
                    )
                )
            ).all()
        )
        if len(matches) != 1:
            return None
        match = matches[0]
        match.methodology_id = methodology.id
        return match

    async def _map_registered_identity(
        self,
        external: ExternalAssessment,
    ) -> bool:
        candidate = REVIEWED_ASSET_CANDIDATES.get(external.asset_symbol.upper())
        if candidate is None:
            return False
        try:
            await CanonicalAssetMappingService(self.session).map_candidate(
                external,
                candidate,
            )
        except AssetIdentityError:
            return False
        return True

    @staticmethod
    def _identity_conflicts(
        bundle: ShariaImportPackBundle,
        source_row_id: str,
        row: dict[str, Any],
    ) -> list[str]:
        problems: list[str] = []
        if source_row_id in bundle.duplicate_or_migrated_source_rows:
            problems.append(
                "The package marks this identity as duplicate, aliased, or migrated; "
                "manual canonical-identity review is required."
            )
        source_symbol = _required_text(row, "symbol_source")
        canonical_symbol = _canonical_symbol(row)
        aliases = {
            str(value).strip().upper()
            for value in row.get("symbol_alias_candidates") or []
            if str(value).strip()
        }
        if "/" in source_symbol or len(aliases) > 1:
            problems.append(
                "The source exposes multiple ticker aliases; a ticker-only merge is forbidden."
            )
        if source_symbol.upper() != canonical_symbol.upper() and canonical_symbol not in aliases:
            problems.append(
                "The source ticker and canonical-symbol candidate differ without a verified alias."
            )
        return problems

    async def _ensure_review_case(
        self,
        *,
        methodology: ShariaMethodology,
        external: ExternalAssessment,
        source_row_id: str,
        conflict_reasons: list[str],
    ) -> tuple[ReviewCase, bool]:
        key = f"methodology-pack-review:{source_row_id}"
        case = await self.session.scalar(
            select(ReviewCase).where(ReviewCase.idempotency_key == key)
        )
        if case is not None:
            return case, False
        open_case = await self.session.scalar(
            select(ReviewCase)
            .where(
                ReviewCase.external_assessment_id == external.id,
                ReviewCase.done_at.is_(None),
            )
            .order_by(ReviewCase.created_at.desc())
            .limit(1)
        )
        if open_case is not None and open_case.state not in TERMINAL_CASE_STATES:
            open_case.methodology_id = methodology.id
            open_case.source_freshness_deadline = (
                datetime.now(UTC)
                + timedelta(hours=self.settings.sharia_source_scan_interval_hours)
            )
            open_case.next_reminder_at = (
                None
                if self.settings.sharia_import_auto_publish
                else open_case.next_reminder_at or datetime.now(UTC)
            )
            requested = {
                *open_case.requested_evidence,
                "Complete the queued official-source factual enrichment.",
                *conflict_reasons,
            }
            open_case.requested_evidence = sorted(requested)
            return open_case, False

        now = datetime.now(UTC)
        unresolved = external.mapping_state != "mapped"
        reasons = (
            [
                "An external methodology row was imported for bounded automatic publication.",
                "Publication waits for exact identity mapping and a completed, separately "
                "labelled factual dossier. The external provider alone controls the status.",
            ]
            if self.settings.sharia_import_auto_publish
            else [
                "An external methodology row was imported with publication disabled.",
                "A human reviewer must verify attribution, identity, evidence, scope, and rights.",
            ]
        )
        if conflict_reasons:
            reasons.extend(conflict_reasons)
        if unresolved and not conflict_reasons:
            reasons.append(
                "Complete canonical identity using name, chain, asset type, contracts, and "
                "official website; the ticker is not sufficient."
            )
        case = ReviewCase(
            case_reference=_case_reference(source_row_id),
            case_type=(
                "source_identity_conflict" if unresolved else "initial_asset_review"
            ),
            state="needs_evidence",
            publication_state="unpublished",
            canonical_asset_id=external.canonical_asset_id,
            external_assessment_id=external.id,
            methodology_id=methodology.id,
            title=(
                f"Review {external.asset_name} under {methodology.name}"
            ),
            priority="high" if unresolved or not external.commercial_display_allowed else "normal",
            risk_severity="high" if unresolved else "medium",
            human_review_reason=" ".join(reasons),
            requested_evidence=sorted(
                {
                    "Complete the queued official-source factual enrichment.",
                    "Explicitly decide every required methodology criterion.",
                    *conflict_reasons,
                }
            ),
            idempotency_key=key,
            due_at=now + timedelta(hours=self.settings.sharia_review_sla_hours),
            source_freshness_deadline=now
            + timedelta(hours=self.settings.sharia_source_scan_interval_hours),
            next_reminder_at=(
                None if self.settings.sharia_import_auto_publish else now
            ),
        )
        self.session.add(case)
        await self.session.flush()
        return case, True


def load_import_pack(configured_path: str) -> ShariaImportPackBundle:
    root = Path(configured_path).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    nested = root / "HilalMarkets_Sharia_Methodology_Import_Pack"
    if not (root / "manifest.json").is_file() and (nested / "manifest.json").is_file():
        root = nested
    required = [
        "README.md",
        "CODEX_IMPLEMENTATION_PROMPT.txt",
        "manifest.json",
        "data/methodologies.json",
        "data/sc_malaysia_compliant_assets.json",
        "data/shariah_review_bureau_compliant_assets.json",
        "data/fasset_compliant_assets.json",
        "data/fasset_noncompliant_guard.json",
        "data/methodology_union_matrix.json",
        "data/passport_seed_records.jsonl",
        "data/ai_enrichment_queue.jsonl",
        "schemas/passport_enrichment.schema.json",
        "docs/ATTRIBUTION_AND_RIGHTS.md",
    ]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ShariaImportPackError(
            "pack_files_missing",
            f"Required import-pack files are missing: {', '.join(missing)}",
        )

    manifest = _load_json(root / "manifest.json")
    if manifest.get("version") != PACK_VERSION:
        raise ShariaImportPackError(
            "pack_version_unsupported",
            f"Expected import pack {PACK_VERSION}.",
        )
    definitions = {
        _required_text(row, "methodology_id"): row
        for row in _load_json(root / "data/methodologies.json")
    }
    if set(definitions) != set(PACKAGE_TO_SYSTEM_CODE):
        raise ShariaImportPackError(
            "methodology_set_invalid",
            "The pack must contain exactly the three approved methodology definitions.",
        )

    rows = {
        methodology_id: _load_json(root / "data" / filename)
        for methodology_id, filename in DATASET_FILES.items()
    }
    guard = _load_json(root / "data/fasset_noncompliant_guard.json")
    seeds = {
        _required_text(row, "passport_seed_id"): row
        for row in _load_jsonl(root / "data/passport_seed_records.jsonl")
    }
    tasks = {
        _required_text(row, "passport_seed_id"): row
        for row in _load_jsonl(root / "data/ai_enrichment_queue.jsonl")
    }
    _validate_bundle(definitions, rows, guard, seeds, tasks, manifest)
    return ShariaImportPackBundle(
        root=root,
        manifest=manifest,
        methodology_definitions=definitions,
        rows=rows,
        fasset_guard=guard,
        passport_seeds=seeds,
        enrichment_tasks=tasks,
        duplicate_or_migrated_source_rows=frozenset(
            _duplicate_or_migrated_rows(rows)
        ),
    )


def _validate_bundle(
    definitions: dict[str, dict[str, Any]],
    rows: dict[str, list[dict[str, Any]]],
    guard: list[dict[str, Any]],
    seeds: dict[str, dict[str, Any]],
    tasks: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    counts = manifest.get("counts") or {}
    expected_manifest = {
        "methodologies": 3,
        "sc_malaysia_compliant": 15,
        "shariah_review_bureau_compliant": 31,
        "fasset_compliant_source_rows": 188,
        "fasset_noncompliant_guard_rows": 52,
        "passport_seeds": 234,
        "ai_enrichment_tasks": 234,
    }
    if any(int(counts.get(key, -1)) != value for key, value in expected_manifest.items()):
        raise ShariaImportPackError(
            "manifest_counts_invalid",
            "Import-pack manifest counts do not match the approved package contract.",
        )
    source_ids: set[str] = set()
    passport_by_source: dict[str, str] = {}
    for seed_id, seed in seeds.items():
        external = seed.get("external_assessment") or {}
        source_row_id = _required_text(external, "source_row_id")
        if source_row_id in passport_by_source:
            raise ShariaImportPackError(
                "duplicate_passport_source_row",
                f"More than one Passport seed references {source_row_id}.",
            )
        passport_by_source[source_row_id] = seed_id
        if seed.get("hilalmarkets_factual_profile") is not None:
            raise ShariaImportPackError(
                "prewritten_ai_profile",
                f"Passport seed {seed_id} contains an unreviewed factual profile.",
            )
        if seed.get("admin_review_required") is not True:
            raise ShariaImportPackError(
                "admin_review_bypass",
                f"Passport seed {seed_id} bypasses admin review.",
            )

    for methodology_id, methodology_rows in rows.items():
        if len(methodology_rows) != EXPECTED_COUNTS[methodology_id]:
            raise ShariaImportPackError(
                "dataset_count_invalid",
                f"{methodology_id} has an unexpected row count.",
            )
        for row in methodology_rows:
            source_row_id = _required_text(row, "source_row_id")
            if source_row_id in source_ids:
                raise ShariaImportPackError(
                    "duplicate_source_row_id",
                    f"Duplicate source row {source_row_id}.",
                )
            source_ids.add(source_row_id)
            if row.get("methodology_id") != methodology_id:
                raise ShariaImportPackError(
                    "methodology_cross_contamination",
                    f"{source_row_id} belongs to a different methodology.",
                )
            if row.get("auto_publish") is not False:
                raise ShariaImportPackError(
                    "auto_publish_forbidden",
                    f"{source_row_id} permits automatic publication.",
                )
            if row.get("publication_state") != "PENDING_ADMIN_REVIEW":
                raise ShariaImportPackError(
                    "publication_gate_missing",
                    f"{source_row_id} is not pending admin review.",
                )
            if row.get("normalized_status") != "ELIGIBLE_EXTERNAL_REFERENCE":
                raise ShariaImportPackError(
                    "noncompliant_row_in_eligible_dataset",
                    f"{source_row_id} is not an eligible external reference.",
                )
            matching_seed_id = passport_by_source.get(source_row_id)
            if matching_seed_id is None or matching_seed_id not in tasks:
                raise ShariaImportPackError(
                    "seed_or_task_missing",
                    f"{source_row_id} has no matching Passport seed and enrichment task.",
                )
            task = tasks[matching_seed_id]
            if task.get("model_output_destination") != "hilalmarkets_factual_profile":
                raise ShariaImportPackError(
                    "ai_destination_invalid",
                    f"{source_row_id} routes AI output outside the factual profile.",
                )
            if task.get("never_write_to") != (
                "external_assessment or source_authority_section"
            ):
                raise ShariaImportPackError(
                    "ai_authority_boundary_missing",
                    f"{source_row_id} does not protect external-authority fields.",
                )

    if len(guard) != 52 or any(
        row.get("normalized_status") != "NOT_ELIGIBLE_EXTERNAL_REFERENCE"
        or row.get("publication_state") != "GUARD_ONLY"
        for row in guard
    ):
        raise ShariaImportPackError(
            "fasset_guard_invalid",
            "The 52-row Fasset non-compliant guard is incomplete or unsafe.",
        )
    compliant_identity = {
        _source_identity_key(row)
        for row in rows["FASSET_SHARIAH_REPORTS"]
    }
    guard_identity = {_source_identity_key(row) for row in guard}
    overlap = compliant_identity & guard_identity
    if overlap:
        raise ShariaImportPackError(
            "fasset_guard_overlap",
            "A Fasset guard identity also appears in the compliant dataset.",
        )
    if len(source_ids) != 234 or len(seeds) != 234 or len(tasks) != 234:
        raise ShariaImportPackError(
            "pack_relationship_count_invalid",
            "Source rows, Passport seeds, and enrichment tasks are not one-to-one.",
        )
    if set(definitions) != set(rows):
        raise ShariaImportPackError(
            "methodology_dataset_mismatch",
            "Methodology definitions and datasets do not match.",
        )


def _duplicate_or_migrated_rows(
    rows_by_methodology: dict[str, list[dict[str, Any]]],
) -> set[str]:
    conflicts: set[str] = set()
    for rows in rows_by_methodology.values():
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        by_name: dict[str, list[dict[str, Any]]] = {}
        by_alias: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_symbol.setdefault(_canonical_symbol(row).upper(), []).append(row)
            by_name.setdefault(_identity_text(row.get("asset_name_source")), []).append(row)
            aliases = {
                str(item).strip().upper()
                for item in row.get("symbol_alias_candidates") or []
                if str(item).strip()
            }
            aliases.add(_canonical_symbol(row).upper())
            for alias in aliases:
                by_alias.setdefault(alias, []).append(row)
        for grouped in (by_symbol, by_name, by_alias):
            for matches in grouped.values():
                if len(matches) > 1:
                    conflicts.update(_required_text(row, "source_row_id") for row in matches)
        for row in rows:
            values = {
                _required_text(row, "symbol_source").upper(),
                _canonical_symbol(row).upper(),
                *{
                    str(item).strip().upper()
                    for item in row.get("symbol_alias_candidates") or []
                },
            }
            if values & {"MATIC", "POL"} and len(values & {"MATIC", "POL"}) > 1:
                conflicts.add(_required_text(row, "source_row_id"))
            if values & {"RNDR", "RENDER"} and len(values & {"RNDR", "RENDER"}) > 1:
                conflicts.add(_required_text(row, "source_row_id"))
    return conflicts


def _methodology_rules(package_id: str) -> dict[str, Any]:
    labels = {
        "SC_MALAYSIA_SAC_DIGITAL_ASSETS": "SC Malaysia SAC",
        "SHARIAH_REVIEW_BUREAU": "Shariah Review Bureau",
        "FASSET_SHARIAH_REPORTS": "Fasset",
    }
    adapters = {
        "SC_MALAYSIA_SAC_DIGITAL_ASSETS": "sc_malaysia",
        "SHARIAH_REVIEW_BUREAU": "srb",
        "FASSET_SHARIAH_REPORTS": "fasset",
    }
    outcomes = ["pass", "qualification", "fail", "not_applicable", "needs_evidence"]
    use_decisions = [
        "covered",
        "qualified",
        "not_covered",
        "not_applicable",
        "under_review",
        "excluded",
    ]
    required_criteria = [
        (
            "canonical_asset_identity",
            "Canonical asset identity",
            "Verify name, chain, asset type, contracts, official website, and exact market.",
            ["canonical_identity"],
        ),
        (
            "official_methodology_reference",
            f"Published {labels[package_id]} reference",
            "Verify authority wording, date, identity, scope, source snapshot, and rights.",
            ["official_external_reference"],
        ),
        (
            "evidence_completeness",
            "Evidence completeness and freshness",
            "Confirm mandatory official evidence is complete, current, and consistent.",
            ["factual_dossier"],
        ),
        (
            "source_scope_and_identity",
            "Source scope and exact asset match",
            "Confirm this exact asset and record jurisdictional and product limitations.",
            ["official_external_reference", "canonical_identity"],
        ),
        (
            "use_specific_factual_review",
            "Hilal Markets use-specific factual review",
            "Review spot, staking, lending, yield, wrappers, and derivatives separately.",
            ["factual_dossier"],
        ),
    ]
    return {
        "schema_version": "1",
        "criteria_version": f"import-pack.{PACK_VERSION}",
        "source_family": SOURCE_FAMILIES[package_id],
        "source_adapter": adapters[package_id],
        "executable": True,
        "spot_only": True,
        "publication_requires_admin_approval": True,
        "required_criteria": [
            {
                "key": key,
                "label": label,
                "description": description,
                "required": True,
                "allowed_outcomes": outcomes,
                "evidence_categories": categories,
                "qualification_rules": {"written_reason_required": True},
                "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
            }
            for key, label, description, categories in required_criteria
        ],
        "use_cases": [
            {
                "key": f"asset_level_{adapters[package_id]}_reference",
                "label": f"Asset-level {labels[package_id]} reference",
                "description": "The exact asset-level status stated by the external source.",
                "required": True,
                "allowed_decisions": use_decisions,
                "criterion_keys": [
                    "official_methodology_reference",
                    "source_scope_and_identity",
                ],
                "evidence_categories": ["official_external_reference"],
                "default_scope": "Only the exact asset-level external reference.",
                "execution_blocking_decisions": [
                    "not_covered",
                    "not_applicable",
                    "under_review",
                    "excluded",
                ],
            },
            {
                "key": "spot_ownership_and_monitoring",
                "label": "Spot ownership and market monitoring",
                "description": "Hilal Markets spot-only, non-execution monitoring scope.",
                "required": True,
                "allowed_decisions": use_decisions,
                "criterion_keys": ["use_specific_factual_review"],
                "evidence_categories": ["factual_dossier"],
                "default_scope": "Spot monitoring only; no leverage or execution.",
                "execution_blocking_decisions": [
                    "not_covered",
                    "not_applicable",
                    "under_review",
                    "excluded",
                ],
            },
        ],
    }


def _evidence_requirements(package_id: str) -> dict[str, Any]:
    critical = [
        "canonical_asset.identity_hash",
        "external_assessment.source_row_id",
        "external_assessment.exact_status_wording",
        "external_assessment.source_snapshot_id",
        "dossier.evidence_package_hash",
    ]
    return {
        "schema_version": "1",
        "mandatory_source_categories": [
            "canonical_identity",
            "official_external_reference",
            "factual_dossier",
        ],
        "minimum_evidence_completeness": 1.0,
        "maximum_source_age_days": 1,
        "critical_missing_fields": critical,
        "contradiction_policy": "block_any_unresolved",
        "review_cadence_days": 1,
        "publication_rights_clearance_required": package_id
        in {"SHARIAH_REVIEW_BUREAU", "FASSET_SHARIAH_REPORTS"},
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShariaImportPackError(
            "pack_json_invalid",
            f"Could not validate import-pack file {path.name}.",
        ) from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not an object")
            rows.append(value)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ShariaImportPackError(
            "pack_jsonl_invalid",
            f"Could not validate import-pack file {path.name}.",
        ) from exc
    return rows


def _seed_for_source_row(
    bundle: ShariaImportPackBundle,
    source_row_id: str,
) -> dict[str, Any]:
    matches = [
        seed
        for seed in bundle.passport_seeds.values()
        if (seed.get("external_assessment") or {}).get("source_row_id")
        == source_row_id
    ]
    if len(matches) != 1:
        raise ShariaImportPackError(
            "passport_seed_not_unique",
            f"Expected one Passport seed for {source_row_id}.",
        )
    return matches[0]


def _external_structured_facts(
    package_methodology_id: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    excluded = {
        "authority_name",
        "asset_name_source",
        "auto_publish",
        "canonical_symbol_candidate",
        "external_status_source",
        "manual_verification_required",
        "methodology_id",
        "normalized_status",
        "publication_state",
        "retrieved_at",
        "source_detail_fields",
        "source_row_id",
        "source_url",
        "symbol_source",
    }
    return {
        "provenance": "external_authority_import_pack",
        "package_version": PACK_VERSION,
        "package_methodology_id": package_methodology_id,
        "source_symbol_wording": row.get("symbol_source"),
        "source_fields": {
            key: value for key, value in row.items() if key not in excluded
        },
        "ai_generated": False,
    }


def _scope(
    bundle: ShariaImportPackBundle,
    package_methodology_id: str,
    row: dict[str, Any],
) -> str:
    return (
        _optional_text(row.get("jurisdiction_scope"))
        or _required_text(
            bundle.methodology_definitions[package_methodology_id],
            "scope",
        )
    )


def _source_reference(
    package_methodology_id: str,
    row: dict[str, Any],
) -> str:
    if package_methodology_id == "SC_MALAYSIA_SAC_DIGITAL_ASSETS":
        return (
            f"{_required_text(row, 'sac_meeting_number')} SAC Meeting "
            f"({_required_text(row, 'decision_date')})"
        )
    if package_methodology_id == "SHARIAH_REVIEW_BUREAU":
        return _required_text(row, "assessment_date_source")
    return _required_text(row, "source_row_id")


def _rights_state(package_methodology_id: str, row: dict[str, Any]) -> str:
    if package_methodology_id == "SC_MALAYSIA_SAC_DIGITAL_ASSETS":
        return "PUBLIC_SOURCE_ATTRIBUTION_REVIEWED"
    if package_methodology_id == "SHARIAH_REVIEW_BUREAU":
        return _required_text(row, "rights_state")
    return "RIGHTS_REVIEW_REQUIRED_BEFORE_COMMERCIAL_PUBLICATION"


def _commercial_display_allowed(
    package_methodology_id: str,
    row: dict[str, Any],
) -> bool:
    if package_methodology_id == "SC_MALAYSIA_SAC_DIGITAL_ASSETS":
        return True
    return bool(row.get("commercial_display_allowed", False))


def _retrieval_datetime(row: dict[str, Any]) -> datetime:
    value = _required_text(row, "retrieved_at")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ShariaImportPackError(
            "retrieval_date_invalid",
            f"Invalid retrieval date on {_required_text(row, 'source_row_id')}.",
        ) from exc
    return datetime.combine(parsed, time.min, tzinfo=UTC)


def _decision_date(row: dict[str, Any]) -> date | None:
    value = _optional_text(row.get("decision_date"))
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ShariaImportPackError(
            "decision_date_invalid",
            f"Invalid decision date on {_required_text(row, 'source_row_id')}.",
        ) from exc


def _canonical_symbol(row: dict[str, Any]) -> str:
    value = (
        _optional_text(row.get("canonical_symbol_candidate"))
        or _required_text(row, "symbol_source")
    ).upper()
    if len(value) > 32:
        raise ShariaImportPackError(
            "symbol_too_long",
            f"Canonical symbol candidate is too long on {row.get('source_row_id')}.",
        )
    return value


def _source_identity_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        _identity_text(row.get("asset_name_source")),
        _identity_text(
            row.get("canonical_symbol_candidate") or row.get("symbol_source")
        ),
    )


def _identity_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _required_text(row: dict[str, Any], key: str) -> str:
    value = _optional_text(row.get(key))
    if value is None:
        raise ShariaImportPackError(
            "required_pack_field_missing",
            f"Required import-pack field {key} is missing.",
        )
    return value


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _case_reference(source_row_id: str) -> str:
    prefix = re.sub(r"[^A-Z0-9]+", "-", source_row_id.upper()).strip("-")[:22]
    suffix = _sha256(source_row_id)[:10].upper()
    return f"IMP-{prefix}-{suffix}"[:40]


def _methodology_description(source_definition: dict[str, Any]) -> str:
    scope = _required_text(source_definition, "scope")
    prohibitions = " ".join(
        str(item).strip()
        for item in source_definition.get("must_not_infer") or []
        if str(item).strip()
    )
    return f"{scope} {prohibitions}".strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

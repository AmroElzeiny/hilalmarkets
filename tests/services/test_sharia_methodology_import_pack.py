import json
from pathlib import Path

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ExternalAssessment,
    PublishedAssetAssessment,
    ReviewCase,
    ShariaMethodology,
    SourceSnapshot,
    TelegramNotificationAttempt,
)
from ai_market_monitor.services.sharia_admin_dashboard import (
    ShariaAdminDashboardService,
)
from ai_market_monitor.services.sharia_import_pack import (
    ShariaMethodologyImportPackService,
    load_import_pack,
)

PACK_ROOT = (
    Path(__file__).resolve().parents[2]
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
)


def test_methodology_import_pack_contract_has_exact_approved_rows():
    bundle = load_import_pack(str(PACK_ROOT))

    # Each authority states its own expected row count in its definition, and the
    # loader has already refused the pack if a dataset disagreed. Asserting the
    # shipped numbers here keeps the approved contract visible in the test.
    assert {
        methodology_id: len(rows)
        for methodology_id, rows in bundle.rows.items()
    } == {
        "SC_MALAYSIA_SAC_DIGITAL_ASSETS": 15,
        "SHARIAH_REVIEW_BUREAU": 31,
        "FASSET_SHARIAH_REPORTS": 188,
    }
    assert {
        methodology_id: len(rows)
        for methodology_id, rows in bundle.rows.items()
    } == {
        package_id: spec.records_count
        for package_id, spec in bundle.specs.items()
    }
    assert bundle.total_guard_rows == 52
    assert len(bundle.passport_seeds) == 234
    assert len(bundle.enrichment_tasks) == 234
    assert all(
        row["publication_state"] == "PENDING_ADMIN_REVIEW"
        and row["auto_publish"] is False
        for rows in bundle.rows.values()
        for row in rows
    )
    assert all(
        task["model_output_destination"] == "hilalmarkets_factual_profile"
        and task["never_write_to"]
        == "external_assessment or source_authority_section"
        for task in bundle.enrichment_tasks.values()
    )


async def test_import_pack_is_independent_idempotent_and_never_publishes(
    test_context,
):
    settings = test_context["settings"].model_copy(
        update={
            "sharia_import_pack_path": str(PACK_ROOT),
            "sharia_admin_telegram_chat_id": "test-admin-chat",
            "sharia_review_reminder_hours": 6,
            "sharia_source_scan_interval_hours": 24,
        }
    )
    bundle = load_import_pack(str(PACK_ROOT))
    guard_ids = {
        row["source_row_id"]
        for rows in bundle.guard_rows.values()
        for row in rows
    }

    async with test_context["session_factory"]() as session:
        service = ShariaMethodologyImportPackService(session, settings)
        first = await service.import_bundle()
        await session.commit()
        second = await service.import_bundle()
        await session.commit()

        methodology_counts = dict(
            (
                await session.execute(
                    select(
                        ShariaMethodology.code,
                        func.count(ExternalAssessment.id),
                    )
                    .join(
                        ExternalAssessment,
                        ExternalAssessment.methodology_id
                        == ShariaMethodology.id,
                    )
                    .group_by(ShariaMethodology.code)
                )
            ).all()
        )
        externals = list(
            (
                await session.scalars(
                    select(ExternalAssessment).order_by(
                        ExternalAssessment.source_row_id
                    )
                )
            ).all()
        )
        cases = list((await session.scalars(select(ReviewCase))).all())
        methodologies = list(
            (
                await session.scalars(
                    select(ShariaMethodology).where(
                        ShariaMethodology.code.in_(
                            {
                                "SC_MALAYSIA_SAC_REFERENCE",
                                "SHARIAH_REVIEW_BUREAU",
                                "FASSET_SHARIAH_REPORTS",
                            }
                        )
                    )
                )
            ).all()
        )
        notifications = int(
            await session.scalar(
                select(func.count(TelegramNotificationAttempt.id))
            )
            or 0
        )
        source_snapshots = int(
            await session.scalar(select(func.count(SourceSnapshot.id))) or 0
        )
        public_assessments = int(
            await session.scalar(select(func.count(AssetShariaAssessment.id)))
            or 0
        )
        publications = int(
            await session.scalar(
                select(func.count(PublishedAssetAssessment.id))
            )
            or 0
        )
        sc_external = next(
            row
            for row in externals
            if row.source_family == "sc_malaysia_sac"
        )
        sc_case = next(
            case
            for case in cases
            if case.external_assessment_id == sc_external.id
        )
        review_detail = await ShariaAdminDashboardService(
            session
        ).case_detail(sc_case.id)

    assert methodology_counts == {
        "SC_MALAYSIA_SAC_REFERENCE": 15,
        "SHARIAH_REVIEW_BUREAU": 31,
        "FASSET_SHARIAH_REPORTS": 188,
    }
    assert first.created_assessments == 234
    assert first.adopted_assessments == 0
    assert first.review_cases_created == 234
    assert first.enrichment_jobs_queued == 234
    assert first.telegram_notifications_queued == 234
    assert first.guard_rows_retained == 52
    assert first.auto_published == 0
    assert len(first.rights_blocked_source_rows) == 219

    assert second.created_assessments == 0
    assert second.adopted_assessments == 0
    assert second.replayed_assessments == 234
    assert second.review_cases_created == 0
    assert second.enrichment_jobs_queued == 0
    assert second.telegram_notifications_queued == 0

    assert len(externals) == 234
    assert len(cases) == 234
    assert notifications == 234
    assert source_snapshots == 3
    assert public_assessments == 0
    assert publications == 0
    assert guard_ids.isdisjoint(
        {row.source_row_id for row in externals}
    )
    assert all(row.methodology_id is not None for row in externals)
    assert all(row.manual_verification_required for row in externals)
    assert all(
        row.normalized_status == "ELIGIBLE_EXTERNAL_REFERENCE"
        for row in externals
    )
    assert all(
        row.structured_facts["provenance"]
        == "external_authority_import_pack"
        and row.structured_facts["ai_generated"] is False
        for row in externals
    )
    external_by_source = {
        row.source_row_id: row
        for row in externals
    }
    assert all(
        external_by_source[source_row_id].mapping_state == "conflict"
        for source_row_id in bundle.duplicate_or_migrated_source_rows
    )
    assert {
        case.external_assessment_id
        for case in cases
    } == {row.id for row in externals}
    assert all(
        "score" not in json.dumps(
            methodology.rules_json,
            sort_keys=True,
        ).casefold()
        for methodology in methodologies
    )
    assert review_detail["external_snapshot"] is not None
    assert review_detail["external_snapshot"].content_hash
    assert review_detail["source_supported_explanation"]
    assert (
        "Complete the queued official-source factual enrichment."
        in review_detail["missing_evidence"]
    )


async def test_import_pack_preserves_source_scope_and_rights_boundaries(
    test_context,
):
    settings = test_context["settings"].model_copy(
        update={"sharia_import_pack_path": str(PACK_ROOT)}
    )
    async with test_context["session_factory"]() as session:
        await ShariaMethodologyImportPackService(
            session,
            settings,
        ).import_bundle()
        await session.commit()

        sc_rows = list(
            (
                await session.scalars(
                    select(ExternalAssessment).where(
                        ExternalAssessment.source_family
                        == "sc_malaysia_sac"
                    )
                )
            ).all()
        )
        srb_rows = list(
            (
                await session.scalars(
                    select(ExternalAssessment).where(
                        ExternalAssessment.source_family
                        == "shariah_review_bureau"
                    )
                )
            ).all()
        )
        fasset_rows = list(
            (
                await session.scalars(
                    select(ExternalAssessment).where(
                        ExternalAssessment.source_family
                        == "fasset_shariah_reports"
                    )
                )
            ).all()
        )

    assert all(
        row.sac_meeting_number
        and row.decision_date
        and row.commercial_display_allowed
        for row in sc_rows
    )
    assert all(
        not row.commercial_display_allowed
        and row.rights_state
        and row.rights_clearance_reference is None
        for row in srb_rows
    )
    assert all(
        not row.commercial_display_allowed
        and row.source_detail_extraction_state
        == "FETCH_AND_VERIFY_FROM_SOURCE_BEFORE_PUBLICATION"
        and all(
            value is None
            for value in row.source_detail_fields.values()
        )
        and row.enrichment_state == "queued"
        for row in fasset_rows
    )
    assert all(
        json.loads(row.exact_row_text)["methodology_id"]
        == "SC_MALAYSIA_SAC_DIGITAL_ASSETS"
        for row in sc_rows
    )
    assert all(
        json.loads(row.exact_row_text)["methodology_id"]
        == "SHARIAH_REVIEW_BUREAU"
        for row in srb_rows
    )
    assert all(
        json.loads(row.exact_row_text)["methodology_id"]
        == "FASSET_SHARIAH_REPORTS"
        for row in fasset_rows
    )

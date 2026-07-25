import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ExternalAssessment,
    PublishedAssetAssessment,
    ShariaMonitoringRun,
    SourceSnapshot,
)
from ai_market_monitor.services.fasset_import import (
    FassetFetchedSource,
    FassetImporter,
    FassetParser,
)

FASSET_HTML = """
<html><body>
  <div>1</div><h2>Bitcoin</h2><div>BTC</div>
  <div>Shariah Compliant</div><div>Not Compliant</div>
  <h4>Platform Purpose</h4><p>Peer-to-peer digital money.</p>
  <h4>Token Utility</h4><p>Payments and value transfer.</p>
  <h3>Blockchain Protocol &amp; Operation</h3>
  <h4>Data Structure</h4><p>Blockchain.</p>
  <h3>Shariah Verdict</h3><h4>Shariah Compliant</h4>

  <div>2</div><h2>Example Asset</h2><div>BAD</div>
  <div>Shariah Compliant</div><div>Not Compliant</div>
  <h4>Platform Purpose</h4><p>Example profile.</p>
  <h4>Token Utility</h4><p>Example utility.</p>
  <h3>Shariah Verdict</h3><h4>Not Compliant</h4>

  <div>3</div><h2>Render</h2><div>RNDR / RENDER</div>
  <div>Shariah Compliant</div><div>Not Compliant</div>
  <h4>Platform Purpose</h4><p>Distributed graphics rendering.</p>
  <h4>Token Utility</h4><p>Compute-market payments.</p>
  <h3>Shariah Verdict</h3><h4>Shariah Compliant</h4>

  <div>4</div><h2>Render</h2><div>RENDER</div>
  <div>Shariah Compliant</div><div>Not Compliant</div>
  <h4>Platform Purpose</h4><p>Distributed GPU rendering and compute.</p>
  <h4>Token Utility</h4><p>Compute-market payments.</p>
  <h3>Shariah Verdict</h3><h4>Shariah Compliant</h4>
</body></html>
"""

FASSET_COMPACT_HTML = """
<html><body>
  <h1>Shariah Reports</h1>
  <div>Bitcoin</div><div>BTC</div><div>Shariah Compliant</div>
  <div>Example Asset</div><div>BAD</div><div>Not Compliant</div>
  <div>Render</div><div>RNDR / RENDER</div><div>Shariah Compliant</div>
  <div>Render</div><div>RENDER</div><div>Shariah Compliant</div>
  <div>Gemini Dollar</div><div>GUSD</div><div>Shariah Compliant</div>
  <div>GUSD</div><div>GUSD</div><div>Shariah Compliant</div>
</body></html>
"""


class StaticFassetFetcher:
    def __init__(self, retrieved_at: datetime, content: str = FASSET_HTML):
        self.retrieved_at = retrieved_at
        self.content = content

    async def fetch(self, url: str) -> FassetFetchedSource:
        return FassetFetchedSource(
            url=url,
            status_code=200,
            content=self.content,
            headers={"content-type": "text/html"},
            retrieved_at=self.retrieved_at,
        )


async def _seed_package_assessment(
    session,
    *,
    source_row_id: str,
    asset_name: str,
    asset_symbol: str,
) -> ExternalAssessment:
    retrieved_at = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
    run = ShariaMonitoringRun(
        run_kind="methodology_pack_import",
        idempotency_key=f"package-seed:{source_row_id}",
        status="completed",
        source_url="https://www.fasset.com/shariah-reports/",
        started_at=retrieved_at,
        completed_at=retrieved_at,
    )
    session.add(run)
    await session.flush()
    snapshot = SourceSnapshot(
        monitoring_run_id=run.id,
        source_url="https://www.fasset.com/shariah-reports/",
        retrieved_at=retrieved_at,
        http_status=200,
        raw_content=None,
        normalized_text="Retained package import row.",
        content_hash=hashlib.sha256(source_row_id.encode()).hexdigest(),
        fetch_status="success",
        scraper_version="methodology-pack-test",
    )
    session.add(snapshot)
    await session.flush()
    assessment = ExternalAssessment(
        source_snapshot_id=snapshot.id,
        source_family="fasset_shariah_reports",
        source_authority="Fasset",
        source_url="https://www.fasset.com/shariah-reports/",
        source_reference=source_row_id,
        source_row_id=source_row_id,
        asset_name=asset_name,
        asset_symbol=asset_symbol,
        exact_status_wording="Shariah Compliant",
        regulatory_scope="Asset-level external reference.",
        retrieval_date=retrieved_at,
        exact_row_text="Retained package import row.",
        structured_facts={"authority_note": "Preserve this package field."},
        source_detail_extraction_state=(
            "FETCH_AND_VERIFY_FROM_SOURCE_BEFORE_PUBLICATION"
        ),
        source_detail_fields={},
        import_hash=hashlib.sha256(f"import:{source_row_id}".encode()).hexdigest(),
        mapping_state="unresolved",
        mapping_notes=[],
    )
    session.add(assessment)
    await session.flush()
    return assessment


def test_fasset_parser_uses_only_explicit_verdict_and_deduplicates_aliases():
    result = FassetParser(minimum_profile_count=4).parse(
        FASSET_HTML,
        url="https://www.fasset.com/shariah-reports",
    )

    assert result.total_profiles == 4
    assert [(row.name, row.symbol) for row in result.profiles] == [
        ("Bitcoin", "BTC"),
        ("Render", "RENDER"),
    ]
    assert {row["reason"] for row in result.excluded_profiles} == {
        "verdict_not_compliant",
        "superseded_duplicate_profile",
    }
    render = result.profiles[-1]
    assert render.exact_status_wording == "Shariah Compliant"
    assert render.facts["platform_purpose"] == (
        "Distributed GPU rendering and compute."
    )


def test_fasset_parser_supports_compact_authority_listing_without_inventing_facts():
    result = FassetParser(minimum_profile_count=6).parse(
        FASSET_COMPACT_HTML,
        url="https://www.fasset.com/shariah-reports",
    )

    assert result.total_profiles == 6
    assert [(row.name, row.symbol) for row in result.profiles] == [
        ("Bitcoin", "BTC"),
        ("Render", "RENDER"),
    ]
    assert result.profiles[0].facts == {}
    assert {row["reason"] for row in result.excluded_profiles} == {
        "verdict_not_compliant",
        "superseded_duplicate_profile",
        "duplicate_symbol_identity_conflict",
    }
    assert sum(
        row["reason"] == "duplicate_symbol_identity_conflict"
        for row in result.excluded_profiles
    ) == 2


async def test_fasset_import_is_idempotent_and_never_publishes(test_context):
    retrieved_at = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)
    settings = test_context["settings"].model_copy(
        update={
            "fasset_minimum_profile_count": 4,
            "sharia_source_scan_interval_hours": 240,
        }
    )
    async with test_context["session_factory"]() as session:
        importer = FassetImporter(
            session,
            settings,
            fetcher=StaticFassetFetcher(retrieved_at),
            parser=FassetParser(minimum_profile_count=4),
        )
        first = await importer.import_latest()
        await session.commit()
        second = await importer.import_latest()
        await session.commit()

        imported = list(
            (
                await session.scalars(
                    select(ExternalAssessment).order_by(
                        ExternalAssessment.asset_symbol
                    )
                )
            ).all()
        )
        run = await session.get(ShariaMonitoringRun, UUID(first.run_id))
        assessment_count = int(
            await session.scalar(select(func.count(AssetShariaAssessment.id))) or 0
        )
        publication_count = int(
            await session.scalar(select(func.count(PublishedAssetAssessment.id))) or 0
        )

    assert first.created_assessments == 2
    assert first.explicit_compliant_profiles == 2
    assert first.excluded_profiles == 2
    assert second.idempotent_replay is True
    assert [row.asset_symbol for row in imported] == ["BTC", "RENDER"]
    assert all(row.source_family == "fasset_shariah_reports" for row in imported)
    assert imported[0].structured_facts["token_utility"] == (
        "Payments and value transfer."
    )
    assert run is not None
    persisted_next_due = run.next_due_at
    if persisted_next_due.tzinfo is None:
        persisted_next_due = persisted_next_due.replace(tzinfo=UTC)
    assert persisted_next_due == retrieved_at + timedelta(days=10)
    assert assessment_count == 0
    assert publication_count == 0


async def test_fasset_live_detail_enriches_unique_package_row_and_blocks_ambiguity(
    test_context,
):
    retrieved_at = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
    settings = test_context["settings"].model_copy(
        update={
            "fasset_minimum_profile_count": 4,
            "sharia_source_scan_interval_hours": 24,
        }
    )
    async with test_context["session_factory"]() as session:
        bitcoin = await _seed_package_assessment(
            session,
            source_row_id="FASSET-001-bitcoin",
            asset_name="Bitcoin",
            asset_symbol="BTC",
        )
        render_a = await _seed_package_assessment(
            session,
            source_row_id="FASSET-032-render",
            asset_name="Render",
            asset_symbol="RENDER",
        )
        render_b = await _seed_package_assessment(
            session,
            source_row_id="FASSET-051-render",
            asset_name="Render",
            asset_symbol="RENDER",
        )
        await session.commit()

        result = await FassetImporter(
            session,
            settings,
            fetcher=StaticFassetFetcher(retrieved_at),
            parser=FassetParser(minimum_profile_count=4),
        ).import_latest()
        await session.commit()
        await session.refresh(bitcoin)
        await session.refresh(render_a)
        await session.refresh(render_b)
        assessment_count = int(
            await session.scalar(select(func.count(ExternalAssessment.id))) or 0
        )
        publication_count = int(
            await session.scalar(select(func.count(PublishedAssetAssessment.id))) or 0
        )

    assert result.created_assessments == 0
    assert result.updated_package_assessments == 1
    assert result.conflicted_package_assessments == 2
    assert assessment_count == 3
    assert bitcoin.source_detail_extraction_state == "FETCHED_AND_VERIFIED"
    assert bitcoin.source_detail_snapshot_id == UUID(result.snapshot_id)
    assert bitcoin.source_detail_fields["token_utility"] == (
        "Payments and value transfer."
    )
    assert bitcoin.structured_facts == {
        "authority_note": "Preserve this package field."
    }
    assert render_a.mapping_state == "identity_conflict"
    assert render_b.mapping_state == "identity_conflict"
    assert render_a.source_detail_fields == {}
    assert render_b.source_detail_fields == {}
    assert publication_count == 0


async def test_fasset_compact_listing_does_not_claim_source_details_were_verified(
    test_context,
):
    retrieved_at = datetime(2026, 7, 24, 9, 0, tzinfo=UTC)
    settings = test_context["settings"].model_copy(
        update={
            "fasset_minimum_profile_count": 6,
            "sharia_source_scan_interval_hours": 24,
        }
    )
    async with test_context["session_factory"]() as session:
        bitcoin = await _seed_package_assessment(
            session,
            source_row_id="FASSET-001-bitcoin",
            asset_name="Bitcoin",
            asset_symbol="BTC",
        )
        await session.commit()
        result = await FassetImporter(
            session,
            settings,
            fetcher=StaticFassetFetcher(retrieved_at, FASSET_COMPACT_HTML),
            parser=FassetParser(minimum_profile_count=6),
        ).import_latest()
        await session.commit()
        await session.refresh(bitcoin)

    assert result.updated_package_assessments == 0
    assert bitcoin.source_detail_extraction_state == (
        "FETCH_AND_VERIFY_FROM_SOURCE_BEFORE_PUBLICATION"
    )
    assert bitcoin.source_detail_snapshot_id is None
    assert bitcoin.source_detail_fields == {}

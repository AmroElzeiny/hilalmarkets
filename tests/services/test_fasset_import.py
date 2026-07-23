from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ExternalAssessment,
    PublishedAssetAssessment,
    ShariaMonitoringRun,
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


class StaticFassetFetcher:
    def __init__(self, retrieved_at: datetime):
        self.retrieved_at = retrieved_at

    async def fetch(self, url: str) -> FassetFetchedSource:
        return FassetFetchedSource(
            url=url,
            status_code=200,
            content=FASSET_HTML,
            headers={"content-type": "text/html"},
            retrieved_at=self.retrieved_at,
        )


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

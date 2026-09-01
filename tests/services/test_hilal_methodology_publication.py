"""Publishing our own standard into a real database, and what it must not disturb.

The module tests prove the *file* is coherent. These prove the act of publishing: that a
methodology row appears, that every coin gets the answer the file records, that running
it twice changes nothing — and, most importantly, that a standard nobody reviewed cannot
reach a person who did not ask for it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    ShariaEvidenceSource,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import (
    ShariaAssetStatus,
    ShariaMethodologyStatus,
)
from ai_market_monitor.services.hilal_methodology import (
    METHODOLOGY_PUBLIC_PATH,
    METHODOLOGY_VERSION,
    UNDER_DEVELOPMENT_NOTICE,
    Outcome,
    admitted_assets,
    admitted_symbols,
    assets_by_outcome,
    evidence_requirements,
    methodology_rules,
    publish,
)
from ai_market_monitor.services.sharia_automated_screen import METHODOLOGY_SYSTEM_CODE
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from ai_market_monitor.services.sharia_screening import (
    AGGREGATE_METHODOLOGY_CODE,
    DEFAULT_ALLOWED_STATUSES,
    ShariaScreeningError,
    ShariaScreeningService,
)

pytestmark = pytest.mark.asyncio


async def _published(test_context):
    async with test_context["session_factory"]() as session:
        result = await publish(session)
        await session.commit()
    return result


async def test_publishing_creates_the_standard_and_every_coin(test_context):
    result = await _published(test_context)
    assert result.methodology_created is True
    assert result.assessments_written == len(admitted_assets())

    async with test_context["session_factory"]() as session:
        methodology = await session.scalar(
            select(ShariaMethodology).where(
                ShariaMethodology.code == METHODOLOGY_SYSTEM_CODE
            )
        )
        assert methodology is not None
        assert methodology.status is ShariaMethodologyStatus.ACTIVE
        assert methodology.version == METHODOLOGY_VERSION
        assert methodology.governing_body == "Hilal Markets"
        # The single most misleading sentence this product could publish would be a
        # reviewer group on a standard that has no reviewers.
        assert "No Shariah advisor" in (methodology.reviewer_group or "")
        assert methodology.rules_json["public_page"] == METHODOLOGY_PUBLIC_PATH
        assert methodology.rules_json["human_reviewed"] is False

        count = await session.scalar(select(func.count(AssetShariaAssessment.id)))
        assert count == len(admitted_assets())


async def test_every_published_row_carries_the_warning_and_says_no_person_reviewed_it(
    test_context,
):
    """The warning travels on the row, so it reaches the Passport and the exported report.

    A warning that lived only in a template would be missing from every surface that
    reads the assessment instead of rendering that template.
    """

    await _published(test_context)
    async with test_context["session_factory"]() as session:
        rows = list(await session.scalars(select(AssetShariaAssessment)))
        assert rows
        for row in rows:
            assert UNDER_DEVELOPMENT_NOTICE in row.qualifications
            assert row.reviewed_by_user_id is None
            assert "no human reviewer" in row.reviewed_by
            assert row.evidence_snapshot["human_reviewed"] is False
            assert row.evidence_snapshot["public_page"] == METHODOLOGY_PUBLIC_PATH


async def test_each_coin_gets_the_answer_the_file_records(test_context):
    await _published(test_context)
    expected = {
        Outcome.ADMITTED: ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
        Outcome.REFUSED: ShariaAssetStatus.EXCLUDED,
        Outcome.NOT_ENOUGH_DATA: ShariaAssetStatus.INSUFFICIENT_INFORMATION,
    }
    async with test_context["session_factory"]() as session:
        stored = {
            row.canonical_asset: row.status
            for row in await session.scalars(select(AssetShariaAssessment))
        }
    for outcome, status in expected.items():
        for asset in assets_by_outcome(outcome):
            assert stored[asset.symbol] is status


async def test_a_refused_coin_keeps_the_sentence_that_refused_it(test_context):
    """A refusal a reader cannot check is an accusation, not evidence."""

    refused = assets_by_outcome(Outcome.REFUSED)
    if not refused:
        pytest.skip("no coin is currently refused, so there is nothing to check")
    await _published(test_context)
    async with test_context["session_factory"]() as session:
        for asset in refused:
            row = await session.scalar(
                select(AssetShariaAssessment).where(
                    AssetShariaAssessment.canonical_asset == asset.symbol
                )
            )
            assert row is not None
            assert row.exclusion_reasons
            assert all(item["reason"].strip() for item in row.exclusion_reasons)


async def test_every_decided_coin_keeps_a_page_a_reader_can_open(test_context):
    await _published(test_context)
    async with test_context["session_factory"]() as session:
        for asset in admitted_assets():
            if asset.outcome is Outcome.NOT_ENOUGH_DATA:
                continue
            row = await session.scalar(
                select(AssetShariaAssessment).where(
                    AssetShariaAssessment.canonical_asset == asset.symbol
                )
            )
            sources = list(
                await session.scalars(
                    select(ShariaEvidenceSource).where(
                        ShariaEvidenceSource.assessment_id == row.id
                    )
                )
            )
            assert sources, asset.symbol
            assert all(item.source_url.startswith("https://") for item in sources)


async def test_publishing_twice_changes_nothing(test_context):
    """Idempotent, and the reason is not tidiness.

    A second run that rewrote every row would stamp today's date on every coin, and the
    whole list would look freshly re-read when not one page had been fetched.
    """

    await _published(test_context)
    async with test_context["session_factory"]() as session:
        again = await publish(session)
        await session.commit()
    assert again.methodology_created is False
    assert again.assessments_written == 0
    assert again.assessments_unchanged == len(admitted_assets())

    async with test_context["session_factory"]() as session:
        count = await session.scalar(select(func.count(AssetShariaAssessment.id)))
    assert count == len(admitted_assets())


async def test_it_never_becomes_the_product_default(test_context):
    """`default_methodology` orders by the newest effective date.

    This standard is the newest row in the table the moment it is published, and this
    test context deliberately configures **no** default code — which is the exact
    condition under which it would otherwise have become everybody's default.
    """

    await _published(test_context)
    async with test_context["session_factory"]() as session:
        service = ShariaScreeningService(session, test_context["settings"])
        default = await service.default_methodology()
    assert default is None or default.code != METHODOLOGY_SYSTEM_CODE


async def test_it_is_still_selectable_on_purpose(test_context):
    """Excluded from the default is not the same as hidden.

    A person may deliberately choose this standard and read what a machine made of a
    project's own website. Removing it from the picker would make the whole thing
    unreachable and this work pointless.
    """

    await _published(test_context)
    async with test_context["session_factory"]() as session:
        service = ShariaScreeningService(session, test_context["settings"])
        selectable = await service.selectable_market_methodologies()
    assert METHODOLOGY_SYSTEM_CODE in {row.code for row in selectable}


@pytest.mark.parametrize(
    ("exchange", "listed"),
    [
        # What each exchange listed against USDT on 31 August 2026, for the ten newly
        # researched coins. Recorded rather than fetched: this is about the rule, and a
        # test that called an exchange would fail on a train.
        ("binance", {"ZEC", "USD1", "ENSO", "PUMP", "RLUSD", "XAUT", "PEPE", "ZKC", "PROM", "U"}),
        ("bybit", {"USD1", "ENSO", "PUMP", "RLUSD", "XAUT", "PEPE", "ZKC"}),
    ],
)
async def test_an_admitted_coin_is_listed_for_every_exchange_that_trades_it(
    test_context, exchange, listed
):
    """Through the real list, with the statuses the market page really asks for.

    `screened_market_context` builds the scope from `provider.list_symbols(exchange)` and
    hands it, plus `DEFAULT_ALLOWED_STATUSES`, to `list_screened_assets`. Seven of the ten
    new coins also trade on Bybit, and they have to appear there without a second
    admission — an admission names a coin, never a coin-on-an-exchange.

    The status filter is part of the check, not a detail: a refused coin and one nobody
    could read are both *in* this standard, and neither may show up in the list a person
    browses for coins to watch.
    """

    await _published(test_context)
    async with test_context["session_factory"]() as session:
        service = ShariaScreeningService(session, test_context["settings"])
        methodology = await session.scalar(
            select(ShariaMethodology).where(
                ShariaMethodology.code == METHODOLOGY_SYSTEM_CODE
            )
        )
        page = await service.list_screened_assets(
            methodology_id=methodology.id,
            statuses=DEFAULT_ALLOWED_STATUSES,
            asset_scope=set(listed),
            page=1,
            limit=50,
        )
    shown = {item.canonical_asset for item in page.items}
    expected = {symbol for symbol in listed if symbol in admitted_symbols()}
    assert shown == expected, f"{exchange}: expected {sorted(expected)}, got {sorted(shown)}"
    assert shown, f"no admitted coin trades on {exchange}, so this proves nothing"
    refused = {item.symbol for item in assets_by_outcome(Outcome.REFUSED)}
    unread = {item.symbol for item in assets_by_outcome(Outcome.NOT_ENOUGH_DATA)}
    assert not (shown & (refused | unread))


async def test_a_passport_opens_for_an_admitted_coin_even_in_production(test_context):
    """Building the Passport is half of what publishing means.

    In a deployed environment the Passport route demands a `PublishedAssetAssessment` —
    the record proving a qualified reviewer released the coin. That record needs an
    external assessment, a research dossier and a *review decision*, and none of the
    three exists for this standard because no review happened. Writing them anyway would
    put a fabricated human decision in the audit trail of a standard whose whole claim is
    that nobody decided, so the route is exempt for this standard instead.

    Checked with `is_deployed` forced on, because that is the only state where the gate
    is armed and the failure would have been invisible until production.
    """

    await _published(test_context)
    settings = test_context["settings"].model_copy(update={"app_env": "production"})
    assert settings.is_deployed is True

    admitted = sorted(admitted_symbols())[0]
    async with test_context["session_factory"]() as session:
        methodology = await session.scalar(
            select(ShariaMethodology).where(
                ShariaMethodology.code == METHODOLOGY_SYSTEM_CODE
            )
        )
        # Named explicitly, exactly as the product does: a person reaches this Passport
        # from the market list with that standard selected. It is never resolved from
        # the default, because the default deliberately cannot be this standard.
        passport = await ShariaPassportReadService(session, settings).current(
            admitted, methodology_id=methodology.id
        )

    assert passport.assessment.canonical_asset == admitted
    assert passport.assessment.methodology_code == METHODOLOGY_SYSTEM_CODE
    assert UNDER_DEVELOPMENT_NOTICE in passport.assessment.qualifications
    assert passport.why_this_status


async def test_a_reviewed_standard_still_needs_its_publication_record(test_context):
    """The other half. Without it the exemption above is a hole in the whole product.

    An assessment under any other standard must still be refused in production until a
    reviewer has released it.
    """

    settings = test_context["settings"].model_copy(update={"app_env": "production"})
    async with test_context["session_factory"]() as session:
        methodology = ShariaMethodology(
            code="SOME_AUTHORITY",
            name="Some authority",
            version="1",
            description="An authority whose decisions a person makes.",
            status=ShariaMethodologyStatus.ACTIVE,
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            # A valid contract, so the refusal below is the publication gate and not the
            # methodology being malformed — which is a different error and would have
            # made this test pass for the wrong reason.
            rules_json=methodology_rules(),
            evidence_requirements_json=evidence_requirements(),
        )
        session.add(methodology)
        await session.flush()
        session.add(
            AssetShariaAssessment(
                canonical_asset="ZZZ",
                asset_name="Zed",
                methodology_id=methodology.id,
                status=ShariaAssetStatus.ELIGIBLE,
                summary="An assessment nobody has released yet, twenty characters plus.",
                qualifications=[],
                exclusion_reasons=[],
                evidence_snapshot={},
                reviewed_by="A reviewer",
                reviewed_at=datetime(2026, 2, 1, tzinfo=UTC),
                valid_from=datetime(2026, 2, 1, tzinfo=UTC),
            )
        )
        await session.commit()

    async with test_context["session_factory"]() as session:
        with pytest.raises(ShariaScreeningError) as raised:
            await ShariaPassportReadService(session, settings).current(
                "ZZZ", methodology_id=methodology.id
            )
    assert raised.value.code == "passport_not_published"


async def test_the_aggregate_view_never_answers_with_a_machine_reading(test_context):
    """`ALL_APPROVED_METHODOLOGIES` is where a reader stops being able to tell.

    With no authority in this database at all, the aggregate must still return nothing
    for these coins rather than falling back to the machine's answer. If it ever
    returned one, a reader on the default "every approved standard" view would be shown
    an unreviewed verdict as though an authority had made it.
    """

    await _published(test_context)
    async with test_context["session_factory"]() as session:
        service = ShariaScreeningService(session, test_context["settings"])
        aggregate = await session.scalar(
            select(ShariaMethodology).where(
                ShariaMethodology.code == AGGREGATE_METHODOLOGY_CODE
            )
        )
        if aggregate is None:
            aggregate = ShariaMethodology(
                code=AGGREGATE_METHODOLOGY_CODE,
                name="All approved methodologies",
                version="1",
                description="Every approved standard, one winner per coin.",
                status=ShariaMethodologyStatus.ACTIVE,
                rules_json={},
                evidence_requirements_json={},
            )
            session.add(aggregate)
            await session.flush()

        winners = await service._winning_assessments(aggregate, assets=set(admitted_symbols()))
    assert winners == {}

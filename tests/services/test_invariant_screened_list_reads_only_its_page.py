"""A list of twelve coins must read twelve reviews, not every review ever recorded.

On 22 August 2026 the dashboard was slow to the point of failing. The cause was one
sentence of code: every list view asked for **all** assessments under a methodology and
then kept a page of them.

    assessments = await self.effective_assessments(methodology.id)   # every asset
    ...
    values[offset : offset + limit]                                  # twelve of them

`AssetShariaAssessment` carries three JSON columns, `evidence_snapshot` among them, so a
whole table of them is not a long list — it is hundreds of megabytes. The Home page read
about 1.6 GB and took roughly thirty-six seconds to draw a strip of twelve coins. It was
not one page either: Home, the Market tab, the Halal Assets list, the coin search inside
the monitor builder and the setup chat all went through the same call. The Market tab
repeated it every two seconds, and the coin search repeated it on every keystroke.

Two rules are asserted here, and the second matters more than the first:

1. **Cost** — whole assessments are read only for the rows that reach the answer.
2. **Meaning** — the answer itself did not change. The order the assessments are chosen
   in, which one wins for an asset, which are published, how they are counted and
   filtered: all identical to reading everything first. This is Shariah status. A faster
   list that shows a different status is not a fix, it is the worst defect this product
   could ship, so the old algorithm is kept here in full and the two are compared.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, or_, select

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    PublishedAssetAssessment,
    User,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.services.sharia_screening import (
    AGGREGATE_METHODOLOGY_CODE,
    DEFAULT_ALLOWED_STATUSES,
    ShariaScreeningService,
    canonical_asset,
)
from tests.services.test_sharia_screening import (
    active_methodology,
    assess,
    screening_settings,
)

#: Enough assets that "reads everything" and "reads one page" cannot be confused, and few
#: enough that the suite stays quick.
ASSET_COUNT = 40
PAGE_LIMIT = 12

#: Every status, cycled over the assets, so the filters below are exercised on real data
#: rather than on a table where every row is eligible.
STATUS_CYCLE = [
    ShariaAssetStatus.ELIGIBLE,
    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
    ShariaAssetStatus.EXCLUDED,
    ShariaAssetStatus.UNDER_REVIEW,
    ShariaAssetStatus.DISPUTED,
    ShariaAssetStatus.INSUFFICIENT_INFORMATION,
]


@contextlib.contextmanager
def count_whole_assessments_read():
    """Count whole `AssetShariaAssessment` rows built from the database.

    SQLAlchemy's `load` event fires once per entity it constructs, and only for entity
    queries — a query that selects a few columns never fires it. That is exactly the
    difference being measured: choosing a winner reads six small columns, and only the
    rows that reach the answer are read whole.

    The first version of this counted objects left in the session's identity map instead.
    That map holds *weak* references, so rows that were read and then dropped were
    collected before the count and the measurement said zero however much had been read.
    It could not fail, which is the same as not existing.
    """
    counter = {"rows": 0}

    def on_load(target, context):  # noqa: ANN001 - SQLAlchemy's own signature
        counter["rows"] += 1

    event.listen(AssetShariaAssessment, "load", on_load)
    try:
        yield counter
    finally:
        event.remove(AssetShariaAssessment, "load", on_load)


def asset_name(index: int) -> str:
    """AAA, AAB, AAC … — fixed width, so sorting is unambiguous."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "A" + letters[index // 26] + letters[index % 26]


async def seed(session):
    """One methodology, `ASSET_COUNT` assets, several assessments for some of them."""
    user = User(display_name="Screened list user")
    session.add(user)
    await session.flush()
    methodology = await active_methodology(session, user.id)
    now = datetime.now(UTC)
    for index in range(ASSET_COUNT):
        asset = asset_name(index)
        await assess(
            session,
            methodology.id,
            user.id,
            asset,
            STATUS_CYCLE[index % len(STATUS_CYCLE)],
            valid_from=now - timedelta(days=10),
        )
        # Every third asset also has a newer review. Which of the two wins is exactly the
        # decision this change must not alter.
        if index % 3 == 0:
            await assess(
                session,
                methodology.id,
                user.id,
                asset,
                STATUS_CYCLE[(index + 1) % len(STATUS_CYCLE)],
                valid_from=now - timedelta(days=2),
            )
    await session.commit()
    return methodology


async def reference_list(service, methodology, **kwargs):
    """The old implementation, kept whole, as the thing the new one must agree with.

    This is deliberately a copy of the code as it stood before the change — including
    reading every row up front. It exists only here, only to be compared against, and is
    never imported by the application.
    """
    statuses = kwargs.get("statuses")
    search = kwargs.get("search")
    asset_scope = kwargs.get("asset_scope")
    page = kwargs.get("page", 1)
    limit = kwargs.get("limit", 30)
    as_of = datetime.now(UTC)

    aggregate = methodology.code == AGGREGATE_METHODOLOGY_CODE
    source_methodologies = []
    methodology_ids = [methodology.id]
    if aggregate:
        source_methodologies = [
            row
            for row in await service.executable_methodologies(as_of=as_of)
            if row.code != AGGREGATE_METHODOLOGY_CODE
        ]
        methodology_ids = [row.id for row in source_methodologies]
    rows = list(
        (
            await service.session.scalars(
                select(AssetShariaAssessment)
                .where(
                    AssetShariaAssessment.methodology_id.in_(methodology_ids),
                    AssetShariaAssessment.valid_from <= as_of,
                    or_(
                        AssetShariaAssessment.valid_until.is_(None),
                        AssetShariaAssessment.valid_until > as_of,
                    ),
                )
                .order_by(
                    AssetShariaAssessment.canonical_asset.asc(),
                    AssetShariaAssessment.valid_from.desc(),
                    AssetShariaAssessment.reviewed_at.desc(),
                    AssetShariaAssessment.created_at.desc(),
                )
            )
        ).all()
    )
    current: dict[str, AssetShariaAssessment] = {}
    for row in rows:
        current.setdefault(row.canonical_asset, row)

    safety_holds = await service.safety_hold_assets(assets=set(current))
    values = list(current.values())
    if aggregate or (service.settings and service.settings.is_deployed):
        assessment_ids = [row.id for row in values]
        published_ids = set(
            (
                await service.session.scalars(
                    select(PublishedAssetAssessment.asset_assessment_id).where(
                        PublishedAssetAssessment.asset_assessment_id.in_(assessment_ids),
                        PublishedAssetAssessment.is_active.is_(True),
                        PublishedAssetAssessment.publication_state == "published",
                    )
                )
            ).all()
        )
        values = [row for row in values if row.id in published_ids]
    if asset_scope is not None:
        normalized_scope = {canonical_asset(asset) for asset in asset_scope}
        values = [row for row in values if row.canonical_asset in normalized_scope]
    count_values = list(values)
    if statuses:
        values = [
            row
            for row in values
            if (
                ShariaAssetStatus.UNDER_REVIEW
                if row.canonical_asset in safety_holds
                else row.status
            )
            in statuses
        ]
    if search:
        needle = search.casefold().strip()
        searched_asset = canonical_asset(search).casefold()
        values = [
            row
            for row in values
            if needle in row.canonical_asset.casefold()
            or needle in (row.asset_name or "").casefold()
            or searched_asset == row.canonical_asset.casefold()
        ]
    values.sort(key=lambda row: (row.canonical_asset, row.reviewed_at))
    counts: dict[str, int] = {}
    for row in count_values:
        effective = (
            ShariaAssetStatus.UNDER_REVIEW
            if row.canonical_asset in safety_holds
            else row.status
        )
        counts[effective.value] = counts.get(effective.value, 0) + 1
    offset = (page - 1) * limit
    return {
        "ids": [row.id for row in values[offset : offset + limit]],
        "assets": [row.canonical_asset for row in values[offset : offset + limit]],
        "statuses": [
            (
                ShariaAssetStatus.UNDER_REVIEW
                if row.canonical_asset in safety_holds
                else row.status
            ).value
            for row in values[offset : offset + limit]
        ],
        "total": len(values),
        "counts": counts,
    }


def actual_shape(result):
    return {
        "ids": [item.id for item in result.items],
        "assets": [item.canonical_asset for item in result.items],
        "statuses": [item.status.value for item in result.items],
        "total": result.total,
        "counts": result.status_counts,
    }


#: Every shape of request the product actually makes. Home and the coin search ask for a
#: page of twelve; the Halal Assets list asks for thirty with a status filter; the Market
#: tab asks for ten thousand; the builder passes a scope.
QUERIES = [
    pytest.param({"page": 1, "limit": PAGE_LIMIT}, id="home-coin-strip"),
    pytest.param({"page": 2, "limit": PAGE_LIMIT}, id="second-page"),
    pytest.param({"page": 4, "limit": PAGE_LIMIT}, id="page-past-the-end"),
    pytest.param(
        {"page": 1, "limit": 30, "statuses": set(DEFAULT_ALLOWED_STATUSES)},
        id="eligible-only",
    ),
    pytest.param(
        {"page": 1, "limit": 30, "statuses": {ShariaAssetStatus.EXCLUDED}},
        id="excluded-only",
    ),
    pytest.param({"page": 1, "limit": 30, "search": "AAB"}, id="search-by-asset"),
    pytest.param({"page": 1, "limit": 30, "search": "asset"}, id="search-by-name"),
    pytest.param({"page": 1, "limit": 30, "search": "zzzz"}, id="search-matching-nothing"),
    pytest.param(
        {"page": 1, "limit": 30, "asset_scope": {"AAA", "AAB", "AAC", "AAD"}},
        id="scoped-to-four",
    ),
    pytest.param({"page": 1, "limit": 30, "asset_scope": set()}, id="scoped-to-none"),
    pytest.param({"page": 1, "limit": 10_000}, id="market-tab-asks-for-everything"),
    pytest.param(
        {
            "page": 1,
            "limit": PAGE_LIMIT,
            "statuses": set(DEFAULT_ALLOWED_STATUSES),
            "search": "AA",
            "asset_scope": {asset_name(index) for index in range(20)},
        },
        id="every-filter-at-once",
    ),
]


@pytest.mark.parametrize("query", QUERIES)
async def test_the_answer_is_identical_to_reading_everything_first(test_context, query):
    """Same coins, same order, same statuses, same totals, same counts.

    Parametrised over every request shape the product makes, not over the one page that
    was reported slow. A change that only keeps Home correct must fail here.
    """
    async with test_context["session_factory"]() as session:
        methodology = await seed(session)
        service = ShariaScreeningService(session, screening_settings())

        expected = await reference_list(service, methodology, **query)
        actual = actual_shape(
            await service.list_screened_assets(methodology_id=methodology.id, **query)
        )

    assert actual == expected, (
        "the screened list changed its answer. This decides which coins are shown as "
        "Halal and with which status - it must be identical to the old reading."
    )


async def test_whole_assessments_are_read_only_for_the_page(test_context):
    """The cost rule: twelve coins on the page, at most twelve full rows in memory.

    Counted from the session's own identity map, which holds every ORM object the session
    has loaded. That measures what actually arrived in memory, so it stays true however
    the query is written.
    """
    async with test_context["session_factory"]() as session:
        methodology = await seed(session)

    async with test_context["session_factory"]() as fresh:
        service = ShariaScreeningService(fresh, screening_settings())
        with count_whole_assessments_read() as counted:
            result = await service.list_screened_assets(
                methodology_id=methodology.id,
                page=1,
                limit=PAGE_LIMIT,
            )

    assert len(result.items) == PAGE_LIMIT
    assert counted["rows"] <= PAGE_LIMIT, (
        f"the page shows {len(result.items)} coins but {counted['rows']} whole "
        f"assessments were read, out of {ASSET_COUNT} assets. Each one carries its "
        "evidence snapshot, so reading the table to keep a page is what made the Home "
        "page take about 1.6 GB and thirty-six seconds."
    )


async def test_a_search_that_matches_nothing_still_reads_nothing(test_context):
    """The coin search runs on every keystroke, including the ones that match nothing."""
    async with test_context["session_factory"]() as session:
        methodology = await seed(session)

    async with test_context["session_factory"]() as fresh:
        service = ShariaScreeningService(fresh, screening_settings())
        with count_whole_assessments_read() as counted:
            result = await service.list_screened_assets(
                methodology_id=methodology.id,
                search="nothing-matches-this",
                page=1,
                limit=PAGE_LIMIT,
            )

    assert result.items == []
    assert counted["rows"] == 0, (
        f"a search matching no coin still read {counted['rows']} whole assessments. "
        "Typing three letters in the monitor builder runs this up to three times."
    )


async def test_an_empty_but_present_scope_reads_nothing(test_context):
    """"These assets, and there are none" must mean none — not "all of them".

    `effective_assessments` used to skip its filter when the caller passed an empty set,
    and so returned the entire table. That fails *open*, and it is the opposite of what
    the caller asked for. `safety_hold_assets` already refused the same input; the two
    now agree. The scan path passes exactly this when an exchange returns no symbols.
    """
    async with test_context["session_factory"]() as session:
        methodology = await seed(session)
        service = ShariaScreeningService(session, screening_settings())

        assert await service.effective_assessments(methodology.id, assets=set()) == {}
        assert await service.safety_hold_assets(assets=set()) == set()
        # And the unfiltered call is still unfiltered, so nothing else moved.
        assert await service.effective_assessments(methodology.id) != {}


def deployed_settings():
    """Settings as they are in production, where the published-Passport filter applies.

    `model_copy` rather than a fresh `Settings`, so the environment validators are not
    re-run for a flag this test only needs for one branch.
    """
    return screening_settings().model_copy(update={"app_env": "staging"})


async def publish(session, assessment, user_id, *, index: int, active=True, state="published"):
    """A live published Passport for one assessment.

    Written straight into the table rather than driven through the governance workflow:
    what is under test is the *filter*, not how a Passport comes to exist.
    """
    row = PublishedAssetAssessment(
        canonical_asset_id=uuid4(),
        external_assessment_id=uuid4(),
        dossier_id=uuid4(),
        review_decision_id=uuid4(),
        asset_assessment_id=assessment.id,
        version=1,
        publication_state=state,
        passport_snapshot={},
        integrity_hash=f"hash-{index:04d}",
        is_active=active,
        published_by_user_id=user_id,
        published_at=datetime.now(UTC) - timedelta(days=1),
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.parametrize("query", QUERIES)
async def test_the_published_filter_is_identical_too(test_context, query):
    """The branch that actually runs in production, compared the same way.

    Everything above runs with the local settings, where a Passport is not required. In
    production it is: an assessment with no live published Passport must not appear, at
    all, under any filter. That is the fail-closed rule of the whole screening layer, and
    it is the branch a test is most likely to miss — so it gets the same full comparison.

    A mixture on purpose: some winners published, one published but no longer active, one
    still a draft. All three of those must be treated as "no Passport".
    """
    async with test_context["session_factory"]() as session:
        methodology = await seed(session)
        service = ShariaScreeningService(session, deployed_settings())

        winners = await service._winning_assessments(methodology)
        ordered = sorted(winners.values(), key=lambda row: row.canonical_asset)
        for index, row in enumerate(ordered):
            if index % 4 == 3:
                continue  # no Passport at all
            await publish(
                session,
                row,
                (await session.scalars(select(User.id))).first(),
                index=index,
                active=index % 8 != 5,
                state="published" if index % 8 != 6 else "draft",
            )
        await session.commit()

        expected = await reference_list(service, methodology, **query)
        actual = actual_shape(
            await service.list_screened_assets(methodology_id=methodology.id, **query)
        )

    assert actual == expected, (
        "the published-Passport filter changed its answer. An asset without a live "
        "published Passport must never be listed."
    )


async def test_nothing_is_listed_before_a_passport_is_published(test_context):
    """Fail closed: reviewed but unpublished shows nothing, and says so."""
    async with test_context["session_factory"]() as session:
        methodology = await seed(session)
        service = ShariaScreeningService(session, deployed_settings())
        result = await service.list_screened_assets(
            methodology_id=methodology.id, page=1, limit=PAGE_LIMIT
        )

    assert result.items == []
    assert result.total == 0
    assert result.warning, (
        "with no published Passport the list is empty and must say why, or the page looks "
        "broken rather than governed."
    )


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: `effective_assessments` reads whole rows, evidence blobs and all. That is the right
#: thing for a handful of named assets and the wrong thing for a page or a count, so a
#: caller must say which assets it means. The service that owns the method is exempt.
SCOPE_EXEMPT = {"sharia_screening.py"}


@pytest.mark.parametrize(
    "path",
    sorted(
        (p for p in SOURCE_ROOT.rglob("*.py") if p.name not in SCOPE_EXEMPT),
        key=lambda p: p.name,
    ),
    ids=lambda p: p.name,
)
def test_no_caller_reads_every_assessment(path):
    """Every `effective_assessments` call outside the service names its assets.

    Asserted over every file, not over the ones that were wrong. Four separate callers
    had grown the same habit — Home, the Market tab, the coin search and the setup chat —
    and each was found only after it had already cost a page its speed.
    """
    text = path.read_text(encoding="utf-8")
    if "effective_assessments(" not in text:
        pytest.skip("does not call it")
    for index, call in enumerate(text.split("effective_assessments(")[1:]):
        # The argument list ends at the matching bracket; a scoped call names `assets`
        # inside it.
        depth = 1
        arguments = []
        for character in call:
            if character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
                if depth == 0:
                    break
            arguments.append(character)
        assert "assets=" in "".join(arguments), (
            f"{path.name} calls effective_assessments (call {index + 1}) without an "
            "`assets=` scope, so it reads every stored review including every evidence "
            "snapshot. Use `eligible_assets` for a count, `list_screened_assets` for a "
            "page, or name the assets."
        )


async def test_counting_eligible_assets_does_not_read_them(test_context):
    """The setup chat shows one number. It used to read every review to get it."""
    async with test_context["session_factory"]() as session:
        methodology = await seed(session)

    async with test_context["session_factory"]() as fresh:
        service = ShariaScreeningService(fresh, screening_settings())
        with count_whole_assessments_read() as counted:
            eligible = await service.eligible_assets(methodology.id)

    async with test_context["session_factory"]() as reference:
        service = ShariaScreeningService(reference, screening_settings())
        assessments = await service.effective_assessments(methodology.id)
        holds = await service.safety_hold_assets(assets=set(assessments))
        expected = {
            asset
            for asset, row in assessments.items()
            if row.status in DEFAULT_ALLOWED_STATUSES and asset not in holds
        }

    assert eligible == expected, "the eligible asset set changed"
    assert counted["rows"] == 0, (
        f"counting eligible assets read {counted['rows']} whole assessments; it needs "
        "only their names and statuses."
    )

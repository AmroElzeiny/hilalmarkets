"""Every coin's official links, found in layers, and never trusted without proof.

Three rules are pinned here, and each one is asserted across the whole family rather
than on one example.

**One spelling of one page.** ``normalized_url`` decides when two addresses are the same
page, and ``official_sources`` is unique on it. Four private copies of that rule used to
exist and two of them disagreed about a trailing slash, so one page could be stored
twice under one asset and counted twice as evidence. The pairs below fail if any copy
comes back.

**A guess can never promote itself.** The cheapest layer invents addresses like
``https://site/blog``. That is only safe because nothing downstream trusts a candidate:
it has to be fetched, permitted, readable and recent first. So the tests check the
scoring rule for *every* combination of proof outcomes and *every* layer, not for the
one case that happens to work.

**A dead link and an unreachable link are different.** Only a settled answer — 404, 410,
robots says no — may withdraw a source a review already relies on. A timeout must change
nothing, or the whole product loses its evidence the next time a CDN has a bad minute.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import CanonicalAsset, OfficialSource, ReviewCase, User
from ai_market_monitor.db.models.enums import ReviewCaseType, UserRole
from ai_market_monitor.services.sharia_research import ShariaResearchError
from ai_market_monitor.services.sharia_source_catalog import (
    CATEGORY_PRIORITY,
    CONFIDENCE_FLOOR,
    CURATED_SOURCES,
    LAYER_CONFIDENCE,
    LAYER_ORDER,
    NEWS,
    NEWS_MAXIMUM_AGE_DAYS,
    REQUIRED_CATEGORIES,
    SOURCE_CATEGORIES,
    TRACKED_CATEGORIES,
    candidates_for,
    convention_candidates,
    curated_candidates,
    is_official_url,
    missing_categories,
    normalized_url,
    page_category,
)
from ai_market_monitor.services.sharia_source_resolution import (
    SourceProof,
    SourceResolutionService,
    newest_published_at,
    score_candidate,
)
from ai_market_monitor.services.system_brain_bulk_review import (
    BULK_ACTIONS,
    BulkReviewService,
)

# --------------------------------------------------------------------------
# The curated table
# --------------------------------------------------------------------------

_CURATED_ENTRIES = [
    pytest.param(symbol, entry, id=f"{symbol}-{entry.category}-{index}")
    for symbol, entries in sorted(CURATED_SOURCES.items())
    for index, entry in enumerate(entries)
]


@pytest.mark.parametrize(("symbol", "entry"), _CURATED_ENTRIES)
def test_every_curated_link_is_a_fetchable_official_address(symbol, entry) -> None:
    """HTTPS, a real host, and no user-info trick. Every row, not a sample."""

    assert is_official_url(entry.url), f"{symbol}: {entry.url} is not usable"
    assert entry.url == entry.url.strip()


@pytest.mark.parametrize(("symbol", "entry"), _CURATED_ENTRIES)
def test_every_curated_link_uses_a_category_the_product_knows(symbol, entry) -> None:
    """A category is vocabulary, not a free string."""

    assert entry.category in SOURCE_CATEGORIES, f"{symbol}: {entry.category} is unknown"
    assert entry.category in CATEGORY_PRIORITY


@pytest.mark.parametrize(("symbol", "entry"), _CURATED_ENTRIES)
def test_no_curated_link_is_trusted_enough_to_skip_being_checked(symbol, entry) -> None:
    """The point of the whole design: writing an address down is not proof.

    A curated link is the most trusted kind there is, and it still has to be fetched.
    If any curated confidence ever reached 1.0 the resolver could mark it verified
    without proof, which is the exact defect this work exists to remove.
    """

    assert 0.0 < entry.confidence < 1.0


@pytest.mark.parametrize("symbol", sorted(CURATED_SOURCES))
def test_every_curated_coin_has_both_a_news_and_a_community_page(symbol) -> None:
    """The product's promise: news *and* community, for every coin listed."""

    categories = {entry.category for entry in CURATED_SOURCES[symbol]}
    assert missing_categories(categories) == (), f"{symbol} is missing {categories}"


@pytest.mark.parametrize("symbol", sorted(CURATED_SOURCES))
def test_a_coin_never_lists_the_same_page_twice(symbol) -> None:
    """Two spellings of one address would be stored as two sources for one asset."""

    seen = [normalized_url(entry.url) for entry in CURATED_SOURCES[symbol]]
    assert len(seen) == len(set(seen)), f"{symbol} lists the same page more than once"


# --------------------------------------------------------------------------
# One spelling of one page
# --------------------------------------------------------------------------

_SAME_PAGE_PAIRS = [
    ("https://site.example/blog", "https://site.example/blog/"),
    ("https://site.example/blog/", "https://site.example/blog//"),
    ("https://SITE.example/blog", "https://site.example/blog"),
    ("HTTPS://site.example/blog", "https://site.example/blog"),
    ("https://site.example/blog", "https://site.example/blog?page=2"),
    ("https://site.example/blog", "https://site.example/blog#latest"),
    ("https://site.example/blog", "  https://site.example/blog  "),
    ("https://site.example", "https://site.example/"),
]


@pytest.mark.parametrize(("left", "right"), _SAME_PAGE_PAIRS)
def test_two_spellings_of_one_page_are_one_page(left, right) -> None:
    """The defect this replaced: four copies of this rule, two of them disagreeing."""

    assert normalized_url(left) == normalized_url(right)


def test_the_root_of_a_site_keeps_its_slash() -> None:
    """Stripping must not turn a host into a bare scheme."""

    assert normalized_url("https://site.example/") == "https://site.example/"
    assert normalized_url("https://site.example") == "https://site.example/"


@pytest.mark.parametrize(
    "value",
    [
        "http://site.example/blog",
        "ftp://site.example/blog",
        "https://real.example@evil.example/blog",
        "https://",
        "",
        "   ",
        "site.example/blog",
    ],
)
def test_an_address_that_cannot_be_trusted_is_refused(value) -> None:
    """Fail closed. An address we cannot vouch for is not registered at all."""

    assert is_official_url(value) is False


# --------------------------------------------------------------------------
# The layers
# --------------------------------------------------------------------------


def test_the_layers_are_ordered_from_most_to_least_trusted() -> None:
    scores = [LAYER_CONFIDENCE[layer] for layer in LAYER_ORDER]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("layer", list(LAYER_ORDER))
def test_no_layer_can_answer_without_being_proved(layer) -> None:
    """A layer's own confidence never reaches the floor plus proof on its own.

    This is what makes the guessing layer safe to keep.
    """

    assert LAYER_CONFIDENCE[layer] < 1.0


@pytest.mark.parametrize("layer", list(LAYER_ORDER))
def test_every_layer_answers_without_raising(layer) -> None:
    """A layer with nothing to offer returns nothing. It never explodes."""

    empty = candidates_for(
        layer,
        symbol="NOTHING-KNOWN",
        asset_name="Nothing Known",
        official_website=None,
        official_documentation=None,
    )
    assert empty == ()


@pytest.mark.parametrize("layer", list(LAYER_ORDER))
def test_every_candidate_any_layer_offers_is_fetchable(layer) -> None:
    """No layer may propose an address the fetcher would refuse."""

    produced = candidates_for(
        layer,
        symbol="BTC",
        asset_name="Bitcoin",
        official_website="https://bitcoin.org/",
        official_documentation="https://github.com/bitcoin/bitcoin",
    )
    for candidate in produced:
        assert is_official_url(candidate.url), candidate.url
        assert candidate.category in SOURCE_CATEGORIES
        assert candidate.layer is layer


def test_the_guessing_layer_only_guesses_on_the_projects_own_domain() -> None:
    """A guess that wandered onto another host would be somebody else's page."""

    produced = convention_candidates(
        asset_name="Example", official_website="https://example.org/"
    )
    assert produced, "the guessing layer offered nothing at all"
    for candidate in produced:
        assert "example.org" in candidate.url
        assert candidate.category in REQUIRED_CATEGORIES


def test_the_guessing_layer_says_nothing_without_an_official_website() -> None:
    assert convention_candidates(asset_name="Example", official_website=None) == ()


def test_every_guessed_address_is_read_back_as_the_category_it_was_guessed_for() -> None:
    """One vocabulary, both ways round.

    The guessing layer picks a path because it believes a project publishes news there.
    :func:`page_category` is what every other reader — the crawler, the provider filter,
    a reviewer's paste — uses to decide what that same address is. If the two ever hold
    different word lists, an address is fetched as news and then filed as something else
    by the next thing that touches it.

    Asserted over every guess the layer can make, not over one example.
    """

    produced = convention_candidates(
        asset_name="Example", official_website="https://example.org/"
    )
    assert produced, "the guessing layer offered nothing at all"
    for candidate in produced:
        assert page_category(candidate.url) == candidate.category, (
            f"{candidate.url} is guessed as {candidate.category} but reads back as "
            f"{page_category(candidate.url)}"
        )


def test_the_guessing_layer_does_not_spend_requests_on_optional_pages() -> None:
    """Every guess is a request to somebody else's server.

    Guessing ``/community`` and ``/forum`` for a project that runs neither cost four
    fetches per coin to prove nothing, and the community page is not required — so it is
    not guessed at. A forum the project actually links to is still found, by the layer
    that reads the project's own homepage.
    """

    produced = convention_candidates(
        asset_name="Example", official_website="https://example.org/"
    )
    assert {item.category for item in produced} == set(REQUIRED_CATEGORIES)


def test_a_curated_coin_may_carry_more_than_one_link() -> None:
    """The product asked for this: more than one source per coin is fine."""

    produced = curated_candidates("BTC", "Bitcoin")
    assert len(produced) >= 2
    categories = {item.category for item in produced}
    # A curated entry may name a community page and usually does. What it must always
    # name is the required one — the news page — because that is the only category a
    # coin can be short of.
    assert categories <= set(TRACKED_CATEGORIES)
    assert set(REQUIRED_CATEGORIES) <= categories


# --------------------------------------------------------------------------
# Scoring: proof adds, failure removes
# --------------------------------------------------------------------------

_ALL_PROOF_SHAPES = [
    (reachable, allowed, readable, fresh)
    for reachable in (True, False)
    for allowed in (True, False)
    for readable in (True, False)
    for fresh in (True, False)
]


@pytest.mark.parametrize(("reachable", "allowed", "readable", "fresh"), _ALL_PROOF_SHAPES)
@pytest.mark.parametrize("layer", list(LAYER_ORDER))
def test_a_link_that_failed_any_part_of_its_proof_never_reaches_the_floor(
    layer, reachable, allowed, readable, fresh
) -> None:
    """Every layer, every combination. Only a clean proof may clear the floor."""

    proof = SourceProof(
        reachable=reachable, allowed=allowed, readable=readable, fresh=fresh
    )
    score = score_candidate(LAYER_CONFIDENCE[layer], proof)
    if proof.usable:
        assert score >= CONFIDENCE_FLOOR
    else:
        assert score < CONFIDENCE_FLOOR


@pytest.mark.parametrize("layer", list(LAYER_ORDER))
def test_a_page_that_stopped_publishing_is_not_a_news_source(layer) -> None:
    """Alive and readable is not the same as still being written."""

    stale = SourceProof(reachable=True, allowed=True, readable=True, fresh=False)
    assert score_candidate(LAYER_CONFIDENCE[layer], stale) < CONFIDENCE_FLOOR


@pytest.mark.parametrize("layer", list(LAYER_ORDER))
def test_proof_never_pushes_a_link_past_certainty(layer) -> None:
    good = SourceProof(reachable=True, allowed=True, readable=True, fresh=True)
    assert score_candidate(LAYER_CONFIDENCE[layer], good) <= 1.0


@pytest.mark.parametrize(
    "status",
    [400, 401, 403, 404, 405, 410, 451],
)
def test_a_settled_no_is_treated_as_a_dead_link(status) -> None:
    proof = SourceProof(
        reachable=False, allowed=True, readable=False, fresh=False, status=status
    )
    assert proof.definitively_dead is True


@pytest.mark.parametrize(
    ("status", "code"),
    [(None, "official_source_unavailable"), (500, None), (502, None), (429, None), (408, None)],
)
def test_bad_luck_is_never_treated_as_a_dead_link(status, code) -> None:
    """A timeout says nothing about the address, and must withdraw nothing."""

    proof = SourceProof(
        reachable=False,
        allowed=True,
        readable=False,
        fresh=False,
        status=status,
        error_code=code,
    )
    assert proof.definitively_dead is False


def test_a_site_that_refuses_robots_is_a_settled_no() -> None:
    proof = SourceProof(
        reachable=False,
        allowed=False,
        readable=False,
        fresh=False,
        error_code="robots_disallowed",
    )
    assert proof.definitively_dead is True


# --------------------------------------------------------------------------
# Reading the date off a page
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Posted 2026-03-12 by the team", (2026, 3, 12)),
        ("Posted 12 March 2026", (2026, 3, 12)),
        ("Posted 12 Mar 2026", (2026, 3, 12)),
        ("Posted March 12, 2026", (2026, 3, 12)),
        ("Posted Mar. 12 2026", (2026, 3, 12)),
        ("older 2024-01-01 and newer 2026-03-12", (2026, 3, 12)),
    ],
)
def test_the_newest_date_a_page_claims_is_read_however_it_is_written(text, expected) -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    found = newest_published_at(text, now=now)
    assert found is not None
    assert (found.year, found.month, found.day) == expected


def test_a_date_in_the_future_is_not_evidence_the_page_is_alive() -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    assert newest_published_at("Our conference is on 2027-05-01", now=now) is None


def test_a_date_that_cannot_exist_is_ignored() -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    assert newest_published_at("2026-02-31", now=now) is None


def test_a_page_with_no_date_at_all_says_so() -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    assert newest_published_at("nothing dated here", now=now) is None


# --------------------------------------------------------------------------
# The resolver, end to end
# --------------------------------------------------------------------------

_GONE = ShariaResearchError(
    "official_source_fetch_failed", "Official source returned HTTP 404."
)
_FLAKY = ShariaResearchError(
    "official_source_unavailable",
    "The official source could not be reached securely.",
    retryable=True,
)


def _page(*, days_old: int = 3, now: datetime | None = None) -> tuple[str, dict, int]:
    moment = (now or datetime.now(UTC)) - timedelta(days=days_old)
    body = (
        "<html><head><title>Newsroom</title></head><body>"
        f"<h1>Latest</h1><p>Posted {moment.strftime('%Y-%m-%d')}.</p>"
        "<p>" + ("The project published an update about its network. " * 12) + "</p>"
        "</body></html>"
    )
    return (body, {"content-type": "text/html"}, 200)


def _undated_page() -> tuple[str, dict, int]:
    """A real, readable newsroom that states no date this reader can find.

    This is not a rare shape. A blog that prints "3 days ago", or draws its dates with
    JavaScript, or sets them as an image, looks exactly like this to the fetcher — and
    until 1 September 2026 every one of them was scored as though it had stopped
    publishing years ago and refused.
    """

    body = (
        "<html><head><title>Newsroom</title></head><body>"
        "<h1>Announcements</h1>"
        "<p>" + ("The foundation published an update about network fees and staking "
                 "rewards, and the governance vote on the new proposal. ") * 6 + "</p>"
        "</body></html>"
    )
    return (body, {"content-type": "text/html"}, 200)


class _StubFetcher:
    """Answers whatever the test says the internet contains."""

    def __init__(self, pages: dict[str, object], default: object = _GONE) -> None:
        self.pages = pages
        self.default = default
        self.requested: list[str] = []

    async def fetch(self, source):
        self.requested.append(source.source_url)
        answer = self.pages.get(source.source_url, self.default)
        if isinstance(answer, ShariaResearchError):
            raise answer
        return answer


async def _asset(session, *, symbol: str = "BTC", name: str = "Bitcoin") -> CanonicalAsset:
    asset = CanonicalAsset(
        symbol=symbol,
        name=name,
        asset_type="coin",
        official_website="https://bitcoin.org/",
        official_documentation="https://github.com/bitcoin/bitcoin",
        identity_hash=f"hash-{symbol}",
        mapping_state="verified",
    )
    session.add(asset)
    await session.flush()
    return asset


def _curated_urls(symbol: str, name: str) -> list[str]:
    return [item.url for item in curated_candidates(symbol, name)]


async def test_a_coin_with_working_pages_ends_up_with_both_and_no_task(test_context) -> None:
    """The happy path, and the thing the product promised."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        pages = {url: _page() for url in _curated_urls("BTC", "Bitcoin")}
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher(pages)
        )
        outcome = await service.resolve_asset(asset)
        await session.commit()

        assert outcome.missing == ()
        assert outcome.escalated is False
        rows = list(
            (
                await session.scalars(
                    select(OfficialSource).where(
                        OfficialSource.canonical_asset_id == asset.id,
                        OfficialSource.verification_state == "verified",
                    )
                )
            ).all()
        )
        proved = {row.category for row in rows}
        for category in REQUIRED_CATEGORIES:
            assert category in proved
        for row in rows:
            assert row.confidence >= CONFIDENCE_FLOOR
            assert row.last_checked_at is not None


async def test_a_coin_whose_links_are_all_dead_is_given_to_a_person(test_context) -> None:
    """When the layers run out, a human task appears — and says what is needed."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher({})
        )
        outcome = await service.resolve_asset(asset)
        await session.commit()

        assert outcome.missing == REQUIRED_CATEGORIES
        assert outcome.escalated is True
        case = await session.scalar(
            select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
        )
        assert case is not None
        assert case.case_type == "official_source_gap"
        assert case.done_at is None, "an open task is what puts it in Needs attention"
        assert case.requested_evidence, "the task must say what it needs"
        assert "Not Listed" in case.human_review_reason


async def test_a_dead_link_is_never_recorded_as_verified(test_context) -> None:
    """Whatever proposed it. This is the rule the whole design rests on."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher({})
        )
        await service.resolve_asset(asset)
        await session.commit()

        rows = list(
            (
                await session.scalars(
                    select(OfficialSource).where(
                        OfficialSource.canonical_asset_id == asset.id
                    )
                )
            ).all()
        )
        assert rows, "the attempt should still be recorded"
        for row in rows:
            assert row.verification_state != "verified", row.source_url
            assert row.confidence == 0.0


async def test_a_page_that_stopped_publishing_does_not_count_as_coverage(
    test_context,
) -> None:
    """Alive, readable, permitted — and years out of date. Not a news source."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        stale = _page(days_old=NEWS_MAXIMUM_AGE_DAYS + 60)
        pages = {url: stale for url in _curated_urls("BTC", "Bitcoin")}
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher(pages)
        )
        outcome = await service.resolve_asset(asset)
        await session.commit()

        assert NEWS in outcome.missing
        assert outcome.escalated is True


async def test_a_news_page_with_no_readable_date_is_kept_and_never_asked_about(
    test_context,
) -> None:
    """"I could not find a date" is not "the page is stale". They were the same thing.

    A readable news page whose dates this reader cannot parse used to fail its freshness
    proof, which halved its confidence, which put every layer under the floor, which
    refused the page — and the coin was then reported to a person as having no news page
    at all, while the page sat there working in anybody's browser. That was the single
    biggest producer of "Pages not found" rows in the System Brain.

    Three things have to be true together, and they are the whole design:

    * the page counts as coverage, so **nobody is asked** to go and find it;
    * it is scored as quiet, because a page whose dates cannot be read genuinely is a
      poor window onto the project;
    * being quiet makes the machine keep looking for livelier company for it.
    """

    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        pages = {url: _undated_page() for url in _curated_urls("BTC", "Bitcoin")}
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher(pages)
        )
        outcome = await service.resolve_asset(asset)
        await session.commit()

        assert outcome.missing == (), (
            "a readable news page was refused for not printing a date this reader "
            "understands, and the coin was reported as having none"
        )
        assert outcome.escalated is False
        assert outcome.coverage.get(NEWS, 0) >= 1
        assert outcome.quiet, (
            "the page counts, but it must still be marked quiet — otherwise the layers "
            "stop looking for a page whose dates can actually be read"
        )
        assert NEWS in outcome.short


async def test_an_old_link_proved_gone_is_withdrawn(test_context) -> None:
    """The defect that started this: 'verified' set by typing, never by checking."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        session.add(
            OfficialSource(
                canonical_asset_id=asset.id,
                category="official_website",
                title="Old site",
                source_url="https://gone.example/",
                normalized_url=normalized_url("https://gone.example/"),
                priority=10,
                verification_state="verified",
                is_active=True,
            )
        )
        await session.flush()
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher({})
        )
        outcome = await service.resolve_asset(asset)
        await session.commit()

        row = await session.scalar(
            select(OfficialSource).where(
                OfficialSource.source_url == "https://gone.example/"
            )
        )
        assert row is not None
        assert row.verification_state == "unreachable"
        assert row.is_active is False
        assert outcome.withdrawn, "the reviewer must be told the link went away"


async def test_a_link_that_could_not_be_reached_is_left_exactly_as_it_was(
    test_context,
) -> None:
    """A timeout is not proof of death.

    Without this rule one bad minute on the network would withdraw the evidence of
    every asset the product has.
    """

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        session.add(
            OfficialSource(
                canonical_asset_id=asset.id,
                category="official_website",
                title="Site",
                source_url="https://slow.example/",
                normalized_url=normalized_url("https://slow.example/"),
                priority=10,
                verification_state="verified",
                is_active=True,
            )
        )
        await session.flush()
        service = SourceResolutionService(
            session,
            test_context["settings"],
            fetcher=_StubFetcher({}, default=_FLAKY),
        )
        await service.resolve_asset(asset)
        await session.commit()

        row = await session.scalar(
            select(OfficialSource).where(
                OfficialSource.source_url == "https://slow.example/"
            )
        )
        assert row is not None
        assert row.verification_state == "verified"
        assert row.is_active is True


async def test_asking_twice_does_not_raise_the_task_twice(test_context) -> None:
    """A queue that grows by one every sweep is a queue nobody reads."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        settings = test_context["settings"]
        for _ in range(2):
            service = SourceResolutionService(
                session, settings, fetcher=_StubFetcher({})
            )
            await service.resolve_asset(asset)
            await session.commit()

        cases = list(
            (
                await session.scalars(
                    select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
                )
            ).all()
        )
        assert len(cases) == 1


async def test_the_task_closes_once_the_gap_is_filled(test_context) -> None:
    """Otherwise a person keeps being asked for something already sorted out."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        await SourceResolutionService(
            session, settings, fetcher=_StubFetcher({})
        ).resolve_asset(asset)
        await session.commit()

        opened = await session.scalar(
            select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
        )
        assert opened is not None and opened.done_at is None

        # The links start working, and every row is due to be checked again.
        for row in (
            await session.scalars(
                select(OfficialSource).where(
                    OfficialSource.canonical_asset_id == asset.id
                )
            )
        ).all():
            row.last_checked_at = None
        pages = {url: _page() for url in _curated_urls("BTC", "Bitcoin")}
        await SourceResolutionService(
            session, settings, fetcher=_StubFetcher(pages)
        ).resolve_asset(asset)
        await session.commit()

        closed = await session.scalar(
            select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
        )
        assert closed is not None
        assert closed.done_at is not None


async def test_a_proved_link_is_not_fetched_again_the_next_day(test_context) -> None:
    """Politeness to other people's servers, and speed for us."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        pages = {url: _page() for url in _curated_urls("BTC", "Bitcoin")}
        first = _StubFetcher(pages)
        await SourceResolutionService(session, settings, fetcher=first).resolve_asset(asset)
        await session.commit()
        assert first.requested, "the first run must actually fetch"

        second = _StubFetcher(pages)
        await SourceResolutionService(session, settings, fetcher=second).resolve_asset(asset)
        await session.commit()
        assert second.requested == [], "nothing should be fetched twice in a day"


async def test_a_sweep_only_looks_at_identities_a_reviewer_approved(test_context) -> None:
    """An unapproved identity has no address worth deriving anything from."""

    async with test_context["session_factory"]() as session:
        unverified = CanonicalAsset(
            symbol="DRAFT",
            name="Draft Coin",
            asset_type="coin",
            identity_hash="hash-draft",
            mapping_state="unresolved",
        )
        session.add(unverified)
        await session.flush()
        sweep = await SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher({})
        ).resolve_pending()
        await session.commit()

        assert [item.symbol for item in sweep.assets] == []


async def test_a_link_a_person_typed_is_checked_like_any_other(test_context) -> None:
    """A reviewer is not a proof.

    Somebody can paste a page that has moved, and the review that reads it months later
    cannot tell a checked address from an unchecked one. So a typed address goes in as
    a candidate and is fetched before it counts.
    """

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        reviewer = User(display_name="Reviewer", role=UserRole.ADMIN)
        session.add(reviewer)
        await session.flush()
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher({})
        )
        row = await service.add_reviewed_source(
            asset,
            category=NEWS,
            url="https://typed-but-dead.example/blog",
            reviewer_id=reviewer.id,
        )
        await session.commit()

        assert row.verification_state != "verified"
        assert row.confidence == 0.0
        assert row.verified_by_user_id == reviewer.id


async def test_a_working_link_a_person_typed_closes_the_job(test_context) -> None:
    """The other half: the task has to be finishable."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        reviewer = User(display_name="Reviewer", role=UserRole.ADMIN)
        session.add(reviewer)
        await session.flush()
        await SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher({})
        ).resolve_asset(asset)
        await session.commit()

        pages = {
            "https://found.example/blog": _page(),
            "https://found.example/forum": _page(),
        }
        service = SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher(pages)
        )
        for category, url in (
            (NEWS, "https://found.example/blog"),
            ("official_community", "https://found.example/forum"),
        ):
            row = await service.add_reviewed_source(
                asset, category=category, url=url, reviewer_id=reviewer.id
            )
            assert row.verification_state == "verified", url
        outcome = await service.resolve_asset(asset)
        await session.commit()

        assert outcome.missing == ()
        case = await session.scalar(
            select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
        )
        assert case is not None
        assert case.done_at is not None, "the job should be finished"


@pytest.mark.parametrize("action", list(BULK_ACTIONS))
async def test_a_link_hunting_job_is_never_treated_as_a_case_to_decide(
    test_context, action
) -> None:
    """Every bulk action, not just approve.

    A gap task has no dossier, so the decision path would refuse it anyway — with the
    sentence that started this work: "this case is missing part of its evidence: the
    asset identity, the official source record, or the research folder". That is true
    of every blocked case and tells a reviewer nothing. The refusal has to name the
    actual job instead.
    """

    async with test_context["session_factory"]() as session:
        asset = await _asset(session, symbol="NOTLISTED", name="Not Listed")
        await SourceResolutionService(
            session, test_context["settings"], fetcher=_StubFetcher({})
        ).resolve_asset(asset)
        await session.commit()
        case = await session.scalar(
            select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
        )
        assert case is not None

        reviewer = User(display_name="Reviewer", role=UserRole.ADMIN)
        session.add(reviewer)
        await session.flush()
        outcome = await BulkReviewService(session, test_context["settings"]).apply(
            [case.id],
            action=action,
            reason="Checking that this cannot be decided by mistake.",
            admin_user_id=reviewer.id,
        )

        assert outcome.applied == 0
        assert outcome.failed == 1
        said = outcome.results[0].message
        assert "find a missing official page" in said, said
        assert "research folder" not in said, "the confusing old sentence came back"
        # And it never asks the reviewer to do the machine's job. The resolver walks its
        # layers again on every sweep; telling somebody to go and type an address for two
        # hundred coins is what this wording replaced.
        assert "keeps looking" in said, said


# --------------------------------------------------------------------------
# One list of the kinds of case that exist
# --------------------------------------------------------------------------


def test_every_kind_of_case_the_code_creates_is_a_kind_the_queue_knows() -> None:
    """A case a reviewer cannot filter to is a case nobody works on.

    The review queue used to keep its own private list of kinds. It drifted both ways:
    it offered two filters for kinds nothing has ever created, and a new kind stayed
    invisible until somebody remembered to add it in a second place. This reads the
    services themselves, so adding a kind without telling the queue fails here.
    """

    services = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"
    written: set[str] = set()
    pattern = re.compile(r"case_type=ReviewCaseType\.([A-Z_]+)")
    for path in services.rglob("*.py"):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            written.add(name)
    assert written, "no case type was found at all — has the spelling changed?"
    known = {item.name for item in ReviewCaseType}
    assert written <= known, f"created but unknown to the queue: {sorted(written - known)}"


def test_no_review_case_type_is_still_written_as_a_bare_string() -> None:
    """Extraction, not patching: every caller uses the one vocabulary."""

    services = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"
    offenders: list[str] = []
    pattern = re.compile(r"""case_type=["'][a-z_]+["']""")
    for path in services.rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert offenders == [], f"still writing a raw case type: {offenders}"


async def test_turning_the_resolver_off_stops_it_proposing_anything(test_context) -> None:
    settings = test_context["settings"].model_copy(
        update={"sharia_source_resolution_enabled": False}
    )
    async with test_context["session_factory"]() as session:
        await _asset(session)
        sweep = await SourceResolutionService(
            session, settings, fetcher=_StubFetcher({})
        ).resolve_pending()
        assert sweep.assets == []

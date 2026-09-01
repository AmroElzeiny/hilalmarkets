"""Finding a coin's own news channels on the open web, and never filing anybody else's.

Four rules are pinned here, each across the whole family rather than on one example.

**Only the project's own page is an official source.** A search for a coin's news
returns market-data sites, exchange listings and press coverage long before it returns
the project. Every one of those is refused, whatever it says in its title.

**One spelling of one channel.** ``t.me/foo``, ``t.me/s/foo`` and ``telegram.me/foo``
are one channel; ``twitter.com/foo`` and ``x.com/foo`` are one feed. Storing them twice
would count one channel twice as evidence, which is the same defect the URL
normalisation rule exists to stop.

**Activity never un-verifies anything.** A page that has gone quiet is still a real
official source. It stops holding a category up on its own — that is all — so the
product looks for more links rather than telling a reviewer that evidence disappeared.

**A dead link and a forbidden one are different jobs.** A 404 needs a replacement
address; a site whose robots policy says no is the right address that the product may
never quote. Both used to be filed as "unreachable", and a reviewer went hunting for a
page that already existed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from ai_market_monitor.core.config import Settings, _is_optional_secret
from ai_market_monitor.db.models import CanonicalAsset, OfficialSource, ReviewCase
from ai_market_monitor.services.sharia_research import (
    FetchTarget,
    OfficialEvidenceFetcher,
    ShariaResearchError,
    extract_links,
)
from ai_market_monitor.services.sharia_source_activity import (
    ACTIVITY_FLOOR,
    CADENCE_TARGET_ITEMS,
    FINANCIAL_TERMS,
    FRESH_WITHIN_DAYS,
    GOVERNANCE_TERMS,
    TOPIC_TERMS,
    measure,
    newest_published_at,
    published_dates,
)
from ai_market_monitor.services.sharia_source_catalog import (
    COMMUNITY,
    LAYER_CONFIDENCE,
    LAYER_ORDER,
    LINKS_REQUIRED_PER_CATEGORY,
    NEWS,
    NEWS_MAXIMUM_AGE_DAYS,
    REQUIRED_CATEGORIES,
    SEARCH_QUERIES,
    SHARED_PUBLISHING_HOSTS,
    SOURCE_CATEGORIES,
    TRACKED_CATEGORIES,
    VERIFICATION_STATES,
    DiscoveryLayer,
    SearchResult,
    candidates_for,
    categories_below,
    channel_candidates,
    classify_channel,
    handle_matches_project,
    is_official_url,
    is_same_project_site,
    normalized_url,
    registrable_domain,
    search_candidates,
    search_queries,
    state_label,
)
from ai_market_monitor.services.sharia_source_discovery import (
    WebSourceDiscovery,
    _rows_to_results,
)
from ai_market_monitor.services.sharia_source_resolution import (
    CANDIDATE,
    NOT_PERMITTED,
    UNREACHABLE,
    VERIFIED,
    SourceProof,
    SourceResolutionService,
    score_candidate,
)

# ---------------------------------------------------------------------------
# Which addresses are a channel, and what kind
# ---------------------------------------------------------------------------

_CHANNELS = [
    ("https://t.me/aptos", NEWS, "https://t.me/s/aptos"),
    ("https://t.me/s/aptos", NEWS, "https://t.me/s/aptos"),
    ("https://telegram.me/aptos", NEWS, "https://t.me/s/aptos"),
    ("https://x.com/Aptos", NEWS, "https://x.com/Aptos"),
    ("https://twitter.com/Aptos", NEWS, "https://x.com/Aptos"),
    ("https://www.reddit.com/r/Aptos/", COMMUNITY, "https://www.reddit.com/r/Aptos/"),
    ("https://old.reddit.com/r/Aptos/", COMMUNITY, "https://www.reddit.com/r/Aptos/"),
    ("https://discord.gg/aptos", COMMUNITY, "https://discord.gg/aptos"),
    ("https://discord.com/invite/aptos", COMMUNITY, "https://discord.gg/aptos"),
    ("https://github.com/aptos-labs/aptos-core", NEWS, "https://github.com/aptos-labs/aptos-core/releases"),
    (
        "https://github.com/aptos-labs/aptos-core/discussions",
        COMMUNITY,
        "https://github.com/aptos-labs/aptos-core/discussions",
    ),
    ("https://medium.com/aptoslabs", NEWS, "https://medium.com/aptoslabs"),
    ("https://medium.com/@aptoslabs", NEWS, "https://medium.com/@aptoslabs"),
    ("https://aptos.mirror.xyz/", NEWS, "https://aptos.mirror.xyz/"),
    ("https://mirror.xyz/aptos", NEWS, "https://mirror.xyz/aptos"),
    ("https://aptos.substack.com/", NEWS, "https://aptos.substack.com/"),
    ("https://www.youtube.com/@aptos", NEWS, "https://www.youtube.com/@aptos"),
]


@pytest.mark.parametrize(("address", "category", "stored"), _CHANNELS)
def test_every_channel_the_product_knows_gets_one_category_and_one_spelling(
    address, category, stored
) -> None:
    """The vocabulary, asserted platform by platform rather than on one example."""

    channel = classify_channel(address)
    assert channel is not None, address
    assert channel.category == category
    assert channel.url == stored
    assert channel.category in SOURCE_CATEGORIES
    assert is_official_url(channel.url)


@pytest.mark.parametrize(("address", "category", "stored"), _CHANNELS)
def test_a_channel_written_any_way_is_stored_as_one_page(address, category, stored) -> None:
    """Two spellings of one channel would be two sources for one coin."""

    channel = classify_channel(address)
    assert channel is not None
    assert normalized_url(channel.url) == normalized_url(stored)


_NOT_CHANNELS = [
    "https://x.com/Aptos/status/1234567890",
    "https://x.com/i/flow/login",
    "https://x.com/search?q=aptos",
    "https://x.com/hashtag/aptos",
    "https://twitter.com/home",
    "https://t.me/+AbCdEfGh",
    "https://t.me/joinchat/AbCdEfGh",
    "https://www.reddit.com/user/somebody",
    "https://www.reddit.com/",
    "https://discord.com/channels/123/456",
    "https://github.com/aptos-labs",
    "https://www.youtube.com/watch?v=abcdefg",
    "http://t.me/aptos",
    "https://coindesk.com/aptos-news",
    "https://www.coingecko.com/en/coins/aptos",
    "https://binance.com/en/price/aptos",
    "https://en.wikipedia.org/wiki/Aptos",
]


@pytest.mark.parametrize("address", _NOT_CHANNELS)
def test_a_single_post_a_platform_page_or_somebody_elses_site_is_not_a_source(
    address,
) -> None:
    """Fail closed. Anything the rules cannot vouch for is simply not proposed."""

    assert classify_channel(address) is None, address


# ---------------------------------------------------------------------------
# Whose page is it
# ---------------------------------------------------------------------------

_SAME_SITE = [
    ("https://blog.ethereum.org/", "https://ethereum.org/"),
    ("https://ethereum.org/news", "https://www.ethereum.org/"),
    ("https://www.ethereum.org/blog", "https://ethereum.org"),
    ("https://forum.example.co.uk/", "https://example.co.uk/"),
]
_DIFFERENT_SITE = [
    ("https://ethereum-news.example/", "https://ethereum.org/"),
    ("https://ethereum.org.evil.example/", "https://ethereum.org/"),
    ("https://notethereum.org/", "https://ethereum.org/"),
    ("https://example.co.uk.evil.example/", "https://example.co.uk/"),
]


@pytest.mark.parametrize(("address", "site"), _SAME_SITE)
def test_a_subdomain_of_the_approved_site_is_the_same_project(address, site) -> None:
    assert is_same_project_site(address, site) is True


@pytest.mark.parametrize(("address", "site"), _DIFFERENT_SITE)
def test_a_lookalike_domain_is_never_the_same_project(address, site) -> None:
    """The whole officialness test rests on this one comparison."""

    assert is_same_project_site(address, site) is False


@pytest.mark.parametrize("host", sorted(SHARED_PUBLISHING_HOSTS))
def test_on_a_host_anybody_can_publish_under_only_the_exact_host_counts(host) -> None:
    """``project.github.io`` and ``evil.github.io`` share a registrable domain.

    Comparing registrable domains there would hand any page on the platform to any
    project that happens to publish on it.
    """

    site = f"https://project.{host}/"
    assert is_same_project_site(f"https://project.{host}/blog", site) is True
    assert is_same_project_site(f"https://somebody-else.{host}/blog", site) is False


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("https://ethereum.org/", "ethereum.org"),
        ("https://blog.ethereum.org/", "ethereum.org"),
        ("https://a.b.c.ethereum.org/", "ethereum.org"),
        ("https://www.example.co.uk/", "example.co.uk"),
        ("https://forum.example.co.uk/", "example.co.uk"),
        ("https://example.com.au/", "example.com.au"),
        ("https://news.example.com.au/", "example.com.au"),
    ],
)
def test_the_owner_of_a_host_is_read_the_same_way_every_time(host, expected) -> None:
    assert registrable_domain(host) == expected


# ---------------------------------------------------------------------------
# Whose handle is it
# ---------------------------------------------------------------------------

_HANDLE_MATCHES = [
    ("aptos", "Aptos", "APT"),
    ("Aptos", "Aptos", "APT"),
    ("aptos_network", "Aptos", "APT"),
    ("AptosOfficial", "Aptos", "APT"),
    ("aptos-labs", "Aptos", "APT"),
    ("apt", "Aptos", "APT"),
    ("ethereum", "Ethereum", "ETH"),
    ("ethereumfoundation", "Ethereum", "ETH"),
]
_HANDLE_REFUSED = [
    ("aptosnews_daily", "Aptos", "APT"),
    ("cryptodaily", "Aptos", "APT"),
    ("coindesk", "Aptos", "APT"),
    ("aptos_fan_club", "Aptos", "APT"),
    ("", "Aptos", "APT"),
]


@pytest.mark.parametrize(("handle", "name", "symbol"), _HANDLE_MATCHES)
def test_a_handle_that_is_the_projects_own_name_is_accepted(handle, name, symbol) -> None:
    assert handle_matches_project(handle, asset_name=name, symbol=symbol) is True


@pytest.mark.parametrize(("handle", "name", "symbol"), _HANDLE_REFUSED)
def test_somebody_elses_handle_is_refused(handle, name, symbol) -> None:
    """Refusing is always safe: the address is not proposed and the next layer runs."""

    assert handle_matches_project(handle, asset_name=name, symbol=symbol) is False


# ---------------------------------------------------------------------------
# The search layer keeps only what belongs to the project
# ---------------------------------------------------------------------------

_THIRD_PARTY_RESULTS = [
    SearchResult(url="https://www.coindesk.com/tag/aptos/", title="Aptos official news"),
    SearchResult(url="https://cointelegraph.com/tags/aptos", title="Aptos news"),
    SearchResult(url="https://www.coingecko.com/en/coins/aptos", title="Aptos price"),
    SearchResult(url="https://www.binance.com/en/price/aptos", title="Aptos official"),
    SearchResult(url="https://x.com/CoinDesk", title="Aptos official X account"),
    SearchResult(url="https://t.me/crypto_signals_vip", title="Aptos official Telegram"),
    SearchResult(url="https://www.reddit.com/r/CryptoCurrency/", title="Aptos community"),
    SearchResult(url="https://medium.com/some-random-blog", title="Aptos official blog"),
]


@pytest.mark.parametrize("result", _THIRD_PARTY_RESULTS, ids=lambda item: item.url)
def test_a_page_about_a_coin_is_never_filed_as_a_page_by_the_coin(result) -> None:
    """The register holds what the project said. Coverage of it is a different thing.

    Every one of these ranks highly for "Aptos official news" and none of them is the
    project speaking, however true the article is.
    """

    produced = search_candidates(
        asset_name="Aptos",
        symbol="APT",
        official_website="https://aptosfoundation.org/",
        results=[result],
    )
    assert produced == (), result.url


def test_a_repository_under_the_projects_own_organisation_is_kept() -> None:
    """And one under somebody else's is not. The organisation name is the handle."""

    ours = search_candidates(
        asset_name="Aptos",
        symbol="APT",
        official_website="https://aptosfoundation.org/",
        results=[SearchResult(url="https://github.com/aptos-labs/aptos-core")],
    )
    theirs = search_candidates(
        asset_name="Aptos",
        symbol="APT",
        official_website="https://aptosfoundation.org/",
        results=[SearchResult(url="https://github.com/some-fan/aptos-tracker")],
    )
    assert [item.url for item in ours] == ["https://github.com/aptos-labs/aptos-core/releases"]
    assert theirs == ()


def test_the_projects_own_pages_survive_the_same_search() -> None:
    """The other half: the filter must not throw away the answer it was looking for."""

    produced = search_candidates(
        asset_name="Aptos",
        symbol="APT",
        official_website="https://aptosfoundation.org/",
        results=[
            *_THIRD_PARTY_RESULTS,
            SearchResult(url="https://aptosfoundation.org/currents", title="Currents"),
            SearchResult(url="https://forum.aptosfoundation.org/", title="Forum"),
            SearchResult(url="https://t.me/aptos_network", title="Aptos on Telegram"),
        ],
    )
    found = {item.url for item in produced}
    assert "https://aptosfoundation.org/currents" in found
    assert "https://forum.aptosfoundation.org/" in found
    assert "https://t.me/s/aptos_network" in found
    for candidate in produced:
        assert candidate.layer is DiscoveryLayer.SEARCH
        # Tracked, not required. A forum that turns up in the answers to the news
        # questions is still kept and proved — the community page is optional, which
        # means "never demanded", not "thrown away when it is there".
        assert candidate.category in TRACKED_CATEGORIES
        assert is_official_url(candidate.url)


@pytest.mark.parametrize("template", SEARCH_QUERIES)
def test_every_question_asked_names_the_coin(template) -> None:
    """A query that lost the coin's name would return the whole crypto internet."""

    query = template.format(name="Aptos", symbol="APT")
    assert "Aptos" in query
    assert "{" not in query


def test_a_coin_with_no_name_is_not_searched_for() -> None:
    assert search_queries(asset_name="  ", symbol="APT") == ()


# ---------------------------------------------------------------------------
# The channel layer reads the project's own site
# ---------------------------------------------------------------------------


def test_the_channels_a_project_links_to_from_its_own_page_are_proposed() -> None:
    """The strongest claim there is short of a person checking: the project said so."""

    produced = channel_candidates(
        asset_name="Aptos",
        official_website="https://aptosfoundation.org/",
        links=[
            "https://t.me/aptos_network",
            "https://x.com/Aptos",
            "https://www.reddit.com/r/Aptos/",
            "https://discord.gg/aptoslabs",
            "https://www.coindesk.com/tag/aptos/",
            "https://aptosfoundation.org/currents",
        ],
    )
    found = {item.url for item in produced}
    assert "https://t.me/s/aptos_network" in found
    assert "https://x.com/Aptos" in found
    assert "https://www.reddit.com/r/Aptos/" in found
    assert "https://aptosfoundation.org/currents" in found
    assert not any("coindesk" in url for url in found)
    for candidate in produced:
        assert candidate.layer is DiscoveryLayer.SOCIAL
        assert candidate.confidence == LAYER_CONFIDENCE[DiscoveryLayer.SOCIAL]


def test_the_same_channel_linked_twice_is_proposed_once() -> None:
    produced = channel_candidates(
        asset_name="Aptos",
        official_website="https://aptosfoundation.org/",
        links=["https://t.me/aptos", "https://t.me/s/aptos", "https://telegram.me/aptos"],
    )
    assert len(produced) == 1


def test_a_page_with_no_links_proposes_nothing() -> None:
    assert channel_candidates(asset_name="A", official_website="https://a.example/", links=[]) == ()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('<a href="https://t.me/foo">t</a>', ("https://t.me/foo",)),
        ('<a href="/blog">b</a>', ("https://site.example/blog",)),
        ('<a href="#top">t</a><a href="mailto:a@b.c">m</a>', ()),
        ('<a href="javascript:void(0)">j</a>', ()),
        ("not html at all", ()),
        ("", ()),
    ],
)
def test_reading_the_links_off_a_page_never_raises(body, expected) -> None:
    """Finding no links is an ordinary answer, not an error."""

    assert extract_links(body, "https://site.example/") == expected


def test_a_pdf_has_no_links_to_read() -> None:
    assert extract_links(b"%PDF-1.7 binary", "https://site.example/paper.pdf") == ()


# ---------------------------------------------------------------------------
# The layers, including the two new ones
# ---------------------------------------------------------------------------


def test_every_layer_is_less_trusted_than_the_one_before_it() -> None:
    scores = [LAYER_CONFIDENCE[layer] for layer in LAYER_ORDER]
    assert scores == sorted(scores, reverse=True)
    assert len(set(LAYER_ORDER)) == len(DiscoveryLayer)


@pytest.mark.parametrize("layer", [DiscoveryLayer.SOCIAL, DiscoveryLayer.SEARCH])
def test_a_layer_that_was_given_nothing_to_look_at_offers_nothing(layer) -> None:
    """Web search being unconfigured must be silent, not an error."""

    assert (
        candidates_for(
            layer,
            symbol="APT",
            asset_name="Aptos",
            official_website="https://aptosfoundation.org/",
            official_documentation="https://aptos.dev/",
        )
        == ()
    )


def test_the_single_entry_point_routes_both_new_layers() -> None:
    social = candidates_for(
        DiscoveryLayer.SOCIAL,
        symbol="APT",
        asset_name="Aptos",
        official_website="https://aptosfoundation.org/",
        official_documentation=None,
        channel_links=["https://x.com/Aptos"],
    )
    search = candidates_for(
        DiscoveryLayer.SEARCH,
        symbol="APT",
        asset_name="Aptos",
        official_website="https://aptosfoundation.org/",
        official_documentation=None,
        search_results=[SearchResult(url="https://aptosfoundation.org/currents")],
    )
    assert [item.layer for item in social] == [DiscoveryLayer.SOCIAL]
    assert [item.layer for item in search] == [DiscoveryLayer.SEARCH]


@pytest.mark.parametrize(
    ("counts", "wanted", "expected"),
    [
        ({}, 1, REQUIRED_CATEGORIES),
        ({NEWS: 1}, 1, ()),
        ({NEWS: 1, COMMUNITY: 1}, 1, ()),
        ({NEWS: 1, COMMUNITY: 1}, 3, (NEWS,)),
        # The community count moves nothing, at either number. A coin with three news
        # pages and no forum is not short and is not missing anything.
        ({NEWS: 3, COMMUNITY: 1}, 3, ()),
        ({NEWS: 3}, 3, ()),
        ({COMMUNITY: 9}, 1, (NEWS,)),
        ({NEWS: 9, COMMUNITY: 9}, 3, ()),
    ],
)
def test_short_is_one_question_asked_with_two_numbers(counts, wanted, expected) -> None:
    """Wanted and required are different numbers read by the same rule."""

    assert categories_below(counts, wanted) == expected


@pytest.mark.parametrize("wanted", [1, 2, 3, 9])
@pytest.mark.parametrize("held", [0, 1, 5])
def test_a_missing_community_page_is_never_a_gap(wanted, held) -> None:
    """The whole rule, across every number either side of it.

    A project that runs no forum, no subreddit and no public Discord is an ordinary
    project, and a page that does not exist can never be found however many layers look
    for it. Until 1 September 2026 every one of those coins opened a task saying "no
    working official community page" that nobody could ever clear.

    Asserted over the family rather than one case: whatever the wanted number is, and
    however many news pages a coin holds, the community count must not be able to put a
    category into the answer.
    """

    with_forum = categories_below({NEWS: held, COMMUNITY: 4}, wanted)
    without_forum = categories_below({NEWS: held}, wanted)
    assert with_forum == without_forum, (
        "whether a coin has a community page changed the answer, so a project that runs "
        "no forum is still being reported as having a gap"
    )
    assert COMMUNITY not in with_forum
    assert COMMUNITY not in categories_below({}, wanted)


# ---------------------------------------------------------------------------
# How active a page is
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("term", TOPIC_TERMS)
def test_every_word_in_the_vocabulary_is_actually_looked_for(term) -> None:
    """Each term individually, so a word cannot be listed and never matched."""

    now = datetime(2026, 8, 23, tzinfo=UTC)
    activity = measure(f"The project explained its {term} in detail.", category=NEWS, now=now)
    assert term in {*activity.financial_terms, *activity.governance_terms}, term


def test_the_two_vocabularies_are_kept_whole() -> None:
    assert set(TOPIC_TERMS) == {*FINANCIAL_TERMS, *GOVERNANCE_TERMS}
    assert len(set(FINANCIAL_TERMS)) == len(FINANCIAL_TERMS)
    assert len(set(GOVERNANCE_TERMS)) == len(GOVERNANCE_TERMS)


@pytest.mark.parametrize(
    "days_old",
    [0, 1, FRESH_WITHIN_DAYS, FRESH_WITHIN_DAYS + 1, 200, NEWS_MAXIMUM_AGE_DAYS - 1,
     NEWS_MAXIMUM_AGE_DAYS, NEWS_MAXIMUM_AGE_DAYS + 100],
)
def test_a_page_never_scores_higher_for_being_older(days_old) -> None:
    """Recency has to fall as a page ages, at every age, and never leave 0..1."""

    now = datetime(2026, 8, 23, tzinfo=UTC)
    older = (now - timedelta(days=days_old)).date().isoformat()
    newer = (now - timedelta(days=max(0, days_old - 1))).date().isoformat()
    old_score = measure(f"Posted {older}.", category=NEWS, now=now)
    new_score = measure(f"Posted {newer}.", category=NEWS, now=now)
    assert 0.0 <= old_score.recency <= 1.0
    assert new_score.recency >= old_score.recency


def test_a_page_with_no_dates_at_all_has_no_recency() -> None:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    activity = measure("Nothing dated here at all.", category=NEWS, now=now)
    assert activity.recency == 0.0
    assert activity.dated_items == 0
    assert activity.newest_published_at is None


def test_a_feed_that_keeps_publishing_scores_above_an_archive() -> None:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    many = " ".join(
        f"Posted {(now - timedelta(days=index * 7)).date().isoformat()}."
        for index in range(CADENCE_TARGET_ITEMS + 2)
    )
    one = f"Posted {(now - timedelta(days=3)).date().isoformat()}."
    feed = measure(many, category=NEWS, now=now)
    archive = measure(one, category=NEWS, now=now)
    assert feed.cadence > archive.cadence


@pytest.mark.parametrize("category", list(SOURCE_CATEGORIES))
def test_an_activity_score_is_always_a_number_between_zero_and_one(category) -> None:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    for text in ("", "nothing", "Posted 2026-08-20. staking governance fees vote treasury upgrade"):
        activity = measure(text, category=category, now=now)
        assert 0.0 <= activity.score <= 1.0
        assert 0.0 <= activity.topic <= 1.0
        assert 0.0 <= activity.cadence <= 1.0
        assert 0.0 <= activity.recency <= 1.0


def test_the_newest_date_is_read_from_the_same_place_as_every_date() -> None:
    """One owner. Two readers of a page's dates would drift apart."""

    now = datetime(2026, 8, 23, tzinfo=UTC)
    text = "older 2024-01-01 and 12 March 2026 and newest June 1, 2026"
    dates = published_dates(text, now=now)
    assert newest_published_at(text, now=now) == dates[0]
    assert dates == tuple(sorted(dates, reverse=True))


def test_a_future_date_is_not_evidence_the_page_is_alive() -> None:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    assert published_dates("Our conference is on 2027-05-01", now=now) == ()


_DATE_SHAPES = [
    "2026-04-09",
    "2026-04-09T07:01:44+00:00",
    "2026-04-09T07:01:44Z",
    "2026-04-09 07:01:44",
    "Posted 2026-04-09.",
    "<time>2026-04-09T07:01:44+00:00</time>",
    "updated=2026-04-09T07:01:44",
    "9 April 2026",
    "9 Apr 2026",
    "April 9, 2026",
    "Apr. 9 2026",
]


@pytest.mark.parametrize("written", _DATE_SHAPES)
def test_a_date_is_read_however_the_page_writes_it(written) -> None:
    """Every shape a real page uses, not the one shape that happened to be tested.

    The timestamp forms are the reason this exists: the old rule needed a word boundary
    after the day, and there is none between ``9`` and ``T``. Every page that states its
    dates as timestamps was read as never having published anything.
    """

    now = datetime(2026, 8, 23, tzinfo=UTC)
    found = published_dates(written, now=now)
    assert found, written
    assert (found[0].year, found[0].month, found[0].day) == (2026, 4, 9), written


@pytest.mark.parametrize(
    "written",
    ["2026-04-091", "2026-04-09-15", "12026-04-09", "2026-13-01", "2026-02-31"],
)
def test_a_run_of_digits_is_not_a_date(written) -> None:
    """Loosening the end of the pattern must not let a longer number in."""

    now = datetime(2026, 8, 23, tzinfo=UTC)
    assert published_dates(written, now=now) == (), written


# ---------------------------------------------------------------------------
# What the states are called
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", sorted(VERIFICATION_STATES))
def test_every_state_has_plain_words_and_no_internal_spelling_reaches_a_person(state) -> None:
    said = state_label(state)
    assert said
    assert "_" not in said, said
    assert said != state or state == said.replace(" ", "_")


def test_the_states_the_resolver_writes_are_the_states_the_vocabulary_knows() -> None:
    """A state written but not named would print as a raw field value on the page."""

    assert set(VERIFICATION_STATES) == {VERIFIED, CANDIDATE, UNREACHABLE, NOT_PERMITTED}


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, None), (None, "robots_disallowed")],
)
def test_a_page_we_are_not_allowed_to_read_is_not_called_a_dead_link(status, code) -> None:
    """A 404 needs a new address. This needs a person to decide it may never be quoted."""

    proof = SourceProof(
        reachable=False,
        allowed=code != "robots_disallowed",
        readable=False,
        fresh=False,
        status=status,
        error_code=code,
    )
    assert proof.forbidden is True
    assert proof.definitively_dead is True


@pytest.mark.parametrize("status", [404, 410, 451, 400, 401, 405])
def test_a_gone_page_is_not_called_forbidden(status) -> None:
    proof = SourceProof(
        reachable=False, allowed=True, readable=False, fresh=False, status=status
    )
    assert proof.forbidden is False
    assert proof.definitively_dead is True


# ---------------------------------------------------------------------------
# What a site's robots.txt answer means
# ---------------------------------------------------------------------------
#
# One wrong reading here removes a whole website silently. Until 1 September 2026 every
# 4xx except 404 was read as "we could not verify the policy" and refused every address
# on the origin — so a site behind an ordinary bot filter, which answers 403 to a plain
# robots.txt request from a datacentre address, had all of its pages refused for ever
# while they were perfectly readable in a browser. The case then told a reviewer the
# project had no news page.
#
# The rule is the robots standard's own (RFC 9309, section 2.3.1), and it is asserted
# across every status a site can answer with rather than on the one that was reported.


def _robots_internet(status: int, *, body: str = "", page_status: int = 200):
    """An internet where robots.txt answers `status` and the page itself is fine."""

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text=body)
        return httpx.Response(
            page_status,
            text=(
                "<html><body><h1>Newsroom</h1><p>"
                + ("A post about fees. " * 40)
                + "</p></body></html>"
            ),
            headers={"content-type": "text/html"},
        )

    return httpx.MockTransport(handler)


async def _try_to_fetch(settings, transport) -> str:
    """Fetch one page through the real fetcher. Returns "" on success, else the code."""

    fetcher = OfficialEvidenceFetcher(settings, transport=transport)
    try:
        await fetcher.fetch(FetchTarget("https://project.example/blog"))
    except ShariaResearchError as exc:
        return exc.code
    return ""


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 410, 418, 451])
async def test_a_site_that_publishes_no_rules_is_open(test_context, status) -> None:
    """Any 4xx on robots.txt means there are no rules, so everything is allowed.

    404 always meant this. 403 is the one that matters in practice — it is what a bot
    filter answers — and it used to refuse the whole site.
    """

    code = await _try_to_fetch(test_context["settings"], _robots_internet(status))
    assert code == "", (
        f"robots.txt answering HTTP {status} refused the page. A 4xx says the site "
        "publishes no rules for us; it says nothing about what the site permits."
    )


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_a_site_that_could_not_say_is_not_read(test_context, status) -> None:
    """The opposite half of the rule, and it must stay.

    "There are no rules" and "we could not find out what the rules are" are opposite
    answers. A 429 or a 5xx is the second one, and reading the site anyway would be
    helping ourselves to a permission nobody gave.
    """

    code = await _try_to_fetch(test_context["settings"], _robots_internet(status))
    assert code == "robots_unavailable", (
        f"robots.txt answering HTTP {status} did not stop the fetch. That is the "
        "product deciding a site's rules for it."
    )


async def test_a_site_that_forbids_us_is_still_refused(test_context) -> None:
    """The rule cuts both ways: a real Disallow is obeyed exactly as before."""

    transport = _robots_internet(200, body="User-agent: *\nDisallow: /\n")
    code = await _try_to_fetch(test_context["settings"], transport)
    assert code == "robots_disallowed"


async def test_one_sweep_asks_a_site_for_its_rules_once(test_context) -> None:
    """Both the answer and the failure are remembered for the run.

    Without this a sweep re-fetches robots.txt once per address, which is a dozen extra
    requests to a site that has already said it cannot answer — the exact behaviour a
    rate-limited site was rate-limiting.
    """

    asked: list[str] = []

    def handler(request):
        asked.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(503)
        return httpx.Response(200, text="<html><body>page</body></html>")

    fetcher = OfficialEvidenceFetcher(
        test_context["settings"], transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ShariaResearchError):
        await fetcher.fetch(FetchTarget("https://project.example/blog"))
    # Whatever the retry layer spent on that first attempt is its business. What matters
    # is that asking again costs nothing at all.
    after_first = asked.count("/robots.txt")
    assert after_first >= 1

    for path in ("/news", "/announcements", "/press"):
        with pytest.raises(ShariaResearchError):
            await fetcher.fetch(FetchTarget(f"https://project.example{path}"))

    assert asked.count("/robots.txt") == after_first, (
        f"robots.txt was fetched {asked.count('/robots.txt') - after_first} more time(s) "
        "for an origin that had already said it could not answer. A sweep tries a dozen "
        "addresses per coin, so that is a dozen extra requests to a site that is already "
        "rate-limiting us."
    )


# ---------------------------------------------------------------------------
# Blank credentials
# ---------------------------------------------------------------------------

_OPTIONAL_SECRETS = sorted(
    name for name, field in Settings.model_fields.items() if _is_optional_secret(field)
)


def test_the_settings_file_still_has_optional_credentials_to_check() -> None:
    assert len(_OPTIONAL_SECRETS) > 10


@pytest.mark.parametrize("name", _OPTIONAL_SECRETS)
def test_a_blank_credential_means_not_configured(name) -> None:
    """``KEY=`` in an env file is "no provider", never "a provider with an empty key".

    Every reader of these asks ``is None``. An empty string is not None, so the caller
    took the configured path and sent nothing, and the provider's 401 looked like a bad
    key rather than a missing one. Asserted for every optional credential, because the
    two examples in git ship that empty line for several of them.
    """

    settings = Settings(_env_file=None, **{name: ""})
    assert getattr(settings, name) is None, name


# ---------------------------------------------------------------------------
# The resolver, end to end
# ---------------------------------------------------------------------------

_GONE = ShariaResearchError("official_source_fetch_failed", "Official source returned HTTP 404.")
_ROBOTS = ShariaResearchError(
    "robots_disallowed", "The official source does not permit automated retrieval."
)


def _live_page(*, days_old: int = 3, now: datetime | None = None) -> tuple[str, dict, int]:
    """A page that is alive, on topic, and publishing."""

    moment = now or datetime.now(UTC)
    posts = "".join(
        f"<p>Posted {(moment - timedelta(days=days_old + index * 7)).date().isoformat()}. "
        "The foundation published a governance proposal about staking rewards, "
        "treasury fees and the protocol upgrade.</p>"
        for index in range(CADENCE_TARGET_ITEMS + 1)
    )
    body = f"<html><head><title>Newsroom</title></head><body><h1>Latest</h1>{posts}</body></html>"
    return (body, {"content-type": "text/html"}, 200)


class _Internet:
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


class _Discovery:
    """A stand-in for the two layers that go and look.

    It answers **every** question ``WebSourceDiscovery`` answers, including the two that
    are only about configuration. A stub that implements a subset passes its own tests
    while the real caller meets an attribute that is not there — which is exactly what
    happened when the gap-case wording started naming the layers that could not run.
    """

    def __init__(
        self,
        links: tuple[str, ...] = (),
        results: tuple[SearchResult, ...] = (),
        *,
        configured: bool = True,
    ) -> None:
        self._links = links
        self._results = results
        self._configured = configured
        self.searched = 0

    @property
    def search_configured(self) -> bool:
        return self._configured

    def search_requirement(self) -> str:
        return "" if self._configured else "Web search is not configured in this test."

    async def channel_links(self, official_website):
        return self._links

    async def search(self, *, asset_name, symbol):
        self.searched += 1
        return self._results


async def _asset(
    session,
    *,
    symbol: str = "APT",
    name: str = "Aptos",
    website: str = "https://aptosfoundation.org/",
) -> CanonicalAsset:
    asset = CanonicalAsset(
        symbol=symbol,
        name=name,
        asset_type="coin",
        official_website=website,
        official_documentation="https://github.com/aptos-labs/aptos-core",
        identity_hash=f"hash-{symbol}",
        mapping_state="verified",
    )
    session.add(asset)
    await session.flush()
    return asset


async def test_a_coin_keeps_collecting_links_past_the_first_working_one(test_context) -> None:
    """The product asked for this: more than four or five links for one coin."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        channels = (
            "https://t.me/aptos_network",
            "https://www.reddit.com/r/Aptos/",
            "https://aptosfoundation.org/currents",
            "https://forum.aptosfoundation.org/",
        )
        pages = {
            "https://t.me/s/aptos_network": _live_page(),
            "https://www.reddit.com/r/Aptos/": _live_page(),
            "https://aptosfoundation.org/currents": _live_page(),
            "https://forum.aptosfoundation.org/": _live_page(),
            "https://aptosfoundation.org/blog": _live_page(),
            "https://aptosfoundation.org/community": _live_page(),
        }
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet(pages),
            discovery=_Discovery(links=channels),
        )
        outcome = await service.resolve_asset(asset, deep=True)
        await session.commit()

        assert outcome.missing == ()
        assert outcome.escalated is False
        for category in REQUIRED_CATEGORIES:
            assert outcome.coverage[category] >= 2, outcome.coverage
        working = list(
            (
                await session.scalars(
                    select(OfficialSource).where(
                        OfficialSource.canonical_asset_id == asset.id,
                        OfficialSource.verification_state == VERIFIED,
                    )
                )
            ).all()
        )
        assert len(working) >= 4, [row.source_url for row in working]


async def test_a_channel_the_project_links_to_is_registered_as_its_own_source(
    test_context,
) -> None:
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        pages = {"https://t.me/s/aptos_network": _live_page()}
        service = SourceResolutionService(
            session,
            test_context["settings"],
            fetcher=_Internet(pages),
            discovery=_Discovery(links=("https://t.me/aptos_network",)),
        )
        await service.resolve_asset(asset, deep=True)
        await session.commit()

        row = await session.scalar(
            select(OfficialSource).where(
                OfficialSource.source_url == "https://t.me/s/aptos_network"
            )
        )
        assert row is not None
        assert row.verification_state == VERIFIED
        assert row.discovery_layer == str(DiscoveryLayer.SOCIAL)
        assert row.category == NEWS


async def test_a_channel_the_site_does_not_let_us_read_is_recorded_as_blocked(
    test_context,
) -> None:
    """It is the right address. It is not a dead link, and a person needs to know which."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        service = SourceResolutionService(
            session,
            test_context["settings"],
            fetcher=_Internet({}, default=_ROBOTS),
            discovery=_Discovery(links=("https://x.com/Aptos",)),
        )
        await service.resolve_asset(asset, deep=True)
        await session.commit()

        row = await session.scalar(
            select(OfficialSource).where(OfficialSource.source_url == "https://x.com/Aptos")
        )
        assert row is not None
        assert row.verification_state == NOT_PERMITTED
        assert state_label(row.verification_state) == "blocked to us"


async def test_a_page_that_went_quiet_is_never_taken_away_from_a_reviewer(
    test_context,
) -> None:
    """Activity makes the machine look for more. It never removes what is there."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        quiet = (
            "<html><head><title>Forum</title></head><body><h1>Threads</h1><p>"
            + ("Welcome to the discussion board. " * 20)
            + "</p></body></html>",
            {"content-type": "text/html"},
            200,
        )
        service = SourceResolutionService(
            session,
            settings,
            fetcher=_Internet({"https://forum.aptosfoundation.org/": quiet}),
            discovery=_Discovery(links=("https://forum.aptosfoundation.org/",)),
        )
        outcome = await service.resolve_asset(asset, deep=True)
        await session.commit()

        row = await session.scalar(
            select(OfficialSource).where(
                OfficialSource.source_url == "https://forum.aptosfoundation.org/"
            )
        )
        assert row is not None
        assert row.verification_state == VERIFIED, "a quiet page is still a real source"
        assert row.is_active is True
        assert row.confidence > 0
        assert COMMUNITY not in outcome.missing, "quiet must never read as missing"


async def test_the_search_layer_says_nothing_when_no_engine_is_configured(
    test_context,
) -> None:
    """No key, no candidates, no error, and nothing else changes."""

    discovery = WebSourceDiscovery(test_context["settings"])
    assert discovery.search_configured is False
    assert await discovery.search(asset_name="Aptos", symbol="APT") == ()
    assert "GOOGLE_SEARCH_API_KEY" in discovery.search_requirement()


async def test_a_searched_link_is_registered_under_the_layer_that_found_it(
    test_context,
) -> None:
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        # An address no curated entry already holds, so the layer credited is the one
        # that actually found it.
        found = "https://aptosfoundation.org/newsroom"
        service = SourceResolutionService(
            session,
            test_context["settings"],
            fetcher=_Internet({found: _live_page()}),
            discovery=_Discovery(results=(SearchResult(url=found),)),
        )
        await service.resolve_asset(asset, deep=True)
        await session.commit()

        row = await session.scalar(
            select(OfficialSource).where(OfficialSource.source_url == found)
        )
        assert row is not None
        assert row.discovery_layer == str(DiscoveryLayer.SEARCH)
        assert row.verification_state == VERIFIED


async def test_the_scheduled_sweep_does_not_spend_a_search_on_a_covered_coin(
    test_context,
) -> None:
    """Searching costs money and somebody else's server a request.

    A coin is looked at properly once, and then not again until its recheck window comes
    round. Without that rule the daily sweep would spend a search on every coin every
    day and find the same links each time. The operator's re-check ignores the calendar,
    because that is what asking for a full re-check means.
    """

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(
            session, symbol="BTC", name="Bitcoin", website="https://bitcoin.org/"
        )
        pages = {
            "https://bitcoincore.org/en/blog/": _live_page(),
            "https://bitcointalk.org/": _live_page(),
            "https://bitcoin.org/": _live_page(),
        }
        first = _Discovery()
        await SourceResolutionService(
            session, settings, fetcher=_Internet(pages), discovery=first
        ).resolve_asset(asset)
        await session.commit()
        assert first.searched == 1, "a coin nobody has looked at properly gets one look"

        again = _Discovery()
        await SourceResolutionService(
            session, settings, fetcher=_Internet(pages), discovery=again
        ).resolve_asset(asset)
        await session.commit()
        assert again.searched == 0, "the next day must cost nothing"

        deep = _Discovery()
        await SourceResolutionService(
            session, settings, fetcher=_Internet(pages), discovery=deep, force_recheck=True
        ).resolve_asset(asset, deep=True)
        await session.commit()
        assert deep.searched == 1


async def test_a_forced_recheck_proves_every_link_but_withdraws_only_trusted_ones(
    test_context,
) -> None:
    """The rule that keeps a human task closable.

    Re-proving a row that was never evidence and reporting it as *withdrawn* would open
    a task every single sweep that nothing could ever close.
    """

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        session.add_all(
            [
                OfficialSource(
                    canonical_asset_id=asset.id,
                    category=NEWS,
                    title="Never proved",
                    source_url="https://never-proved.example/blog",
                    normalized_url=normalized_url("https://never-proved.example/blog"),
                    priority=30,
                    verification_state=CANDIDATE,
                    is_active=True,
                    last_checked_at=datetime.now(UTC),
                ),
                OfficialSource(
                    canonical_asset_id=asset.id,
                    category=COMMUNITY,
                    title="Trusted",
                    source_url="https://trusted.example/forum",
                    normalized_url=normalized_url("https://trusted.example/forum"),
                    priority=40,
                    verification_state=VERIFIED,
                    is_active=True,
                    confidence=0.9,
                    last_checked_at=datetime.now(UTC),
                ),
            ]
        )
        await session.flush()
        internet = _Internet({}, default=_GONE)
        outcome = await SourceResolutionService(
            session,
            settings,
            fetcher=internet,
            discovery=_Discovery(),
            force_recheck=True,
        ).resolve_asset(asset)
        await session.commit()

        assert "https://never-proved.example/blog" in internet.requested
        assert "https://trusted.example/forum" in internet.requested
        withdrawn = " ".join(outcome.withdrawn)
        assert "trusted.example" in withdrawn
        assert "never-proved.example" not in withdrawn


async def test_a_forced_recheck_fetches_each_address_once(test_context) -> None:
    """Twice would be two requests to somebody's server for one answer."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(
            session, symbol="BTC", name="Bitcoin", website="https://bitcoin.org/"
        )
        pages = {
            "https://bitcoincore.org/en/blog/": _live_page(),
            "https://bitcointalk.org/": _live_page(),
        }
        await SourceResolutionService(
            session, settings, fetcher=_Internet(pages), discovery=_Discovery()
        ).resolve_asset(asset)
        await session.commit()

        internet = _Internet(pages)
        await SourceResolutionService(
            session,
            settings,
            fetcher=internet,
            discovery=_Discovery(),
            force_recheck=True,
        ).resolve_asset(asset, deep=True)
        await session.commit()

        assert len(internet.requested) == len(set(internet.requested)), internet.requested


async def test_the_official_website_is_registered_and_proved_like_any_other_link(
    test_context,
) -> None:
    """It was the one address nothing ever fetched, because no layer proposes it."""

    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        pages = {"https://aptosfoundation.org/": _live_page()}
        await SourceResolutionService(
            session,
            test_context["settings"],
            fetcher=_Internet(pages),
            discovery=_Discovery(),
        ).resolve_asset(asset)
        await session.commit()

        row = await session.scalar(
            select(OfficialSource).where(
                OfficialSource.source_url == "https://aptosfoundation.org/"
            )
        )
        assert row is not None
        assert row.last_checked_at is not None, "the homepage was registered and never checked"


async def test_a_link_is_never_counted_twice_towards_coverage(test_context) -> None:
    """Coverage is counted from the rows, never added up as the layers go.

    Two places keeping the same tally is how a link gets counted twice: once when the
    sweep read the asset's rows, and again when a later layer offered the same address
    and found it already checked. The coin then looked better covered than it was and
    the layers stopped looking early.
    """

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(
            session, symbol="BTC", name="Bitcoin", website="https://bitcoin.org/"
        )
        pages = {
            "https://bitcoincore.org/en/blog/": _live_page(),
            "https://bitcointalk.org/": _live_page(),
            "https://bitcoin.org/": _live_page(),
        }
        first = await SourceResolutionService(
            session, settings, fetcher=_Internet(pages), discovery=_Discovery()
        ).resolve_asset(asset)
        await session.commit()

        second = await SourceResolutionService(
            session, settings, fetcher=_Internet(pages), discovery=_Discovery()
        ).resolve_asset(asset)
        await session.commit()

        working = {
            category: sum(
                1
                for row in (
                    await session.scalars(
                        select(OfficialSource).where(
                            OfficialSource.canonical_asset_id == asset.id,
                            OfficialSource.category == category,
                            OfficialSource.verification_state == VERIFIED,
                        )
                    )
                ).all()
                if row.is_active
            )
            for category in TRACKED_CATEGORIES
        }
        assert second.coverage == working, "the second run counted links it already had"
        assert first.coverage == working


async def test_being_short_of_the_wanted_number_never_opens_a_task(test_context) -> None:
    """A queue that says "this coin has two news pages instead of three" is unreadable."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        asset = await _asset(
            session, symbol="BTC", name="Bitcoin", website="https://bitcoin.org/"
        )
        pages = {
            "https://bitcoincore.org/en/blog/": _live_page(),
            "https://bitcointalk.org/": _live_page(),
        }
        outcome = await SourceResolutionService(
            session, settings, fetcher=_Internet(pages), discovery=_Discovery()
        ).resolve_asset(asset)
        await session.commit()

        assert outcome.short, "the coin is below the wanted number, which is the point"
        assert outcome.missing == ()
        assert outcome.escalated is False
        case = await session.scalar(
            select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
        )
        assert case is None


@pytest.mark.parametrize("layer", list(LAYER_ORDER))
def test_no_layer_can_reach_the_floor_without_being_proved(layer) -> None:
    """Including the two new ones. The whole design rests on this."""

    for reachable in (True, False):
        for allowed in (True, False):
            for readable in (True, False):
                for fresh in (True, False):
                    proof = SourceProof(
                        reachable=reachable, allowed=allowed, readable=readable, fresh=fresh
                    )
                    score = score_candidate(LAYER_CONFIDENCE[layer], proof)
                    assert (score >= LINKS_REQUIRED_PER_CATEGORY * 0.70) is proof.usable


@pytest.mark.parametrize(
    ("rows", "url_key", "title_key"),
    [
        ([{"link": "https://a.example/"}], "link", "title"),
        ([{"url": "https://a.example/"}], "url", "title"),
        ([], "link", "title"),
        (None, "link", "title"),
        ("not a list", "link", "title"),
        ([{"link": ""}, {"link": "not-a-url"}, "junk", None], "link", "title"),
    ],
)
def test_one_engines_answer_is_read_in_one_place(rows, url_key, title_key) -> None:
    """A malformed answer produces nothing. It never raises into the sweep."""

    produced = _rows_to_results(rows, url_key=url_key, title_key=title_key)
    for item in produced:
        assert item.url.startswith("https://")


def test_the_activity_floor_is_a_real_number_the_product_can_reach() -> None:
    """A floor of 1.0 would mean nothing ever counts; 0.0 would mean the check is off."""

    assert 0.0 < ACTIVITY_FLOOR < 1.0


# ---------------------------------------------------------------------------
# What the re-check script prints
# ---------------------------------------------------------------------------


async def test_the_recheck_report_is_written_in_words_a_person_can_act_on(
    test_context, tmp_path
) -> None:
    """The report is the whole point of the script, so its shape is pinned.

    Two things it must never do: print an internal state word like ``not_permitted`` at
    a reader, and lose the activity number that says why a link is being replaced.
    """

    from scripts.recheck_official_sources import _asset_report, _write_report

    async with test_context["session_factory"]() as session:
        asset = await _asset(session)
        pages = {
            "https://t.me/s/aptos_network": _live_page(),
            "https://aptosfoundation.org/currents": _live_page(),
        }
        service = SourceResolutionService(
            session,
            test_context["settings"],
            fetcher=_Internet(pages, default=_ROBOTS),
            discovery=_Discovery(
                links=("https://t.me/aptos_network", "https://x.com/Aptos")
            ),
            force_recheck=True,
        )
        outcome = await service.resolve_asset(asset, deep=True)
        rows = list(
            (
                await session.scalars(
                    select(OfficialSource).where(
                        OfficialSource.canonical_asset_id == asset.id
                    )
                )
            ).all()
        )
        await session.commit()

    report = _asset_report(asset, rows, outcome, activity_floor=ACTIVITY_FLOOR)
    assert report["symbol"] == "APT"
    assert report["links"], "the report must list what was checked"
    for link in report["links"]:
        assert link["state"] in set(VERIFICATION_STATES.values()), link
        assert "_" not in str(link["state"])
        assert link["category"] in set(SOURCE_CATEGORIES.values())
    proved = [item for item in report["links"] if item["state"] == "working"]
    assert proved, "the working links must be reported as working"
    assert all(item["activity"] is not None for item in proved)
    blocked = [item for item in report["links"] if item["state"] == "blocked to us"]
    assert blocked, "the X account the site forbids must be shown as blocked, not gone"

    destination = tmp_path / "report.md"
    _write_report(
        destination,
        {
            "finished_at": "2026-08-23T00:00:00+00:00",
            "assets_checked": 1,
            "links_checked": len(report["links"]),
            "links_working": len(proved),
            "links_quiet": len(report["quiet_links"]),
            "links_added": len(report["newly_proved"]),
            "links_withdrawn": len(report["withdrawn"]),
            "people_asked": int(report["person_asked"]),
            "tasks_closed": 0,
            "search": "off",
            "browser": "on, using playwright, up to 40 page(s) per run",
            "assets": [report],
        },
    )
    written = destination.read_text(encoding="utf-8")
    assert "Official source re-check" in written
    assert "APT" in written
    assert "not_permitted" not in written, "an internal word reached the report"
    assert "Shariah status" in written, "the report must say what the score is not"
    # Whether a browser was available decides whether the run could help most of the
    # coins it was pointed at, so it is reported rather than left to be guessed at.
    assert "browser" in written.casefold()

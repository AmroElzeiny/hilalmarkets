"""What counts as a project's own voice, and what a crawl is allowed to spend.

Every rule here was written after a real coin was judged wrongly, and each names the
coin, because a rule whose reason is forgotten is a rule somebody deletes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_market_monitor.services.coin_evidence_crawler import (
    DEFAULT_PAGE_BUDGET,
    DEFAULT_PER_HOST_BUDGET,
    ECOSYSTEM_PATH_WORDS,
    MINIMUM_DOCUMENT_CHARACTERS,
    EvidenceDocument,
    EvidenceFolder,
    _is_readable,
    _normalise,
)
from ai_market_monitor.services.sharia_source_catalog import (
    DOCUMENTATION,
    NEWS,
    WEBSITE,
    is_aggregator_url,
    page_category,
)

NOW = datetime.now(UTC)


def _document(url: str, category: str = WEBSITE) -> EvidenceDocument:
    return EvidenceDocument(
        url=url, category=category, title="t", text="x" * 500, fetched_at=NOW
    )


# --------------------------------------------------------------------------------
# Whose page is this?
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://coinmarketcap.com/currencies/solana/",
        "https://www.coingecko.com/en/coins/solana",
        "https://en.wikipedia.org/wiki/Ethereum",
        "https://www.investopedia.com/terms/e/ethereum.asp",
        "https://www.coindesk.com/markets/2026/01/01/something",
    ],
)
def test_a_page_about_a_project_is_not_the_projects_page(url):
    """Wikipedia's article on Ethereum refused Ethereum for running a futures business.

    An encyclopedia paragraph about how regulators classify ether is writing *about*
    the project. Read as Ethereum's own description of itself, it was a confession.
    """

    assert is_aggregator_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gate.com/trade/GUSD_USDT",
        "https://www.binance.com/en/price/solana",
        "https://www.okx.com/markets/prices/aptos",
    ],
)
def test_an_exchange_listing_page_is_not_the_projects_page(url):
    """`gate.com/trade/GUSD_USDT` was returned as Gemini Dollar's own website."""

    assert is_aggregator_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gemini.com/dollar",
        "https://crypto.com/cro",
        "https://www.coinbase.com/blog/introducing-something",
    ],
)
def test_an_exchange_that_is_also_the_issuer_keeps_its_own_pages(url):
    """The other half, and the reason the host alone cannot decide.

    Gemini really does publish Gemini Dollar, and Crypto.com really does publish CRO.
    Banning the host would silence the project's own site while trying to silence
    somebody else's listing of it.
    """

    assert is_aggregator_url(url) is False


@pytest.mark.parametrize("word", sorted(ECOSYSTEM_PATH_WORDS))
def test_an_ecosystem_page_is_never_the_projects_own_description(word):
    """A chain's own site is largely a showcase of what other people built on it.

    Cardano's site lists an interview with a lending protocol. Avalanche's says a fund
    manager chose it for a money market fund. Both pages are written by the chain and
    neither describes the chain.
    """

    document = _document(f"https://project.example/{word}/something", DOCUMENTATION)
    assert document.is_primary is False


def test_the_projects_own_documentation_is_primary():
    """Without this the rule above passes by refusing everything."""

    assert _document("https://project.example/docs/intro", DOCUMENTATION).is_primary


def test_a_news_page_is_never_primary():
    assert _document("https://project.example/blog/post", NEWS).is_primary is False


# --------------------------------------------------------------------------------
# What kind of page is this?
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://project.example/", WEBSITE),
        ("https://project.example/whitepaper", DOCUMENTATION),
        ("https://project.example/tokenomics", DOCUMENTATION),
        ("https://docs.project.example/", DOCUMENTATION),
        ("https://project.example/blog/one", NEWS),
        ("https://project.example/news", NEWS),
        ("https://project.example/research/whitepaper", DOCUMENTATION),
    ],
)
def test_a_page_is_filed_by_what_it_is(url, expected):
    """Documentation wins over the broader words, because its own are the specific ones."""

    assert page_category(url) == expected


# --------------------------------------------------------------------------------
# One spelling per page, and a budget.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://project.example/docs",
        "https://project.example/docs/",
        "https://project.example/docs#intro",
        "https://PROJECT.example/docs",
        "https://www.project.example/docs",
        "https://WWW.Project.Example/docs/#intro",
    ],
)
def test_one_page_has_one_spelling(url):
    """Fetching the same words three times spends a third of the budget on nothing.

    The ``www.`` spellings cost more than a wasted fetch, which is why they are here.
    A refusal needs the same activity on two of the project's own pages, and on
    31 August 2026 Ethereum was refused for running a lending business on the strength
    of ``ethereum.org/developers/tools`` and ``www.ethereum.org/developers/tools`` —
    one page, counted twice. Two spellings of one page do not corroborate each other.
    """

    assert _normalise(url) == "https://project.example/docs"


@pytest.mark.parametrize("word", ["tools", "libraries", "sdks", "wallets"])
def test_a_directory_of_other_peoples_software_is_not_self_description(word):
    """The ecosystem page in another costume.

    ``ethereum.org/developers/tools`` lists hundreds of third-party projects a line at a
    time — "Seamless Protocol is the largest native lending and borrowing DeFi platform
    on Base". Read as Ethereum describing itself, that line helped refuse Ethereum.
    """

    document = _document(f"https://project.example/developers/{word}", DOCUMENTATION)
    assert document.is_primary is False


@pytest.mark.parametrize(
    "url",
    [
        "https://project.example/logo.svg",
        "https://project.example/app.js",
        "https://project.example/careers",
        "https://project.example/privacy",
        "https://project.example/feed.xml",
    ],
)
def test_pages_that_say_nothing_about_the_project_are_not_fetched(url):
    assert _is_readable(url) is False


def test_the_pages_worth_reading_are_fetched():
    for url in (
        "https://project.example/docs",
        "https://project.example/whitepaper.pdf",
        "https://project.example/blog/post",
    ):
        assert _is_readable(url) is True


def test_the_page_budget_is_bounded():
    """A documentation site with ten thousand pages must not become the whole run.

    The upper bound is not a guess at a safe number — it is the screen's stated reach.
    The methodology says the screen reads *up to eighty of a project's own pages*, and
    that a rule needing more than that is out of its scope rather than unanswered. Those
    two sentences are only true while this number is eighty, so the bound is exact in
    both directions and changing it means changing what the product claims about itself.
    """

    assert DEFAULT_PAGE_BUDGET == 80
    # Per host, so one deep documentation tree cannot spend the whole budget alone.
    assert DEFAULT_PER_HOST_BUDGET < DEFAULT_PAGE_BUDGET
    assert MINIMUM_DOCUMENT_CHARACTERS > 0


# --------------------------------------------------------------------------------
# An empty folder is the only "we cannot say".
# --------------------------------------------------------------------------------


def test_a_folder_with_one_page_is_not_empty():
    folder = EvidenceFolder(symbol="ANY", documents=[_document("https://a.example/")])
    assert folder.is_empty is False
    assert folder.primary_documents


def test_a_folder_with_only_failures_is_empty():
    folder = EvidenceFolder(symbol="ANY", failures={"https://a.example/": "too_short"})
    assert folder.is_empty is True

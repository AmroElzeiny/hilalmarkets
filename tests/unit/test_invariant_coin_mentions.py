"""One reader for "which coin did the person name", shared by both assistants.

The defect this file pins: the public assistant's Passport lookup kept its own regex and
a three-entry nickname table (bitcoin, ethereum, solana), while Hilal read every listing.
"Is chainlink halal?" found LINK in the dashboard and nothing on the public site. The
fix is one module both import, so the tests below assert the *rule* for every spelling
of every listed coin, and that neither assistant carries a private copy any more.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_market_monitor.services.coin_mentions import (
    CoinListingIndex,
    names_in,
    spelling_keys,
    tickers_in,
)

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "src" / "ai_market_monitor" / "services"

#: Listings as the database holds them: symbol, name, and one market pair each.
LISTINGS = [
    ("BTC", "Bitcoin"),
    ("ETH", "Ethereum"),
    ("SOL", "Solana"),
    ("LINK", "Chainlink"),
    ("LTC", "Litecoin"),
    ("BCH", "Bitcoin Cash"),
    ("NOT", "Notcoin"),
]
MARKETS = [(f"{symbol}/USDT", symbol) for symbol, _ in LISTINGS]


@pytest.fixture(scope="module")
def index() -> CoinListingIndex:
    return CoinListingIndex.build(LISTINGS, MARKETS)


def _spellings(symbol: str, name: str) -> list[str]:
    """Every way a person writes one coin in a question."""

    return [
        symbol,
        f"${symbol}",
        symbol.lower(),
        name,
        name.lower(),
        f"{symbol}/USDT",
        f"{symbol}USDT",
        f"{symbol.lower()}usdt",
    ]


@pytest.mark.parametrize(
    ("symbol", "spelling"),
    [(symbol, spelling) for symbol, name in LISTINGS for spelling in _spellings(symbol, name)],
)
def test_every_spelling_of_every_listed_coin_resolves_to_it(index, symbol, spelling):
    assert index.symbol_for(spelling) == symbol


@pytest.mark.parametrize(
    ("symbol", "template"),
    [
        (symbol, template)
        for symbol, _ in LISTINGS
        for template in ("Is {} halal?", "what about {} now", "Tell me about the {} coin")
    ],
)
def test_a_question_naming_one_coin_is_about_that_coin(index, symbol, template):
    name = dict(LISTINGS)[symbol]
    for spelling in (symbol, name, f"${symbol}"):
        assert symbol in index.asked_about(template.format(spelling)), spelling


@pytest.mark.parametrize(
    "question",
    ["is bitcoin cash halal", "Is Bitcoin Cash halal?", "what about the bitcoin cash coin"],
)
def test_a_two_word_name_wins_over_the_word_inside_it(index, question):
    """A filler word before the name used to turn "is bitcoin" into Bitcoin first."""

    assert index.asked_about(question) == ["BCH"]
    # No phrase is built around a filler word.
    phrases = [item.lower() for item in names_in(question) if " " in item]
    assert "is bitcoin" not in phrases and "the bitcoin" not in phrases


def test_a_marked_symbol_is_the_whole_answer(index):
    """"Is BTC not allowed?" is about BTC — not also about a coin called NOT."""

    assert index.asked_about("Is BTC not allowed?") == ["BTC"]
    assert index.asked_about("is $sol not halal") == ["SOL"]


def test_a_question_that_names_no_coin_is_about_none(index):
    for question in (
        "How do I set up an alert?",
        "What does Hilal Markets cost?",
        "Can I use Telegram?",
    ):
        assert index.asked_about(question) == [], question


def test_shouting_is_not_a_list_of_tickers():
    assert tickers_in("IS BTC HALAL") == []
    assert tickers_in("Is BTC halal?") == ["BTC"]
    assert tickers_in("what about $ltc") == ["$ltc"]


def test_names_keep_their_order_and_pairs_come_first():
    assert names_in("bitcoin cash price")[:2] == ["bitcoin cash", "cash price"]


def test_spelling_is_mechanical_only():
    assert spelling_keys("The Bitcoin coin") & spelling_keys("Bitcoin")
    assert not (spelling_keys("bitcoin") & spelling_keys("ethereum"))


@pytest.mark.parametrize(
    "module",
    ["hilal_chat_knowledge.py", "public_support_tools.py", "public_chat.py"],
)
def test_no_assistant_keeps_its_own_coin_reader(module):
    """The duplicates are gone: no nickname table, no private token regex."""

    text = (SERVICES / module).read_text(encoding="utf-8")
    assert '"bitcoin": "BTC"' not in text
    assert "_TOKEN = re.compile" not in text
    assert "_NOISE = re.compile" not in text

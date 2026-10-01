"""Meme coins are not covered by the Hilal Markets Methodology — asserted as a rule.

The owner decided on 1 October 2026 that every meme coin is *not suitable* under this
standard. A coin is known to be a meme coin in three ways, and each is tested across its
whole family rather than on one example:

* the owner's list — every coin on it, against every outcome the file could claim;
* the provider's label — every shape of meme tag CoinMarketCap uses, and words that only
  look like one;
* the project's own words — condition WG-07, read by the same page reader as every other
  condition (its found / quoted / denied / corroborated properties are asserted for every
  phrase by ``test_invariant_evidence_screen.py``).

And one reader of the word "meme" for the whole product: the market evaluator's "no meme
coins" card asks the same function the methodology does.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ai_market_monitor.services import hilal_methodology as hm
from ai_market_monitor.services.coin_evidence_crawler import (
    EvidenceDocument,
    EvidenceFolder,
)
from ai_market_monitor.services.meme_coins import (
    MEME_CONDITION,
    MEME_RULE_SENTENCE,
    MEME_WORDS,
    SELF_DESCRIBED_CONDITION,
    MemeSignal,
    is_meme_label,
    meme_finding,
    owner_list,
)
from ai_market_monitor.services.sharia_automated_screen import Activity
from ai_market_monitor.services.sharia_conditions import (
    Detection,
    Status,
    condition,
    status_of,
)
from ai_market_monitor.services.sharia_evidence_screen import EvidenceVerdict, decide

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)
LISTED = sorted(owner_list())

#: Every shape a meme label takes at CoinMarketCap, plus category words.
MEME_LABELS = [
    "memes",
    "Memes",
    "animal-memes",
    "political-memes",
    "celebrity-memes",
    "solana-meme-coins",
    "Meme",
    "Meme coin",
    "memecoin",
    "Memecoins",
]

#: Words that contain the letters and are not the word.
NOT_MEME_LABELS = [
    "",
    "   ",
    None,
    "memento",
    "memetic-art",
    "layer-1",
    "coin",
    "token",
    "solana-ecosystem",
    "governance",
]


def _empty(symbol: str) -> EvidenceFolder:
    return EvidenceFolder(symbol=symbol, documents=[])


def _own_page(text: str, url: str) -> EvidenceDocument:
    return EvidenceDocument(
        url=url,
        category="official_website",
        title="Project",
        text=text,
        fetched_at=NOW,
        seeded=True,
    )


# --------------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("label", MEME_LABELS)
def test_every_meme_label_is_read_as_a_meme(label):
    assert is_meme_label(label)


@pytest.mark.parametrize("label", NOT_MEME_LABELS)
def test_a_word_that_only_looks_like_meme_is_not_one(label):
    assert not is_meme_label(label)


@pytest.mark.parametrize("word", sorted(MEME_WORDS))
def test_every_meme_word_counts_on_its_own_and_inside_a_slug(word):
    assert is_meme_label(word)
    assert is_meme_label(f"animal-{word}")
    assert is_meme_label(f"{word.upper()} coin")


# --------------------------------------------------------------------------------
# The owner's list
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", LISTED)
def test_every_listed_coin_is_a_meme_with_a_reason_and_a_source(symbol):
    finding = meme_finding(symbol)
    assert finding is not None
    assert finding.signal is MemeSignal.OWNER_LIST
    assert finding.reason.startswith(MEME_RULE_SENTENCE)
    assert finding.source_url.startswith("https://")
    assert finding.condition == MEME_CONDITION


@pytest.mark.parametrize("symbol", LISTED)
def test_a_listed_coin_is_found_whatever_case_it_is_written_in(symbol):
    assert meme_finding(symbol.lower()) is not None
    assert meme_finding(f" {symbol} ") is not None


def test_a_coin_with_no_list_entry_and_no_label_is_not_a_meme():
    assert meme_finding("BTC", tags=["mineable", "pow", "layer-1"], category="coin") is None


@pytest.mark.parametrize("label", MEME_LABELS)
def test_a_provider_label_alone_makes_a_coin_a_meme(label):
    finding = meme_finding("NEWCOIN", tags=["layer-1", label], provider_slug="new-coin")
    assert finding is not None
    assert finding.signal is MemeSignal.PROVIDER_TAG
    assert finding.source_url == "https://coinmarketcap.com/currencies/new-coin/"
    assert label.strip() in finding.reason


@pytest.mark.parametrize("label", MEME_LABELS)
def test_a_provider_category_alone_makes_a_coin_a_meme(label):
    assert meme_finding("NEWCOIN", category=label) is not None


# --------------------------------------------------------------------------------
# The automated screen
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", LISTED)
def test_a_listed_meme_coin_is_refused_even_when_nothing_could_be_read(symbol):
    decision = decide(symbol, symbol, _empty(symbol))
    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE
    assert MEME_CONDITION in decision.matched_conditions
    assert decision.blocking_activities == [Activity.NO_UNDERLYING_UTILITY]
    assert decision.reasons[0].text.startswith(MEME_RULE_SENTENCE)
    assert decision.reasons[0].url.startswith("https://")


@pytest.mark.parametrize("label", MEME_LABELS)
def test_a_provider_tagged_meme_is_refused_even_when_nothing_could_be_read(label):
    decision = decide("NEWCOIN", "New Coin", _empty("NEWCOIN"), provider_tags=[label])
    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE
    assert MEME_CONDITION in decision.matched_conditions


@pytest.mark.parametrize("label", [item for item in NOT_MEME_LABELS if item])
def test_a_non_meme_label_changes_nothing(label):
    decision = decide("NEWCOIN", "New Coin", _empty("NEWCOIN"), provider_tags=[label])
    assert decision.verdict is EvidenceVerdict.NOT_ENOUGH_DATA
    assert MEME_CONDITION not in decision.matched_conditions


def test_the_self_described_rule_is_an_approved_text_condition_on_the_same_activity():
    rule = condition(SELF_DESCRIBED_CONDITION)
    assert status_of(SELF_DESCRIBED_CONDITION) is Status.APPROVED
    assert rule.detection is Detection.TEXT and rule.phrases
    assert rule.activity is condition(MEME_CONDITION).activity
    # Singular only: "memecoins" is how a launchpad talks about other people's coins.
    assert all(not phrase.endswith("s") for phrase in rule.phrases)


@pytest.mark.parametrize("phrase", condition(SELF_DESCRIBED_CONDITION).phrases)
def test_a_project_calling_itself_a_meme_coin_on_its_own_pages_is_refused(phrase):
    pages = [
        _own_page(
            f"Woof is a {phrase} built by its community. Woof is the {phrase} for "
            f"everyone, and holders of Woof run every part of it. Woof is a {phrase}.",
            url=f"https://woof.example/{number}",
        )
        for number in range(4)
    ]
    decision = decide("WOOF", "Woof", EvidenceFolder(symbol="WOOF", documents=pages))
    assert decision.verdict is EvidenceVerdict.NOT_ELIGIBLE
    assert SELF_DESCRIBED_CONDITION in decision.matched_conditions


# --------------------------------------------------------------------------------
# The published list
# --------------------------------------------------------------------------------


def test_the_published_list_loads_and_refuses_every_listed_coin_it_names():
    assets = {item.symbol: item for item in hm.admitted_assets()}
    for symbol in LISTED:
        if symbol not in assets:
            continue
        asset = assets[symbol]
        assert asset.outcome is hm.Outcome.REFUSED
        assert asset.is_meme_refusal
        assert MEME_CONDITION in asset.matched_conditions
        assert symbol not in hm.admitted_symbols()


def test_pepe_is_refused_as_a_meme_coin():
    pepe = next(item for item in hm.admitted_assets() if item.symbol == "PEPE")
    assert pepe.status.value == "excluded"
    assert pepe.summary().startswith("Pepe (PEPE) is a meme coin.")
    assert pepe.exclusion_reasons[0]["code"] == MEME_CONDITION


@pytest.mark.parametrize("symbol", LISTED)
@pytest.mark.parametrize("outcome", [hm.Outcome.ADMITTED, hm.Outcome.NOT_ENOUGH_DATA])
def test_a_listed_meme_coin_can_never_be_published_as_anything_but_refused(symbol, outcome):
    source = hm.Source(
        url="https://project.example/",
        title="Project",
        category="official_website",
        retrieved_at=date(2026, 10, 1),
    )
    with pytest.raises(hm.AdmissionError) as raised:
        hm.AdmittedAsset(
            symbol=symbol,
            name=symbol,
            admission=hm.Admission.AUTOMATED_SCREEN,
            outcome=outcome,
            decided_on=date(2026, 10, 1),
            reasons=("Nothing refused it.",),
            sources=(source,),
        )
    assert raised.value.code == "meme_coin_not_refused"


@pytest.mark.parametrize("symbol", LISTED)
def test_a_listed_meme_coin_refused_for_another_reason_is_still_refused_wrongly(symbol):
    """Refused is not enough: the record has to say *why* — the meme rule, first."""

    source = hm.Source(
        url="https://project.example/",
        title="Project",
        category="official_website",
        retrieved_at=date(2026, 10, 1),
    )
    with pytest.raises(hm.AdmissionError):
        hm.AdmittedAsset(
            symbol=symbol,
            name=symbol,
            admission=hm.Admission.AUTOMATED_SCREEN,
            outcome=hm.Outcome.REFUSED,
            decided_on=date(2026, 10, 1),
            reasons=("The project's own business is lending money.",),
            sources=(source,),
            matched_conditions=("RB-01",),
        )


def test_the_published_record_states_the_rule_and_its_list():
    rules = hm.methodology_rules()
    assert rules["meme_coins_covered"] is False
    assert rules["meme_condition"] == MEME_CONDITION
    assert rules["meme_list"] == LISTED
    assert MEME_RULE_SENTENCE in rules["meme_rule"]
    assert "meme_coin_exclusion" in {item["key"] for item in rules["required_criteria"]}
    assert MEME_RULE_SENTENCE in hm.methodology_description()
    payload = hm.page_payload()
    assert {item["symbol"] for item in payload["memeRule"]["listed"]} == set(LISTED)
    assert payload["memeRule"]["text"] == hm.MEME_RULE


# --------------------------------------------------------------------------------
# One reader of the word
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("label", [*MEME_LABELS, *[item for item in NOT_MEME_LABELS if item]])
def test_the_market_card_and_the_methodology_read_meme_the_same_way(label):
    from ai_market_monitor.engine import evaluator

    assert evaluator.is_meme_label is is_meme_label
    assert evaluator.is_meme_label(label) == (meme_finding("X", category=label) is not None)


def test_no_module_keeps_its_own_list_of_meme_words():
    """The duplicate-vocabulary failure, guarded for this word: one owner, no copies."""

    allowed = {
        ROOT / "src/ai_market_monitor/services/meme_coins.py",
        # The register's phrases for WG-07, read by the shared page reader.
        ROOT / "src/ai_market_monitor/services/sharia_conditions.py",
    }
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src/ai_market_monitor").rglob("*.py")
        if path not in allowed and '"memecoin"' in path.read_text(encoding="utf-8")
    ]
    assert offenders == []

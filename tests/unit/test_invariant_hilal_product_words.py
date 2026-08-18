"""Hilal can explain this product's own words, and only from what it was given.

The rule Hilal is held to above all others is that every fact it states comes from the
evidence it was handed. That rule had a hole in it: the evidence carried coins,
standards, plans and the board a person is drawing, and nothing at all about what the
words *on* that board mean. So the most common beginner question on the canvas — "what
is a Group?" — had two possible answers and both were wrong: say it did not know, about
the product it is supposed to be the expert on, or invent one.

These tests pin the fix as a rule rather than as one word:

* the vocabulary reaches the model on **every** turn, not only on the canvas;
* every word in it is **citable**, like a coin or a plan;
* every word is one the **interface actually prints**, so a renamed control fails here
  rather than leaving Hilal explaining a word nobody can see;
* the definitions stay inside the lines Hilal may not cross — no ruling, no advice, no
  number.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.services.hilal_chat_agent import _instructions
from ai_market_monitor.services.hilal_chat_knowledge import Evidence
from ai_market_monitor.services.hilal_product_words import (
    PRODUCT_WORDS,
    product_words,
    word_id,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "ai_market_monitor" / "static"
TEMPLATES = ROOT / "src" / "ai_market_monitor" / "templates"

#: Everything the canvas draws or says, as one body of text. The words below have to be
#: findable in it — a definition of a word the interface does not use is a definition of
#: nothing.
CANVAS = "\n".join(
    path.read_text(encoding="utf-8")
    for path in (
        TEMPLATES / "hilal" / "dashboard_test" / "monitor.html",
        STATIC / "hm-monitor-plan.js",
        STATIC / "hm-monitor-test.js",
        STATIC / "hm-monitor-board.js",
    )
    if path.exists()
).lower()


def test_the_vocabulary_travels_on_every_turn() -> None:
    """Not only when the canvas is open.

    Somebody who is lost asks "what is a group" from wherever they happen to be, and an
    answer that depended on which page they were on would go missing at exactly the
    moment it is needed.
    """

    evidence = Evidence(words=product_words())
    payload = evidence.to_payload()

    assert payload["words_this_product_uses"], "the vocabulary is not in the evidence"
    assert len(payload["words_this_product_uses"]) == len(PRODUCT_WORDS)


def test_every_word_can_be_cited_like_any_other_record() -> None:
    """An answer names the rows it rests on. A row that cannot be named cannot be used."""

    evidence = Evidence(words=product_words())
    for word in PRODUCT_WORDS:
        assert word_id(word) in evidence.ids, word


@pytest.mark.parametrize("word", sorted(PRODUCT_WORDS), ids=lambda item: item)
def test_every_word_is_explained_in_plain_language(word: str) -> None:
    """Written for a beginner who may not be a native English speaker.

    A definition that needs a second technical word to explain the first is not a
    definition, and this audience is named in `CLAUDE.md` rather than assumed.
    """

    meaning = PRODUCT_WORDS[word]
    assert meaning.endswith("."), word
    assert len(meaning) <= 400, word
    # No machinery, no jargon, no Latin.
    for forbidden in ("i.e.", "e.g.", "boolean", "operator", "parameter", "json", "API"):
        assert forbidden.lower() not in meaning.lower(), (word, forbidden)


#: The words that are printed on the canvas itself, and the spelling the canvas uses.
#:
#: These are the ones a person can point at and ask about, so these are the ones whose
#: spelling has to match. The rest of the vocabulary — "alert", "watchlist", "approve" —
#: is product-wide and is checked for plain language above, not against this one page.
WORDS_THE_CANVAS_PRINTS = (
    "all of these",
    "any of these",
    "none of these",
    "set aside",
    "condition",
    "group",
)


@pytest.mark.parametrize("word", WORDS_THE_CANVAS_PRINTS, ids=lambda item: item)
def test_every_product_word_matches_the_interface(word: str) -> None:
    """The word Hilal explains is the word on the screen, spelled the same way.

    Explaining "the AND box" to somebody looking at a card that says "all of these" is
    the same failure as naming a button that is not there: it sends a person looking for
    something they cannot find, and they conclude the product is broken rather than that
    the answer was.
    """

    assert word in PRODUCT_WORDS, f"{word!r} is on the canvas and has no explanation"
    assert word in CANVAS, f"{word!r} is explained and the canvas does not print it"


def test_no_definition_gives_a_ruling_a_number_or_advice() -> None:
    """The vocabulary is evidence, and evidence obeys the same lines the answers do.

    A definition is the easiest place for a rule to leak: it is written once, read every
    turn, and looks like documentation rather than like something Hilal says.
    """

    for word, meaning in PRODUCT_WORDS.items():
        lowered = meaning.lower()
        # No ruling. Status comes from the review process and is repeated, never given.
        for ruling in ("halal", "haram", "permissible", "forbidden"):
            assert ruling not in lowered, (word, ruling)
        # No advice, and no promise.
        for advice in ("you should buy", "good time", "profit", "guarantee", "risk-free"):
            assert advice not in lowered, (word, advice)
        # No number to put in a field. Every number is the trader's own.
        assert not re.search(r"\b\d+(\.\d+)?\s?%", meaning), word


def test_the_shariah_entry_says_where_a_status_comes_from_and_nothing_more() -> None:
    """The one entry that could quietly become a religious explanation.

    It may say that a review recorded a result, under a named standard, on a named date.
    It may not say what any status means, and it must say plainly that the assistant does
    not decide.
    """

    meaning = PRODUCT_WORDS["shariah status"].lower()
    assert "review" in meaning
    assert "standard" in meaning
    assert "never decides anything itself" in meaning


def test_the_instructions_tell_hilal_to_calm_somebody_down_and_ask_one_question() -> None:
    """A lost person gets a kind line and **one** concrete question, in that order.

    Not a list of steps. Somebody who has just said they do not understand anything is
    the last person who can follow six numbered instructions, and answering them with a
    procedure is how an assistant makes being stuck feel worse.
    """

    text = _instructions().lower()
    assert "words_this_product_uses" in text
    assert "ask **one** question" in text
    assert "concrete" in text
    assert "never open with a list of steps" in text
    # And it still may not do the two things it must never do, whoever is asking.
    assert "you never give financial advice" in text

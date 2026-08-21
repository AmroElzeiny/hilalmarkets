"""The sentence the Builder writes back has to read like English.

When somebody picks a card, the Builder writes the rule out as a sentence and stores it
as the rule's provenance. That sentence is what a person reads before approving a
monitor, so it is product copy, not a debug string.

Two things were wrong with it, and between them they affected almost every card:

* ``"happens"`` and ``"does not happen"`` are whole verbs, and the template put a linking
  ``"is"`` in front of them. **262 of the 369 cards** read "Bollinger re-entry **is
  happens** on the 15m candle", because every yes/no card uses one of those two
  comparisons.
* Settings were printed with Python's own words: "with confirmation required **False**".

The product is built for beginners and for people who do not read English as a first
language. Neither of those is something they should have to decipher.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.builder_operations import (
    _build,
    _probe_values,
    mechanic_catalog,
)

OFFERED = [mechanic for mechanic in mechanic_catalog() if mechanic.available]


def _sentence(key: str) -> str:
    mechanic = next(item for item in OFFERED if item.key == key)
    _, sentence = _build(
        mechanic,
        _probe_values(mechanic),
        source_turn_id="sentence-audit",
        node_id="card_1",
        required=True,
    )
    return sentence


#: One build per card, shared by every case below.
_SENTENCES = {mechanic.key: _sentence(mechanic.key) for mechanic in OFFERED}

KEYS = sorted(_SENTENCES)


def test_there_are_sentences_to_check() -> None:
    """Guards the cases below against passing because nothing was built."""

    assert len(_SENTENCES) > 300


@pytest.mark.parametrize("key", KEYS)
def test_the_sentence_is_not_is_happens(key: str) -> None:
    """A verb does not need "is" in front of it."""

    sentence = _SENTENCES[key]
    for broken in (" is happens", " is does not happen"):
        assert broken not in sentence, f"{key} reads: {sentence}"


@pytest.mark.parametrize("key", KEYS)
def test_no_setting_is_shown_in_pythons_words(key: str) -> None:
    """A yes or no is written "yes" or "no", never ``True`` or ``False``."""

    sentence = _SENTENCES[key]
    mechanic = next(item for item in OFFERED if item.key == key)
    for parameter in mechanic.parameters:
        if parameter.kind != "boolean":
            continue
        words = parameter.name.replace("_", " ")
        for wrong in (f"{words} True", f"{words} False"):
            assert wrong not in sentence, f"{key} reads: {sentence}"


@pytest.mark.parametrize("key", KEYS)
def test_the_sentence_never_shows_an_internal_field_name(key: str) -> None:
    """No ``snake_case`` reaches the reader. The name is spelled out or not shown."""

    sentence = _SENTENCES[key]
    assert "_" not in sentence, f"{key} shows an internal name: {sentence}"


@pytest.mark.parametrize("key", KEYS)
def test_the_sentence_says_which_candle_size_it_watches(key: str) -> None:
    """Every rule runs on a timeframe, and the person approving it must be told which."""

    assert "on the " in _SENTENCES[key], f"{key} reads: {_SENTENCES[key]}"

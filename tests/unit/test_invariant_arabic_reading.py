"""Arabic and Arabizi instructions must compile, not fall through to a sentinel.

Run `20260726T171424Z` scored `msa_arabic` 0.11, `egyptian_arabic` 0.15 and `arabizi`
0.22, all producing the same artifact: a `clarification_required` blocked sentinel and
a blocking `no_supported_monitor_condition`.

The cause was not translation. Symbols, percentages and Latin timeframes were already
read correctly — the *direction of movement* was invisible, because the movement
vocabulary held only English words. With no direction the percentage formula refuses
to compile, so nothing at all was produced.

HilalMarkets is built for Muslim traders; Arabic and Arabizi are first-class input,
not a localisation afterthought. These cases assert the reading rule across script,
dialect and transliteration.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.engine.comparators import detect_comparator
from ai_market_monitor.engine.formula_compiler import parse_percentage_formula
from ai_market_monitor.engine.price_movement import (
    DOWN_TERMS,
    UP_TERMS,
    movement_direction,
)
from ai_market_monitor.engine.turn_fragments import extract_symbols, extract_timeframes
from ai_market_monitor.schemas.strategy import Comparator, StrategyDirection

GTE = Comparator.GREATER_THAN_OR_EQUAL
LTE = Comparator.LESS_THAN_OR_EQUAL


def _spec(text: str):
    return parse_percentage_formula(
        text, default_timeframe="15m", default_direction=StrategyDirection.LONG
    )


@pytest.mark.parametrize(
    ("text", "direction", "threshold"),
    [
        # Modern Standard Arabic
        ("ابحث عن عملات صعدت 5% اليوم", "up", 5.0),
        ("أريد مراقبة BTCUSDT عندما يرتفع السعر 2%", "up", 2.0),
        ("عملات انخفضت 3% اليوم", "down", 3.0),
        ("عملات هبطت 1.5%", "down", 1.5),
        # Egyptian Arabic
        ("عايز اعرف العملات اللي نزلت 3%", "down", 3.0),
        ("عايز العملات اللي طلعت 4%", "up", 4.0),
        # Arabizi / Franco-Arabic
        ("3ayez 3omlat tel3et 5% el naharda", "up", 5.0),
        ("3ayez BTCUSDT lama yenzel 2%", "down", 2.0),
        ("raqeb ETHUSDT lama yetla3 1.5%", "up", 1.5),
        ("3omlat nezlet 7.5%", "down", 7.5),
    ],
)
def test_arabic_and_arabizi_moves_compile(text: str, direction: str, threshold: float) -> None:
    spec = _spec(text)
    assert spec is not None, text
    assert spec.direction == direction, (text, spec.direction)
    assert spec.threshold_percent == pytest.approx(threshold)


@pytest.mark.parametrize(
    ("text", "timeframe"),
    [
        ("على فريم 15 دقيقة", "15m"),
        ("على فريم 5 دقائق", "5m"),
        ("على فريم 4 ساعات", "4h"),
        ("3ala 15 de2i2a", "15m"),
        ("3ala 4 sa3at", "4h"),
        ("3ala 15m", "15m"),
    ],
)
def test_arabic_and_arabizi_timeframes_are_read(text: str, timeframe: str) -> None:
    assert timeframe in extract_timeframes(text), (text, extract_timeframes(text))


@pytest.mark.parametrize(
    ("text", "comparator"),
    [
        ("على الأقل 5%", GTE),
        ("لا يقل عن 5%", GTE),
        ("بحد أدنى 5%", GTE),
        ("على الأكثر 5%", LTE),
        ("لا يزيد عن 5%", LTE),
        ("بحد أقصى 5%", LTE),
        ("أقل من 30", Comparator.LESS_THAN),
        ("أكبر من 70", Comparator.GREATER_THAN),
        ("3ala el a2al 5%", GTE),
        ("3ala el aktar 5%", LTE),
    ],
)
def test_arabic_and_arabizi_comparisons_are_read(text: str, comparator: Comparator) -> None:
    assert detect_comparator(text) is comparator, (text, detect_comparator(text))


def test_an_arabic_upper_bound_is_not_inverted() -> None:
    """The same never-invert rule as English: a stated ceiling stays a ceiling."""
    spec = _spec("عملات صعدت على الأكثر 5%")
    assert spec is not None
    assert spec.comparator is LTE


def test_arabic_today_anchors_to_the_daily_open() -> None:
    """`اليوم` is the same anchor as `today`; missing it silently changed the
    measurement reference to the previous candle."""
    spec = _spec("عملات صعدت 5% اليوم")
    assert spec is not None
    assert spec.formula == "reference_to_current"
    assert spec.reference_timeframe == "1d"
    assert spec.reference_field == "open"


def test_symbols_survive_an_arabic_sentence() -> None:
    assert extract_symbols("أريد مراقبة BTCUSDT على فريم 15m") == ("BTCUSDT",)


@pytest.mark.parametrize("term", [t for t in UP_TERMS if not t.isascii()])
def test_every_arabic_up_term_reads_as_up(term: str) -> None:
    assert movement_direction(f"السعر {term} اليوم") == "up"


@pytest.mark.parametrize("term", [t for t in DOWN_TERMS if not t.isascii()])
def test_every_arabic_down_term_reads_as_down(term: str) -> None:
    assert movement_direction(f"السعر {term} اليوم") == "down"


def test_an_arabic_term_is_not_matched_inside_a_longer_word() -> None:
    """`[a-z]` does not bound Arabic script, so the boundary had to be widened or
    `نزل` would match inside unrelated words that merely share those letters."""
    assert movement_direction("المنزل جميل") is None

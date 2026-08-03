import pytest

from ai_market_monitor.engine.text_normalization import repair_utf8_mojibake

#: Every Latin-1 punctuation mark that a UTF-8 C2 lead byte can introduce. Each one is
#: the same defect as the middle dot found in a template, so the repair is asserted for
#: the whole family rather than for the one that was reported.
LATIN1_PUNCTUATION = ("\xa0", "«", "»", "°", "·", "©", "®", "±", "¢", "£", "¥", "§")


@pytest.mark.parametrize("mark", LATIN1_PUNCTUATION)
def test_repairs_latin1_punctuation_captured_through_windows_1251(mark: str) -> None:
    original = f"Explore{mark}plan"
    damaged = original.encode("utf-8").decode("cp1251")
    assert damaged != original
    assert repair_utf8_mojibake(damaged) == original


@pytest.mark.parametrize("mark", LATIN1_PUNCTUATION)
def test_leaves_undamaged_latin1_punctuation_alone(mark: str) -> None:
    value = f"Explore{mark}plan"
    assert repair_utf8_mojibake(value) == value


def test_repairs_comparison_symbol_captured_through_windows_1251() -> None:
    assert repair_utf8_mojibake("move в‰¤ 5%") == "move ≤ 5%"


def test_repairs_arabic_captured_through_windows_1251() -> None:
    assert repair_utf8_mojibake("ШЄЩ…ШЄ Ш§Щ„Щ…Щ€Ш§ЩЃЩ‚Ш©") == "تمت الموافقة"


def test_leaves_valid_unicode_unchanged() -> None:
    value = "تمت الموافقة: move ≤ 5%"
    assert repair_utf8_mojibake(value) == value


@pytest.mark.parametrize(
    "value",
    (
        "он…",  # a Russian word then an ellipsis
        "это — правда",  # an em dash between Russian words
        "смотри «сюда»",  # Russian quotation marks
        "цена: 100 ₽",
    ),
)
def test_leaves_real_russian_alone(value: str) -> None:
    """The Cyrillic reading of a UTF-8 lead byte is also an ordinary Russian letter.

    Reading a pair as damage and "repairing" it would turn a real sentence into
    private-use characters, so a repair that produces them is refused.
    """

    assert repair_utf8_mojibake(value) == value


def test_repairs_chinese_captured_through_windows_1251() -> None:
    original = "已添加 ETH。"
    assert repair_utf8_mojibake(original.encode("utf-8").decode("cp1251")) == original

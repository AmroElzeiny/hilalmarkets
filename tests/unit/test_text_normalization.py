from ai_market_monitor.engine.text_normalization import repair_utf8_mojibake


def test_repairs_comparison_symbol_captured_through_windows_1251() -> None:
    assert repair_utf8_mojibake("move в‰¤ 5%") == "move ≤ 5%"


def test_repairs_arabic_captured_through_windows_1251() -> None:
    assert repair_utf8_mojibake("ШЄЩ…ШЄ Ш§Щ„Щ…Щ€Ш§ЩЃЩ‚Ш©") == "تمت الموافقة"


def test_leaves_valid_unicode_unchanged() -> None:
    value = "تمت الموافقة: move ≤ 5%"
    assert repair_utf8_mojibake(value) == value

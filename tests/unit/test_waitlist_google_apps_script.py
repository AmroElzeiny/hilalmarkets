from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "google_apps_script" / "waitlist_webhook.gs"


def test_waitlist_receiver_creates_missing_named_sheet() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "let sheet = spreadsheet.getSheetByName(sheetName);" in source
    assert "if (!sheet) sheet = spreadsheet.insertSheet(sheetName);" in source
    assert "sheet_unavailable" not in source


def test_waitlist_receiver_deduplicates_delivery_and_email() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "if (findDeliveryId_(sheet, deliveryId))" in source
    assert "if (findEmail_(sheet, email))" in source
    assert "function findEmail_(sheet, email)" in source
    assert ".getRange(2, 1, sheet.getLastRow() - 1, 1)" in source
    assert ".matchCase(false)" in source
    assert ".matchEntireCell(true)" in source

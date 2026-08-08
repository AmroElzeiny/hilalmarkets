import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "google_apps_script" / "waitlist_webhook.gs"


def _array_length(source: str, name: str) -> int:
    """How many entries a top-level `const NAME = [ ... ];` literal holds."""

    match = re.search(rf"const {name} = \[(.*?)\];", source, re.DOTALL)
    assert match, name
    return len([item for item in match.group(1).split(",") if item.strip()])


def _appended_row_length(source: str) -> int:
    """How many cells each new signup writes."""

    match = re.search(r"sheet\.appendRow\(\[(.*?)\]\);", source, re.DOTALL)
    assert match
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return len([item for item in body.split("\n") if item.strip().rstrip(",")])


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


def test_every_row_shape_agrees_with_the_headers() -> None:
    """Headers, new rows, migrated rows and the column numbers are one layout.

    Adding or removing a column touches five places. When they disagree the sheet does
    not fail: it quietly writes each value one cell to the side, so the status column
    fills with delivery ids and nobody notices until somebody reads the sheet.
    """

    source = SCRIPT.read_text(encoding="utf-8")
    columns = _array_length(source, "WAITLIST_HEADERS")

    assert _appended_row_length(source) == columns
    # The hidden delivery id is the last column, and everything before it is visible.
    assert f"const WAITLIST_DELIVERY_ID_COLUMN = {columns};" in source
    assert "const WAITLIST_VISIBLE_COLUMNS = WAITLIST_DELIVERY_ID_COLUMN - 1;" in source
    # Both migration paths build rows of exactly that width. The closing bracket is tied
    # to the opening indentation, or one block's match runs on into the next one's.
    blocks = re.findall(r"( *)return \[\n(.*?)\n\1\];", source, re.DOTALL)
    assert len(blocks) == 2
    for _indent, migrated in blocks:
        body = re.sub(r"//[^\n]*", "", migrated)
        cells = [item for item in body.split("\n") if item.strip().rstrip(",")]
        assert len(cells) == columns, migrated


def test_the_withdrawn_consent_column_is_read_but_never_written() -> None:
    """A sheet that already grew the consent column comes back to the current layout.

    The question was withdrawn because the box was offered already ticked. The old header
    is still recognised so an existing sheet can be repaired without losing rows, but no
    new row is ever given a consent value.
    """

    source = SCRIPT.read_text(encoding="utf-8")

    assert "WAITLIST_HEADERS_WITH_CONSENT" in source
    assert "matchesHeaderSet_(rows[0], WAITLIST_HEADERS_WITH_CONSENT)" in source
    assert "'Beta Testing Consent'" not in _array_literal(source, "WAITLIST_HEADERS")
    assert "consentLabel_" not in source
    assert "beta_contact_consent" not in source


def _array_literal(source: str, name: str) -> str:
    match = re.search(rf"const {name} = \[(.*?)\];", source, re.DOTALL)
    assert match, name
    return match.group(1)

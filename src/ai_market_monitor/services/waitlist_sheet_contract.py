"""The one place that decides what the Google Sheet receives for a waitlist signup.

The receiving Google Apps Script is deployed outside this repository and reads a fixed,
small set of field names. When the request body was built inline next to the HTTP call,
nothing in the codebase owned that agreement: the two sides drifted apart and every
signup was rejected as unauthorized, because the script looked for ``secret`` while the
server sent ``webhook_secret``.

So the body is built here and nowhere else. ``WAITLIST_SHEET_FIELDS`` is the whole
contract, and :func:`build_waitlist_sheet_payload` is the only way to produce it. A field
the script does not read is not "extra data" — it is a field the receiver ignores, so it
is not sent. What Hilal Markets keeps for itself (when the person signed up, which page
they came from, first-touch attribution) stays in this product's own database; the sheet
is a delivery target, not the record.

The secret belongs to this side of the wire only. It is placed into the body here, at the
moment of sending, and never stored, never logged, and never rendered into any page.
"""

from __future__ import annotations

from typing import Final

WAITLIST_SHEET_SOURCE: Final = "hilalmarkets_waitlist"
WAITLIST_SHEET_STATUS: Final = "waitlist"
WAITLIST_SHEET_UNKNOWN_COUNTRY: Final = "unknown"

#: Every field the deployed Apps Script reads, and no other. Order is documentation only.
WAITLIST_SHEET_FIELDS: Final[tuple[str, ...]] = (
    "secret",
    "email",
    "name",
    "source",
    "country",
    "status",
)

#: Field names an earlier version of this integration sent. The receiver never read them,
#: so sending them again would be noise on the wire, not information. Named here so a test
#: can assert the whole family is gone rather than the one name somebody remembered.
WAITLIST_SHEET_RETIRED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "webhook_secret",
        "event_id",
        "submitted_at",
        "source_page",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "referrer",
        "landing_page",
    }
)


def build_waitlist_sheet_payload(
    *,
    secret: str,
    email: str,
    country: str | None,
) -> dict[str, str]:
    """Build the exact request body the deployed Apps Script reads.

    ``name`` is always empty: the public form asks for an email address and nothing else,
    so there is no name to send. An invented or derived one would be a value nobody gave.

    A missing or blank country becomes ``"unknown"`` rather than an empty cell, so a
    reader of the sheet can tell "we do not know" from "nobody filled this in".
    """

    return {
        "secret": secret,
        "email": email,
        "name": "",
        "source": WAITLIST_SHEET_SOURCE,
        "country": (country or "").strip() or WAITLIST_SHEET_UNKNOWN_COUNTRY,
        "status": WAITLIST_SHEET_STATUS,
    }


def redact_waitlist_sheet_payload(payload: dict[str, str]) -> dict[str, str]:
    """A copy safe to put in an error message or a log line.

    Nothing in this service logs the request body today. This exists so that the first
    person who wants to log it has a correct way to do it already sitting next to the
    builder, instead of reaching for the raw dictionary and shipping the secret.
    """

    return {
        key: ("***" if key == "secret" else value) for key, value in payload.items()
    }

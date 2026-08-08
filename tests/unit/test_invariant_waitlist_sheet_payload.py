"""The waitlist Sheet request body has one owner, and it is the only thing on the wire.

The receiving Google Apps Script lives outside this repository. It reads six field names
and ignores everything else. When the body was built inline beside the HTTP call, nothing
owned that agreement: the server sent ``webhook_secret`` while the script looked for
``secret``, so every single signup came back as "unauthorized" and no waitlist row ever
reached the sheet.

Naming the six fields in a test that only checked one example would let the same drift
happen again to a different field. So every rule below is asserted across its whole
family: every field, every retired field, and every shape a country value arrives in.
"""

import inspect
import re

import pytest

from ai_market_monitor.services.public_forms import PublicFormsService
from ai_market_monitor.services.security_review import SecurityReviewService
from ai_market_monitor.services.waitlist_sheet_contract import (
    WAITLIST_SHEET_FIELDS,
    WAITLIST_SHEET_RETIRED_FIELDS,
    WAITLIST_SHEET_SOURCE,
    WAITLIST_SHEET_STATUS,
    WAITLIST_SHEET_UNKNOWN_COUNTRY,
    build_waitlist_sheet_payload,
    redact_waitlist_sheet_payload,
)


def _payload(**overrides) -> dict[str, str]:
    arguments = {
        "secret": "sheet-secret",
        "email": "visitor@example.com",
        "country": "EG",
    }
    arguments.update(overrides)
    return build_waitlist_sheet_payload(**arguments)


def test_the_body_carries_exactly_the_fields_the_script_reads():
    """Not "contains" — equals. An extra field is a field the receiver throws away."""

    assert _payload() == {
        "secret": "sheet-secret",
        "email": "visitor@example.com",
        "name": "",
        "source": "hilalmarkets_waitlist",
        "country": "EG",
        "status": "waitlist",
    }
    assert tuple(_payload()) == WAITLIST_SHEET_FIELDS


@pytest.mark.parametrize("field", sorted(WAITLIST_SHEET_RETIRED_FIELDS))
def test_no_retired_field_is_ever_sent_again(field):
    """Every name the old body used, checked one by one.

    ``webhook_secret`` was the one that broke delivery. Asserting only that name would
    leave the other ten free to come back the next time somebody adds "just one more
    useful value" to the request.
    """

    assert field not in _payload()


@pytest.mark.parametrize(
    "country",
    [None, "", "   ", "\t", "\n"],
)
def test_every_empty_shape_of_country_becomes_the_same_written_word(country):
    """A blank cell and "we do not know" must not look the same to a person reading it.

    A country arrives missing in more than one shape: absent, empty, or whitespace that a
    header put there. Each one is the same fact, so each one is written the same way.
    """

    assert _payload(country=country)["country"] == WAITLIST_SHEET_UNKNOWN_COUNTRY


@pytest.mark.parametrize("country", ["EG", "gb", "US", "SA"])
def test_a_country_that_is_known_is_passed_through_unchanged(country):
    assert _payload(country=country)["country"] == country


@pytest.mark.parametrize(
    "email",
    ["visitor@example.com", "a.b+tag@example.co.uk", "someone@sub.domain.example"],
)
def test_the_email_sent_is_the_email_stored_and_nothing_is_derived_from_it(email):
    """The form asks for an email and nothing else, so ``name`` stays empty.

    Splitting the address to invent a first name would put a value in the sheet that the
    person never gave. An empty cell is honest; a guessed name is not.
    """

    payload = _payload(email=email)
    assert payload["email"] == email
    assert payload["name"] == ""


def test_the_source_and_status_are_fixed_words_and_not_derived_from_the_visit():
    """Both are constants. They label the list, not the visit that produced the row."""

    assert _payload(country=None)["source"] == WAITLIST_SHEET_SOURCE
    assert _payload(country="EG")["source"] == WAITLIST_SHEET_SOURCE
    assert _payload(email="other@example.com")["status"] == WAITLIST_SHEET_STATUS
    assert WAITLIST_SHEET_SOURCE == "hilalmarkets_waitlist"
    assert WAITLIST_SHEET_STATUS == "waitlist"


def test_the_sending_code_builds_the_body_only_through_the_owner():
    """No second copy of the field names inside the code that sends the request.

    The whole failure was two places deciding what a field is called. One builder means
    there is only one place that can be wrong, and it is the place the tests above pin.
    Only the sending method is read here: the rest of the service legitimately uses words
    like ``email`` and ``source_page`` for this product's own database record.
    """

    source = inspect.getsource(PublicFormsService.process_waitlist_due)
    assert "build_waitlist_sheet_payload(" in source
    for retired in WAITLIST_SHEET_RETIRED_FIELDS:
        assert f'"{retired}"' not in source, retired
    # No hand-written dictionary of the live field names either.
    for field in WAITLIST_SHEET_FIELDS:
        assert not re.search(rf'"{field}"\s*:', source), field


def test_the_secret_never_reaches_a_log_line_or_an_error_message():
    """A body that is safe to print keeps every field except the one that is a key."""

    redacted = redact_waitlist_sheet_payload(_payload())
    assert redacted["secret"] == "***"
    assert "sheet-secret" not in str(redacted)
    assert {key: value for key, value in redacted.items() if key != "secret"} == {
        key: value for key, value in _payload().items() if key != "secret"
    }


@pytest.mark.parametrize(
    "name",
    ["secret", "webhook_secret", "waitlist_google_sheets_webhook_secret"],
)
def test_the_shared_redactor_also_hides_the_new_field_name(name):
    """The application-wide redactor knows ``secret`` as well as ``webhook_secret``.

    Renaming the field would have quietly moved it outside the redactor's list if that
    list only knew the old name.
    """

    redacted = SecurityReviewService().redact({name: "sheet-secret"})
    assert redacted == {name: "[redacted]"}

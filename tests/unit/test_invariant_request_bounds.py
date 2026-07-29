"""INV-13: a long conversation must not become an internal error.

Turns a4, a5 and a6 of `revert_correction-001` returned HTTP 500 with "Nothing was
created, changed, or activated". The cause was not the trader's wording: the derived
compiler request carries the accumulated setup text, the schema caps that field at
5000 characters, and once the conversation grew past the cap every subsequent turn
raised a validation error the chat could not recover from.

The canonical strategy state — not the transcript — is the authority for settled
fields, so bounding the raw text loses no decision.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.ai_setup_chat import (
    _bounded_setup_text,
    _guided_setup,
    _setup_text_limit,
)


def test_the_limit_is_read_from_the_schema_not_hard_coded() -> None:
    """A hard-coded copy would drift from the schema silently, and the drift only
    shows up as a 500 in a long conversation."""
    for constraint in GuidedSetupRequest.model_fields["setup_text"].metadata:
        limit = getattr(constraint, "max_length", None)
        if isinstance(limit, int):
            assert _setup_text_limit() == limit
            return
    pytest.fail("setup_text no longer declares a max_length")


@pytest.mark.parametrize("turns", [1, 8, 40, 400])
def test_any_conversation_length_builds_a_valid_request(turns: int) -> None:
    line = "watch SOLUSDT on the 15m when the bearish move is at least 1.0%"
    text = "\n".join(f"{line} (turn {index})" for index in range(turns))
    request = _guided_setup(text)
    assert request.setup_text is not None
    assert len(request.setup_text) <= _setup_text_limit()


def test_bounding_keeps_the_most_recent_wording() -> None:
    """The operative instruction is the latest one, so the newest lines survive."""
    lines = [f"line {index} " + "x" * 200 for index in range(60)]
    bounded = _bounded_setup_text("\n".join(lines))
    assert lines[-1] in bounded
    assert lines[0] not in bounded


def test_bounding_never_cuts_a_line_in_half() -> None:
    lines = [f"requirement {index}: RSI below {index}" for index in range(500)]
    bounded = _bounded_setup_text("\n".join(lines))
    for kept in bounded.split("\n"):
        assert kept in lines


def test_short_text_is_returned_unchanged() -> None:
    text = "watch BTCUSDT on the 1h when RSI is at most 30"
    assert _bounded_setup_text(text) == text


def test_a_single_oversized_line_keeps_its_end() -> None:
    """One paragraph longer than the whole budget still has to produce a valid
    request; the operative instruction sits at the end of it."""
    text = "x" * 12_000 + " RSI at most 30"
    bounded = _bounded_setup_text(text)
    assert len(bounded) <= _setup_text_limit()
    assert bounded.endswith("RSI at most 30")

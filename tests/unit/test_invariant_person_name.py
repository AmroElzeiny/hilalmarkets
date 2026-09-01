"""One rule for the name this product greets somebody by.

Four modules worked this out separately and had already drifted: two truncation limits,
three different answers when there is no name, and three that would happily have greeted
somebody by their email address — two of them carrying comments promising they never
would. This file asserts the rule itself, and that all four readers go through it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from ai_market_monitor.core.person_name import (
    GREETING_NAME_LIMIT,
    greeting_name,
    is_usable_name,
)

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: Every module that answers "what do we call this person". Each one is a place a
#: greeting reaches a real reader: an email, a chat reply, a dashboard.
READERS = (
    "services/affiliate.py",
    "services/hilal_chat.py",
    "services/public_chat.py",
    "services/account_admin.py",
)


# ---------------------------------------------------------------------------
# The rule.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "trader@example.com",
        "123456@example.com",
        "a.b-c@sub.domain.co.uk",
        "  spaced@example.com  ",
    ],
)
def test_an_email_address_is_never_a_name(address: str) -> None:
    """The whole address is refused, and so is the part in front of the @.

    Signing up with an email used to fill the account's name in from the local part of
    the address, so every greeting in the product handed out a fragment of somebody's
    address. Chopping at the @ is not the fix — the local part *is* the address in all
    but punctuation, which is why an affiliate must never be shown it in place of a
    customer's name.
    """

    assert is_usable_name(address) is False
    assert greeting_name(address) == ""
    # And it never falls through to the piece before the @ either.
    assert greeting_name(address, fallback="there") == "there"


@pytest.mark.parametrize("empty", ["", "   ", None, "\t\n"])
def test_nothing_is_nothing(empty: str | None) -> None:
    assert is_usable_name(empty) is False
    assert greeting_name(empty) == ""
    assert greeting_name(empty, fallback="there") == "there"


@pytest.mark.parametrize(
    "value",
    ["123456", "!!!", "42", "---", "#1"],
)
def test_a_name_needs_a_letter_in_it(value: str) -> None:
    """`123456@example.com` produced exactly this, and it is not a person's name."""

    assert is_usable_name(value) is False
    assert greeting_name(value) == ""


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Amina Yusuf", "Amina"),
        ("Amina", "Amina"),
        ("  Amina   Yusuf  ", "Amina"),
        ("Abd al-Rahman ibn Awf", "Abd"),
        ("Zhang Wei", "Zhang"),
        ("Ольга Иванова", "Ольга"),
        ("محمد علي", "محمد"),
    ],
)
def test_a_greeting_uses_the_first_word(given: str, expected: str) -> None:
    """"Assalamu Alaikum Amina," not "Assalamu Alaikum Amina Yusuf,"."""

    assert greeting_name(given) == expected


def test_a_very_long_name_is_cut_to_one_length_everywhere() -> None:
    """One limit, not the 40 of one reader and the 80 of another."""

    long_name = "A" * 200
    assert greeting_name(long_name) == "A" * GREETING_NAME_LIMIT


def test_the_first_usable_candidate_wins() -> None:
    """The name typed for *this* purpose beats the one on the account.

    An affiliate application carries a name somebody chose for the programme, and that is
    the name the three affiliate emails greet them by.
    """

    assert greeting_name("Amina Yusuf", "Karim Hassan") == "Amina"
    # An unusable first candidate is skipped rather than ending the search — otherwise an
    # account with no name would silently suppress the one the person just typed.
    assert greeting_name("", "Karim Hassan") == "Karim"
    assert greeting_name(None, "Karim Hassan") == "Karim"
    assert greeting_name("trader@example.com", "Karim Hassan") == "Karim"


def test_the_fallback_is_a_word_and_never_a_guess() -> None:
    assert greeting_name(None, fallback="there") == "there"
    assert greeting_name("Amina", fallback="there") == "Amina"


# ---------------------------------------------------------------------------
# Everybody reads it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relative", READERS)
def test_every_greeting_reader_imports_the_one_rule(relative: str) -> None:
    """A reader that stops importing this has started keeping its own copy again."""

    text = (SOURCE / relative).read_text(encoding="utf-8")
    assert "core.person_name import" in text, f"{relative} no longer reads the one rule"


@pytest.mark.parametrize("relative", READERS)
def test_no_reader_splits_a_display_name_by_hand(relative: str) -> None:
    """The shape all four copies had: `display_name.split()[0]`, each with its own cap.

    Matched on the parsed tree rather than on the text, so a comment quoting the old code
    — this file's own docstring does — cannot fail it.
    """

    tree = ast.parse((SOURCE / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # `<anything>.display_name.split()` — the call that used to start every copy.
        if not isinstance(node, ast.Call):
            continue
        method = node.func
        if not isinstance(method, ast.Attribute) or method.attr != "split":
            continue
        target = method.value
        if isinstance(target, ast.Attribute) and target.attr == "display_name":
            raise AssertionError(
                f"{relative} splits display_name by hand again; use greeting_name()"
            )

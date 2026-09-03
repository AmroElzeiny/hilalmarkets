"""One account per email address, and every door says which door it is.

Two questions this answers as rules rather than as cases:

1. **Signing up with Google and then with a password does not make a second account.**
   It cannot: `user_identities` is unique on (provider, normalized_identifier). What the
   product owes the person is a sentence that tells them what actually happened.

2. **A Google password cannot be "synced".** Google never gives it to us and never will.
   What can be shared is the account — and it already is. So the rule worth asserting is
   that every place which refuses a password on a Google account says so, instead of
   answering "email or password is incorrect" about a password that does not exist.

The second one was a real dead end: the same person, pressing the same button, was told
the same untrue thing for ever, with no sentence anywhere naming the button that works.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.core.auth_pages import _ERRORS, alert_for
from ai_market_monitor.db.models import UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider
from ai_market_monitor.services.web_auth import (
    SIGN_IN_DOOR_GOOGLE,
    SIGN_IN_DOOR_PASSWORD,
    WebAuthService,
    sign_in_door,
    uses_google_only,
)

WEB_AUTH_SOURCE = Path("src/ai_market_monitor/services/web_auth.py")

#: Every link an error's button can point at. Kept beside the pages that build it so a
#: new answer cannot name an address nobody serves.
LINKS = {
    "signin": "/signin",
    "signup": "/signup",
    "signin_code": "/signin/code",
    "reset": "/reset-password",
    "support": "mailto:office@hilalmarkets.com",
}


def _identity(password_hash: str | None) -> UserIdentity:
    return UserIdentity(
        provider=IdentityProvider.EMAIL,
        provider_subject="someone@example.com",
        normalized_identifier="someone@example.com",
        display_identifier="someone@example.com",
        password_hash=password_hash,
        profile_data={},
    )


# ---------------------------------------------------------------------------
# Which door.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password_hash", "expected"),
    [
        (None, SIGN_IN_DOOR_GOOGLE),
        ("", SIGN_IN_DOOR_GOOGLE),
        ("pbkdf2_sha256$200000$abc$def", SIGN_IN_DOOR_PASSWORD),
    ],
)
def test_a_stored_password_is_the_whole_of_the_test(
    password_hash: str | None, expected: str
) -> None:
    identity = _identity(password_hash)
    assert sign_in_door(identity) == expected
    assert uses_google_only(identity) is (expected == SIGN_IN_DOOR_GOOGLE)


# ---------------------------------------------------------------------------
# What each refusal says.
# ---------------------------------------------------------------------------


def test_signing_up_again_names_the_door_the_account_really_has() -> None:
    """Telling a Google customer to "sign in with your password instead" sends them to a
    password that does not exist. They can only go round again."""

    google = WebAuthService._already_exists(_identity(None))
    password = WebAuthService._already_exists(_identity("pbkdf2_sha256$1$a$b"))
    assert google.code == "account_exists_google"
    assert password.code == "account_exists"
    assert google.code != password.code


def test_a_password_on_a_google_account_is_not_called_incorrect() -> None:
    """It is neither correct nor incorrect. There is nothing to check it against."""

    with pytest.raises(Exception) as refusal:
        WebAuthService._check_password(_identity(None), "anything at all")
    assert refusal.value.code == "google_sign_in_required"  # type: ignore[attr-defined]


@pytest.mark.parametrize("typed", ["", "wrong-password", "CorrectHorse123!"])
def test_no_password_at_all_opens_a_google_account(typed: str) -> None:
    """Whatever is typed, including nothing, the answer names Google rather than blaming
    the person's typing."""

    with pytest.raises(Exception) as refusal:
        WebAuthService._check_password(_identity(None), typed)
    assert refusal.value.code == "google_sign_in_required"  # type: ignore[attr-defined]


def test_a_wrong_password_on_a_password_account_still_says_so() -> None:
    """The new answer must not swallow the old one: a real wrong password is still a
    wrong password, and must not be told to press a Google button."""

    with pytest.raises(Exception) as refusal:
        WebAuthService._check_password(
            _identity("pbkdf2_sha256$200000$c2FsdA==$ZGlnZXN0"), "wrong"
        )
    assert refusal.value.code == "invalid_login"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Every refusal has words written for it.
# ---------------------------------------------------------------------------


def test_every_refusal_the_sign_in_pages_can_raise_is_answered_in_plain_words() -> None:
    """A code with no sentence written for it falls through to "Something went wrong",
    which tells a person nothing about which box to change.

    That is not hypothetical: `invalid_name` could always be raised, had no answer, and
    left a whole class of sign-ups at a dead end. The class is checked, not the member —
    every code this module can raise must have words, including ones added later.
    """

    source = WEB_AUTH_SOURCE.read_text(encoding="utf-8")
    raised = set(re.findall(r'WebAuthError\(\s*"([a-z_0-9]+)"', source))
    assert raised, "no error codes found — the pattern stopped matching"
    # Email failures are answered as a class by prefix, because a customer can do nothing
    # about any of them and every new one upstream would leak the same way.
    unanswered = {
        code
        for code in raised
        if code not in _ERRORS and not code.startswith(("smtp_", "email_"))
    }
    assert not unanswered, f"these refusals have no words written for them: {sorted(unanswered)}"


@pytest.mark.parametrize("code", ["account_exists_google", "google_sign_in_required"])
def test_the_new_answers_offer_a_button_that_goes_somewhere(code: str) -> None:
    """An answer with a dead button is an answer with no way forward."""

    alert = alert_for(page="signin", message=None, error=code, ttl_minutes=15, links=LINKS)
    assert alert is not None
    assert alert.tone == "error"
    assert alert.action_label, f"{code} offers no next step"
    assert alert.action_href.startswith(("/", "mailto:")), alert.action_href
    # Written for a beginner: no error code, no field name, no jargon in the sentence.
    assert code not in alert.body
    assert "identity" not in alert.body.lower()


def test_the_two_answers_are_not_the_same_sentence() -> None:
    """They describe two different situations. One sentence for both is how the wrong
    instruction reached the wrong person in the first place."""

    exists = alert_for(
        page="signup", message=None, error="account_exists", ttl_minutes=15, links=LINKS
    )
    exists_google = alert_for(
        page="signup",
        message=None,
        error="account_exists_google",
        ttl_minutes=15,
        links=LINKS,
    )
    assert exists is not None and exists_google is not None
    assert exists.body != exists_google.body
    assert "Google" in exists_google.body
    assert "Google" not in exists.body

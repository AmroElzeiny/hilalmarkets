"""Who Cloudflare Access may hand to System Brain, for every identity and every allowlist.

The gate used to understand one kind of caller: a person, named by the verified email
Cloudflare puts in ``cf-access-authenticated-user-email``. A service token is a machine, so
Access issues it no email at all -- it is named by ``cf-access-client-id``. The old check
therefore refused every service token no matter how Cloudflare was configured, and the
refusal looked identical to a wrong password, which is why it was worth naming here.

These tests assert the rule rather than the two cases that prompted it::

    admitted  <=>  the flag is off
                   OR (an Access assertion is present
                       AND the caller is named on one of the two allowlists)

Every combination of assertion, email, client ID and allowlist contents is generated, and
the expected answer is computed from that rule -- so a fix that only helps a service token,
or that quietly lets an unnamed caller in, fails here.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from ai_market_monitor.api.routers.system_brain import (
    _matches_any,
    _require_cloudflare_access,
)
from ai_market_monitor.core.config import Settings

ASSERTION = "a-signed-access-jwt"
KNOWN_EMAIL = "owner@hilalmarkets.com"
KNOWN_TOKEN = "5f4dcc3b5aa765d61d8327deb882cf99.access"


class _Request:
    """Only the header bag is read, and Access header names are case-insensitive."""

    def __init__(self, assertion: str, email: str, client_id: str) -> None:
        self.headers = Headers(
            {
                "cf-access-jwt-assertion": assertion,
                "cf-access-authenticated-user-email": email,
                "cf-access-client-id": client_id,
            }
        )


class _Settings:
    def __init__(
        self,
        *,
        required: bool = True,
        emails: frozenset[str] = frozenset(),
        tokens: frozenset[str] = frozenset(),
    ) -> None:
        self.system_brain_cloudflare_access_required = required
        self.system_brain_authorized_emails = emails
        self.system_brain_authorized_service_token_ids = tokens


async def _admits(request: _Request, settings: _Settings) -> bool:
    try:
        await _require_cloudflare_access(request, settings)  # type: ignore[arg-type]
    except HTTPException as refusal:
        assert refusal.status_code == 403
        return False
    return True


@pytest.mark.parametrize("assertion", ["", ASSERTION])
@pytest.mark.parametrize("email", ["", KNOWN_EMAIL, "stranger@example.com"])
@pytest.mark.parametrize("client_id", ["", KNOWN_TOKEN, "0000.access"])
@pytest.mark.parametrize("allowed_emails", [frozenset(), frozenset({KNOWN_EMAIL})])
@pytest.mark.parametrize("allowed_tokens", [frozenset(), frozenset({KNOWN_TOKEN})])
async def test_gate_admits_exactly_the_callers_it_was_told_about(
    assertion: str,
    email: str,
    client_id: str,
    allowed_emails: frozenset[str],
    allowed_tokens: frozenset[str],
) -> None:
    expected = bool(assertion) and (email in allowed_emails or client_id in allowed_tokens)
    admitted = await _admits(
        _Request(assertion, email, client_id),
        _Settings(emails=allowed_emails, tokens=allowed_tokens),
    )
    assert admitted is expected


@pytest.mark.parametrize("email", ["", KNOWN_EMAIL, "stranger@example.com"])
@pytest.mark.parametrize("client_id", ["", KNOWN_TOKEN, "0000.access"])
async def test_an_unconfigured_deployment_admits_nobody(email: str, client_id: str) -> None:
    """Fail closed. Naming no operator must refuse everyone, never admit everyone."""

    admitted = await _admits(_Request(ASSERTION, email, client_id), _Settings())
    assert admitted is False


@pytest.mark.parametrize("email", ["", "stranger@example.com"])
@pytest.mark.parametrize("client_id", ["", "0000.access"])
async def test_the_flag_off_is_the_only_way_past_without_an_identity(
    email: str, client_id: str
) -> None:
    assert await _admits(_Request("", email, client_id), _Settings(required=False)) is True
    assert await _admits(_Request("", email, client_id), _Settings(required=True)) is False


@pytest.mark.parametrize(
    "hostile",
    # Every value here is latin-1, because that is the only alphabet an HTTP header can
    # carry -- Starlette decodes headers as latin-1, so this is the whole reachable range.
    # The dangerous part of it is the accented middle: past ASCII, still a legal header.
    ["héllo@example.com", "ÿ@example.com", "a" * 5000, "\x00", "ünïcödé.access"],
)
async def test_a_hostile_header_is_refused_and_never_crashes(hostile: str) -> None:
    """A diagnostic must not become the failure.

    ``hmac.compare_digest`` raises ``TypeError`` on a non-ASCII ``str``. Reached through
    the router that is an HTTP 500 on an *unauthorised* request -- a refusal turning into
    a crash. Every one of these must be a plain, quiet 403.
    """

    settings = _Settings(emails=frozenset({KNOWN_EMAIL}), tokens=frozenset({KNOWN_TOKEN}))
    assert await _admits(_Request(ASSERTION, hostile, ""), settings) is False
    assert await _admits(_Request(ASSERTION, "", hostile), settings) is False


@pytest.mark.parametrize("value", ["", "héllo", "a" * 5000])
def test_matching_never_raises_whatever_the_header_holds(value: str) -> None:
    assert _matches_any(value, frozenset({KNOWN_EMAIL})) is False
    assert _matches_any(value, frozenset()) is False


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("", frozenset()),
        ("   ", frozenset()),
        (KNOWN_TOKEN, frozenset({KNOWN_TOKEN})),
        (f"{KNOWN_TOKEN},0000.access", frozenset({KNOWN_TOKEN, "0000.access"})),
        (f"{KNOWN_TOKEN}; 0000.access", frozenset({KNOWN_TOKEN, "0000.access"})),
        (f"{KNOWN_TOKEN}\n0000.access", frozenset({KNOWN_TOKEN, "0000.access"})),
        (f"  {KNOWN_TOKEN.upper()}  ", frozenset({KNOWN_TOKEN})),
    ],
)
def test_service_token_ids_are_split_like_every_other_operator_list(
    configured: str, expected: frozenset[str]
) -> None:
    """Same separators and same case folding as the email allowlist beside it."""

    settings = Settings.model_construct(system_brain_access_service_token_ids=configured)
    assert settings.system_brain_authorized_service_token_ids == expected

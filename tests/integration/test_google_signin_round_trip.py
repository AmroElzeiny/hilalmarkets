"""The Google door, driven through the real routes on every name the product answers on.

One deployment answers on two hostnames — the marketing name and the application name —
and Caddy sends both to this same application. Both of them serve `/signin` and
`/signup`, so both of them can start a Google sign-in.

The rule these tests hold the routes to is one sentence: **the trip must come back to the
name it started on.** When it does not, two things break at once, and neither of them
looks like an error to the person pressing the button:

* the popup hands the page underneath a `postMessage` aimed at its own origin, so a
  popup that finished on the other name delivers nothing. The window shuts and the page
  sits there, exactly as if the button were dead;
* the session cookie belongs to whichever host answered the callback, so the person is
  signed in on a name they are not looking at.

These are checked for every configured host rather than for the one that was noticed
first, and the failure test is written so that the old behaviour cannot pass.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio

from tests.conftest import _build_context

PUBLIC_HOST = "hilalmarkets.com"
APP_HOST = "app.hilalmarkets.com"

#: Both names, because a rule proved on one of them is not a rule.
EVERY_HOST = [PUBLIC_HOST, APP_HOST]


@pytest_asyncio.fixture
async def two_host_context():
    """The product as deployed: a marketing name and an application name, one app."""

    async for context in _build_context(
        public_base_url=f"https://{PUBLIC_HOST}",
        app_base_url=f"https://{APP_HOST}",
        google_oauth_client_id="test-client.apps.googleusercontent.com",
        google_oauth_client_secret="test-secret",
    ):
        yield context


def _redirect_uri_sent_to_google(location: str) -> str:
    query = parse_qs(urlsplit(location).query)
    return query["redirect_uri"][0]


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("host", EVERY_HOST)
@pytest.mark.parametrize("mode", ["signin", "signup"])
async def test_the_trip_returns_to_the_name_it_started_on(two_host_context, host, mode):
    """Press Google on either name, in either mode, and Google is told to come back here.

    This is the test the old code fails: it always named the application host, so a
    person who started on the marketing name got a popup that could never talk to the
    page that opened it.
    """

    response = await two_host_context["client"].get(
        f"/auth/google/start?mode={mode}&popup=1",
        headers={"host": host},
        follow_redirects=False,
    )

    assert response.status_code in {302, 303, 307}
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/")

    returned_to = _redirect_uri_sent_to_google(location)
    assert _host_of(returned_to) == host, (
        f"a trip started on {host} would have come back to {_host_of(returned_to)}, "
        "so the popup could never reach the page that opened it"
    )
    assert returned_to.endswith("/auth/google/callback")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", EVERY_HOST)
async def test_the_address_google_is_told_is_one_the_client_knows(two_host_context, host):
    """Google matches the return address character for character against its own list.

    So whatever the route sends has to be one of the strings `Settings` publishes — the
    list a person pastes into the Google console. A route that invented a third string
    would fail at Google with a message no customer could act on.
    """

    settings = two_host_context["settings"]
    response = await two_host_context["client"].get(
        "/auth/google/start?mode=signin",
        headers={"host": host},
        follow_redirects=False,
    )

    returned_to = _redirect_uri_sent_to_google(response.headers["location"])
    assert returned_to in settings.google_oauth_redirect_uris


@pytest.mark.asyncio
async def test_a_forged_host_cannot_invent_a_new_return_address(two_host_context):
    """The hostname only ever *chooses* from a closed set built out of settings.

    A `Host` header is supplied by whoever is calling. Letting it contribute the string
    itself would let somebody point the return address at their own site; letting it pick
    from two addresses we registered ourselves can do nothing worse than pick the other
    one of our own names.
    """

    settings = two_host_context["settings"]
    for forged in ("evil.example.com", "hilalmarkets.com.evil.example.com", "localhost"):
        response = await two_host_context["client"].get(
            "/auth/google/start?mode=signin",
            headers={"host": forged},
            follow_redirects=False,
        )
        returned_to = _redirect_uri_sent_to_google(response.headers["location"])
        assert returned_to in settings.google_oauth_redirect_uris
        assert _host_of(returned_to) != forged


@pytest.mark.asyncio
@pytest.mark.parametrize("host", EVERY_HOST)
async def test_the_button_is_drawn_on_both_names(two_host_context, host):
    """A page that draws the button must be a page whose trip can finish.

    Both names serve the sign-in pages, so if the button is drawn on both then both have
    to work. This test exists so that the pair can never drift apart: if somebody later
    stops serving these pages on one name, this fails and says so.
    """

    for page in ("/signin", "/signup"):
        response = await two_host_context["client"].get(page, headers={"host": host})
        assert response.status_code == 200
        assert 'data-testid="auth-google"' in response.text, (
            f"{page} on {host} does not offer the Google button"
        )
        assert "/auth/google/start" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("host", EVERY_HOST)
async def test_the_callback_redeems_with_the_address_the_trip_started_with(
    two_host_context, host, monkeypatch
):
    """Google refuses the exchange when the two halves of the trip disagree.

    The address sent when the person was pushed to Google and the address sent when the
    code is redeemed have to be the same string. It is carried in the signed state rather
    than guessed a second time, and this proves the callback really uses it.
    """

    from ai_market_monitor.services import google_oauth

    started = await two_http_get(two_host_context, host)
    state = parse_qs(urlsplit(started).query)["state"][0]
    expected = _redirect_uri_sent_to_google(started)

    seen: dict[str, str] = {}

    async def fake_exchange(self, *, code: str, redirect_uri: str):
        seen["redirect_uri"] = redirect_uri
        raise google_oauth.GoogleOAuthError("google_unavailable", "stopped on purpose")

    monkeypatch.setattr(google_oauth.GoogleOAuthService, "exchange", fake_exchange)

    await two_host_context["client"].get(
        f"/auth/google/callback?code=abc&state={state}",
        headers={"host": host},
        follow_redirects=False,
    )

    assert seen["redirect_uri"] == expected


async def two_http_get(context, host: str) -> str:
    response = await context["client"].get(
        "/auth/google/start?mode=signin&popup=1",
        headers={"host": host},
        follow_redirects=False,
    )
    return response.headers["location"]


# ---------------------------------------------------------------------------
# The whole trip, all the way to a signed-in person.
# ---------------------------------------------------------------------------
#
# Everything above proves the trip leaves and comes back on the right name. These prove
# what happens when it succeeds: an account exists, a session cookie is set, and pressing
# the button a second time lands in the *same* account instead of making another one
# beside it.
#
# Google is replaced by a stub, so no network call is made — but only the transport is
# replaced. The real code still reads the identity token, checks who it was issued for,
# refuses an unconfirmed address, and builds the profile.


def _identity_token(*, audience: str, email: str, **claims) -> str:
    """An identity token shaped exactly like Google's, minus the signature.

    The application deliberately does not check the signature: the token arrives in the
    body of a server-to-server HTTPS response carrying our own client secret, and the
    transport is the proof. So a stub only has to get the *shape* right, and the real
    `_claims_from` still checks `aud` and `iss` against our own settings.
    """

    import base64
    import json

    body = {
        "sub": "google-subject-1",
        "iss": "https://accounts.google.com",
        "aud": audience,
        "email": email,
        "email_verified": True,
        "given_name": "Sara",
        "family_name": "Ahmed",
    }
    body.update(claims)
    raw = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return "header." + raw + ".signature"


class _StubGoogle:
    """Stands in for `oauth2.googleapis.com` for one token exchange."""

    def __init__(self, id_token: str, record: dict):
        self._id_token = id_token
        self._record = record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, **kwargs):
        self._record["url"] = url
        self._record["form"] = dict(data or {})
        return httpx.Response(
            200,
            json={"id_token": self._id_token, "access_token": "unused"},
            request=httpx.Request("POST", url),
        )


def _stub_google(monkeypatch, settings, email: str) -> dict:
    from ai_market_monitor.services import google_oauth

    record: dict = {}
    token = _identity_token(
        audience=(settings.google_oauth_client_id or "").strip(), email=email
    )
    monkeypatch.setattr(
        google_oauth.httpx,
        "AsyncClient",
        lambda *a, **k: _StubGoogle(token, record),
    )
    return record


async def _count_users(context) -> int:
    from sqlalchemy import func, select

    from ai_market_monitor.db.models import User

    async with context["session_factory"]() as session:
        return int(await session.scalar(select(func.count()).select_from(User)) or 0)


async def _finish_trip(context, host, monkeypatch, email, code="a-real-looking-code"):
    """Press the button on `host` and come all the way back."""

    record = _stub_google(monkeypatch, context["settings"], email)
    started = await two_http_get(context, host)
    state = parse_qs(urlsplit(started).query)["state"][0]
    response = await context["client"].get(
        "/auth/google/callback?code=" + code + "&state=" + state,
        headers={"host": host},
        follow_redirects=False,
    )
    return response, record, started


@pytest.mark.asyncio
@pytest.mark.parametrize("host", EVERY_HOST)
async def test_signing_up_with_google_creates_an_account_and_signs_the_person_in(
    two_host_context, host, monkeypatch
):
    """The happy path, on every name the product answers on."""

    context = two_host_context
    assert await _count_users(context) == 0

    response, record, started = await _finish_trip(
        context, host, monkeypatch, "new.person@example.com"
    )

    # The exchange really happened, and it used the address the trip started with.
    assert record["url"] == "https://oauth2.googleapis.com/token"
    assert record["form"]["redirect_uri"] == _redirect_uri_sent_to_google(started)
    assert record["form"]["grant_type"] == "authorization_code"

    # An account now exists and the person is holding a session for it.
    assert await _count_users(context) == 1, "no account was created"
    assert "amm_session" in response.cookies, "the person was not signed in"

    # And nothing on the way out says the trip failed.
    assert "error=google" not in response.text
    assert "error=google" not in response.headers.get("location", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", EVERY_HOST)
async def test_pressing_google_twice_lands_in_the_same_account(
    two_host_context, host, monkeypatch
):
    """The address is the identity, so the second trip is a sign-in, not a second account.

    This is the rule that stops one person owning two profiles for one email — the fault
    that shows up much later as a paid plan attached to an account they cannot reach.
    """

    context = two_host_context
    for attempt in (1, 2):
        response, _, _ = await _finish_trip(
            context,
            host,
            monkeypatch,
            "returning@example.com",
            code="code-" + str(attempt),
        )
        assert "amm_session" in response.cookies, (
            "trip " + str(attempt) + " did not sign anybody in"
        )
        count = await _count_users(context)
        assert count == 1, "trip " + str(attempt) + " left " + str(count) + " accounts"


@pytest.mark.asyncio
async def test_a_google_account_reaches_the_profile_the_password_door_made(
    two_host_context, monkeypatch
):
    """Pressing Google with an address that already has a password signs into *that*
    account, rather than quietly making a second one beside it."""

    from datetime import UTC, datetime

    from ai_market_monitor.services.web_auth import WebAuthService

    context = two_host_context
    email = "both.doors@example.com"

    async with context["session_factory"]() as session:
        await WebAuthService(session, context["settings"]).create_account(
            normalized=email,
            display_identifier=email,
            first_name="Sara",
            last_name="Ahmed",
            password_hash="not-a-real-hash",
            now=datetime.now(UTC),
            grant_reason="test fixture",
        )
        await session.commit()

    assert await _count_users(context) == 1

    response, _, _ = await _finish_trip(context, APP_HOST, monkeypatch, email)

    assert "amm_session" in response.cookies, "the existing account could not be reached"
    assert await _count_users(context) == 1, "a second account was made for one address"


@pytest.mark.asyncio
async def test_an_address_google_has_not_confirmed_never_reaches_an_account(
    two_host_context, monkeypatch
):
    """The whole door rests on `email_verified`.

    Without it, anybody who could persuade Google to issue a token for an address they do
    not own would be handed the matching Hilal Markets account. Proved here through the
    real route, not only against the service in isolation.
    """

    from ai_market_monitor.services import google_oauth

    context = two_host_context
    settings = context["settings"]
    token = _identity_token(
        audience=(settings.google_oauth_client_id or "").strip(),
        email="not.mine@example.com",
        email_verified=False,
    )
    monkeypatch.setattr(
        google_oauth.httpx,
        "AsyncClient",
        lambda *a, **k: _StubGoogle(token, {}),
    )

    started = await two_http_get(context, APP_HOST)
    state = parse_qs(urlsplit(started).query)["state"][0]
    response = await context["client"].get(
        "/auth/google/callback?code=code&state=" + state,
        headers={"host": APP_HOST},
        follow_redirects=False,
    )

    assert await _count_users(context) == 0, "an unconfirmed address was given an account"
    assert "amm_session" not in response.cookies
    assert "google_email_unverified" in (
        response.text + response.headers.get("location", "")
    )

"""A person whose session ran out is sent to sign in, and then straight back.

Found on the System Brain: every one of its pages answered an expired session with the
bare text ``{"detail": "Dashboard session required"}`` on a white screen. Behind it were
three separate holes, and these tests hold each one shut as a rule, not as one case:

1. **A page opened in a browser is never answered with raw refusal text.** Every GET page
   the application serves is opened with no session, and each must redirect to sign-in.
   Script calls on an open page still get the refusal they can read.
2. **The return address is honoured.** ``/signin?next=…`` was written by the dashboard for
   months and read by nothing, so every sign-in door is driven here and must land on it.
3. **Only our own pages are a return address.** Every shape of "another site" is refused,
   because a sign-in page that forwards to a stranger's address is a phishing tool.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from starlette.datastructures import Headers

from ai_market_monitor.core.return_path import (
    MAX_RETURN_PATH_LENGTH,
    RETURN_QUERY_KEY,
    referring_path_of,
    safe_return_path,
    sign_in_url,
    wants_html_page,
)
from ai_market_monitor.services.google_oauth import GoogleOAuthService
from tests.conftest import _build_context

PASSWORD = "CorrectHorse123!"
RETURN_TO = "/dashboard/system-brain/cases?view=urgent"

#: What a browser sends when a person opens an address, and what script sends.
PAGE_OPEN = {"accept": "text/html,application/xhtml+xml", "sec-fetch-dest": "document"}
SCRIPT_CALL = {"accept": "*/*", "sec-fetch-dest": "empty"}


# ---------------------------------------------------------------------------
# 3. Which addresses may be carried.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/dashboard/system-brain",
        "/dashboard/system-brain/cases?view=urgent",
        "/dashboard/monitors?page=2&sort=name",
        "/system-brain/stats",
    ],
)
def test_a_page_on_this_site_is_kept_exactly(path: str) -> None:
    assert safe_return_path(path) == path


@pytest.mark.parametrize(
    "hostile",
    [
        None,
        "",
        "   ",
        "dashboard",  # not a path at all
        "https://example.invalid/dashboard",
        "http://example.invalid",
        "javascript:alert(1)",
        "//example.invalid/dashboard",  # read by browsers as another site
        "/\\example.invalid",  # also read as another site by some browsers
        "/dashboard\\..\\x",
        "/\thttps://example.invalid",  # browsers strip the tab and read the rest
        "/dash board",
        "/dashboard\r\nSet-Cookie: x=1",
        "/dashboard\x00",
        "/" + "a" * MAX_RETURN_PATH_LENGTH,  # one character too long
    ],
)
def test_anything_that_is_not_a_plain_page_here_is_refused(hostile: str | None) -> None:
    assert safe_return_path(hostile) is None


@pytest.mark.parametrize(
    "door",
    [
        "/signin",
        "/signin?next=/dashboard",
        "/signin/code",
        "/signup",
        "/signup/verify",
        "/logout",
        "/auth/google/start",
        "/auth/google/callback?code=x",
        "/reset-password",
    ],
)
def test_a_sign_in_door_is_never_a_destination(door: str) -> None:
    """Coming back to one of these loops a person through sign-in, or out again."""

    assert safe_return_path(door) is None


def test_a_name_that_only_begins_like_a_door_is_still_a_page() -> None:
    assert safe_return_path("/signing-guide") == "/signing-guide"


def test_the_sign_in_address_escapes_what_it_carries() -> None:
    url = sign_in_url(RETURN_TO)
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).path == "/signin"
    assert query[RETURN_QUERY_KEY] == [RETURN_TO]
    assert query["message"] == ["session_required"]
    assert sign_in_url("https://example.invalid") == "/signin?message=session_required"


# ---------------------------------------------------------------------------
# Page or script.
# ---------------------------------------------------------------------------


class _Request:
    def __init__(self, headers: dict[str, str], url: str = "http://testserver/x") -> None:
        self.headers = Headers(headers)
        parts = urlsplit(url)
        self.url = type("U", (), {"scheme": parts.scheme, "netloc": parts.netloc})()


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"sec-fetch-dest": "document"}, True),
        ({"sec-fetch-dest": "document", "accept": "*/*"}, True),
        ({"sec-fetch-dest": "empty", "accept": "text/html"}, False),  # header wins
        ({"sec-fetch-dest": "iframe"}, False),
        ({"accept": "text/html,application/xhtml+xml"}, True),  # older browser
        ({"accept": "application/json"}, False),
        ({"accept": "*/*"}, False),
        ({}, False),
    ],
)
def test_page_or_script_is_decided_by_what_the_browser_says(
    headers: dict[str, str], expected: bool
) -> None:
    assert wants_html_page(_Request(headers)) is expected


@pytest.mark.parametrize(
    ("referer", "expected"),
    [
        ("http://testserver/dashboard/system-brain/cases/1", "/dashboard/system-brain/cases/1"),
        ("/dashboard/system-brain", "/dashboard/system-brain"),
        ("https://example.invalid/dashboard", None),
        ("http://testserver.example.invalid/dashboard", None),  # a longer host
        ("", None),
    ],
)
def test_a_form_goes_back_to_the_page_it_was_on_only_if_that_page_is_ours(
    referer: str, expected: str | None
) -> None:
    assert referring_path_of(_Request({"referer": referer})) == expected


# ---------------------------------------------------------------------------
# 1. Every page, opened with no session.
# ---------------------------------------------------------------------------


def _signed_in_get_pages(app) -> list[str]:
    """Every GET page with a fixed address that answers a missing session with 401."""

    def walk(routes, prefix: str = ""):
        for route in routes:
            # Newer FastAPI keeps an included router whole instead of copying its routes
            # onto the application, so both shapes are walked.
            context = getattr(route, "include_context", None)
            if context is not None and not callable(context):
                yield from walk(route.original_router.routes, prefix + context.prefix)
            elif isinstance(route, APIRoute):
                yield prefix + route.path, route.methods

    pages = []
    for path, methods in walk(app.routes):
        if "GET" not in methods or "{" in path or path.startswith("/api/"):
            continue
        pages.append(path)
    return sorted(set(pages))


@pytest.mark.asyncio
async def test_every_page_sends_a_person_without_a_session_to_sign_in(test_context) -> None:
    """The rule for every page, not only the System Brain ones that were reported.

    No page may answer a browser with a 401 body. Pages that do not need a session are
    free to answer normally; the only thing refused is the dead end.
    """

    client = test_context["client"]
    pages = _signed_in_get_pages(test_context["app"])
    brain_pages = [page for page in pages if "system-brain" in page]
    assert len(brain_pages) >= 10, brain_pages  # the family this was found in

    dead_ends = []
    for page in pages:
        response = await client.get(page, headers=PAGE_OPEN, follow_redirects=False)
        if response.status_code == 401:
            dead_ends.append(page)
            continue
        if page in brain_pages:
            assert response.status_code == 303, (page, response.status_code)
            location = response.headers["location"]
            assert urlsplit(location).path == "/signin", (page, location)
            assert parse_qs(urlsplit(location).query)[RETURN_QUERY_KEY] == [page]
    assert dead_ends == []


@pytest.mark.asyncio
async def test_the_page_address_keeps_its_query_string(test_context) -> None:
    response = await test_context["client"].get(
        RETURN_TO, headers=PAGE_OPEN, follow_redirects=False
    )
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)[RETURN_QUERY_KEY] == [
        RETURN_TO
    ]


@pytest.mark.asyncio
async def test_script_on_an_open_page_still_gets_a_refusal_it_can_read(test_context) -> None:
    """A redirect here would hand script the sign-in page's HTML instead of an answer."""

    response = await test_context["client"].get(
        "/api/v1/system-brain/conversations", headers=SCRIPT_CALL, follow_redirects=False
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Dashboard session required"


@pytest.mark.asyncio
async def test_a_refused_form_goes_back_to_the_page_holding_the_form(test_context) -> None:
    response = await test_context["client"].post(
        "/dashboard/system-brain/logout",
        headers={**PAGE_OPEN, "referer": "http://testserver/dashboard/system-brain/cases"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)[RETURN_QUERY_KEY] == [
        "/dashboard/system-brain/cases"
    ]


# ---------------------------------------------------------------------------
# 2. Every door honours the return address.
# ---------------------------------------------------------------------------


async def _create_account(context, email: str) -> None:
    client = context["client"]
    await client.post(
        "/signup/password",
        data={
            "email": email,
            "display_name": "Test Person",
            "password": PASSWORD,
            "repeat_password": PASSWORD,
        },
        follow_redirects=False,
    )
    code = context["settings"].email_test_outbox[-1]["code"]
    await client.post("/signup/verify", data={"email": email, "code": code})
    client.cookies.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carried", "expected"),
    [
        (RETURN_TO, RETURN_TO),
        ("https://example.invalid/dashboard", "/dashboard?message=login_successful"),
        ("//example.invalid", "/dashboard?message=login_successful"),
        (None, "/dashboard?message=login_successful"),
    ],
)
async def test_signing_in_with_a_password_lands_on_the_page_asked_for(
    test_context, carried: str | None, expected: str
) -> None:
    await _create_account(test_context, "password-door@example.com")
    data = {"email": "password-door@example.com", "password": PASSWORD}
    if carried is not None:
        data["next"] = carried
    response = await test_context["client"].post("/signin", data=data, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == expected


@pytest.mark.asyncio
async def test_signing_in_with_an_emailed_code_lands_on_the_page_asked_for(
    test_context,
) -> None:
    await _create_account(test_context, "code-door@example.com")
    client = test_context["client"]
    requested = await client.post(
        "/signin/code/request",
        data={"email": "code-door@example.com", "next": RETURN_TO},
        follow_redirects=False,
    )
    # The address rides through the "code sent" screen as well.
    assert parse_qs(urlsplit(requested.headers["location"]).query)[RETURN_QUERY_KEY] == [
        RETURN_TO
    ]
    code = test_context["settings"].email_test_outbox[-1]["code"]
    response = await client.post(
        "/signin/code/verify",
        data={"email": "code-door@example.com", "code": code, "next": RETURN_TO},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == RETURN_TO


@pytest.mark.asyncio
async def test_a_wrong_password_does_not_lose_the_return_address(test_context) -> None:
    await _create_account(test_context, "wrong-once@example.com")
    client = test_context["client"]
    refused = await client.post(
        "/signin",
        data={"email": "wrong-once@example.com", "password": "Wrong123!!", "next": RETURN_TO},
        follow_redirects=False,
    )
    page = await client.get(refused.headers["location"])
    assert f'name="next" value="{RETURN_TO}"'.replace("&", "&amp;") in page.text


@pytest.mark.asyncio
async def test_the_sign_in_page_never_writes_a_foreign_address_into_its_forms(
    test_context,
) -> None:
    page = await test_context["client"].get(
        "/signin", params={"next": "https://example.invalid/dashboard"}
    )
    assert page.status_code == 200
    assert 'name="next"' not in page.text
    assert "example.invalid" not in page.text


@pytest.mark.asyncio
async def test_a_chosen_plan_still_wins_over_the_return_address(test_context) -> None:
    """A person who pressed "buy" came to buy. The checkout screen is what they asked for."""

    await _create_account(test_context, "buyer@example.com")
    response = await test_context["client"].post(
        "/signin",
        data={
            "email": "buyer@example.com",
            "password": PASSWORD,
            "next": RETURN_TO,
            "plan_code": "trader",
            "billing_interval": "monthly",
        },
        follow_redirects=False,
    )
    assert response.headers["location"].startswith("/dashboard/billing")


@pytest_asyncio.fixture
async def google_context():
    async for context in _build_context(
        google_oauth_client_id="test-client.apps.googleusercontent.com",
        google_oauth_client_secret="test-secret",
    ):
        yield context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("carried", "expected"),
    [(RETURN_TO, RETURN_TO), ("https://example.invalid", "")],
)
async def test_the_google_door_carries_only_a_safe_return_address(
    google_context, carried: str, expected: str
) -> None:
    response = await google_context["client"].get(
        "/auth/google/start", params={"next": carried}, follow_redirects=False
    )
    assert response.status_code == 303
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    carried_state = GoogleOAuthService(google_context["settings"]).read_state(state)
    assert carried_state["next"] == expected

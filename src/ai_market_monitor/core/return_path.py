"""One owner for "the page somebody was trying to open before they were asked to sign in".

Three separate things used to be decided in three places, and each place understood a
different subset of them:

* whether a request is a page a person is looking at, or a background call made by
  script on a page they already have open;
* where to send a person whose session has run out;
* whether the address handed back afterwards is one of our own pages.

The third one is the dangerous one. A return address that arrives in a query string is
typed by whoever sent the link, so ``/signin?next=https://example.invalid/`` would hand
our sign-in page to a stranger's site — the person signs in with us and lands on a copy
of our dashboard somewhere else. :func:`safe_return_path` is the only place that
judgement is made, and it refuses everything except a plain path on this site.

A refused address is not repaired and not truncated. It is dropped, and the person lands
on the usual page after signing in. Guessing at a half-valid address is how somebody ends
up somewhere they never asked for.
"""

from __future__ import annotations

from urllib.parse import urlencode

#: The query key that carries the return address, on every page that carries one.
RETURN_QUERY_KEY = "next"

#: The longest return address we will carry. Longer than any real page address here, and
#: short enough that a query string cannot be used to push something large through the
#: sign-in pages.
MAX_RETURN_PATH_LENGTH = 512

#: Doors, not destinations. Coming back to one of these after signing in either loops a
#: person straight back to the sign-in page or signs them out again the moment they
#: arrive, so a return address pointing at one is dropped like any other bad address.
_NOT_A_DESTINATION = (
    "/signin",
    "/signup",
    "/logout",
    "/auth",
    "/reset-password",
)


def safe_return_path(raw: str | None) -> str | None:
    """The address to come back to, or ``None`` when it may not be trusted.

    Kept: a path on this site, with its query string, such as
    ``/dashboard/system-brain/cases?view=urgent``.

    Refused: anything naming another site (``https://…``, ``//other.example``), anything
    using a backslash (some browsers read ``/\\other.example`` as another site),
    anything with a space, a tab or a line break in it, anything over
    :data:`MAX_RETURN_PATH_LENGTH`, and the sign-in doors themselves.
    """

    if not raw:
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_RETURN_PATH_LENGTH:
        return None
    if not candidate.startswith("/"):
        return None
    # `//host` and `/\host` are both read as "another site" by browsers, which is the
    # whole attack: the address looks like one of ours and is not.
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return None
    if "\\" in candidate:
        return None
    # Any whitespace at all, including the control characters a browser strips before it
    # reads an address. `/\thttps://other.example` becomes `https://other.example`.
    if any(character.isspace() or ord(character) < 0x20 for character in candidate):
        return None
    path = candidate.split("?", 1)[0].split("#", 1)[0]
    if any(path == door or path.startswith(f"{door}/") for door in _NOT_A_DESTINATION):
        return None
    return candidate


def return_path_of(request: object) -> str | None:
    """The address of the page being asked for, ready to be carried through sign-in.

    The query string is part of it. Dropping it sent somebody who was looking at one
    filtered list back to the unfiltered one, which is a different page as far as the
    person is concerned.
    """

    url = getattr(request, "url", None)
    if url is None:
        return None
    path = getattr(url, "path", "") or ""
    query = getattr(url, "query", "") or ""
    return safe_return_path(f"{path}?{query}" if query else path)


def referring_path_of(request: object) -> str | None:
    """The page a form was submitted from, when that page is one of ours.

    A form that is refused cannot be replayed, so the address of the form itself is not
    where the person wants to end up — the page holding the form is. The browser names
    that page in ``Referer``; anything pointing somewhere else is dropped by
    :func:`safe_return_path` like any other address.
    """

    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    referer = (headers.get("referer") or "").strip()
    if not referer:
        return None
    url = getattr(request, "url", None)
    origin = f"{getattr(url, 'scheme', '')}://{getattr(url, 'netloc', '')}"
    if referer.startswith("/"):
        return safe_return_path(referer)
    if origin and referer.startswith(f"{origin}/"):
        return safe_return_path(referer[len(origin) :])
    return None


def wants_html_page(request: object) -> bool:
    """Whether a browser is opening this address as a page, rather than calling it.

    A page that a person is looking at should never be answered with raw error text.
    Script on an open page should never be answered with a redirect, because it would
    quietly receive the sign-in page's HTML where it expected an answer.

    Browsers say which of the two it is in ``Sec-Fetch-Dest``, and that is believed
    first. Where the header is missing — an older browser, or a test client — the
    ``Accept`` header decides: a page navigation asks for HTML, a script call does not.
    """

    headers = getattr(request, "headers", None)
    if headers is None:
        return False
    destination = (headers.get("sec-fetch-dest") or "").strip().casefold()
    if destination:
        return destination == "document"
    accept = (headers.get("accept") or "").casefold()
    return "text/html" in accept


def sign_in_url(return_to: str | None, *, message: str = "session_required") -> str:
    """Where to send somebody who has to sign in before we can show them the page."""

    query: dict[str, str] = {}
    target = safe_return_path(return_to)
    if target:
        query[RETURN_QUERY_KEY] = target
    if message:
        query["message"] = message
    return f"/signin?{urlencode(query)}" if query else "/signin"

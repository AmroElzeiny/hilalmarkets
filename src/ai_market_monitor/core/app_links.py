"""Links into the product, on the product's own hostname when it has one.

One deployment answers on two names: the marketing site and the dashboard. Every page,
assistant answer and preview card that sends somebody into the product builds its
address here, so none of them can keep a visitor on the marketing hostname by writing a
plain path of its own.
"""

from __future__ import annotations

from ai_market_monitor.core.config import Settings


def app_host(settings: Settings) -> str | None:
    """The hostname the dashboard is served on, or ``None`` if it has none of its own.

    "None of its own" is the important half. Locally, and in any deployment that runs the
    whole product on one name, ``APP_BASE_URL`` and ``PUBLIC_BASE_URL`` are the same host
    — and taking the root over there would replace the landing page with a redirect to
    sign-in for every visitor, including the ones who have never heard of the product.
    So the root only becomes the dashboard when the two names really are different.
    """

    if settings.app_base_url is None:
        return None
    host = (settings.app_base_url.host or "").strip().lower()
    public = (settings.public_base_url.host or "").strip().lower()
    if not host or host == public:
        return None
    return host


def app_link(settings: Settings, path: str) -> str:
    """A link into the product, on the product's own hostname when it has one.

    The whole point of `APP_BASE_URL` is that the dashboard is served at
    `https://app.hilalmarkets.com`. That was half true: the *root* of that hostname
    served the dashboard, but every way into the product from the marketing site was a
    plain path — "Start free", "Sign in", "Open dashboard" — so a visitor who pressed one
    stayed on `hilalmarkets.com` and used the whole product from there. Two hostnames
    served the same signed-in pages, and the one named after the product was the one
    almost nobody reached.

    When the two names are the same — locally, and in any single-domain install — this
    returns the plain path, so nothing changes and no absolute URL is written into a page
    that does not need one.
    """

    host = app_host(settings)
    if host is None:
        return path
    return f"{str(settings.app_base_url).rstrip('/')}{path}"


def site_link(settings: Settings, path: str) -> str:
    """A link to the marketing site, on the marketing hostname when the product has its own.

    The mirror of `app_link`. The sign-in page, and every public page a signed-in person
    opens from the dashboard, is served on `app.hilalmarkets.com` — and there `/` is the
    dashboard, which sends a visitor who is not signed in straight back to sign-in. So
    "Back to the website", the logo, and every `/#pricing`-style anchor written as a plain
    path reloaded the sign-in page instead of leaving it.

    Every link to the home page, or to an anchor on it, is built here. When the two names
    are the same this returns the plain path, so a local run writes no absolute URL.
    """

    if app_host(settings) is None:
        return path
    return f"{str(settings.public_base_url).rstrip('/')}{path}"

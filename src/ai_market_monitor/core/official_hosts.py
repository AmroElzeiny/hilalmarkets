"""Is this the same website? One answer for every importer that follows a redirect.

A Shariah authority's page is imported only from the host it is configured on. A redirect
to some other host could be anybody's page, so the importers refuse it — that rule stays.

But ``www.fasset.com`` and ``fasset.com`` are the same website. Fasset moved its reports
page from the first to the second, answering every request with a redirect, and from
then on the importer refused its own official source every day: "Fasset redirected the
importer outside the configured official host". The scholars' reviews it carries stopped
being refreshed while the log called it a security refusal.

Two importers compared the raw host names, and the evidence crawler had its own rule for
dropping ``www.``. They read "same site" from here now: equal once a single leading
``www.`` is removed. Nothing else is loosened — a different domain, a subdomain other
than ``www``, or no host at all is still a different site.
"""

from __future__ import annotations

from urllib.parse import urlsplit

__all__ = ["same_official_host", "site_host"]


def site_host(host: str | None) -> str:
    """A host name as a website's identity: lower case, without a leading ``www.``."""

    return (host or "").strip().casefold().removeprefix("www.")


def same_official_host(configured_url: str, final_url: str) -> bool:
    """Did a fetch of ``configured_url`` end on the same website?"""

    expected = site_host(urlsplit(configured_url).hostname)
    final = site_host(urlsplit(final_url).hostname)
    return bool(expected) and expected == final

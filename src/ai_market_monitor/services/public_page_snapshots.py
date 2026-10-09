"""The words of the React pages, in the HTML the server sends.

The home page, Features, How It Works, the Hilal Markets Methodology, Contact and the
legal pages are drawn by the landing bundle into an empty ``<div id="root">``. The
header and footer of the Market page and of every Passport are drawn the same way. A
reader that does not run JavaScript — most search and AI crawlers, and Google's first
pass — received a page with a title and nothing else: no heading, no text, no links.
Google reported ``/features`` as "Crawled — currently not indexed".

**What this does.** A browser opens each of those pages exactly as a visitor would,
waits for the bundle to draw it, and keeps what it drew. The server puts that copy
inside the empty slot, and the bundle replaces it with the live drawing as soon as it
starts. So there is still one source for every word: the React page itself. Nothing
here is written down a second time.

**A copy is used only while it is still true.** Each page carries a version key: a
fingerprint of the data the server handed the bundle (prices, menus, the methodology
register, contact details) and of the bundle files themselves. A copy is kept with the
key of the page it was taken from, and the server uses it only when that key is the
page's key today. Change a price, approve a coin, ship a new bundle — and the old copy
is simply not used until the next one is taken. A stale copy is never shown.

The copies are taken by the worker on a schedule (``refresh_public_page_snapshots``).
Most runs only read each page's key and find nothing to do; the browser starts only
when a key has changed.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.money import money_json_dumps

logger = logging.getLogger(__name__)

#: The empty slots the landing bundle draws into.
SNAPSHOT_SLOTS: tuple[str, ...] = ("root", "hm-site-footer")

#: Every page drawn by the landing bundle, and the page whose copy it uses. A Passport
#: and its Full Report wear the Market page's header and footer, so they share its copy
#: rather than taking one per coin.
SNAPSHOT_SOURCE_BY_PATH: Mapping[str, str] = {
    "/": "/",
    "/features": "/features",
    "/how-it-works": "/how-it-works",
    "/hilal-methodology": "/hilal-methodology",
    "/contact": "/contact",
    "/privacy": "/privacy",
    "/terms": "/terms",
    "/cookies": "/cookies",
    "/markets": "/markets",
}
#: The Passport pages, which use the Market page's copy.
PASSPORT_SNAPSHOT_SOURCE = "/markets"

#: The meta tag carrying the page's version key. The worker reads it from the page.
RENDER_KEY_META = "hm-render-key"

_REDIS_PREFIX = "hm:page-snapshot:v1:"
#: A copy larger than this is not kept: something has gone wrong with the page.
_MAX_SLOT_CHARACTERS = 600_000
_PAGE_SECONDS = 45.0
_CLOSE_SECONDS = 10.0
_USER_AGENT = "HilalMarketsPageSnapshot/1.0"

_LANDING_ASSETS = Path(__file__).resolve().parents[1] / "static" / "landing" / "assets"
_SCRIPT = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_KEY_META = re.compile(
    r'<meta name="' + RENDER_KEY_META + r'" content="([0-9a-f]+)"', re.IGNORECASE
)
#: The class the scroll reveal adds once a block has been seen. Removed from the copy so
#: a block looks the same before the bundle starts as it did before this module existed.
_SEEN_CLASS = re.compile(r"(?<=[\s\"])is-visible(?=[\s\"])")


@lru_cache(maxsize=1)
def _bundle_fingerprint() -> str:
    digest = hashlib.sha256()
    for name in ("landing.js", "landing.css"):
        with contextlib.suppress(OSError):
            digest.update((_LANDING_ASSETS / name).read_bytes())
    return digest.hexdigest()


def render_key(runtime_config: Mapping[str, Any]) -> str:
    """The version key of a page: what the bundle was handed, and the bundle itself.

    Analytics settings are left out. They change nothing the bundle draws, and they
    differ between machines, so including them would only make a good copy look stale.
    """

    drawn = {key: value for key, value in runtime_config.items() if key != "analytics"}
    # Written by the page's own JSON writer and read back, so the key is the key of the
    # JSON the bundle receives: ``Decimal("10.50")`` and the number the page carries are
    # the same value here.
    drawn = json.loads(money_json_dumps(drawn))
    payload = json.dumps(drawn, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        (payload + "\n" + _bundle_fingerprint()).encode("utf-8")
    ).hexdigest()[:32]


def snapshot_source(path: str) -> str | None:
    """Which page's copy a page uses, or ``None`` when it has none."""

    if path in SNAPSHOT_SOURCE_BY_PATH:
        return SNAPSHOT_SOURCE_BY_PATH[path]
    if path.startswith("/passports/"):
        return PASSPORT_SNAPSHOT_SOURCE
    return None


def _redis(settings: Settings) -> Any:
    from redis.asyncio import Redis

    return Redis.from_url(
        settings.redis_url, socket_connect_timeout=1, socket_timeout=1
    )


async def load_snapshot(settings: Settings, path: str, key: str) -> dict[str, str]:
    """The kept copy for ``path``, slot by slot — empty unless it matches ``key``.

    Never raises: without a copy the page is served as it always was.
    """

    source = snapshot_source(path)
    if source is None or settings.app_env == "test":
        return {}
    client = _redis(settings)
    try:
        raw = await client.get(_REDIS_PREFIX + source)
    except Exception:  # noqa: BLE001 - no copy is the old page, not a broken one
        return {}
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()
    return snapshot_slots(raw, key)


def snapshot_slots(raw: bytes | str | None, key: str) -> dict[str, str]:
    """The slots of a stored copy, only when it was taken from a page with ``key``."""

    if not raw:
        return {}
    try:
        stored = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(stored, dict) or stored.get("key") != key:
        return {}
    slots = stored.get("slots")
    if not isinstance(slots, dict):
        return {}
    return {
        slot: html
        for slot, html in slots.items()
        if slot in SNAPSHOT_SLOTS and isinstance(html, str)
    }


def clean_slot(html: str) -> str:
    """What is kept of one drawn slot: no scripts, no "already seen" marks."""

    return _SEEN_CLASS.sub("", _SCRIPT.sub("", html)).strip()


def page_key(html: str) -> str | None:
    match = _KEY_META.search(html)
    return match.group(1) if match else None


async def refresh_snapshots(settings: Settings) -> dict[str, Any]:
    """Take a new copy of every page whose key has changed since its last copy."""

    import httpx

    base = settings.public_site_url.rstrip("/")
    client = _redis(settings)
    report: dict[str, Any] = {"checked": 0, "taken": [], "failed": {}}
    stale: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": _USER_AGENT}, follow_redirects=False
        ) as http:
            for path in sorted(set(SNAPSHOT_SOURCE_BY_PATH.values())):
                report["checked"] += 1
                try:
                    response = await http.get(base + path)
                except httpx.HTTPError as exc:
                    report["failed"][path] = type(exc).__name__
                    continue
                key = page_key(response.text) if response.status_code == 200 else None
                if key is None:
                    report["failed"][path] = f"no key (HTTP {response.status_code})"
                    continue
                stored = await client.get(_REDIS_PREFIX + path)
                if not snapshot_slots(stored, key):
                    stale[path] = key
        if stale:
            for path, slots, failure in await _draw_pages(base, list(stale)):
                if failure:
                    report["failed"][path] = failure
                    continue
                if slots.pop("__key__", None) != stale[path]:
                    # The page changed between reading its key and drawing it. The next
                    # run takes it again; a copy under the wrong key is never kept.
                    report["failed"][path] = "key changed while drawing"
                    continue
                await client.set(
                    _REDIS_PREFIX + path,
                    json.dumps({"key": stale[path], "slots": slots}),
                )
                report["taken"].append(path)
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()
    if report["failed"]:
        logger.warning("public_page_snapshots_incomplete %s", report["failed"])
    return report


_READ_SLOTS = """
(slots) => {
  const meta = document.querySelector('meta[name="KEY_META"]');
  const out = { __key__: meta ? meta.getAttribute('content') : null };
  for (const id of slots) {
    const node = document.getElementById(id);
    if (node && node.children.length) out[id] = node.innerHTML;
  }
  return out;
}
""".replace("KEY_META", RENDER_KEY_META)


async def _draw_pages(
    base: str, paths: list[str]
) -> list[tuple[str, dict[str, Any], str]]:
    """Each page drawn by a real browser: ``(path, slots, failure)``."""

    from playwright.async_api import async_playwright

    from ai_market_monitor.services.sharia_page_render import LAUNCH_ARGUMENTS

    results: list[tuple[str, dict[str, Any], str]] = []
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True, args=list(LAUNCH_ARGUMENTS))
        try:
            # Reduced motion: counters show their final number at once, rather than
            # whatever number they had reached when the copy was taken.
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 900},
                reduced_motion="reduce",
            )
            for path in paths:
                page = await context.new_page()
                try:
                    await asyncio.wait_for(
                        _draw_one(page, base + path), timeout=_PAGE_SECONDS
                    )
                    slots = await page.evaluate(_READ_SLOTS, list(SNAPSHOT_SLOTS))
                except Exception as exc:  # noqa: BLE001 - reported per page
                    results.append((path, {}, type(exc).__name__))
                    continue
                finally:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(page.close(), timeout=_CLOSE_SECONDS)
                kept: dict[str, Any] = {"__key__": slots.get("__key__")}
                for slot in SNAPSHOT_SLOTS:
                    html = slots.get(slot)
                    if isinstance(html, str) and html:
                        html = clean_slot(html)
                        if len(html) <= _MAX_SLOT_CHARACTERS:
                            kept[slot] = html
                if len(kept) == 1:
                    results.append((path, {}, "the page drew nothing"))
                else:
                    results.append((path, kept, ""))
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(browser.close(), timeout=_CLOSE_SECONDS)
    return results


async def _draw_one(page: Any, url: str) -> None:
    await page.goto(url, wait_until="load")
    await page.wait_for_selector("#root > *", state="attached")
    # Let the first effects run, so the copy is the settled page and not its first frame.
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=5_000)

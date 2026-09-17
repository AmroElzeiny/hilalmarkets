"""Every dashboard page describes itself to Hilal, in its own words.

Each page carrying the chat publishes one named description through
`publish(name, describe)` in `hm-page-context.js` — read off what the page already
shows, never invented, with no visible change to the page itself. What only a
running browser can answer is that every page actually does it, that the words are
the page's own, and that the server accepts what was sent.

The questions asked are deliberately ones Hilal refuses. A refusal is produced by
the application itself with no provider call, so these tests exercise the real path
end to end — page description, snapshot, validation, answer — without spending
anything.

New file. No existing browser file is touched.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.browser.conftest import (
    close_any_open_guide,
    seed_sharia_screened_market,
    signup,
)

#: Every dashboard page showing Hilal, the name it publishes under, and where it
#: lives. Passport needs a seeded coin, reached under its standard the way the
#: market page links it. The printable report carries no chat window (its handler
#: sets no chat chrome), so it is covered separately below: its publisher ships
#: with the page's own script, and the server is shown to accept its words.
PAGES = [
    ("screened_market", "/dashboard/market"),
    ("opportunities", "/dashboard/opportunities"),
    ("watch_plans", "/dashboard/monitors"),
    ("passport", "/dashboard/market/sol?methodology_id={methodology_id}"),
    ("watchlist", "/dashboard/market"),
    ("connections", "/dashboard/connections"),
    ("research", "/dashboard/research"),
    ("settings", "/dashboard/settings"),
    ("support", "/dashboard/support"),
    ("subscription", "/dashboard/subscription"),
]

REFUSED = "should I buy bitcoin right now"


def _open_chat(page: Page, base_url: str, path: str) -> None:
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    close_any_open_guide(page)
    expect(page.locator("[data-hilal-open]")).to_be_visible()
    page.locator("[data-hilal-open]").click()
    expect(page.locator("[data-hilal-window]")).to_be_visible()
    # The welcome is drawn once the history has been read. The conversation is
    # shared across pages, so this is the first message only on the first page.
    expect(page.locator("[data-hilal-thread] .hilal-msg").first).to_be_visible()


def test_every_page_carries_its_own_description_and_the_server_accepts_it(
    page: Page, base_url: str, browser_app
) -> None:
    """R15: one page at a time, through the real path.

    Opens each page, asks a question Hilal refuses, and reads the description back
    out of the request the browser actually sent. The refusal arriving proves the
    server accepted the whole view — unknown names, over-long words and all.
    """

    email = signup(page, base_url)
    assert browser_app.database_url, "this test needs the auto-started server"
    seeded = seed_sharia_screened_market(browser_app.database_url, email)
    # The Passport and the report are reached under the seeded standard, the way the
    # market page links them: without it there is no current assessment to open.
    pages = [
        (name, path.format(methodology_id=seeded["methodology_id"]))
        for name, path in PAGES
    ]

    sent: list[dict] = []
    page.on(
        "request",
        lambda request: sent.append(request.post_data_json)
        if request.url.endswith("/dashboard/hilal/message") and request.post_data
        else None,
    )

    for name, path in pages:
        sent.clear()
        _open_chat(page, base_url, path)
        page.locator("[data-hilal-input]").fill(REFUSED)
        page.locator("[data-hilal-send]").click()
        answer = page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
        expect(answer.locator(".hilal-bubble")).to_have_attribute(
            "data-mode", "REFUSAL", timeout=15_000
        )

        assert sent, f"nothing was posted from {path}"
        view = sent[-1].get("view") or {}
        described = view.get(name)
        assert described, f"{path} published no {name!r} description: {sorted(view)}"
        assert described.get("heading"), (
            f"{path} described itself without its own heading: {described}"
        )
        # The words are the page's, not the assistant's. The heading on screen says
        # the same, so if these two ever differ, one of them is inventing.
        on_screen = page.locator("h1").first.inner_text().strip()
        assert described["heading"].strip() == on_screen[:120], (
            f"Hilal was told {described['heading']!r} while {path} says {on_screen!r}"
        )
        page.locator("[data-hilal-open]").click()
        expect(page.locator("[data-hilal-window]")).to_be_hidden()


def test_the_report_page_description_is_wired_and_accepted(
    page: Page, base_url: str, browser_app
) -> None:
    """R15 for the printable report: no chat window lives there, so nothing can
    snapshot — but the page's own script already publishes its words, and the
    server accepts them. Asked directly, with the dashboard's own form token."""

    email = signup(page, base_url)
    assert browser_app.database_url, "this test needs the auto-started server"
    seeded = seed_sharia_screened_market(browser_app.database_url, email)
    page.goto(
        f"{base_url}/dashboard/market/sol/report"
        f"?methodology_id={seeded['methodology_id']}",
        wait_until="domcontentloaded",
    )
    close_any_open_guide(page)
    # The publisher ships with the page's own script, and the words it reads are
    # really on the page: the coin heading and its numbered sections.
    scripts = page.evaluate(
        "() => [...document.querySelectorAll('script')].map(n => n.src)"
    )
    assert any("hm-passport-test.js" in src for src in scripts)
    heading = page.locator("h1").first.inner_text().strip()
    assert heading
    sections = page.locator(".t-report h2").all_inner_texts()
    assert len(sections) >= 8, sections

    answer = page.evaluate(
        """async ([heading, sections]) => {
          const response = await fetch('/api/v1/dashboard/hilal/message', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRF-Token': document.body.dataset.csrfToken,
            },
            body: JSON.stringify({
              message: 'should I buy bitcoin right now',
              view: {
                page: 'report',
                report: {heading, summary: sections[1] || null, points: sections},
              },
              client_message_id: 'report-wired-' + Date.now(),
            }),
          });
          return {ok: response.ok, status: response.status, body: await response.json()};
        }""",
        [heading, [item.strip() for item in sections]],
    )
    assert answer["ok"], answer
    assert answer["body"]["mode"] == "REFUSAL", answer["body"]


def test_an_over_long_heading_is_cut_down_never_refused(
    page: Page, base_url: str
) -> None:
    """R21 in front of a person: a page that grew too many words still gets an answer."""

    signup(page, base_url)
    sent: list[dict] = []
    page.on(
        "request",
        lambda request: sent.append(request.post_data_json)
        if request.url.endswith("/dashboard/hilal/message") and request.post_data
        else None,
    )
    _open_chat(page, base_url, "/dashboard/market")
    page.evaluate("() => { document.querySelector('h1').textContent = 'x'.repeat(500); }")
    page.locator("[data-hilal-input]").fill(REFUSED)
    page.locator("[data-hilal-send]").click()
    answer = page.locator("[data-hilal-thread] .hilal-msg[data-who='assistant']").last
    expect(answer.locator(".hilal-bubble")).to_have_attribute(
        "data-mode", "REFUSAL", timeout=15_000
    )
    assert sent, "nothing was posted"
    heading = ((sent[-1].get("view") or {}).get("screened_market") or {}).get("heading")
    assert heading == "x" * 120, f"the heading was not cut to the cap: {len(heading or '')}"

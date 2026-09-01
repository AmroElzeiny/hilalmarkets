"""The rules the five sign-in pages are held to, checked without a browser.

`/signup`, `/signin`, `/signin/code`, `/reset-password` and the confirm step they hand
off to. Everything here is a rule about the **family**, never about the one page a fault
was first noticed on: a contrast pair is computed for every surface it lands on, an error
code is checked for every code any route can emit, and a duplicated implementation is
looked for in every file that could hold a second copy.

What only a real browser can settle — that a rule is not overridden by a later one, that
a countdown counts, that the six boxes fill in — is in
`tests/browser/test_auth_pages_e2e.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ai_market_monitor.core.auth_pages import (
    AUTH_PAGES,
    CODE_RESEND_SECONDS,
    PASSWORD_RULES,
    PRODUCT_PROMISES,
    alert_for,
    browser_password_rules,
    page_copy,
    password_validation_error,
)
from ai_market_monitor.core.copy_rules import (
    BRAND_NAME_PATTERN,
    FORBIDDEN_CLAIM_PHRASES,
    SHARIA_SPELLING_PATTERN,
)
from tests.support.contrast import contrast, flatten

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_market_monitor"
STATIC = SRC / "static"
TEMPLATES = SRC / "templates"

AUTH_HTML = TEMPLATES / "auth.html"
AUTH_MACROS = TEMPLATES / "hilal" / "macros" / "auth_fields.html"
AUTH_ALERT = TEMPLATES / "hilal" / "partials" / "auth_alert.html"
AUTH_CSS = STATIC / "hilalmarkets-auth.css"
AUTH_JS = STATIC / "hilalmarkets-auth.js"
#: The other stylesheet these pages load. It is shared with the dashboard, and it used to
#: carry the previous sign-in design as well.
SHARED_CSS = STATIC / "hilalmarkets.css"
BRAND_CSS = STATIC / "hilalmarkets-brand.css"
COOKIE_CSS = STATIC / "hilalmarkets-cookie.css"
WEB_AUTH = SRC / "services" / "web_auth.py"
ROUTER = SRC / "api" / "routers" / "dashboard.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    """CSS with its comments removed, so prose about a colour is not read as a colour."""

    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _strip_template_comments(markup: str) -> str:
    """Jinja and HTML comments removed, for the same reason.

    Several comments in these files quote the code they replaced — `error.replace(...)`
    among them — and a rule that forbids that code must not be tripped by the note
    explaining why it is forbidden.
    """

    without_jinja = re.sub(r"\{#.*?#\}", "", markup, flags=re.DOTALL)
    return re.sub(r"<!--.*?-->", "", without_jinja, flags=re.DOTALL)


def _without_print_rules(css: str) -> str:
    """CSS with every `@media print` block removed, matched by braces.

    Hiding a component on paper is not styling it. One stylesheet hides the cookie
    banner when a dashboard page is printed, which is placement, not a second opinion
    about what the banner looks like.
    """

    out = []
    index = 0
    while True:
        found = re.search(r"@media\s+print[^{]*\{", css[index:])
        if not found:
            out.append(css[index:])
            return "".join(out)
        start = index + found.start()
        out.append(css[index:start])
        depth = 0
        cursor = index + found.end() - 1
        while cursor < len(css):
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        index = cursor + 1


#: Every colour these pages may paint, and where it comes from.
#:
#: This is the rule "no new main colours" written so a machine can check it. Each entry
#: is a token declared in `hilalmarkets-brand.css`; the file below is scanned and any
#: sixth hue is a colour somebody invented.
PALETTE = {
    "ink": "#2b2e35",
    "ink-strong": "#202329",
    "copy": "#50555e",
    "muted": "#63696f",
    "canvas": "#f5f8fb",
    "surface": "#ffffff",
    "surface-soft": "#fafbfc",
    "hairline": "#e1e5ea",
    "hairline-strong": "#d0d6de",
    "control-line": "#79828d",
    "apple": "#cbfa4d",
    "apple-soft": "#f1fadf",
    "apple-pale": "#e8fbbf",
    "apple-deep": "#55712a",
    "success": "#46551b",
    "info": "#1f6e97",
    "info-bg": "#e2f1f9",
    "info-line": "#bcdcec",
    "warning": "#8a6316",
    "warning-bg": "#fdf2df",
    "danger": "#8d3029",
    "danger-strong": "#6c271f",
    "danger-bg": "#fff5f3",
    "danger-line": "#e4b8b2",
    "neutral-bg": "#eef1f4",
    "on-ink-soft": "#aeb4bd",
    "on-ink-line": "#767b83",
}

#: The apple wash in the brand frame behind the card: the accent at 10%, on the canvas.
#: Nothing readable sits on it — it is checked so that a *later* change cannot quietly
#: put text there and be believed.
FRAME_WASH = flatten(PALETTE["apple"], 0.10, PALETTE["canvas"])

#: (what, foreground, background, minimum). Every pair the six pages paint.
#:
#: Normal text needs 4.5:1 (WCAG 1.4.3). A boundary a person has to see in order to use
#: a control needs 3:1 (1.4.11). Nothing on these pages is large enough to claim the 3:1
#: relaxation for big text, so everything readable is held to 4.5.
TEXT_PAIRS = [
    ("heading", PALETTE["ink"], PALETTE["surface"], 4.5),
    ("lede", PALETTE["copy"], PALETTE["surface"], 4.5),
    ("field label", PALETTE["ink"], PALETTE["surface"], 4.5),
    ("field hint", PALETTE["muted"], PALETTE["surface"], 4.5),
    ("typed value", PALETTE["ink"], PALETTE["surface-soft"], 4.5),
    ("placeholder", PALETTE["muted"], PALETTE["surface-soft"], 4.5),
    ("unmet password rule", PALETTE["muted"], PALETTE["surface"], 4.5),
    ("met password rule", PALETTE["success"], PALETTE["surface"], 4.5),
    ("tick inside a met rule", PALETTE["surface"], PALETTE["apple-deep"], 4.5),
    ("field error", PALETTE["danger"], PALETTE["surface"], 4.5),
    ("field error on its own fill", PALETTE["danger"], PALETTE["danger-bg"], 4.5),
    ("error banner heading", PALETTE["danger-strong"], PALETTE["danger-bg"], 4.5),
    ("error banner text", PALETTE["danger"], PALETTE["danger-bg"], 4.5),
    ("error banner mark", PALETTE["surface"], PALETTE["danger"], 4.5),
    ("success banner text", PALETTE["success"], PALETTE["apple-soft"], 4.5),
    ("success banner mark", PALETTE["ink"], PALETTE["apple"], 4.5),
    ("information banner text", PALETTE["info"], PALETTE["info-bg"], 4.5),
    ("information banner mark", PALETTE["surface"], PALETTE["info"], 4.5),
    ("banner action label", PALETTE["surface"], PALETTE["ink"], 4.5),
    ("caps lock warning", PALETTE["warning"], PALETTE["warning-bg"], 4.5),
    ("step chip", PALETTE["success"], PALETTE["apple-soft"], 4.5),
    ("primary button label", PALETTE["ink"], PALETTE["apple"], 4.5),
    ("secondary button label", PALETTE["ink"], PALETTE["surface"], 4.5),
    ("switch line", PALETTE["copy"], PALETTE["surface"], 4.5),
    ("switch link", PALETTE["apple-deep"], PALETTE["surface"], 4.5),
    ("forgot link", PALETTE["apple-deep"], PALETTE["surface"], 4.5),
    ("legal row", PALETTE["muted"], PALETTE["surface"], 4.5),
    ("legal row hovered", PALETTE["ink"], PALETTE["canvas"], 4.5),
    ("code digit", PALETTE["ink"], PALETTE["surface-soft"], 4.5),
    ("sent-to line", PALETTE["copy"], PALETTE["surface-soft"], 4.5),
    ("resend label", PALETTE["muted"], PALETTE["surface"], 4.5),
    ("waiting resend button", PALETTE["muted"], PALETTE["neutral-bg"], 4.5),
    ("other-door button label", PALETTE["ink"], PALETTE["surface"], 4.5),
    ("other-door button label hovered", PALETTE["ink"], PALETTE["surface-soft"], 4.5),
    ("other-door mark", PALETTE["apple-deep"], PALETTE["apple-soft"], 4.5),
    ("back link", PALETTE["copy"], PALETTE["surface"], 4.5),
    ("skip link", PALETTE["surface"], PALETTE["ink"], 4.5),
    # The three promises under the button, which used to be a list on a dark panel.
    ("promise title", PALETTE["ink"], PALETTE["surface"], 4.5),
    ("promise sentence", PALETTE["muted"], PALETTE["surface"], 4.5),
    ("promise tick", PALETTE["ink"], PALETTE["apple"], 4.5),
    # The card and the legal row sit on the canvas now, not inside a white panel, so
    # every pair on them is measured against the canvas rather than against white.
    ("legal row", PALETTE["muted"], PALETTE["canvas"], 4.5),
    ("legal row hovered", PALETTE["ink"], PALETTE["surface"], 4.5),
    # Nothing is written on the brand frame. This pair exists so that if a heading is
    # ever moved on top of it, the move fails here instead of on somebody's screen.
    ("anything written on the brand frame", PALETTE["ink"], FRAME_WASH, 4.5),
]

BOUNDARY_PAIRS = [
    ("field edge on its own fill", PALETTE["control-line"], PALETTE["surface-soft"], 3.0),
    ("field edge on white", PALETTE["control-line"], PALETTE["surface"], 3.0),
    ("focused field edge", PALETTE["apple-deep"], PALETTE["surface"], 3.0),
    ("invalid field edge", PALETTE["danger"], PALETTE["danger-bg"], 3.0),
    ("valid field edge", PALETTE["apple-deep"], PALETTE["surface"], 3.0),
    ("code box edge", PALETTE["control-line"], PALETTE["surface-soft"], 3.0),
    ("filled code box edge", PALETTE["apple-deep"], PALETTE["surface"], 3.0),
    ("unmet rule mark edge", PALETTE["control-line"], PALETTE["surface"], 3.0),
    ("other-door button edge", PALETTE["control-line"], PALETTE["surface"], 3.0),
    ("resend button edge", PALETTE["control-line"], PALETTE["surface"], 3.0),
    # The shared focus indicator, on every surface it can land on here.
    ("focus ring on white", PALETTE["ink-strong"], PALETTE["surface"], 3.0),
    ("focus ring on the canvas", PALETTE["ink-strong"], PALETTE["canvas"], 3.0),
    ("focus ring on the apple button", PALETTE["ink-strong"], PALETTE["apple"], 3.0),
    ("focus ring on the near-black skip link", PALETTE["apple"], PALETTE["ink"], 3.0),
]


@pytest.mark.parametrize(("what", "front", "back", "least"), TEXT_PAIRS)
def test_every_word_on_these_pages_is_readable(
    what: str, front: str, back: str, least: float
) -> None:
    measured = contrast(front, back)
    assert measured >= least, f"{what}: {front} on {back} is {measured:.2f}:1, needs {least}"


@pytest.mark.parametrize(("what", "front", "back", "least"), BOUNDARY_PAIRS)
def test_every_edge_a_person_must_see_is_visible(
    what: str, front: str, back: str, least: float
) -> None:
    measured = contrast(front, back)
    assert measured >= least, f"{what}: {front} on {back} is {measured:.2f}:1, needs {least}"


def test_the_pages_invent_no_colour() -> None:
    """A sixth hue in the stylesheet is a colour nobody approved.

    Only translucent whites, the accent at low opacity and near-black shadows may be
    written as `rgba(...)`, because none of those is a new colour: each one is a token
    already in the palette, thinned.
    """

    css = _strip_comments(_text(AUTH_CSS))
    approved = {value.lower() for value in PALETTE.values()}
    for found in re.findall(r"#[0-9a-fA-F]{3,8}\b", css):
        assert found.lower() in approved, f"{found} is not in the approved palette"

    for red, green, blue in re.findall(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", css):
        triple = (int(red), int(green), int(blue))
        assert triple in {
            (255, 255, 255),  # surface, thinned, for cards on the near-black panel
            (203, 250, 77),  # apple, thinned, for the focus glow and the brand frame
            (43, 46, 53),  # ink, thinned, for a shadow
            (141, 48, 41),  # danger, thinned, for the glow on a field at fault
        }, f"rgba{triple} is not a thinned brand colour"


def test_only_the_auth_stylesheet_lays_out_the_auth_pages() -> None:
    """No other stylesheet may position this card, and one silently did.

    `hilalmarkets.css` is loaded by these pages *before* `hilalmarkets-auth.css`, and it
    still held the previous design: `.auth-shell{...;grid-template-columns:1fr 1fr}`, a
    dark emerald `.auth-brand` column, two floating glass cards, a two-column name grid.

    Only one of those rules had visible markup left to match, and it was the damaging
    one. `.hilal-auth-page .auth-shell` is the more specific selector, so it wins every
    property it *declares* — but it centres with `place-items` and never sets
    `grid-template-columns`, so nothing overrode the two columns. The shell kept them,
    the single card dropped into the first, and every one of these pages drew its form
    against the left edge with half the window empty beside it. Nothing failed; it just
    looked wrong, on all six pages, for as long as the dead rule sat there.

    The whole block is gone. This asserts it stays gone, because a stylesheet that used
    to own a design is exactly where the next stale rule comes from.
    """

    shared = _strip_comments(_text(SHARED_CSS))
    for selector in (
        ".auth-shell",
        ".auth-brand",
        ".auth-visual",
        ".auth-card-float",
        ".auth-form-wrap",
        ".auth-name-grid",
        ".auth-divider",
        ".auth-form",
    ):
        assert selector not in shared, (
            f"{selector} is styled in hilalmarkets.css again; "
            "hilalmarkets-auth.css is the only owner of these pages"
        )

    # And the owner centres the card rather than placing it in a column of a wider grid.
    auth = _strip_comments(_text(AUTH_CSS))
    shell = re.search(r"\.hilal-auth-page \.auth-shell\s*\{([^}]*)\}", auth)
    assert shell is not None, "the shell rule is gone"
    assert "place-items: center" in shell.group(1)
    assert "grid-template-columns" not in shell.group(1), (
        "the shell holds one centred card, not a row of columns"
    )


def test_the_palette_is_declared_where_the_product_can_read_it() -> None:
    """Every colour above is a token, not a value typed into a component."""

    brand = _strip_comments(_text(BRAND_CSS))
    for name, value in PALETTE.items():
        assert f"--hm-{name}: {value}" in brand, f"--hm-{name} is not declared as {value}"


def test_the_product_and_the_website_draw_the_same_control_edge() -> None:
    """Two stylesheets, one value — the pattern already used for the focus ring.

    The website's build is separate from the product's, so the number has to appear in
    both files. This is what stops them drifting.
    """

    site = ROOT / "Hilal-Markets-Website" / "src" / "index.css"
    if not site.exists():  # pragma: no cover - the website is a separate checkout
        pytest.skip("the website source is not present")
    assert f"--color-control: {PALETTE['control-line']}" in _text(site)


# ---------------------------------------------------------------------------
# The password rule: one owner, two readers.
# ---------------------------------------------------------------------------


def test_the_server_checks_exactly_the_rules_the_page_shows() -> None:
    shown = [rule["key"] for rule in browser_password_rules()]
    assert shown == [rule.key for rule in PASSWORD_RULES]
    assert len(shown) == 4


def test_a_password_no_longer_has_to_contain_a_symbol() -> None:
    """The rule that asked for punctuation is gone, everywhere at once.

    It is checked as the *absence of a behaviour*, not as a missing line in a table:
    a perfectly ordinary letters-and-numbers password must now be accepted by the
    server, and no rule may still be looking for a character outside letters and
    numbers. A leftover copy of that rule anywhere would fail here.
    """

    assert password_validation_error("Halal2026") is None
    assert not any(rule.key == "symbol" for rule in PASSWORD_RULES)
    for rule in PASSWORD_RULES:
        assert rule.check("Halal2026"), f"{rule.key} still refuses a plain password"
        assert "special character" not in rule.failure.lower()
        assert "symbol" not in rule.label.lower()


#: For each rule: a password that breaks that one rule and no other, and the character
#: that fixes it. Adding a fifth rule without adding a line here fails the test below,
#: which is what stops the family from being checked one member short.
BREAKS_ONLY = {
    "length": ("aA7", "aaa"),
    "lowercase": ("AAAAA7", "a"),
    "uppercase": ("aaaaa7", "A"),
    "digit": ("aaaaaA", "7"),
}


def test_the_broken_password_table_covers_every_rule() -> None:
    assert set(BREAKS_ONLY) == {rule.key for rule in PASSWORD_RULES}


@pytest.mark.parametrize("rule", PASSWORD_RULES, ids=lambda rule: rule.key)
def test_breaking_one_rule_is_named_by_that_rule(rule) -> None:
    """A password that satisfies every rule but this one is refused, by name."""

    broken, cure = BREAKS_ONLY[rule.key]
    assert password_validation_error(broken) == rule.failure
    assert password_validation_error(broken + cure) is None


def test_a_good_password_passes_and_an_empty_one_does_not() -> None:
    assert password_validation_error("TraceEdge1!") is None
    assert password_validation_error("") == PASSWORD_RULES[0].failure


def test_the_page_never_writes_the_password_rule_out_by_hand() -> None:
    """It was written twice, in two wordings, and neither matched the five checks."""

    for path in (AUTH_HTML, AUTH_MACROS):
        body = _text(path)
        assert "at least 6 characters" not in body.lower()
        assert "lowercase, capital" not in body.lower()
    assert "auth_password_rules" in _text(AUTH_MACROS)


def test_the_browser_patterns_are_unicode_aware() -> None:
    """`[a-z]` would refuse a password the server had already accepted.

    `str.islower()` is the Unicode *Lowercase* property, not the ASCII range, so an
    ASCII-only pattern in the browser would leave a rule unticked for a password the
    server is perfectly happy with — and the person would never work out why.
    """

    patterns = {rule["key"]: rule["pattern"] for rule in browser_password_rules()}
    assert patterns["lowercase"] == r"\p{Lowercase}"
    assert patterns["uppercase"] == r"\p{Uppercase}"
    assert patterns["digit"] == r"\p{Nd}"
    assert patterns["length"] == r".{6,}"
    assert set(patterns) == {"length", "lowercase", "uppercase", "digit"}


# ---------------------------------------------------------------------------
# The wait before a new code.
# ---------------------------------------------------------------------------


def test_the_resend_wait_has_one_owner() -> None:
    """It was `timedelta(seconds=60)` in two places and a sentence in a template."""

    source = _text(WEB_AUTH)
    assert "seconds=CODE_RESEND_SECONDS" in source
    assert "seconds=60" not in source
    assert "Wait one minute" not in source
    assert CODE_RESEND_SECONDS == 60


def test_the_page_counts_down_the_server_s_own_wait() -> None:
    assert "resendSeconds" in _text(AUTH_HTML)
    assert "auth_code_resend_seconds" in _text(AUTH_HTML)
    assert "config.resendSeconds" in _text(AUTH_JS)


def test_the_page_states_the_real_code_lifetime_and_try_count() -> None:
    """Two numbers a person needs, both read from the settings that enforce them."""

    macros = _text(AUTH_MACROS)
    assert "auth_code_ttl_minutes" in macros
    assert "auth_code_max_attempts" in macros
    assert "auth_code_ttl_minutes" in _text(ROUTER)


# ---------------------------------------------------------------------------
# Every error, in plain words.
# ---------------------------------------------------------------------------

#: Where an error code that can reach one of these pages is raised.
ERROR_SOURCES = (
    SRC / "services" / "web_auth.py",
    SRC / "services" / "dashboard_links.py",
    SRC / "services" / "telegram_account_links.py",
    # The Google door raises its own class. It was added to this list in the same change
    # that added the door: a new way in whose failures nobody scans is exactly how a
    # customer ends up reading "Something went wrong" and having nothing to do about it.
    SRC / "services" / "google_oauth.py",
    SRC / "api" / "routers" / "dashboard.py",
)

#: Codes raised in those files that never travel to a sign-in page.
#:
#: `invalid_name` is caught and re-raised as part of sign-up validation before any
#: redirect; the rest belong to routes that answer with JSON.
NOT_SHOWN_HERE = {"invalid_name"}


#: `raise SomeError("code", ...)`, however the call happens to be wrapped over lines.
#:
#: Only these three types travel to a sign-in page. An earlier version of this pattern
#: also matched any `"code",` followed by a sentence, which swept up four `BillingError`
#: codes that never leave the checkout — a scan that reports work nobody has to do is a
#: scan people learn to ignore.
_RAISED = re.compile(
    r"(?:WebAuthError|DashboardLinkError|TelegramAccountLinkError|GoogleOAuthError)"
    r"\(\s*\n?\s*\"([a-z_]+)\""
)


def _codes_in(path: Path) -> set[str]:
    return set(_RAISED.findall(_text(path)))


def test_every_error_code_has_words_a_person_can_read() -> None:
    """The whole family, not the codes that happened to be listed.

    The template used to end its chain with `error.replace('_', ' ').title()`, so any
    code nobody had thought of arrived on screen as "Smtp Authentication Failed". This
    walks the modules that raise them and insists each one has been answered.
    """

    codes: set[str] = set()
    for path in ERROR_SOURCES:
        codes |= _codes_in(path)
    codes -= NOT_SHOWN_HERE
    assert codes, "no error codes were found; the scan is broken, not the product"

    unanswered = []
    for code in sorted(codes):
        alert = alert_for(
            page="signin",
            message=None,
            error=code,
            ttl_minutes=10,
            links={"signin": "/signin", "signup": "/signup", "support": "mailto:x@y.z"},
        )
        assert alert is not None
        if alert.title == "Something went wrong":
            unanswered.append(code)
    assert not unanswered, f"these codes fall through to the catch-all: {unanswered}"


def test_an_email_failure_never_shows_a_customer_an_operator_instruction() -> None:
    """One of them told the person signing up to edit an environment variable."""

    for code in (
        "email_unavailable",
        "email_adapter_disabled",
        "smtp_required_fields_missing",
        "smtp_delivery_failed",
        "smtp_authentication_failed",
        "smtp_sender_refused",
        "smtp_recipient_refused",
        "smtp_temporary_failure",
    ):
        alert = alert_for(
            page="signup",
            message=None,
            error=code,
            ttl_minutes=10,
            links={"support": "mailto:x@y.z"},
        )
        assert alert is not None
        assert alert.title == "We could not send the email"
        combined = f"{alert.title} {alert.body}".lower()
        for leak in ("smtp", "environment", "restart", "adapter", "=", "_"):
            assert leak not in combined, f"{code} leaks {leak!r} to a customer"


def test_no_error_code_is_ever_printed_raw() -> None:
    for path in (AUTH_HTML, AUTH_MACROS, AUTH_ALERT):
        body = _strip_template_comments(_text(path))
        assert ".title()" not in body
        assert "error.replace" not in body
        assert "message.replace" not in body


def test_a_refusal_offers_the_next_step_where_there_is_one() -> None:
    links = {
        "signin": "/signin?plan_code=pro",
        "signup": "/signup?plan_code=pro",
        "signin_code": "/signin/code?plan_code=pro",
        "support": "mailto:help@example.com",
    }
    expected = {
        "account_exists": "/signin?plan_code=pro",
        "account_not_registered": "/signup?plan_code=pro",
        "invalid_login": "/signin/code?plan_code=pro",
        "account_banned": "mailto:help@example.com",
    }
    for code, href in expected.items():
        alert = alert_for(page="signin", message=None, error=code, ttl_minutes=10, links=links)
        assert alert is not None
        # The plan a person chose has to survive the detour, which is why the link is
        # built by the router and handed in rather than written in the copy table.
        assert alert.action_href == href, code
        assert alert.action_label


def test_a_code_error_offers_a_new_code_rather_than_a_link() -> None:
    for code in ("invalid_code", "code_expired", "code_locked"):
        alert = alert_for(
            page="signin_code", message=None, error=code, ttl_minutes=10, links={}
        )
        assert alert is not None
        assert alert.action_resends is True
        assert alert.action_href == ""


def test_the_code_sent_message_never_promises_more_than_was_sent() -> None:
    """`/signin/code` answers the same way whether or not the account exists.

    So its message may not say a code *was* sent. The other two only reach this message
    once the account is known, and hedging there would be a needless second lie.
    """

    hedged = alert_for(
        page="signin_code", message="code_sent", error=None, ttl_minutes=10, links={}
    )
    assert hedged is not None
    assert "If that email has an account" in hedged.body

    plain = alert_for(
        page="signup_verify", message="code_sent", error=None, ttl_minutes=10, links={}
    )
    assert plain is not None
    assert "If that email has an account" not in plain.body
    assert "10 minutes" in plain.body


def test_nothing_is_said_when_there_is_nothing_to_say() -> None:
    assert (
        alert_for(page="signin", message=None, error=None, ttl_minutes=10, links={}) is None
    )
    assert (
        alert_for(page="signin", message="not_a_message", error=None, ttl_minutes=10, links={})
        is None
    )


# ---------------------------------------------------------------------------
# The journey.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", AUTH_PAGES)
def test_every_page_knows_where_it_is_in_the_journey(page: str) -> None:
    for has_email, code_sent in ((False, False), (True, False), (False, True)):
        copy = page_copy(page, has_email=has_email, code_sent=code_sent)
        assert copy.title
        assert copy.submit
        # `lede` is deliberately empty on the pages whose heading already says the whole
        # thing, so it is not required — but when there is one it must be a real
        # sentence rather than a stray space left behind by an edit.
        assert copy.lede == copy.lede.strip()
        steps = copy.journey.steps
        assert 0 <= copy.journey.current < len(steps)
        assert steps[-1].destination, "a journey ends somewhere, and arriving is not a form"
        assert 1 <= copy.journey.position <= copy.journey.total
        assert copy.journey.total == sum(1 for step in steps if not step.destination)


def test_a_single_step_is_not_announced_as_a_journey() -> None:
    assert page_copy("signin").journey.shows_counter is False
    assert page_copy("signup").journey.shows_counter is True
    assert page_copy("signup_verify").journey.position == 3


def test_signing_up_is_three_steps_in_a_fixed_order() -> None:
    """Your details, then the password, then the code. In that order, counted that way.

    It used to be one screen with five boxes and a five-line checklist on it, which did
    not fit a laptop window: the button a person came to press was under the fold on the
    first page of the product. Splitting it also moves "you already have an account" to
    before anybody invents a password they will never use.

    Step one carries two boxes — a name and an address — rather than one. Both are things
    we cannot go on without, and finding out about either of them after a password has
    been chosen means sending somebody backwards past it.
    """

    steps = [page_copy(page) for page in ("signup", "signup_password", "signup_verify")]
    assert [copy.journey.position for copy in steps] == [1, 2, 3]
    assert all(copy.journey.total == 3 for copy in steps)
    assert all(copy.journey.shows_counter for copy in steps)
    keys = [copy.journey.steps[copy.journey.current].key for copy in steps]
    assert keys == ["details", "password", "confirm"]


@pytest.mark.parametrize("page", ("signin_code", "reset_password"))
def test_a_two_part_page_decides_its_own_state_in_one_place(page: str) -> None:
    """Both forms live behind one address, and the template used to work out which.

    It did it twice, with two slightly different conditions.
    """

    assert page_copy(page).state == "ask"
    assert page_copy(page, code_sent=True).state == "enter"
    assert page_copy(page, has_email=True).state == "enter"
    assert page_copy(page, code_sent=True).journey.position == 2


# ---------------------------------------------------------------------------
# The three promises under the button.
# ---------------------------------------------------------------------------


def test_there_are_exactly_three_promises_and_one_of_them_is_a_boundary() -> None:
    """Two things the product does, one thing it never does.

    The third is not decoration. "Watching, never trading" is the boundary this whole
    product is built on, and the sign-up page is the one screen where every single
    customer reads it. A fourth promise, or a set with no boundary in it, fails here.
    """

    assert len(PRODUCT_PROMISES) == 3
    assert len({promise.key for promise in PRODUCT_PROMISES}) == 3
    joined = " ".join(promise.title for promise in PRODUCT_PROMISES).lower()
    # The boundary, in the title itself. It used to be carried by the explaining sentence
    # underneath ("never places an order and never holds your money"); that sentence is
    # gone, so the promise has to survive in the four words that are left.
    assert "never trading" in joined
    assert "evidence" in joined
    for promise in PRODUCT_PROMISES:
        assert promise.title
        assert len(promise.title.split()) <= 5, promise.title


def test_the_promises_are_written_once_and_only_read_by_the_page() -> None:
    """A promise about screening and about never trading may not have two copies.

    They used to be three blocks of markup inside `auth.html`, which meant no copy rule
    and no sentence-case test could ever see them, and any second page was free to make
    a slightly different promise.
    """

    macros = _text(AUTH_MACROS)
    assert "auth_promises" in macros
    body = _text(AUTH_HTML) + macros
    for promise in PRODUCT_PROMISES:
        assert promise.title not in body, f"{promise.key} is written out in the markup"


def test_every_page_a_person_can_land_on_shows_the_promises() -> None:
    """Including the ones in the middle of signing up. Especially those."""

    html = _text(AUTH_HTML)
    branches = re.findall(r"auth\.page == '([a-z_]+)'", html)
    assert set(branches) == set(AUTH_PAGES)
    assert html.count("{{ promises() }}") >= len(AUTH_PAGES)


# ---------------------------------------------------------------------------
# The Google door.
# ---------------------------------------------------------------------------


def _settings(**overrides):
    from ai_market_monitor.core.config import Settings

    base = {
        "app_secret_key": "x" * 48,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "public_base_url": "https://hilalmarkets.com",
    }
    base.update(overrides)
    return Settings(**base)


def test_the_google_button_is_never_offered_before_it_can_work() -> None:
    """A button that opens a window and then fails is worse than no button.

    One property decides it, and both the page and the two routes read that one
    property. This is the "offered but not runnable" fault this codebase keeps finding,
    written as a rule before it can happen again.
    """

    assert _settings().google_signin_enabled is False
    id_only = _settings(google_oauth_client_id="abc.apps.googleusercontent.com")
    assert id_only.google_signin_enabled is False
    assert _settings(google_oauth_client_secret="shh").google_signin_enabled is False
    assert (
        _settings(
            google_oauth_client_id="abc.apps.googleusercontent.com",
            google_oauth_client_secret="shh",
        ).google_signin_enabled
        is True
    )
    # And the page asks that property, rather than deciding for itself.
    assert "auth_google_enabled" in _text(AUTH_MACROS)
    assert "google_signin_enabled" in _text(ROUTER)


def test_the_address_google_returns_to_is_decided_in_one_place() -> None:
    """Never assembled from the incoming request.

    Google matches the redirect address character for character. Behind Cloudflare the
    request's own scheme and host are not the public ones, so building it from the
    request is the classic way this works in development and fails on the day it is
    deployed.
    """

    assert (
        _settings().google_oauth_redirect_uri
        == "https://hilalmarkets.com/auth/google/callback"
    )
    assert (
        _settings(app_base_url="https://app.hilalmarkets.com").google_oauth_redirect_uri
        == "https://app.hilalmarkets.com/auth/google/callback"
    )
    router = _text(ROUTER)
    assert "settings.google_oauth_redirect_uri" in router
    assert "request.url_for" not in router


def test_google_is_asked_for_the_email_and_the_name_and_nothing_else() -> None:
    from ai_market_monitor.services import google_oauth

    assert google_oauth.GOOGLE_SCOPES == ("openid", "email", "profile")


@pytest.mark.parametrize(
    "code",
    [
        "google_cancelled",
        "google_unavailable",
        "google_disabled",
        "google_link_expired",
        "google_email_unverified",
        "google_email_missing",
    ],
)
def test_every_google_failure_is_answered_in_plain_words(code: str) -> None:
    """No OAuth vocabulary reaches a customer. They cannot act on any of it."""

    alert = alert_for(
        page="signin",
        message=None,
        error=code,
        ttl_minutes=10,
        links={"signup": "/signup", "support": "mailto:x@y.z"},
    )
    assert alert is not None
    assert alert.title != "Something went wrong"
    combined = f"{alert.title} {alert.body}".lower()
    for jargon in ("oauth", "token", "redirect", "state", "scope", "client id", "http"):
        assert jargon not in combined, f"{code} leaks {jargon!r} to a customer"


def test_a_google_account_is_made_with_no_password_at_all() -> None:
    """Google proved the address; there is nothing left to prove, and nothing to type.

    `verify_password` refuses a null hash, so an account made this way cannot be opened
    with any password until its owner sets one through "I forgot my password". The
    Google door therefore cannot weaken the password door.
    """

    from ai_market_monitor.core.security import verify_password

    source = _text(WEB_AUTH)
    assert "async def signin_or_signup_with_google" in source
    assert "password_hash=None" in source
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False


def test_the_google_door_refuses_an_address_google_has_not_confirmed() -> None:
    """Without this, anyone who could get a token for an unconfirmed address would be
    handed the matching Hilal Markets account."""

    from ai_market_monitor.services.google_oauth import (
        GoogleOAuthError,
        GoogleOAuthService,
    )

    service = GoogleOAuthService(
        _settings(
            google_oauth_client_id="abc.apps.googleusercontent.com",
            google_oauth_client_secret="shh",
        )
    )
    claims = {
        "sub": "1",
        "email": "person@example.com",
        "given_name": "Sara",
        "family_name": "Ahmed",
    }
    for unconfirmed in (None, False, "false", "maybe", 0):
        with pytest.raises(GoogleOAuthError) as raised:
            service._profile_from({**claims, "email_verified": unconfirmed})
        assert raised.value.code == "google_email_unverified"

    for confirmed in (True, "true", "True"):
        profile = service._profile_from({**claims, "email_verified": confirmed})
        assert profile.email == "person@example.com"
        assert (profile.first_name, profile.last_name) == ("Sara", "Ahmed")


def test_a_google_account_with_one_word_name_still_has_a_name() -> None:
    """Reading only `given_name` would have left those people greeted by nobody."""

    from ai_market_monitor.services.google_oauth import GoogleOAuthService

    service = GoogleOAuthService(
        _settings(
            google_oauth_client_id="abc.apps.googleusercontent.com",
            google_oauth_client_secret="shh",
        )
    )
    profile = service._profile_from(
        {"sub": "1", "email": "a@b.com", "email_verified": True, "name": "Amina"}
    )
    assert profile.first_name == "Amina"
    assert profile.last_name == ""


def test_the_popup_only_ever_accepts_a_message_from_this_site() -> None:
    """`message` is a public event. Any framed page can fire one."""

    js = _text(AUTH_JS)
    assert "event.origin !== window.location.origin" in js
    assert 'data.source !== "hilal-markets-google"' in js
    # And the address it is told to go to is a path here, never a full address, so the
    # popup cannot be talked into sending somebody to another site.
    assert 'target.startsWith("/")' in js


def test_the_google_button_still_works_with_no_script_and_with_popups_blocked() -> None:
    """It is a real link that script upgrades, not a button that script invents."""

    macros = _text(AUTH_MACROS)
    assert 'href="{{ auth_google_href }}"' in macros
    js = _text(AUTH_JS)
    assert "if (!popup) return;" in js


# ---------------------------------------------------------------------------
# Writing.
# ---------------------------------------------------------------------------


def _sentence_case(text: str) -> bool:
    """No Title Case (brand guide, section 11).

    Sentence by sentence: inside one sentence, only the first word is capitalised. A
    full stop starts a new sentence and a new first word — an earlier version of this
    helper read a whole paragraph as one sentence and reported every correct second
    sentence as Title Case.

    The product's own names are capitalised because they are names, so they are taken
    out before the check rather than added to a list of exceptions afterwards.
    """

    names = ("Hilal Markets", "Watchlists", "Watchlist", "Shariah", "Telegram", "Caps Lock")
    stripped = text
    for name in names:
        stripped = stripped.replace(name, "x")
    for sentence in re.split(r"(?<=[.!?])\s+", stripped):
        words = [word for word in re.split(r"[\s·]+", sentence) if word]
        if any(word[:1].isupper() for word in words[1:]):
            return False
    return True


@pytest.mark.parametrize("page", AUTH_PAGES)
def test_headings_and_buttons_are_sentence_case(page: str) -> None:
    """"Sign Up", "Login With One-Time Code" and "Send Verification Code" all were not.

    They were dynamic values, so the site-wide heading rule — which skips a heading
    holding Jinja — never saw them.
    """

    for has_email in (False, True):
        copy = page_copy(page, has_email=has_email)
        assert _sentence_case(copy.title), copy.title
        assert _sentence_case(copy.submit), copy.submit
        assert _sentence_case(copy.lede), copy.lede


def test_the_journey_and_the_three_promises_are_sentence_case() -> None:
    for page in AUTH_PAGES:
        for step in page_copy(page).journey.steps:
            assert _sentence_case(step.title), step.title
            assert _sentence_case(step.hint), step.hint
    for promise in PRODUCT_PROMISES:
        assert _sentence_case(promise.title), promise.title


def test_the_copy_breaks_none_of_the_brand_rules() -> None:
    """The same three checks `core/copy_rules.py` runs over the rest of the product."""

    from ai_market_monitor.core import auth_pages as module

    sources = [
        _text(AUTH_HTML),
        _text(AUTH_MACROS),
        _text(AUTH_ALERT),
        _text(Path(module.__file__)),
    ]
    for body in sources:
        lowered = body.lower()
        for phrase in FORBIDDEN_CLAIM_PHRASES:
            assert phrase not in lowered, phrase
        assert not SHARIA_SPELLING_PATTERN.search(body)
        assert not BRAND_NAME_PATTERN.search(body)


# ---------------------------------------------------------------------------
# Accessibility rules a stylesheet can be held to.
# ---------------------------------------------------------------------------


def test_the_pages_have_exactly_one_first_level_heading() -> None:
    """There were two: the panel's marketing line and the form's title.

    The panel is gone entirely now, so the form's title is the only one left.
    """

    body = _text(AUTH_HTML)
    assert body.count("<h1") == 1


def test_the_second_panel_is_gone_and_has_not_come_back() -> None:
    """One card, in the middle of one screen.

    The near-black column down the left took half a laptop window and pushed the form
    itself under the fold — so on the first page of the product, the button a person
    came to press needed a scroll. Nothing may re-introduce it: not the markup, not the
    stylesheet, and not a second grid column in the shell.
    """

    body = _text(AUTH_HTML)
    for gone in ("auth-aside", "auth-journey-step", "auth-trust"):
        assert gone not in body, f"the second panel is back: {gone}"

    css = _strip_comments(_text(AUTH_CSS))
    for gone in (".auth-aside", ".auth-journey", ".auth-trust"):
        assert gone not in css, f"the second panel is still styled: {gone}"

    shell = re.search(r"\.auth-shell\s*\{(.*?)\}", css, re.DOTALL)
    assert shell, ".auth-shell is not declared"
    assert "grid-template-columns" not in shell.group(1), "the shell has columns again"
    assert "place-items: center" in shell.group(1)
    assert "min-height: 100dvh" in shell.group(1)


def test_the_page_is_built_to_fit_one_screen_without_clipping_anything() -> None:
    """Fits a screen, and still reachable when it cannot.

    The card is sized to fit a laptop window with no scrollbar. But at 200% text size,
    or on a short window with a keyboard open, it will not fit — and then the page must
    *scroll*, never clip. A fixed height with `overflow: hidden` would have put the
    button somewhere no one could reach, which is worse than the scrollbar it removes.
    """

    css = _strip_comments(_text(AUTH_CSS))
    shell = re.search(r"\.auth-shell\s*\{(.*?)\}", css, re.DOTALL)
    assert shell
    assert "height: 100dvh" not in shell.group(1).replace("min-height: 100dvh", "")
    assert "max-height" not in shell.group(1)
    # And the card gives up spacing before the page gives up fitting.
    assert "@media (max-height:" in css


def test_every_target_is_at_least_44_pixels() -> None:
    """WCAG 2.5.8. The old legal row sat at 32px and had no icons to aim at."""

    css = _strip_comments(_text(AUTH_CSS))
    for selector in (
        ".auth-back",
        ".auth-reveal",
        ".auth-legal a",
        ".auth-alert-action",
        ".auth-resend-btn",
        # The Google button and the code door share this shape.
        ".auth-alt",
    ):
        block = re.search(rf"{re.escape(selector)}\s*(?:,[^{{]*)?\{{(.*?)\}}", css, re.DOTALL)
        assert block, f"{selector} is not declared"
        assert "min-height: 44px" in block.group(1), f"{selector} is under 44px tall"
    assert "min-height: 52px" in css, "the text boxes must clear 44px too"
    assert "min-height: 54px" in css, "the main button must clear 44px too"


def test_a_state_override_never_hides_inside_where() -> None:
    """`:where()` counts for nothing, and that is how every error came out green.

    `.hilal-auth-page :where(.dash-flash.error, ...)` lost to the plain rule above it, so
    "Wrong email or password" was painted in the success colours on all five pages. No
    rule that changes a state may be written that way again.
    """

    css = _strip_comments(_text(AUTH_CSS))
    for line in css.splitlines():
        if ":where(" not in line:
            continue
        assert not re.search(r":where\([^)]*(?:error|invalid|is-|data-)", line), line


def test_the_page_says_its_state_in_words_as_well_as_in_colour() -> None:
    """brand guide.md section 10, and WCAG 1.4.1."""

    alert = _text(AUTH_ALERT)
    assert "Problem:" in alert and "Done:" in alert and "Note:" in alert
    macros = _text(AUTH_MACROS)
    assert "auth-rule-state" in macros
    assert "still needed" in macros
    # Where you are in the journey is a sentence — "Step 2 of 3 · Your password" — and
    # not a coloured dot somebody has to interpret.
    html = _text(AUTH_HTML)
    assert "Step {{ auth.journey.position }} of {{ auth.journey.total }}" in html


def test_every_change_of_state_is_announced() -> None:
    macros = _text(AUTH_MACROS)
    assert macros.count('aria-live="polite"') >= 3
    js = _text(AUTH_JS)
    for announcement in ("digits entered", "rules met", "ask for a new code"):
        assert announcement in js


def test_every_control_is_labelled_and_described() -> None:
    macros = _text(AUTH_MACROS)
    assert macros.count("<label") >= 3
    assert 'aria-describedby="{{ id }}-error' in macros
    assert 'aria-describedby="auth-code-error' in macros
    assert 'aria-controls="{{ id }}"' in macros
    assert 'aria-pressed="false"' in macros


def test_there_is_a_way_straight_to_the_form_for_a_keyboard() -> None:
    html = _text(AUTH_HTML)
    assert 'class="auth-skip" href="#auth-card"' in html
    assert 'id="auth-card"' in html


def test_less_motion_really_means_none() -> None:
    css = _strip_comments(_text(AUTH_CSS))
    reduced = css.split("prefers-reduced-motion", 1)
    assert len(reduced) == 2, "the pages have no reduced-motion form"
    tail = reduced[1]
    assert "transition: none" in tail
    assert "animation: none" in tail
    assert "transform: none" in tail


def test_motion_comes_from_the_shared_layer() -> None:
    """No component owns a duration, an easing or the reduced-motion decision."""

    js = _text(AUTH_JS)
    assert 'from "./hm-motion.js"' in js
    assert 'matchMedia("(prefers-reduced-motion' not in js
    assert not re.search(r"duration:\s*[\d.]+", js), "a raw duration was written here"


def test_nothing_loops_for_decoration() -> None:
    """One animation repeats, and only while the form is really being sent."""

    css = _strip_comments(_text(AUTH_CSS))
    repeating = re.findall(r"animation:[^;]*infinite[^;]*;", css)
    assert len(repeating) == 1, repeating
    assert 'data-busy="true"' in css


def test_a_hidden_label_is_never_hidden_from_a_screen_reader() -> None:
    """`display: none` on a label leaves a button with no accessible name at all.

    That is exactly how nine links in the side menu lost their names. The reveal button's
    word is clipped on a small screen, never removed.
    """

    css = _strip_comments(_text(AUTH_CSS))
    block = re.search(r"\.auth-reveal-text\s*\{(.*?)\}", css, re.DOTALL)
    assert block, ".auth-reveal-text is not declared"
    assert "display: none" not in block.group(1)
    assert "clip-path: inset(50%)" in block.group(1)


# ---------------------------------------------------------------------------
# The cookie banner: one owner, four pages.
# ---------------------------------------------------------------------------

#: Every template that includes the shared cookie partial.
COOKIE_TEMPLATES = (
    TEMPLATES / "auth.html",
    TEMPLATES / "hilal" / "base_public.html",
    TEMPLATES / "hilal" / "base_dashboard.html",
    TEMPLATES / "hilal" / "public" / "react_site.html",
)


def test_every_page_that_draws_the_cookie_banner_styles_it() -> None:
    """The sign-in pages included it and loaded neither stylesheet that styled it.

    All four rendered the banner and the settings window as raw blocks under the form —
    including a dialog marked `aria-hidden="true"` that was on screen and reachable with
    Tab.
    """

    for path in COOKIE_TEMPLATES:
        body = _text(path)
        assert "hilal/partials/cookie_banner.html" in body, f"{path.name} lost the partial"
        assert "hilalmarkets-cookie.css" in body, f"{path.name} draws it without styling it"


def test_the_cookie_banner_is_styled_in_exactly_one_place() -> None:
    """It was styled in two, and the two disagreed."""

    owners = []
    for path in sorted(STATIC.glob("*.css")):
        css = _without_print_rules(_strip_comments(_text(path)))
        if re.search(r"\.cookie-(?:banner|modal|category|actions|fixed)\b\s*[,{:.\[]", css):
            owners.append(path.name)
    assert owners == ["hilalmarkets-cookie.css"], owners


def test_the_dashboard_moves_the_banner_without_redrawing_it() -> None:
    """Where it sits is the surface's business; how it looks is not."""

    dashboard = _strip_comments(_text(STATIC / "hilalmarkets-dashboard-v2.css"))
    assert "--hm-cookie-left" in dashboard
    assert "var(--sidebar)" in dashboard
    shared = _strip_comments(_text(COOKIE_CSS))
    assert "var(--hm-cookie-left" in shared


def test_the_consent_switches_are_a_real_size_again() -> None:
    """The rule that sized them was dropped by the browser and nobody noticed.

    Its comment in `hilalmarkets-public.css` opened with `\\*` instead of `/*`, so the
    whole comment was read as part of the selector, the selector was invalid, and the
    rule went in the bin. The switches were the browser's own 13px default in the
    browser's own blue.
    """

    css = _strip_comments(_text(COOKIE_CSS))
    block = re.search(r"\.cookie-category input\s*\{(.*?)\}", css, re.DOTALL)
    assert block, "the switches have no size"
    assert "width: 24px" in block.group(1)
    assert "accent-color: var(--hm-apple-deep" in block.group(1)


def test_no_stylesheet_opens_a_comment_the_wrong_way() -> None:
    """The defect class, not the one instance: a malformed comment eats the rule after it."""

    for path in sorted(STATIC.glob("*.css")):
        for number, line in enumerate(_text(path).splitlines(), start=1):
            assert not line.lstrip().startswith("\\*"), f"{path.name}:{number} {line.strip()}"


# ---------------------------------------------------------------------------
# Shipping.
# ---------------------------------------------------------------------------


def test_every_page_asks_for_the_same_release_of_every_asset() -> None:
    """One cache key for the whole product. A stale half is how a page half-updates."""

    keys = set()
    for path in TEMPLATES.rglob("*.html"):
        keys |= set(re.findall(r"\?v=([0-9A-Za-z\-]+)", _text(path)))
    assert len(keys) == 1, f"the templates ask for {len(keys)} different releases: {sorted(keys)}"


def test_the_browser_gets_the_rules_the_server_uses() -> None:
    """The page's script reads the five rules out of the template, not out of itself."""

    html = _text(AUTH_HTML)
    assert "window.HilalMarketsAuth" in html
    assert "auth_password_rules" in html
    assert "hilalmarkets-auth.js" in html
    assert 'type="module"' in html


def test_the_handles_the_browser_suite_signs_up_with_are_still_there() -> None:
    """Roughly two hundred browser tests walk a person through these pages.

    Their handles are a contract. Renaming one turns an unrelated failure into a mystery.
    """

    html = _text(AUTH_HTML) + _text(AUTH_MACROS)
    for handle in (
        "auth-email",
        "auth-password",
        "auth-repeat-password",
        "auth-submit",
        "auth-google",
        "signup-form",
        "signup-password-form",
        "login-form",
    ):
        # Either quote, because a macro is called with one and writes the other.
        assert f'"{handle}"' in html or f"'{handle}'" in html, handle
    assert 'name="code"' in html
    assert "Verify and create account" in json.dumps(
        [page_copy(page).submit for page in AUTH_PAGES]
    )
    # The two name boxes are gone with the one-screen sign-up. Nothing may quietly put
    # them back on the first step, which is the step that has to stay one question.
    assert "auth-first-name" not in html
    assert "auth-last-name" not in html

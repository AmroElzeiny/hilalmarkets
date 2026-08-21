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

#: A card on the near-black panel: white at 4.5% and 4%, flattened.
JOURNEY_CARD = flatten("#ffffff", 0.045, PALETTE["ink"])
TRUST_CARD = flatten("#ffffff", 0.04, PALETTE["ink"])

#: (what, foreground, background, minimum). Every pair the four pages paint.
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
    ("alternative card title", PALETTE["ink"], PALETTE["surface"], 4.5),
    ("alternative card note", PALETTE["muted"], PALETTE["surface"], 4.5),
    ("alternative card mark", PALETTE["apple-deep"], PALETTE["apple-soft"], 4.5),
    # On the near-black panel.
    ("panel headline", PALETTE["surface"], PALETTE["ink"], 4.5),
    ("panel headline accent", PALETTE["apple"], PALETTE["ink"], 4.5),
    ("back link", PALETTE["hairline"], PALETTE["ink"], 4.5),
    ("step title", PALETTE["surface"], JOURNEY_CARD, 4.5),
    ("step hint", PALETTE["on-ink-soft"], JOURNEY_CARD, 4.5),
    ("step state word", PALETTE["on-ink-soft"], JOURNEY_CARD, 4.5),
    ("current step state word", PALETTE["ink"], PALETTE["apple"], 4.5),
    ("done step state word", PALETTE["apple"], JOURNEY_CARD, 4.5),
    ("step mark", PALETTE["hairline"], JOURNEY_CARD, 4.5),
    ("current step mark", PALETTE["ink"], PALETTE["apple"], 4.5),
    ("trust title", PALETTE["surface"], TRUST_CARD, 4.5),
    ("trust note", PALETTE["on-ink-soft"], TRUST_CARD, 4.5),
    ("trust mark", PALETTE["ink"], PALETTE["apple"], 4.5),
    ("skip link", PALETTE["surface"], PALETTE["ink"], 4.5),
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
    ("alternative card edge", PALETTE["control-line"], PALETTE["surface"], 3.0),
    ("resend button edge", PALETTE["control-line"], PALETTE["surface"], 3.0),
    ("step mark edge", PALETTE["on-ink-line"], PALETTE["ink"], 3.0),
    ("step connector", PALETTE["on-ink-line"], PALETTE["ink"], 3.0),
    # Not the line colour: measured on the translucent card the chip really sits on it
    # comes out at 2.79:1, because that card is lighter than the panel behind it.
    ("state chip edge", PALETTE["on-ink-soft"], JOURNEY_CARD, 3.0),
    # The shared focus indicator, on every surface it can land on here.
    ("focus ring on white", PALETTE["ink-strong"], PALETTE["surface"], 3.0),
    ("focus ring on the canvas", PALETTE["ink-strong"], PALETTE["canvas"], 3.0),
    ("focus ring on the apple button", PALETTE["ink-strong"], PALETTE["apple"], 3.0),
    ("focus halo on the near-black panel", PALETTE["apple"], PALETTE["ink"], 3.0),
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
    assert len(shown) == 5


#: For each rule: a password that breaks that one rule and no other, and the character
#: that fixes it. Adding a sixth rule without adding a line here fails the test below,
#: which is what stops the family from being checked one member short.
BREAKS_ONLY = {
    "length": ("aA7!", "aaa"),
    "lowercase": ("AAAAA7!", "a"),
    "uppercase": ("aaaaa7!", "A"),
    "digit": ("aaaaaA!", "7"),
    "symbol": ("aaaaaA7", "!"),
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
    assert patterns["symbol"] == r"[^\p{L}\p{N}]"
    assert patterns["length"] == r".{6,}"


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
    r"(?:WebAuthError|DashboardLinkError|TelegramAccountLinkError)\(\s*\n?\s*\"([a-z_]+)\""
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
        assert copy.lede
        assert copy.submit
        steps = copy.journey.steps
        assert 0 <= copy.journey.current < len(steps)
        assert steps[-1].destination, "a journey ends somewhere, and arriving is not a form"
        assert 1 <= copy.journey.position <= copy.journey.total
        assert copy.journey.total == sum(1 for step in steps if not step.destination)


def test_a_single_step_is_not_announced_as_a_journey() -> None:
    assert page_copy("signin").journey.shows_counter is False
    assert page_copy("signup").journey.shows_counter is True
    assert page_copy("signup_verify").journey.position == 2


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


def test_the_journey_and_the_trust_list_are_sentence_case() -> None:
    for page in AUTH_PAGES:
        for step in page_copy(page).journey.steps:
            assert _sentence_case(step.title), step.title
            assert _sentence_case(step.hint), step.hint


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

    The panel's line is a paragraph now. It was never the page's subject.
    """

    body = _text(AUTH_HTML)
    assert body.count("<h1") == 1
    assert "auth-aside-line" in body


def test_every_target_is_at_least_44_pixels() -> None:
    """WCAG 2.5.8. The old legal row sat at 32px and had no icons to aim at."""

    css = _strip_comments(_text(AUTH_CSS))
    for selector in (
        ".auth-back",
        ".auth-reveal",
        ".auth-legal a",
        ".auth-alert-action",
        ".auth-resend-btn",
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
    html = _text(AUTH_HTML)
    for word in ("Done", "You are here", "Next"):
        assert word in html


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


def test_there_is_a_way_past_the_panel_for_a_keyboard() -> None:
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
        "auth-first-name",
        "auth-last-name",
        "auth-email",
        "auth-password",
        "auth-repeat-password",
        "auth-submit",
        "signup-form",
        "login-form",
    ):
        # Either quote, because a macro is called with one and writes the other.
        assert f'"{handle}"' in html or f"'{handle}'" in html, handle
    assert 'name="code"' in html
    assert "Verify and create account" in json.dumps(
        [page_copy(page).submit for page in AUTH_PAGES]
    )

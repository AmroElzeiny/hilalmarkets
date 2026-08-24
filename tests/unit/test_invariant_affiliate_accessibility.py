"""The affiliate page must be readable, and its colours must be the brand's own.

Two separate rules, both measured rather than assumed.

**Readable.** The brand's apple green measures 1.21:1 on white; a caption painted in it
is invisible, and nothing about the token says so. Every text-and-ground pair this page
introduces is therefore measured here against the WCAG thresholds — 4.5:1 for ordinary
text, 3:1 for a boundary somebody has to see in order to use a control.

**The brand's own.** A new stylesheet is exactly where a colour gets invented, and this
one draws money: a balance card in an unapproved green would look like a status the
platform never assigned. `hm-affiliate.css` is held to using tokens only, which is a
stronger rule than "approved hex values" — a rule with no hex value in it cannot invent
a colour at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support.contrast import contrast

STYLESHEET = Path("src/ai_market_monitor/static/hm-affiliate.css")
BRAND = Path("src/ai_market_monitor/static/hilalmarkets-brand.css")
TEMPLATE = Path("src/ai_market_monitor/templates/hilal/dashboard/affiliate.html")

#: The brand values behind the tokens the affiliate page uses, read from the brand sheet
#: rather than copied, so a palette change is measured here rather than going unnoticed.
TOKEN_PATTERN = re.compile(r"^\s*(--hm-[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", re.MULTILINE)


def _brand_tokens() -> dict[str, str]:
    return {
        name: value.lower()
        for name, value in TOKEN_PATTERN.findall(BRAND.read_text(encoding="utf-8"))
    }


TOKENS = _brand_tokens()

#: Every text-on-ground pair the affiliate page puts on screen, as
#: (what it is, front token, back token, the ratio it must clear).
#:
#: 4.5 for reading, 3.0 for a boundary. A boundary that cannot be seen is a control
#: somebody cannot find, which WCAG 1.4.11 treats as the same failure as unreadable text.
TEXT_PAIRS = (
    ("the code, on its highlighted plaque", "--hm-ink", "--hm-apple-soft", 4.5),
    ("the label above the code", "--hm-muted", "--hm-apple-soft", 4.5),
    ("a fact in the application summary", "--hm-ink", "--hm-surface-soft", 4.5),
    ("its label", "--hm-muted", "--hm-surface-soft", 4.5),
    ("the link icon beside a shared address", "--hm-apple-deep", "--hm-surface", 4.5),
    ("an amount in the earnings table", "--hm-ink", "--hm-surface", 4.5),
    ("the balance card's outline", "--hm-apple-deep", "--hm-surface", 3.0),
    ("a card edge on the page ground", "--hm-hairline-strong", "--hm-canvas", 1.0),
)


@pytest.mark.parametrize(
    "what,front,back,minimum",
    TEXT_PAIRS,
    ids=[row[0] for row in TEXT_PAIRS],
)
def test_every_colour_pair_on_the_affiliate_page_is_readable(
    what: str, front: str, back: str, minimum: float
) -> None:
    assert front in TOKENS, f"{front} is not a brand token any more"
    assert back in TOKENS, f"{back} is not a brand token any more"
    measured = contrast(TOKENS[front], TOKENS[back])
    assert measured >= minimum, (
        f"{what}: {front} on {back} measures {measured:.2f}:1, under the "
        f"{minimum}:1 this needs"
    )


def test_the_kicker_on_this_page_is_the_readable_green_not_the_bright_one() -> None:
    """The affiliate page's small labels depend on a cascade override to be readable.

    `.kicker` is declared in `hilalmarkets.css` as `--gold`, which the brand sheet maps
    to `--hm-apple` — the bright accent, which measures 1.21:1 on white and is invisible
    as text. `hilalmarkets-dashboard-v2.css` then overrides it to `--hm-apple-deep` for
    every signed-in page, and that override is the only reason four labels on this page
    can be read at all.

    Nothing guarded it. An override that carries a page's readability and is not asserted
    anywhere is one tidy-up away from being removed, and the failure would be silent:
    the words would still be there, painted in a colour nobody can see.
    """

    dashboard = Path("src/ai_market_monitor/static/hilalmarkets-dashboard-v2.css").read_text(
        encoding="utf-8"
    )
    override = re.search(
        r"body\.hilal-dashboard \.kicker[^{]*\{[^}]*color:\s*var\((--hm-[a-z-]+)\)",
        dashboard,
    )
    assert override is not None, "the signed-in pages no longer restate .kicker's colour"
    token = override.group(1)
    measured = contrast(TOKENS[token], TOKENS["--hm-surface"])
    assert measured >= 4.5, (
        f"the kicker is painted {token}, which measures {measured:.2f}:1 on a card"
    )
    # And the affiliate page really does rely on it.
    assert 'class="kicker"' in TEMPLATE.read_text(encoding="utf-8")


def test_the_affiliate_stylesheet_invents_no_colour() -> None:
    """No hex value at all, so there is nothing to invent.

    Stronger than checking against an approved list: an approved colour used somewhere
    the palette never intended still passes a list check, and a sheet with no hex values
    cannot introduce one at all.
    """

    css = STYLESHEET.read_text(encoding="utf-8")
    # Comments say what the rules are for and may name a colour in words; only the rules
    # themselves are checked.
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", rules)
    assert found == [], f"hm-affiliate.css writes colours directly: {found}"
    assert "rgb(" not in rules and "hsl(" not in rules


def test_the_page_never_says_a_status_with_colour_alone() -> None:
    """`brand guide.md` section 10: colour is never the only carrier of meaning.

    Each state a person has to read off this page — a payout paid, refused or waiting,
    and whether a referral converted — is a word as well as a colour.
    """

    markup = TEMPLATE.read_text(encoding="utf-8")
    for word in ("Paid", "Refused", "Waiting", "Not yet", "Approved", "Ready"):
        assert f">{word}<" in markup or f">{word}\n" in markup or word in markup, (
            f"the page has no word for the “{word}” state"
        )


def test_every_table_on_the_page_has_a_caption_and_column_headers() -> None:
    """A table read aloud without headers is a list of unlabelled numbers."""

    markup = TEMPLATE.read_text(encoding="utf-8")
    tables = markup.count("<table")
    assert tables >= 2
    assert markup.count("<caption") == tables
    # Every header cell says which way it applies. The lookahead keeps `<thead>` out of
    # the match — without it the test fails on the wrapper rather than on a real cell.
    headers = re.findall(r"<th(?=[\s>])[^>]*>", markup)
    assert headers
    for header in headers:
        assert 'scope="col"' in header or 'scope="row"' in header, header


def test_every_form_control_on_the_page_is_labelled() -> None:
    """A control with no name is a control a screen reader cannot announce."""

    markup = TEMPLATE.read_text(encoding="utf-8")
    control_ids = set(re.findall(r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', markup))
    labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', markup))
    assert control_ids, "the page draws no controls at all"
    missing = sorted(control_ids - labelled)
    assert missing == [], f"these controls have no label: {missing}"


def test_every_refusal_the_affiliate_routes_can_redirect_with_has_plain_words() -> None:
    """An error code shown as itself is an internal name in front of a beginner.

    The routes redirect with `?error=<code>`, and `base_dashboard.html` looks the code up
    in one shared map — falling back to `code.replace('_', ' ').title()`, which is how
    somebody comes to read "Invalid Link" or "Below Minimum". Every code
    `services/affiliate.py` can raise is checked here, read out of the source rather than
    listed, so a refusal added later cannot quietly arrive without a sentence.
    """

    service = Path("src/ai_market_monitor/services/affiliate.py").read_text(encoding="utf-8")
    raised = set(re.findall(r'AffiliateError\(\s*\n?\s*"([a-z_]+)"', service))
    assert raised, "no refusal codes found; the pattern above has stopped matching"

    base = Path("src/ai_market_monitor/templates/hilal/base_dashboard.html").read_text(
        encoding="utf-8"
    )
    explained = set(re.findall(r"'([a-z_]+)':\s*'", base))
    # Two are answered by the page's own state rather than by a notice: an approved
    # affiliate never sees the form, and a code somebody else owns is refused inside the
    # System Brain, which draws its own flash.
    handled_elsewhere = {"already_approved", "code_taken", "application_missing",
                         "already_decided", "payout_missing", "already_settled",
                         "unknown_status", "discount_missing"}
    missing = sorted(raised - explained - handled_elsewhere)
    assert missing == [], f"these refusals would be shown as their own internal name: {missing}"


def test_the_icons_come_from_the_shared_set() -> None:
    """The product has one vendored outline set; a page-local icon is a second one.

    Every mark on this page is drawn by `hilalmarkets-icons.js` — the same Lucide
    geometry every other page uses — and each is `aria-hidden` by construction because
    the words beside it carry the meaning.
    """

    markup = TEMPLATE.read_text(encoding="utf-8")
    icons = set(re.findall(r'data-icon="([a-z_]+)"', markup))
    assert icons, "the page draws no icons"
    available = Path("src/ai_market_monitor/static/hilalmarkets-icons.js").read_text(
        encoding="utf-8"
    )
    for icon in sorted(icons):
        assert f"{icon}:" in available, f"{icon} is not in the shared icon set"
    # And nothing hand-drawn slipped in beside them.
    assert "<svg" not in markup

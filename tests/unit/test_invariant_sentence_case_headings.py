"""Interface headings are written in sentence case.

`brand guide.md` section 11: "Use sentence case. Do not use Title Case where every main
word is capitalised. Do not use ALL CAPS for headings." Three of the guide's rules are
already enforced by `core/copy_rules.py` rather than left to review; this is the fourth,
kept separate because it needs to know the product's own proper nouns.

The audit of `/dashboard` found one Title Case heading. Looking for the rest of the
family found seven more, on the strategy pages, the builder and System Brain. This test
is why an eighth cannot be added quietly.

A **proper noun is not Title Case.** "Halal Assets" and "Shariah Evidence Passport" are
the names of things in this product, and capitalising a name is correct. So the check
needs the list of names, which is `PRODUCT_NAMES` below — the one place that says what
this product calls its own features.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "ai_market_monitor" / "templates"
#: The other half of the public site. Half of these pages are Jinja and half are React,
#: and the rule was only ever checked on the Jinja half — so a Title Case heading on the
#: landing page, the contact form or either legal document was never reported.
REACT_PAGES = ROOT / "Hilal-Markets-Website" / "src"

#: What this product calls its own things. Capitalised because they are names.
#:
#: Add a name here only when it really is one. Adding a phrase to silence this test is
#: how the rule stops meaning anything.
PRODUCT_NAMES = (
    "Hilal Markets",
    "Halal Assets",
    "Halal Asset",
    "Shariah Evidence Passport",
    "Evidence Passport",
    "Asset Passport",
    "Strategy Guard",
    "Trading Assistant",
    "System Brain",
    "Watchlist",
    "Watchlists",
    "Monitor",
    "Passport",
    "Shariah",
    "Telegram",
    "WhatsApp",
    "Google",
    "Consent Mode",
    # The published documents, by their own names. These are what `core/site_content.py`
    # calls them in the footer, the page titles and the sitemap, so a heading that says
    # anything else would be naming a different document.
    "Privacy Policy",
    "Terms of Use",
    "Cookie Policy",
    "Risk Disclosure",
    "Binance",
    "Bybit",
    "Solana",
    "Bitcoin",
    "Ethereum",
    "Hilal",
)

#: Anything that is not really a word: punctuation, a number, a section marker.
_SKIP = re.compile(r"^[^A-Za-z]*$")

#: A numbered section — "2. Why", "6. Sources". The number is not the sentence, so the
#: word after it is the first word and is capitalised in sentence case like any other.
_NUMBERED = re.compile(r"^\s*\d+[.)]\s*")

#: A heading whose text is static. Anything with Jinja in it is a value, not a heading
#: this file wrote, and its wording belongs to whatever produced it.
_HEADING = re.compile(r"<h([1-4])\b[^>]*>([^<>{}]+)</h\1>")


#: A React heading with plain text inside it. A heading holding `{value}` is a value,
#: not wording this file chose, so it is skipped for the same reason a Jinja one is.
_JSX_HEADING = re.compile(r"<h([1-4])\b[^>]*>\s*([^<>{}]+?)\s*</h\1>")

#: Section titles declared as data rather than written as markup. Both legal documents
#: build their headings this way, so a rule that only read JSX would read neither.
_DECLARED_TITLE = re.compile(r"^\s*title: '([^']{3,})',$", re.M)


def _headings() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for match in _HEADING.finditer(path.read_text(encoding="utf-8")):
            text = " ".join(match.group(2).split())
            if len(text) < 3:
                continue
            found.append((str(path.relative_to(TEMPLATES)).replace("\\", "/"), text))
    for path in sorted(REACT_PAGES.rglob("*.tsx")):
        # `imports/` is generated from Figma; its text is the prototype's, and a rule
        # enforced on generated output is enforced on the generator instead.
        if "imports" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        name = str(path.relative_to(REACT_PAGES)).replace("\\", "/")
        for match in _JSX_HEADING.finditer(source):
            text = " ".join(match.group(2).split())
            if len(text) >= 3:
                found.append((name, text))
        for match in _DECLARED_TITLE.finditer(source):
            found.append((name, " ".join(match.group(1).split())))
    return found


def _without_names(text: str) -> str:
    """The heading with every product name taken out, longest name first."""

    for name in sorted(PRODUCT_NAMES, key=len, reverse=True):
        text = text.replace(name, " ")
    return text


def test_there_are_headings_to_check() -> None:
    """The scan finds headings at all, so a silent zero can never be a pass."""

    assert len(_headings()) >= 40


@pytest.mark.parametrize("template,text", _headings())
def test_a_heading_is_not_title_case(template: str, text: str) -> None:
    opening = _NUMBERED.sub("", text)
    words = [word for word in _without_names(opening).split() if not _SKIP.match(word)]
    if not words:
        return
    # The first word of a sentence is capitalised; that is sentence case, not Title Case.
    rest = words[1:] if opening.startswith(words[0]) else words
    capitalised = [word for word in rest if word[:1].isupper()]
    assert not (len(rest) >= 1 and len(capitalised) == len(rest)), (
        f"{template} has a Title Case heading: {text!r}. `brand guide.md` section 11 "
        "asks for sentence case. If a capitalised phrase here is the name of something "
        "in the product, add the name to PRODUCT_NAMES instead of changing the rule."
    )


@pytest.mark.parametrize("template,text", _headings())
def test_a_heading_is_not_all_caps(template: str, text: str) -> None:
    letters = [character for character in text if character.isalpha()]
    assert not (len(letters) > 3 and all(character.isupper() for character in letters)), (
        f"{template} has an ALL CAPS heading: {text!r}."
    )

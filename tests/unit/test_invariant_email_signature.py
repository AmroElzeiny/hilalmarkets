"""The signature somebody pastes into their mail client is held to the email rules.

An email a person writes by hand is still a Hilal Markets email. Until now it was the
one piece of the product that nothing checked, and it had drifted exactly the way an
unchecked copy always does:

* **The picture in the logo's place was not the logo.** It was a near-black rounded
  square with the symbol knocked out of it and a green tile added in the corner — a
  drawing that exists nowhere else in the brand. `brand guide.md` section 5 lists the
  permitted forms, and a tile is not one of them; section 4 says not to recreate the
  artwork when an official asset exists, and one does.
* **There were four pictures.** The other three were pale circles standing in for
  "website", "email" and "guide". Outlook blocks pictures by default, so the ordinary
  case was four empty boxes and a signature carrying no brand at all.
* **The colours and typefaces were typed out by hand**, beside — not from —
  `email_branding`, which is the module that decides what every sent email looks like.

Every rule below is written about the **class**, not about those three faults. Each
colour is checked, each typeface is checked, each cell is checked, and the whole block is
compared against what `email_branding` renders — so a hand edit to the file, or a fifth
picture, or a sixth colour, fails here rather than in somebody's inbox.
"""

from __future__ import annotations

import base64
import html
import re
import struct
from pathlib import Path

import pytest

from ai_market_monitor.core.copy_rules import scan_text
from ai_market_monitor.services.email_branding import (
    APPLE,
    APPLE_DEEP,
    APPLE_SOFT,
    BODY_FONT,
    CANVAS,
    COPY,
    DISPLAY_FONT,
    EMAIL_LOGO_HEIGHT,
    EMAIL_LOGO_WIDTH,
    HAIRLINE,
    HAIRLINE_STRONG,
    INK,
    INK_RAISED,
    INK_STRONG,
    SIGNATURE_CONTACTS,
    SIGNATURE_DISCLAIMER,
    SIGNATURE_LOGO_PATH,
    SIGNATURE_WIDTH,
    SURFACE,
    SURFACE_SOFT,
    signature_block,
)
from tests.support.contrast import contrast

ROOT = Path(__file__).resolve().parents[2]
PAGE_FILE = ROOT / "email signature.html"
STATIC = ROOT / "src" / "ai_market_monitor" / "static"
LOGO_FILE = STATIC / SIGNATURE_LOGO_PATH.removeprefix("/static/")
SITE_LOGO = STATIC / "hilal-markets-logo.svg"

BUILDER = ".venv/Scripts/python scripts/build_email_signature.py"

PAGE = PAGE_FILE.read_text(encoding="utf-8")

#: The pictures the old signature carried. The first one is the invented tile; the other
#: three are the icons that stood beside it. None of them may come back, under any name.
RETIRED_PICTURES = (
    "hm-signature-mark.png",
    "hm-signature-globe.png",
    "hm-signature-mail.png",
    "hm-signature-arrow.png",
)

#: Every colour `email_branding` declares. Read from the module rather than repeated, so
#: this list cannot be the thing that goes stale.
PALETTE = frozenset(
    {
        APPLE,
        APPLE_DEEP,
        APPLE_SOFT,
        CANVAS,
        COPY,
        HAIRLINE,
        HAIRLINE_STRONG,
        INK,
        INK_RAISED,
        INK_STRONG,
        SURFACE,
        SURFACE_SOFT,
    }
)

#: Anything that would change the logo's shape, colour or edges. `brand guide.md`
#: section 5: no rotating, stretching, compressing, shadows, outlines or gradients — and
#: no turning it back into a tile, which is what `border-radius` did to it before.
RESHAPING = ("border-radius", "clip-path", "transform", "box-shadow", "filter", "opacity")


def _copied_block() -> str:
    """Exactly the markup the Copy button puts on the clipboard.

    The builder writes it on a line of its own inside the box, so the whole block is one
    line and nothing around it can be mistaken for part of the signature.
    """

    inside = PAGE.split('<div id="signature"', 1)[1]
    return inside.split("\n", 1)[1].split("\n", 1)[0]


BLOCK = _copied_block()
COLOURS_USED = sorted({found.lower() for found in re.findall(r"#[0-9a-fA-F]{6}", BLOCK)})
FACES_USED = sorted(set(re.findall(r'font-family:([^;"]+)', BLOCK)))
TEXT_COLOURS = sorted(
    {found.lower() for found in re.findall(r"[^-]color:(#[0-9a-fA-F]{6})", BLOCK)}
)
CELLS = re.findall(r"<td\b[^>]*>", BLOCK)
LINKS = re.findall(r'<a href="([^"]+)"', BLOCK)


def _logo_source() -> str:
    found = re.search(r'<img src="([^"]+)"', BLOCK)
    assert found is not None, "The signature has no picture at all."
    return found.group(1)


def _name_and_role() -> tuple[str, str]:
    """Who the signature is for, read back out of the file.

    Read rather than assumed, so this suite still guards the file after somebody builds
    a signature for a second person.
    """

    written = [
        html.unescape(text)
        for text in re.findall(r">([^<>]+)</div>", BLOCK)
        if text != "&nbsp;"
    ]
    assert len(written) == 2, f"Expected a name and a role, found {written}."
    return written[0], written[1]


# ── The rules have something to check ────────────────────────────────────────


def test_the_block_was_found_and_is_a_whole_table():
    """A rule that matches nothing passes for the wrong reason."""

    assert BLOCK.startswith("<table")
    assert BLOCK.endswith("</table>")
    assert len(COLOURS_USED) >= 4
    assert len(CELLS) >= 8
    assert len(LINKS) == len(SIGNATURE_CONTACTS)


# ── One owner ────────────────────────────────────────────────────────────────


def test_the_signature_is_exactly_what_email_branding_renders():
    """The file is built, never edited.

    This is the whole guard in one line. A colour changed by hand, a size nudged, a
    fourth contact row typed in — any of it, and the file has stopped being what the one
    module that owns the design says a signature looks like.
    """

    name, role = _name_and_role()
    assert signature_block(name=name, role=role, logo_src=_logo_source()) == BLOCK, (
        f"'email signature.html' no longer matches email_branding.signature_block. "
        f"Change the module, then rebuild the file: {BUILDER}"
    )


def test_the_page_is_written_with_plain_line_endings():
    """Windows line endings would make the file depend on which machine built it."""

    assert b"\r\n" not in PAGE_FILE.read_bytes()


# ── One picture, and it is the real logo ─────────────────────────────────────


def test_the_whole_page_carries_exactly_one_picture():
    """Outlook blocks pictures by default. Everything else has to be text."""

    assert PAGE.count("<img") == 1
    assert BLOCK.count("<img") == 1


def test_the_picture_in_the_page_is_the_built_logo_byte_for_byte():
    """Carried inside the file, so the signature needs nothing online to work."""

    source = _logo_source()
    assert source.startswith("data:image/png;base64,")
    carried = base64.b64decode(source.split(",", 1)[1])
    assert carried == LOGO_FILE.read_bytes(), (
        f"The picture inside the page is not {LOGO_FILE.name}. Rebuild it: {BUILDER}"
    )


def test_the_built_logo_is_a_png_at_the_shape_the_site_logo_draws():
    """Not redrawn, and not stretched: the site's own artwork, at the site's own shape."""

    assert LOGO_FILE.is_file(), f"{LOGO_FILE} is missing. Run: {BUILDER}"
    data = LOGO_FILE.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "No mail client draws an SVG."
    width, height = struct.unpack(">II", data[16:24])

    view_box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', SITE_LOGO.read_text("utf-8"))
    assert view_box is not None
    site_ratio = float(view_box.group(1)) / float(view_box.group(2))
    assert abs(site_ratio - width / height) < 0.05, (
        "The signature's picture is a different shape from the site's own logo."
    )
    assert abs(site_ratio - EMAIL_LOGO_WIDTH / EMAIL_LOGO_HEIGHT) < 0.05, (
        "The size the markup declares would squash the logo."
    )
    assert width >= EMAIL_LOGO_WIDTH * 2, "Written too small to stay sharp on a phone."


def test_the_signature_and_the_email_header_show_the_same_logo_at_the_same_size():
    """Two sizes would be two logos. Both come from `email_branding`."""

    assert f'width="{EMAIL_LOGO_WIDTH}" height="{EMAIL_LOGO_HEIGHT}"' in BLOCK
    assert f"width:{EMAIL_LOGO_WIDTH}px;height:{EMAIL_LOGO_HEIGHT}px" in BLOCK


def test_the_logo_is_never_reshaped_into_something_else():
    """The tile that used to sit here was the logo put inside a rounded square.

    Every property below is a way to do that again: round its corners, cut it to a
    shape, turn it, or paint an edge or a shadow around it.
    """

    tag = BLOCK.split("<img", 1)[1].split(">", 1)[0]
    for forbidden in RESHAPING:
        assert forbidden not in tag, (
            f"The signature's logo is reshaped with {forbidden}. brand guide.md "
            "section 5 allows the logo as drawn, and nothing else."
        )


def test_a_blocked_picture_still_says_the_brand_name():
    """With pictures switched off, the signature must still name the company."""

    tag = BLOCK.split("<img", 1)[1].split(">", 1)[0]
    assert 'alt="Hilal Markets"' in tag
    # The alt text inherits the picture's own colour, so it is set on purpose.
    assert f"color:{INK}" in tag
    assert f"font-family:{DISPLAY_FONT}" in tag
    assert contrast(INK, SURFACE) >= 4.5


@pytest.mark.parametrize("name", RETIRED_PICTURES)
def test_the_invented_mark_and_its_icons_never_come_back(name: str):
    assert name not in PAGE
    assert not list(STATIC.rglob(name)), (
        f"{name} is back in the static files. The signature shows the real logo and "
        "nothing else."
    )


def test_the_old_picture_folder_is_gone():
    assert not (STATIC / "email-signature").exists()


# ── One palette, two typefaces ───────────────────────────────────────────────


@pytest.mark.parametrize("colour", COLOURS_USED)
def test_every_colour_comes_from_the_shared_palette(colour: str):
    """A sixth hue is a colour somebody invented rather than one the brand has."""

    assert colour in PALETTE, (
        f"{colour} is not one of the colours email_branding declares. Add it there, or "
        "use one that is already in the palette."
    )


@pytest.mark.parametrize("face", FACES_USED)
def test_every_typeface_comes_from_the_shared_pair(face: str):
    assert face in {BODY_FONT, DISPLAY_FONT}, (
        f"{face} is not the email body or display stack. email_branding owns both."
    )


@pytest.mark.parametrize("colour", TEXT_COLOURS)
def test_every_text_colour_is_readable_on_the_signature_background(colour: str):
    """Measured, not assumed. Apple green on white is 1.21:1 and cannot carry words."""

    assert contrast(colour, SURFACE) >= 4.5


@pytest.mark.parametrize("cell", CELLS, ids=[f"cell-{index}" for index in range(len(CELLS))])
def test_every_cell_paints_its_own_background(cell: str):
    """A signature sits inside somebody else's message, whose colours it cannot choose.

    A client that turns that message dark leaves any cell without a background of its
    own showing dark text on dark.
    """

    assert f"background:{SURFACE}" in cell


def test_the_picture_paints_its_own_background_too():
    """A near-black logo on nothing at all disappears in a dark thread."""

    tag = BLOCK.split("<img", 1)[1].split(">", 1)[0]
    assert f"background:{SURFACE}" in tag


# ── What it says, and where it goes ──────────────────────────────────────────


@pytest.mark.parametrize("contact", SIGNATURE_CONTACTS, ids=lambda contact: contact.label)
def test_every_way_to_reach_us_is_named_in_plain_words(contact):
    assert f'href="{contact.url}"' in BLOCK
    assert f">{html.escape(contact.text)}</a>" in BLOCK
    assert len(contact.label.split()) == 1, "The label column holds one plain word."
    assert contact.label == contact.label.strip()


@pytest.mark.parametrize("url", LINKS)
def test_every_link_goes_somewhere_a_mail_client_can_open(url: str):
    """No relative addresses: a signature is read far away from this website."""

    assert url.startswith(("https://", "mailto:"))


@pytest.mark.parametrize("line", SIGNATURE_DISCLAIMER)
def test_the_boundary_is_written_into_every_signature(line: str):
    assert html.escape(line) in BLOCK


def test_the_signature_says_what_hilal_markets_is_not():
    """The same boundary every sent email carries, in two short lines."""

    words = " ".join(SIGNATURE_DISCLAIMER).lower()
    assert "not a broker" in words
    assert "does not execute trades" in words
    assert "investment advice" in words


def test_the_words_in_the_page_follow_the_brand_copy_rules():
    """The name, the spelling, the forbidden claims, and the encoding traps.

    The picture is taken out first: it is a long run of letters and digits, and it is
    not words anybody reads.
    """

    prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", PAGE)
    violations = scan_text(prose, PAGE_FILE)
    assert violations == (), [item.describe(ROOT) for item in violations]


# ── The block is a signature, not a page ─────────────────────────────────────


def test_the_signature_carries_nothing_a_mail_client_would_throw_away():
    """Class names, stylesheets and scripts do not survive being pasted into a mailbox.

    A style that lives in a class is a style the reader never sees, and nothing warns
    anybody: the signature simply arrives plain.
    """

    for forbidden in ("class=", "<style", "<script", "<html", "<!doctype"):
        assert forbidden not in BLOCK.lower()


def test_no_shape_is_drawn_by_hand_in_the_signature():
    """The retired tile had a green corner cut with `clip-path`, which Word drops."""

    assert "clip-path" not in BLOCK
    assert "polygon(" not in BLOCK


def test_the_block_declares_a_width_outlook_can_use():
    """Word ignores `max-width`, so the attribute has to say the same number."""

    assert f'width="{SIGNATURE_WIDTH}"' in BLOCK
    assert f"max-width:{SIGNATURE_WIDTH}px" in BLOCK


@pytest.mark.parametrize(
    "instruction", ["Copy signature", "See all settings", "Ctrl + V", "Then, in Gmail"]
)
def test_the_instructions_never_end_up_inside_somebody_s_signature(instruction: str):
    assert instruction not in BLOCK

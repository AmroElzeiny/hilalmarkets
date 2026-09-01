"""One design for every email Hilal Markets sends.

An email is a page. It follows `brand guide.md` exactly as a screen does — Geometria for
headings, Onest for body, near-black text, apple green as *one* accent rather than
decoration, sentence case, generous space, no religious or crypto clichés — and it
follows the accessibility rules the product follows: contrast measured, status never
carried by colour alone, and never a wall of text.

What an email cannot do is behave like a page, so this file is written against what mail
clients really support:

* **Tables and inline styles only.** No stylesheet, no class names, no JavaScript. The
  Outlook desktop clients render with Word, which ignores most modern CSS.
* **No web font may be required.** Geometria and Onest are named first and fall back to
  the system stack, so the words are readable even when neither loads — which is most of
  the time.
* **It has to read with images switched off.** The header logo is the only picture in
  the whole design, and it carries ``alt="Hilal Markets"`` styled to look like the
  wordmark, so a blocked image still shows the brand name. Nothing else is an image:
  status is a coloured band *plus* a word *plus* a plain-text mark, so switching images
  off, or being unable to see the colour, costs nothing.
* **Dark mode must not eat the text.** Every cell paints its own background *and* its
  own colour, so a client that recolours the page cannot leave dark text on dark.
* **A phone is the common case.** One column, 16px body text, and a button tall enough
  to hit.

Every template in the product is built from the blocks below, so the code email, the
welcome email, the receipt and every alert look like one product. Adding a template
means composing these — never writing a second frame.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ai_market_monitor.core.config import Settings

EMAIL_SHELL_MARKER = 'data-hm-email-shell="true"'


@dataclass(frozen=True, slots=True)
class BrandedEmail:
    subject: str
    text_body: str
    html_body: str


# ── The palette ──────────────────────────────────────────────────────────────
#
# The same values as `--hm-*` in static/hilalmarkets-brand.css. They are repeated here
# rather than imported because a mail client cannot read a custom property, and a test
# fails if the two ever disagree.

INK = "#2b2e35"
INK_STRONG = "#202329"
COPY = "#50555e"  # 7.5:1 on white — the smallest text in an email still uses this
CANVAS = "#f5f8fb"
SURFACE = "#ffffff"
SURFACE_SOFT = "#fafbfc"
HAIRLINE = "#e1e5ea"
HAIRLINE_STRONG = "#d0d6de"
APPLE = "#cbfa4d"
APPLE_SOFT = "#f1fadf"
APPLE_DEEP = "#55712a"
#: One step up from the header's near-black, for the chip that names the kind of message.
#: Apple green measures 8.8:1 on it, so the words in the chip are readable; the chip
#: itself is only just lighter than the header, which is what makes it read as a label
#: rather than as a second block of colour.
INK_RAISED = "#3a3e47"

#: Status colours. Each is paired with a tinted background and a plain-text mark, never
#: used alone. `brand guide.md` section 10: brand colour and product status stay
#: separate, and a status is never communicated by colour on its own.
TONES: dict[str, tuple[str, str, str]] = {
    # tone: (text colour, background, the mark)
    "success": ("#46551b", APPLE_SOFT, "✓"),
    "warning": ("#8a6316", "#fdf2df", "!"),
    "danger": ("#8d3029", "#fff5f3", "!"),
    "information": ("#1f6e97", "#e2f1f9", "i"),
    "neutral": ("#5b626b", "#eef1f4", "•"),
}

#: Fonts. The brand faces first, then a system stack that exists everywhere.
DISPLAY_FONT = "Geometria, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
BODY_FONT = "Onest, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

#: Body copy. 16px is the smallest comfortable reading size on a phone.
_P = f"margin:0 0 16px;color:{COPY};font-size:16px;line-height:1.7"
_SMALL = f"margin:0;color:{COPY};font-size:13px;line-height:1.65"

# ── The logo in the header ───────────────────────────────────────────────────
#
# The white Hilal Markets logo, the same mark the website and the dashboard show. It is
# a PNG because no mail client draws an SVG, and it is *built from* the site's own
# `hilal-markets-logo.svg` by `scripts/build_email_logo.py` rather than redrawn, so the
# two can never end up being different logos.
#
# It is served from the public site, like every button in every email already is. If a
# reader blocks pictures — which is the normal setting in Outlook — the `alt` text is
# styled to look like the wordmark, so the header still says Hilal Markets in white.

EMAIL_LOGO_PATH = "/static/email/hilal-markets-logo-white.png"
EMAIL_LOGO_WIDTH = 178
EMAIL_LOGO_HEIGHT = 28

#: What kind of message this is, for the chip in the top-right of the header.
#:
#: An email whose body is written somewhere else arrives through ``send_transactional``,
#: which knows only its ``purpose``. One table here turns that into words a reader
#: understands, so those emails say what they are in the same place as every other one.
#: A purpose that is not listed simply gets no chip — never an invented label.
MESSAGE_KIND_BY_PURPOSE: dict[str, str] = {
    "account_access_changed": "Your account",
    "account_notice": "Your account",
    "affiliate_programme": "Affiliate",
    "alert": "Market alert",
    "connection_test": "Test message",
    "operational_alert": "Platform notice",
    "payment_success": "Billing",
    "public_contact_office": "Contact form",
    "public_inquiry_customer": "Support",
    "public_inquiry_office": "Support",
    "support_ticket": "Support ticket",
}


def message_kind_label(purpose: str | None) -> str | None:
    """The words the header chip shows for a delivery purpose, or nothing."""

    return MESSAGE_KIND_BY_PURPOSE.get((purpose or "").strip())


def _e(value: Any) -> str:
    """Escape anything on its way into the markup. Every value passes through this."""

    return html.escape(str(value if value is not None else ""))


def _attr(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


# ── The blocks every template is made of ─────────────────────────────────────


def paragraph(text: str) -> str:
    return f'<p style="{_P}">{_e(text)}</p>'


def greeting_line(text: str) -> str:
    """The line that opens a message written to one person, by name.

    Three templates wrote this bold paragraph out by hand. One owner, because that is
    exactly how the receipt ended up a slightly different shade of the same thing.
    """

    return f'<p style="{_P}"><strong style="color:{INK}">{_e(text)}</strong></p>'


def lead(text: str) -> str:
    """The one sentence under the title that says why this arrived."""

    return f'<p style="margin:0 0 20px;color:{INK};font-size:17px;line-height:1.6">{_e(text)}</p>'


def status_block(*, tone: str, label: str, meaning: str) -> str:
    """What happened, as a colour **and** a word **and** a mark.

    The mark is a plain character, not an image and not an emoji: it survives images
    being switched off, it is not a coloured pictograph that changes meaning between
    platforms, and a screen reader reads the words beside it either way.
    """

    colour, background, mark = TONES.get(tone, TONES["neutral"])
    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:0 0 24px;border-collapse:separate;background:{background};'
        f'border-radius:18px"><tr>'
        f'<td width="8" style="background:{colour};border-radius:18px 0 0 18px"> </td>'
        f'<td width="52" valign="top" style="padding:16px 0 16px 14px;background:{background}">'
        # The mark sits in its own chip rather than beside the words, so it reads as a
        # mark instead of as a stray letter stuck to the front of the sentence.
        f'<span style="display:inline-block;width:26px;height:26px;line-height:26px;'
        f'border-radius:13px;background:{SURFACE};color:{colour};font-size:14px;'
        f'font-weight:700;text-align:center" aria-hidden="true">{mark}</span></td>'
        f'<td valign="top" style="padding:16px 18px 16px 12px;background:{background}">'
        f'<div style="margin:0 0 4px;color:{colour};font-family:{DISPLAY_FONT};'
        f'font-size:16px;font-weight:700;line-height:1.35">{_e(label)}</div>'
        f'<div style="color:{COPY};font-size:15px;line-height:1.6">{_e(meaning)}</div>'
        f"</td></tr></table>"
    )


@dataclass(frozen=True, slots=True)
class EmailLink:
    """A value in a fact table that is somewhere to go rather than something to read.

    A raw address printed in a table is two problems at once: it is unreadable — nobody
    parses `https://…/dashboard/market/sol` to learn it is the Passport — and it is one
    long unbreakable word, which is what pushed these emails sideways on a phone.
    """

    label: str
    url: str


def fact_table(
    rows: Sequence[tuple[str, str | EmailLink]], *, title: str | None = None
) -> str:
    """Facts as label and value, one per line.

    A table rather than a paragraph, because "never a wall of text" is a rule and a
    person scanning an alert is looking for one value, not reading prose.
    """

    if not rows:
        return ""
    heading = (
        f'<div style="margin:0 0 8px;color:{INK};font-family:{DISPLAY_FONT};'
        f'font-size:15px;font-weight:700">{_e(title)}</div>'
        if title
        else ""
    )
    cells = "".join(
        "<tr>"
        f'<td style="padding:11px 0;border-bottom:1px solid {HAIRLINE};color:{COPY};'
        f'font-size:14px;line-height:1.5" valign="top">{_e(label)}</td>'
        f'<td align="right" style="padding:11px 0;border-bottom:1px solid {HAIRLINE};'
        f'color:{INK};font-size:14px;font-weight:700;line-height:1.5;'
        # A long value has to be allowed to break. Without this a single unbreakable
        # value widens the table past the card and the whole email scrolls sideways.
        f'word-break:break-word;overflow-wrap:anywhere" valign="top">'
        f"{_value(value)}</td></tr>"
        for label, value in rows
    )
    return (
        f'<div style="margin:0 0 24px">{heading}'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="border-collapse:collapse;table-layout:fixed;width:100%">{cells}</table></div>'
    )


def _value(value: str | EmailLink) -> str:
    if isinstance(value, EmailLink):
        return (
            f'<a href="{_attr(value.url)}" style="color:{APPLE_DEEP};font-weight:700;'
            f'text-decoration:underline">{_e(value.label)}</a>'
        )
    return _e(value)


def check_list(items: list[tuple[str, bool, str]], *, title: str) -> str:
    """What was true and what was not, each said in words as well as in colour.

    ``items`` is (name, passed, detail). "Met" and "Not met yet" are written out, so the
    list is complete for somebody who cannot see the two colours apart.
    """

    if not items:
        return ""
    rows = []
    for name, passed, detail in items:
        colour, background, mark = TONES["success" if passed else "warning"]
        word = "Met" if passed else "Not met yet"
        rows.append(
            "<tr>"
            f'<td width="30" valign="top" style="padding:9px 10px 9px 0">'
            f'<span style="display:inline-block;width:22px;height:22px;line-height:22px;'
            f'border-radius:11px;background:{background};color:{colour};font-size:13px;'
            f'font-weight:700;text-align:center" aria-hidden="true">{mark}</span></td>'
            f'<td valign="top" style="padding:9px 0;border-bottom:1px solid {HAIRLINE}">'
            f'<div style="color:{INK};font-size:15px;font-weight:700;line-height:1.45">'
            f"{_e(name)}</div>"
            f'<div style="color:{colour};font-size:13px;font-weight:700;line-height:1.5">'
            f"{_e(word)}</div>"
            + (
                f'<div style="color:{COPY};font-size:13px;line-height:1.55">{_e(detail)}</div>'
                if detail
                else ""
            )
            + "</td></tr>"
        )
    return (
        f'<div style="margin:0 0 24px">'
        f'<div style="margin:0 0 6px;color:{INK};font-family:{DISPLAY_FONT};'
        f'font-size:15px;font-weight:700">{_e(title)}</div>'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="border-collapse:collapse">{"".join(rows)}</table></div>'
    )


def button(label: str, url: str, *, secondary: bool = False) -> str:
    """The one clear action. 48px tall, so it is a comfortable target on a phone."""

    background = SURFACE if secondary else APPLE
    border = HAIRLINE_STRONG if secondary else APPLE
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" '
        f'style="margin:0 0 12px"><tr><td style="border-radius:999px;'
        f'background:{background};border:1px solid {border}">'
        f'<a href="{_attr(url)}" style="display:inline-block;padding:14px 26px;'
        f"color:{INK};font-family:{BODY_FONT};font-size:15px;font-weight:700;"
        f'line-height:20px;text-decoration:none">{_e(label)}</a>'
        f"</td></tr></table>"
    )


def code_block(code: str, *, caption: str) -> str:
    """A one-time code, big enough to read off a phone and copy by hand."""

    return (
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:0 0 24px;border-collapse:separate;border:1px solid {HAIRLINE_STRONG};'
        f'border-radius:20px;background:{SURFACE_SOFT}"><tr>'
        f'<td align="center" style="padding:26px 20px;background:{SURFACE_SOFT}">'
        f'<div style="margin:0 0 10px;color:{COPY};font-size:13px;line-height:1.5">'
        f"{_e(caption)}</div>"
        f'<div style="color:{INK_STRONG};font-family:{DISPLAY_FONT};font-size:34px;'
        f'font-weight:700;letter-spacing:.18em;line-height:1.2">{_e(code)}</div>'
        f"</td></tr></table>"
    )


def plain_text_block(text: str) -> str:
    """A message that is already written, laid out in the product's own type.

    Three emails carry a body that was composed as plain text somewhere else — the
    automated support reply, the contact-page copy the office receives, and the support
    ticket. Each of them wrote its own wrapper, and two of the three asked for Arial,
    which is not a Hilal Markets face. So a person who wrote in for help got the one
    email in the product whose words were set in a different typeface from every other.

    One owner, for the same reason every other vocabulary in this codebase has one: the
    three copies had already drifted, and a fourth caller would have drifted further.
    ``white-space:pre-wrap`` keeps the paragraphs the author actually typed instead of
    turning the whole message into one block.
    """

    return (
        f'<div style="margin:0;color:{COPY};font-family:{BODY_FONT};font-size:15px;'
        f'line-height:1.7;white-space:pre-wrap">{_e(text)}</div>'
    )


def link_row(links: Sequence[EmailLink]) -> str:
    """The smaller places to go, under the one clear action.

    A second and third button competes with the first one; a bare line of addresses is
    unreadable. So the extra destinations are named links on one line, and they wrap on
    a phone instead of pushing the card sideways.
    """

    if not links:
        return ""
    separator = f'<span style="color:{HAIRLINE_STRONG}">&nbsp;&nbsp;•&nbsp;&nbsp;</span>'
    rendered = separator.join(
        f'<a href="{_attr(link.url)}" style="color:{APPLE_DEEP};font-weight:700;'
        f'text-decoration:none;white-space:nowrap">{_e(link.label)}</a>'
        for link in links
    )
    return (
        f'<p style="margin:0 0 4px;font-size:14px;line-height:2">{rendered}</p>'
    )


def note(text: str) -> str:
    """A quiet line under the main content. Still 13px and still 7.5:1 on white."""

    return f'<p style="margin:20px 0 0;color:{COPY};font-size:13px;line-height:1.65">{_e(text)}</p>'


def divider() -> str:
    return (
        f'<div style="margin:24px 0;border-top:1px solid {HAIRLINE};'
        f'font-size:0;line-height:0">&nbsp;</div>'
    )


# ── The signature a person pastes into their own mail client ─────────────────
#
# An email somebody writes by hand is still a Hilal Markets email, so it is held to the
# same rules as the ones the product sends: one palette, two typefaces, and **one
# picture** — because Outlook blocks pictures by default and everything a reader needs
# has to survive that.
#
# What was here before broke the picture rule twice. The image in the logo's place was
# not the logo: it was a near-black rounded square with the symbol knocked out of it and
# a green tile added in the corner. That drawing exists nowhere else in the brand, and
# `brand guide.md` section 5 does not list it as a permitted form — it lists the
# horizontal logo, the symbol on its own, and the two monochrome versions. Beside it sat
# three more pictures: pale tinted circles standing in for "website", "email" and
# "guide". With pictures switched off the whole signature was four empty boxes and no
# brand at all.
#
# There is now one picture and it is the real logo, built from the site's own
# `hilal-markets-logo.svg` by `scripts/build_email_logo.py` — the same source, at the
# same size, as the logo in the header of every email the product sends. Nothing is
# redrawn, so the two can never become different logos. Everything else is text: it
# survives blocked pictures, it can be selected and copied, and a screen reader reads it.

#: Where the built picture is served from, and the default `src` for a served signature.
#: The pasteable page passes a `data:` URI instead, so that file needs nothing online.
SIGNATURE_LOGO_PATH = "/static/email/hilal-markets-logo-dark.png"

#: How wide the block is allowed to get. Narrow enough to sit under a short reply
#: without dominating it, wide enough that the disclaimer takes two lines rather than four.
SIGNATURE_WIDTH = 460

#: The label column. "Website" is the longest word in it.
SIGNATURE_LABEL_WIDTH = 72


@dataclass(frozen=True, slots=True)
class SignatureContact:
    """One way to reach Hilal Markets: what it is, what it says, and where it goes."""

    label: str
    text: str
    url: str


#: The same three destinations for everybody, so two people's signatures cannot disagree
#: about the office address. Only the name and the role change from person to person.
SIGNATURE_CONTACTS: tuple[SignatureContact, ...] = (
    SignatureContact(
        "Email", "office@hilalmarkets.com", "mailto:office@hilalmarkets.com"
    ),
    SignatureContact("Website", "hilalmarkets.com", "https://hilalmarkets.com"),
    SignatureContact("Help", "How Hilal Markets works", "https://hilalmarkets.com/help"),
)

#: What the product is, and the boundary it does not cross. The same sentence the footer
#: of every sent email carries, shortened to two lines.
SIGNATURE_DISCLAIMER: tuple[str, ...] = (
    "Screening, evidence and monitoring in line with Islamic principles.",
    "Not a broker. Hilal Markets does not execute trades, hold funds, or give "
    "investment advice.",
)


def _signature_rule() -> str:
    """A hairline that is a filled cell rather than a border.

    The Outlook desktop clients render with Word, which drops a `border-top` on an empty
    `div` often enough that the rule is simply missing. A one-pixel cell with a
    background is the shape every client agrees on.
    """

    return (
        f'<div style="height:1px;background:{HAIRLINE};font-size:0;line-height:0">'
        "&nbsp;</div>"
    )


def signature_logo(src: str) -> str:
    """The one picture, and what a reader sees when the picture is blocked.

    The `alt` text is styled rather than left bare, for the same reason the header's is:
    a blocked picture must still say Hilal Markets, in the brand's own display face,
    inside the box the picture would have filled.

    The image paints its own white background. A signature is pasted into somebody
    else's message, so it has no control over the page around it; in a client that turns
    that page dark, a near-black logo on nothing at all would disappear.
    """

    return (
        f'<img src="{_attr(src)}" alt="Hilal Markets" '
        f'width="{EMAIL_LOGO_WIDTH}" height="{EMAIL_LOGO_HEIGHT}" '
        f'style="display:block;width:{EMAIL_LOGO_WIDTH}px;height:{EMAIL_LOGO_HEIGHT}px;'
        f'border:0;outline:none;text-decoration:none;background:{SURFACE};color:{INK};'
        f'font-family:{DISPLAY_FONT};font-size:19px;font-weight:700;'
        f'letter-spacing:-.02em;line-height:{EMAIL_LOGO_HEIGHT}px">'
    )


def signature_block(*, name: str, role: str, logo_src: str | None = None) -> str:
    """One person's signature: the logo, who they are, where to reach us, the boundary.

    Read top to bottom it goes brand, person, contact, limits — which is the order
    somebody scans a signature in, and the order that puts the smallest type last.

    Every cell paints its own background and its own colour. That is the same rule the
    sent emails follow, and it matters more here: a signature lives inside a thread
    whose colours belong to whoever the reply is going to.
    """

    rows = "".join(
        "<tr>"
        f'<td width="{SIGNATURE_LABEL_WIDTH}" valign="top" '
        f'style="padding:5px 14px 5px 0;background:{SURFACE};color:{COPY};'
        f'font-family:{BODY_FONT};font-size:13px;line-height:1.5">'
        f"{_e(contact.label)}</td>"
        f'<td valign="top" style="padding:5px 0;background:{SURFACE};'
        f'font-family:{BODY_FONT};font-size:14px;line-height:1.5">'
        f'<a href="{_attr(contact.url)}" style="color:{APPLE_DEEP};font-weight:700;'
        f'text-decoration:none">{_e(contact.text)}</a></td>'
        "</tr>"
        for contact in SIGNATURE_CONTACTS
    )
    disclaimer = "<br>".join(_e(line) for line in SIGNATURE_DISCLAIMER)
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        f'width="{SIGNATURE_WIDTH}" style="border-collapse:collapse;width:100%;'
        f'max-width:{SIGNATURE_WIDTH}px;background:{SURFACE}">'
        f'<tr><td style="padding:0 0 20px;background:{SURFACE};line-height:0">'
        f"{signature_logo(logo_src or SIGNATURE_LOGO_PATH)}</td></tr>"
        f'<tr><td style="padding:0;background:{SURFACE}">'
        f'<div style="margin:0;color:{INK_STRONG};font-family:{DISPLAY_FONT};'
        f'font-size:24px;font-weight:500;letter-spacing:-.02em;line-height:1.25">'
        f"{_e(name)}</div>"
        f'<div style="margin:5px 0 0;color:{COPY};font-family:{BODY_FONT};'
        f'font-size:14px;font-weight:700;line-height:1.45">{_e(role)}</div>'
        "</td></tr>"
        f'<tr><td style="padding:16px 0 0;background:{SURFACE}">'
        f"{_signature_rule()}</td></tr>"
        f'<tr><td style="padding:12px 0 0;background:{SURFACE}">'
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        f'style="border-collapse:collapse">{rows}</table></td></tr>'
        f'<tr><td style="padding:16px 0 0;background:{SURFACE}">'
        f"{_signature_rule()}</td></tr>"
        f'<tr><td style="padding:12px 0 0;background:{SURFACE};color:{COPY};'
        f'font-family:{BODY_FONT};font-size:12px;line-height:1.7">{disclaimer}</td></tr>'
        "</table>"
    )


class HilalMarketsEmailRenderer:
    """Render one Hilal Markets frame, and the templates built inside it."""

    def __init__(self, settings: Settings):
        self.settings = settings

    # ── The frame ────────────────────────────────────────────────────────────

    def ensure_shell(
        self,
        *,
        subject: str,
        html_body: str,
        preheader: str | None = None,
        eyebrow: str | None = None,
    ) -> str:
        if EMAIL_SHELL_MARKER in html_body:
            return html_body
        return self.shell(
            title=subject,
            preheader=preheader or subject,
            content_html=html_body,
            eyebrow=eyebrow,
        )

    def logo(self) -> str:
        """The white wordmark, and what a reader sees when pictures are blocked.

        The `alt` text is styled, not left bare. Outlook blocks pictures by default, and
        an unstyled `alt` in a near-black header is near-black text on near-black — the
        header would look empty. Styled, a blocked picture still reads "Hilal Markets"
        in white, in the brand's own display face.
        """

        base_url = str(self.settings.public_base_url).rstrip("/")
        return (
            f'<img src="{_attr(base_url + EMAIL_LOGO_PATH)}" alt="Hilal Markets" '
            f'width="{EMAIL_LOGO_WIDTH}" height="{EMAIL_LOGO_HEIGHT}" '
            f'style="display:block;width:{EMAIL_LOGO_WIDTH}px;'
            f'height:{EMAIL_LOGO_HEIGHT}px;border:0;outline:none;text-decoration:none;'
            f'color:{SURFACE};font-family:{DISPLAY_FONT};font-size:19px;font-weight:700;'
            f'letter-spacing:-.02em;line-height:{EMAIL_LOGO_HEIGHT}px">'
        )

    def kind_chip(self, label: str) -> str:
        """What kind of message this is, in the top-right of the header.

        It replaces a decorative shape that sat there and meant nothing. A reader with
        nine Hilal Markets emails in an inbox can now tell a sign-in code from a market
        alert from a receipt before reading a single word of the message.

        A table rather than a styled `span`, because the Outlook desktop clients render
        with Word and ignore padding on inline elements — the words would touch the edge
        of the chip. Word also ignores the rounded corner, which costs nothing: the chip
        is then a square tag, and still a tag.
        """

        return (
            '<table role="presentation" cellspacing="0" cellpadding="0" '
            'style="border-collapse:separate"><tr>'
            f'<td style="padding:7px 14px;border-radius:999px;background:{INK_RAISED};'
            f'color:{APPLE};font-family:{BODY_FONT};font-size:12px;font-weight:700;'
            f'letter-spacing:.04em;line-height:1;white-space:nowrap">{_e(label)}</td>'
            "</tr></table>"
        )

    def shell(
        self,
        *,
        title: str,
        preheader: str,
        content_html: str,
        eyebrow: str | None = None,
        footer_reason: str | None = None,
    ) -> str:
        """The frame every email shares.

        `eyebrow` names what kind of message this is. It is shown as a chip in the
        top-right of the header, opposite the logo, so somebody can tell a market alert
        from an account notice before reading a word of the body.

        `footer_reason` says *why this arrived and how to stop it*. It is part of the
        frame rather than left to each template, because an email that does not say why
        it arrived is the one thing that turns a useful alert into something a person
        marks as junk.
        """

        base_url = str(self.settings.public_base_url).rstrip("/")
        reason = footer_reason or (
            "You are receiving this because you have a Hilal Markets account. "
            "You can change which messages you receive in your dashboard."
        )
        chip_html = self.kind_chip(eyebrow) if eyebrow else "&nbsp;"
        return (
            "<!doctype html>"
            f'<html lang="en" {EMAIL_SHELL_MARKER}><head>'
            '<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            # Tells a client this design is light. Without it, some clients invert the
            # whole message and put dark text on a dark card.
            '<meta name="color-scheme" content="light">'
            '<meta name="supported-color-schemes" content="light">'
            f"<title>{_e(title)}</title></head>"
            f'<body style="margin:0;padding:0;background:{CANVAS};color:{INK};'
            f'font-family:{BODY_FONT};-webkit-font-smoothing:antialiased">'
            # The preview line the inbox shows next to the subject. Hidden in the body.
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">'
            f"{_e(preheader)}</div>"
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="width:100%;background:{CANVAS};padding:28px 12px">'
            '<tr><td align="center">'
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="width:100%;max-width:600px;border-collapse:separate;overflow:hidden;'
            f'border:1px solid {HAIRLINE};border-radius:24px;background:{SURFACE}">'
            # Header: the logo on the left, what kind of message this is on the right.
            #
            # Both halves are things a reader uses. What used to sit here was the name
            # typed out as text, a strapline nobody needed twice, and a green shape that
            # meant nothing — decoration in the most valuable space in the message.
            f'<tr><td style="padding:24px 30px 30px;background:{INK};color:{SURFACE}">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="border-collapse:collapse"><tr>'
            f'<td valign="middle" style="background:{INK};line-height:0">{self.logo()}</td>'
            f'<td align="right" valign="middle" style="background:{INK};'
            f'padding-left:12px">{chip_html}</td>'
            "</tr></table>"
            f'<div style="margin-top:26px">'
            f'<h1 style="margin:0;color:{SURFACE};font-family:{DISPLAY_FONT};'
            f'font-size:27px;font-weight:500;line-height:1.2">{_e(title)}</h1></div>'
            "</td></tr>"
            f'<tr><td style="padding:30px;background:{SURFACE}">{content_html}</td></tr>'
            f'<tr><td style="padding:22px 30px;border-top:1px solid {HAIRLINE};'
            f'background:{SURFACE_SOFT};color:{COPY};font-size:12px;line-height:1.7">'
            f"{_e(reason)}<br><br>"
            "Hilal Markets supports research, screening and monitoring. It does not "
            "execute trades, hold funds, promise returns, or make personal religious "
            "rulings. Nothing in this email is financial advice.<br>"
            # Who sent this: Hilal Markets, and nothing else.
            #
            # These two lines used to print `SITE_LEGAL_NAME` and `SITE_COMPANY_ADDRESS`
            # straight out of the environment, which put a founder's personal name, an
            # unrelated company and a street address at the bottom of every message the
            # product sends — a sign-in code, a market alert, a receipt. It is the wrong
            # name in front of a customer and it hands out a home address to anybody who
            # ever asked for a one-time code.
            #
            # The two settings still exist and the legal pages on the website still use
            # them, because a Terms page really does have to name a legal entity. An
            # email footer does not: the sender is the product.
            f'<a href="{_attr(base_url)}" style="color:{APPLE_DEEP};font-weight:700;'
            f'text-decoration:none">Hilal Markets</a>'
            "</td></tr></table></td></tr></table></body></html>"
        )

    # ── Templates ────────────────────────────────────────────────────────────

    def auth_code(
        self,
        *,
        recipient: str,
        code: str,
        purpose: str,
        ttl_minutes: int,
    ) -> BrandedEmail:
        """The one-time code. The shortest email the product sends, on purpose.

        One job: show the code. Everything else is under it and quiet, because somebody
        opening this is mid-sign-in with the code already half-typed.
        """

        # The second word of each pair is the header chip: the *kind* of message, in two
        # or three words, never a sentence and never a repeat of the title beside it.
        titles = {
            "login": ("Your sign-in code", "Sign in"),
            "password_reset": ("Reset your password", "Password reset"),
            "signup": ("Confirm your email", "New account"),
            "system_brain": ("Your administrator code", "Administrator"),
        }
        leads = {
            "login": "Use this code to finish signing in.",
            "password_reset": "Use this code to continue resetting your password.",
            "signup": "Use this code to finish creating your Hilal Markets account.",
            "system_brain": "Use this code to complete your protected administrator sign-in.",
        }
        subjects = {
            "login": "Your Hilal Markets sign-in code",
            "password_reset": "Your Hilal Markets password reset code",
            "signup": "Confirm your Hilal Markets email",
            "system_brain": "Your Hilal Markets administrator code",
        }
        title, eyebrow = titles.get(purpose, ("Your verification code", "Security"))
        introduction = leads.get(purpose, "Use this code to continue securely.")
        subject = subjects.get(purpose, "Your Hilal Markets verification code")

        text_body = (
            f"{title}\n\n"
            f"{introduction}\n\n"
            f"Code: {code}\n\n"
            f"It expires in {ttl_minutes} minutes.\n\n"
            "If you did not ask for this code, you can ignore this email. "
            "Nobody can use it without your email address.\n\n"
            "Hilal Markets will never ask you to send this code to anyone."
        )
        content = (
            lead(introduction)
            + code_block(code, caption="Your one-time code")
            + status_block(
                tone="information",
                label=f"This code expires in {ttl_minutes} minutes",
                meaning="After that, ask for a new one. Each code can be used once.",
            )
            + paragraph(
                "If you did not ask for this code, you can ignore this email. "
                "Nobody can use it without your email address."
            )
            + note(
                f"Sent to {_masked_email(recipient)}. Hilal Markets will never ask you "
                "to send this code to anyone, including us."
            )
        )
        return BrandedEmail(
            subject=subject,
            text_body=text_body,
            html_body=self.shell(
                title=title,
                eyebrow=eyebrow,
                preheader=f"Your code expires in {ttl_minutes} minutes.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because this code was requested for your "
                    "email address. If that was not you, no action is needed."
                ),
            ),
        )

    def signup_welcome(self, *, first_name: str, email: str) -> BrandedEmail:
        """Sent once the account exists. Says what to do first, in three steps.

        Not a wall of welcome copy. Somebody who has just signed up wants to know what
        this does and what to press, and nothing else.
        """

        name = (first_name or "").strip()
        greeting = f"Assalamu Alaikum {name}," if name else "Assalamu Alaikum,"
        base_url = str(self.settings.public_base_url).rstrip("/")
        steps = [
            (
                "Look at which coins passed screening",
                "Every coin carries an Evidence Passport: who reviewed it, when, and why.",
            ),
            (
                "Make your first Watchlist",
                "Answer a few plain questions. Hilal Markets then watches the market for you.",
            ),
            (
                "Choose how you want to be told",
                "Email, Telegram, or a notice waiting in the dashboard. You choose which.",
            ),
        ]
        text_body = (
            f"{greeting}\n\n"
            "Your Hilal Markets account is ready.\n\n"
            "Three things to do first:\n"
            + "".join(
                f"{index}. {step}. {detail}\n"
                for index, (step, detail) in enumerate(steps, 1)
            )
            + f"\nOpen your dashboard: {base_url}/dashboard\n\n"
            "Hilal Markets screens, monitors and shows evidence. It never buys or sells "
            "anything for you, and it never tells you what to buy.\n\n"
            "Hilal Markets"
        )
        rows = "".join(
            "<tr>"
            f'<td width="34" valign="top" style="padding:0 12px 16px 0">'
            f'<span style="display:inline-block;width:26px;height:26px;line-height:26px;'
            f'border-radius:13px;background:{APPLE_SOFT};color:{APPLE_DEEP};'
            f'font-family:{DISPLAY_FONT};font-size:13px;font-weight:700;'
            f'text-align:center">{index}</span></td>'
            f'<td valign="top" style="padding:0 0 16px">'
            f'<div style="color:{INK};font-size:16px;font-weight:700;line-height:1.45">'
            f"{_e(name)}</div>"
            f'<div style="color:{COPY};font-size:14px;line-height:1.6">{_e(detail)}</div>'
            "</td></tr>"
            for index, (name, detail) in enumerate(steps, 1)
        )
        content = (
            greeting_line(greeting)
            + lead("Your account is ready. Here is what to do first.")
            + f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:0 0 22px;border-collapse:collapse">{rows}</table>'
            + button("Open your dashboard", f"{base_url}/dashboard")
            + divider()
            + status_block(
                tone="information",
                label="What Hilal Markets does, and does not do",
                meaning=(
                    "It screens coins, watches the market for the conditions you choose, "
                    "and shows the evidence behind every status. It never buys or sells "
                    "anything, and it never tells you what to buy."
                ),
            )
            + note(f"This account is registered to {_masked_email(email)}.")
        )
        return BrandedEmail(
            subject="Your Hilal Markets account is ready",
            text_body=text_body,
            html_body=self.shell(
                title="Your account is ready",
                eyebrow="Welcome",
                preheader="Three things to do first.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because a Hilal Markets account was just "
                    "created with this email address."
                ),
            ),
        )

    # ── The affiliate programme ──────────────────────────────────────────────

    def affiliate_application_received(
        self,
        *,
        first_name: str,
        requested_code: str,
        decision_hours: int,
    ) -> BrandedEmail:
        """The receipt. Its whole job is to say *we have it* and *when you will hear*.

        A form that says "thank you" and then goes quiet is the reason people email
        support two days later. The waiting time is passed in rather than written here,
        so the page and the email can never promise different things.
        """

        greeting = _greeting_for(first_name)
        base_url = str(self.settings.public_base_url).rstrip("/")
        rows: list[tuple[str, str | EmailLink]] = [
            ("Code you asked for", requested_code),
            ("Answer within", f"{decision_hours} hours"),
        ]
        text_body = (
            f"{greeting}\n\n"
            "We have your affiliate application.\n\n"
            + "".join(f"{label}: {value}\n" for label, value in rows)
            + "\nWe read every application by hand. Nothing else is needed from you "
            "right now.\n\n"
            f"Your dashboard: {base_url}/dashboard/affiliate\n\n"
            "Hilal Markets"
        )
        content = (
            greeting_line(greeting)
            + lead("We have your application. Nothing else is needed from you right now.")
            + status_block(
                tone="information",
                label="With us now",
                meaning=(
                    f"A person reads every application. You will hear back within "
                    f"{decision_hours} hours."
                ),
            )
            + fact_table(rows)
            + button("See your application", f"{base_url}/dashboard/affiliate")
            + note(
                "The code and the discount are set by our team when we answer. What you "
                "asked for is a request, not a setting."
            )
        )
        return BrandedEmail(
            subject="We have your Hilal Markets affiliate application",
            text_body=text_body,
            html_body=self.shell(
                title="Application received",
                eyebrow="Affiliate",
                preheader=f"We will answer within {decision_hours} hours.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because you applied to the Hilal Markets "
                    "affiliate programme."
                ),
            ),
        )

    def affiliate_application_approved(
        self,
        *,
        first_name: str,
        discount_code: str,
        discount_percent: str,
        commission_percent: str,
        referral_url: str,
        minimum_payout: str,
    ) -> BrandedEmail:
        """Yes — and immediately, the three numbers that decide everything after it.

        The code, what it saves the customer, and what the affiliate keeps. Those are the
        only facts that change what somebody does next, so they are a table and not a
        paragraph.
        """

        greeting = _greeting_for(first_name)
        base_url = str(self.settings.public_base_url).rstrip("/")
        rows: list[tuple[str, str | EmailLink]] = [
            ("Your code", discount_code),
            ("Your audience saves", f"{discount_percent}%"),
            ("You keep", f"{commission_percent}% of what they pay"),
            ("Your link", EmailLink("Open your affiliate link", referral_url)),
        ]
        text_body = (
            f"{greeting}\n\n"
            "You are a Hilal Markets affiliate.\n\n"
            + "".join(
                f"{label}: {value.url if isinstance(value, EmailLink) else value}\n"
                for label, value in rows
            )
            + f"\nYou can ask for a payout once you have {minimum_payout}. "
            "Payouts are sent in crypto.\n\n"
            f"Your dashboard: {base_url}/dashboard/affiliate\n\n"
            "Hilal Markets"
        )
        content = (
            greeting_line(greeting)
            + lead("You are an affiliate. Here is everything you need.")
            + status_block(
                tone="success",
                label="Approved",
                meaning="Your code works from now. Share it wherever you said you would.",
            )
            + fact_table(rows, title="Your programme")
            + button("Open your affiliate page", f"{base_url}/dashboard/affiliate")
            + note(
                f"You can ask for a payout once you have {minimum_payout}. Payouts are "
                "sent in crypto, and you choose the coin and the network."
            )
        )
        return BrandedEmail(
            subject="You are a Hilal Markets affiliate",
            text_body=text_body,
            html_body=self.shell(
                title="You are an affiliate",
                eyebrow="Affiliate",
                preheader=f"Your code is {discount_code}.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because your Hilal Markets affiliate "
                    "application was approved."
                ),
            ),
        )

    def affiliate_application_rejected(
        self, *, first_name: str, reason: str | None
    ) -> BrandedEmail:
        """No — with the reason, and with the door left open.

        A refusal with nothing after it is the one answer an applicant cannot act on, so
        a reason is always shown: the reviewer's own words when there are any, and a
        plain sentence when there are none.
        """

        greeting = _greeting_for(first_name)
        base_url = str(self.settings.public_base_url).rstrip("/")
        explanation = (reason or "").strip() or (
            "We could not approve this one. You are welcome to apply again with more "
            "about where you will share Hilal Markets."
        )
        text_body = (
            f"{greeting}\n\n"
            "We could not approve your affiliate application this time.\n\n"
            f"{explanation}\n\n"
            "You can apply again whenever you like — the form is open on your "
            f"dashboard: {base_url}/dashboard/affiliate\n\n"
            "Hilal Markets"
        )
        content = (
            greeting_line(greeting)
            + lead("We could not approve your application this time.")
            + status_block(
                tone="warning",
                label="Not approved",
                meaning=explanation,
            )
            + paragraph(
                "The form is open again on your dashboard. Applying a second time is "
                "welcome, and it is read by a person exactly like the first."
            )
            + button("Apply again", f"{base_url}/dashboard/affiliate")
        )
        return BrandedEmail(
            subject="About your Hilal Markets affiliate application",
            text_body=text_body,
            html_body=self.shell(
                title="Not approved this time",
                eyebrow="Affiliate",
                preheader="You can apply again whenever you like.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because you applied to the Hilal Markets "
                    "affiliate programme."
                ),
            ),
        )

    def connection_test(self) -> BrandedEmail:
        """The email somebody gets when they press "send me a test".

        It has one job: prove that email works, and look exactly like a real alert so
        nothing about the real thing is a surprise later.
        """

        base_url = str(self.settings.public_base_url).rstrip("/")
        text_body = (
            "Email is working\n\n"
            "You asked Hilal Markets to send you a test, and here it is. Real "
            "notifications will look like this one.\n\n"
            "You will be told when a coin's Shariah status changes, when something on "
            "one of your lists comes close, and when everything you asked for happens.\n\n"
            f"Your connections: {base_url}/dashboard/connections\n\n"
            "Hilal Markets"
        )
        content = (
            status_block(
                tone="success",
                label="Email is working",
                meaning=(
                    "You asked for a test and it arrived. Real notifications will look "
                    "like this one."
                ),
            )
            + paragraph(
                "You will be told when a coin's Shariah status changes, when something "
                "on one of your lists comes close, and when everything you asked for "
                "happens."
            )
            + button("See your connections", f"{base_url}/dashboard/connections")
            + note("Nothing about your account changed. This was only a test.")
        )
        return BrandedEmail(
            subject="Your Hilal Markets email is working",
            text_body=text_body,
            html_body=self.shell(
                title="Email is working",
                eyebrow="Test message",
                preheader="You asked for a test, and here it is.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because you asked Hilal Markets to send a "
                    "test to this address."
                ),
            ),
        )

    def monitor_test_alert(self, *, monitor_name: str, in_plain_words: str) -> BrandedEmail:
        """The one message sent when a monitor is switched on from the canvas.

        It proves two things at once, which is why it is not the plain connection test:
        that email reaches this person, and that the monitor they just drew is the one
        now watching. So it quotes the monitor back in the same plain sentence the board
        showed them, and says plainly that nothing has happened in the market yet.
        """

        base_url = str(self.settings.public_base_url).rstrip("/")
        text_body = (
            f"{monitor_name} is now watching\n\n"
            "This is a test message, sent because you just switched this monitor on. "
            "Nothing has happened in the market yet.\n\n"
            f"What it watches for: {in_plain_words}\n\n"
            "A real alert will arrive the same way, and will say which coin it is about "
            "and what happened.\n\n"
            f"Your monitors: {base_url}/dashboard/monitors\n\n"
            "Hilal Markets"
        )
        content = (
            status_block(
                tone="success",
                label="Email is working",
                meaning=(
                    "This is a test, sent because you just switched this monitor on. "
                    "Nothing has happened in the market yet."
                ),
            )
            + paragraph(f"What {monitor_name} watches for:")
            + paragraph(in_plain_words)
            + button("See your monitors", f"{base_url}/dashboard/monitors")
            + note(
                "A real alert will look like this one and will name the coin and what "
                "happened. You can change how you are told in your settings."
            )
        )
        return BrandedEmail(
            subject=f"Test: {monitor_name} is now watching",
            text_body=text_body,
            html_body=self.shell(
                title=f"{monitor_name} is now watching",
                eyebrow="Test message",
                preheader="Your monitor is on. This is the test message.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because you switched on a monitor and asked "
                    "to be told by email."
                ),
            ),
        )

    def access_changed(
        self,
        *,
        first_name: str,
        plan_name: str,
        duration_label: str,
        ends_at_label: str | None,
    ) -> BrandedEmail:
        subject = f"Your Hilal Markets access is now {plan_name}"
        greeting = f"Assalamu Alaikum {first_name or 'there'},"
        expiry_line = (
            f"Your access is scheduled through {ends_at_label}."
            if ends_at_label
            else "Your access has no scheduled end date."
        )
        rows = [
            ("Access", plan_name),
            ("Duration", duration_label),
            ("WhatsApp", "Not included"),
        ]
        if ends_at_label:
            rows.insert(2, ("Access through", ends_at_label))
        text_body = (
            f"{greeting}\n\n"
            "Your Hilal Markets access has been updated by our team.\n\n"
            + "".join(f"{label}: {value}\n" for label, value in rows)
            + f"{expiry_line}\n\n"
            "You can sign in now and continue using your available screening, Watchlist, "
            "market-check, evidence, and monitoring tools.\n\n"
            "Hilal Markets"
        )
        dashboard_url = f"{str(self.settings.public_base_url).rstrip('/')}/dashboard"
        content = (
            greeting_line(greeting)
            + lead("Your access has been updated by our team. The change is active now.")
            + fact_table(rows)
            + paragraph(expiry_line)
            + button("Open Hilal Markets", dashboard_url)
            + note(
                "This access update does not authorise trade execution. Hilal Markets "
                "remains a screening, evidence and monitoring platform."
            )
        )
        return BrandedEmail(
            subject=subject,
            text_body=text_body,
            html_body=self.shell(
                title=f"{plan_name} is active",
                eyebrow="Your account",
                preheader=f"Your Hilal Markets access is now {plan_name}.",
                content_html=content,
                footer_reason=(
                    "You are receiving this because the access on your Hilal Markets "
                    "account changed."
                ),
            ),
        )


def _greeting_for(first_name: str) -> str:
    """One greeting, so every message opens the same way.

    Three templates were each building this line themselves, and two of them fell back to
    "there" while the third dropped the name entirely.
    """

    name = (first_name or "").strip()
    return f"Assalamu Alaikum {name}," if name else "Assalamu Alaikum,"


def _masked_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return "your email address"
    visible = local[:1]
    return f"{visible}{'*' * max(2, min(len(local) - 1, 6))}@{domain}"


def safe_email_payload(value: Any) -> str:
    return html.escape(str(value or ""))

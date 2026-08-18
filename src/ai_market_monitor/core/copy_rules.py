"""The words customer-facing copy may not use, and the one spelling rule.

Owned here and imported by both readers — ``scripts/check_release_invariants.py``
and ``tests/unit/test_copy_lint.py``. They used to keep their own lists, which is the
duplicate-vocabulary failure this repository keeps repeating: two guards, each
understanding a different subset, each passing while the other would have failed.

Three separate rules, kept apart because they fail for different reasons.

*Deprecated product words* are old names for things that still exist. "Watch Plan"
became "Watchlist"; leaving both taught two words for one thing.

*Forbidden marketing and religious claims* come from the brand guide, section 17.
These are not style preferences. "100% halal" and "guaranteed profit" are claims the
product has no standing to make, and a single one of them in a template undoes the
evidence-led position everything else is built on.

*The Sharia/Shariah spelling* is settled by the brand guide, section 16: accessible
marketing copy says "Islamic principles", and technical or methodological usage says
"Shariah" — Shariah screening, Shariah methodology, Shariah status, Evidence
Passport, policies and review records.

The spelling rule is deliberately **case sensitive**, and that is what makes it safe
to enforce. Customer prose capitalises the word; internal identifiers do not. So
``Sharia-screened`` in a footer is caught, while ``/api/v1/sharia/market-quotes``,
``sharia-product.css`` and the ``sharia_status`` macro are untouched. Renaming any of
those would mean an API path change, a migration and a cache-busting asset rename —
none of which belongs in a copy fix, and all of which this phase deliberately leaves
alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "CUSTOMER_COPY_SUFFIXES",
    "CopyViolation",
    "FORBIDDEN_CLAIM_PHRASES",
    "FORBIDDEN_PRODUCT_PHRASES",
    "MOJIBAKE_MARKERS",
    "SHARIA_SPELLING_PATTERN",
    "customer_copy_sources",
    "scan_customer_copy",
    "scan_text",
]

#: Old product names that must not come back, one template at a time.
FORBIDDEN_PRODUCT_PHRASES: Final[tuple[str, ...]] = (
    "watch plan",
    "watch plans",
    "halal market",
    "market scanner",
)

#: Brand guide section 17. Matched case-insensitively as whole phrases.
#:
#: "shariah approved" is refused in *static* copy on purpose. The brand guide allows
#: it only when a named authority formally supports it, and that case is never a
#: hard-coded string: it is rendered from Passport data that carries the authority,
#: the methodology, its version and the decision date. A fixed sentence in a template
#: cannot carry any of those, so in a template it is always the unsupported claim.
FORBIDDEN_CLAIM_PHRASES: Final[tuple[str, ...]] = (
    "100% halal",
    "100 percent halal",
    "guaranteed halal",
    "guaranteed profit",
    "guaranteed return",
    "guaranteed returns",
    "winning signal",
    "winning signals",
    "risk-free",
    "risk free",
    "buy now",
    "sell now",
    "ai trades for you",
    "shariah approved",
    "sharia approved",
)

#: Capital ``Sharia`` not followed by an ``h``. Case sensitive; see the module note.
SHARIA_SPELLING_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bSharia\b")

#: The brand name written without its space, in prose. Brand guide section 4: running
#: text, legal text and product copy say **Hilal Markets**; ``HilalMarkets`` is not a
#: permitted styling of it.
#:
#: Like the spelling rule above, this is safe to enforce only because it can tell prose
#: from an identifier. A word character, an underscore, a dot or a slash on either side
#: means the match is part of a name — ``HilalMarketsEmailRenderer``,
#: ``window.HilalMarketsConsentConfig``, ``hilalmarkets-guide.css``,
#: ``HilalMarkets/1.0`` in a User-Agent, ``HilalMarkets_Sharia_Methodology_Import_Pack``
#: — and renaming any of those would be a code, asset or wire change rather than a copy
#: change. Only the word standing on its own is caught, which is the only form a
#: customer ever reads.
#: The trailing dot is deliberately conditional. ``(?![\w./])`` excluded a following
#: dot outright, which spared ``HilalMarkets.com`` — correct — but also spared every
#: sentence that simply ends with the name: "Welcome to HilalMarkets." went unreported.
#: A dot means "attribute or domain" only when a word character follows it; otherwise it
#: is a full stop, and what precedes a full stop is prose.
BRAND_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.$/])HilalMarkets(?![\w/]|\.\w)"
)

#: Punctuation that has been through a Windows codepage and come back wrong.
#:
#: A dash, a quote or an ellipsis is three bytes in UTF-8 beginning ``E2 80``. Read that
#: file as the machine's ANSI codepage and write it back as UTF-8, and those three bytes
#: become two or three visible junk characters. Every bulk text rewrite on Windows can do
#: it, and nothing else notices: the file still parses, the tests still import it, and the
#: only thing that changed is what a customer reads.
#:
#: It had already happened. ``“RSI length” must be one of the choices shown`` — a message
#: shown to somebody filling in a rule — was on screen with junk where its quotes should
#: be. The lint ran over that exact file every commit and was not looking for this.
#:
#: The pairs below are the opening bytes seen through CP1251 and CP1252, which are the two
#: this happens with in practice.
MOJIBAKE_MARKERS: Final[tuple[str, ...]] = ("вЂ", "â€", "Ã¢", "Ã©", "Ã¢â‚¬")

CUSTOMER_COPY_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".html", ".py", ".js", ".ts", ".tsx"}
)


@dataclass(frozen=True, slots=True)
class CopyViolation:
    """One rule broken in one place, named well enough to fix without searching."""

    path: Path
    line: int
    rule: str
    found: str

    def describe(self, root: Path | None = None) -> str:
        location = self.path.relative_to(root) if root else self.path
        return f"{location}:{self.line}: {self.rule}: {self.found!r}"


def customer_copy_sources(root: Path) -> tuple[Path, ...]:
    """Every source a customer's words can come from.

    The public assistant's knowledge and the plan catalog are included because both
    are read straight back to a customer. A phrase banned in a template and allowed
    in the answer the assistant gives is not banned.
    """

    candidates = (
        root / "src" / "ai_market_monitor" / "templates" / "hilal",
        root / "src" / "ai_market_monitor" / "templates" / "dashboard_public.html",
        root / "src" / "ai_market_monitor" / "templates" / "auth.html",
        root / "src" / "ai_market_monitor" / "core" / "site_content.py",
        root / "src" / "ai_market_monitor" / "core" / "plans.py",
        root / "src" / "ai_market_monitor" / "core" / "product_boundaries.py",
        # Degradation banners are customer copy even though they live beside the
        # metrics that trigger them. Linting the templates but not these would leave
        # the messages shown during an outage — the ones read most carefully —
        # unchecked.
        root / "src" / "ai_market_monitor" / "observability" / "banners.py",
        root / "src" / "ai_market_monitor" / "services" / "product_language.py",
        root / "src" / "ai_market_monitor" / "services" / "public_chat_knowledge.py",
        # Words a customer reads that do not come from a template.
        #
        # The list above was "the public website", and it left out every other place
        # the product speaks: the email frame and its subjects, Telegram and WhatsApp
        # messages, and the assistant's own replies. That is how the brand name went
        # on being written without its space in five languages while a lint that
        # existed to catch exactly that reported nothing — it was not looking.
        root / "src" / "ai_market_monitor" / "services" / "email_branding.py",
        root / "src" / "ai_market_monitor" / "services" / "account_emails.py",
        root / "src" / "ai_market_monitor" / "services" / "payment_emails.py",
        root / "src" / "ai_market_monitor" / "telegram" / "service.py",
        root / "src" / "ai_market_monitor" / "whatsapp" / "rendering.py",
        root / "src" / "ai_market_monitor" / "whatsapp" / "service.py",
        # The dashboard routers. A page whose words are decided in Python rather than
        # in its template is still a page a customer reads: `/main` writes every
        # headline, tile and explanation in `main_dashboard.py`, and the redesigned
        # pages do the same in `dashboard_test.py`. Linting the templates and not these
        # would leave the newest copy in the product unchecked — the same gap the note
        # above describes, one layer further in.
        root / "src" / "ai_market_monitor" / "api" / "routers" / "main_dashboard.py",
        root / "src" / "ai_market_monitor" / "api" / "routers" / "dashboard_test.py",
        root / "src" / "ai_market_monitor" / "api" / "routers" / "dashboard.py",
        root / "src" / "ai_market_monitor" / "engine" / "conversation_language.py",
        root / "src" / "ai_market_monitor" / "engine" / "builder_contract.py",
        root / "src" / "ai_market_monitor" / "engine" / "builder_operations.py",
        root / "src" / "ai_market_monitor" / "engine" / "builder_boolean.py",
        # The public site's own pages. Half of this website is Jinja and half is React,
        # and only the Jinja half was ever linted — so the landing page, the contact
        # form and both legal documents could say anything at all. That is not a small
        # gap: the Privacy Policy and the Terms of Use are the most carefully read text
        # this product publishes, and they live here.
        #
        # `imports/` is deliberately left out. It is generated from Figma, its strings
        # are class names and coordinates rather than sentences, and a rule enforced on
        # generated output would be enforced on the generator instead.
        root / "Hilal-Markets-Website" / "src" / "App.tsx",
        root / "Hilal-Markets-Website" / "src" / "pages",
        root / "Hilal-Markets-Website" / "src" / "legal",
        root / "Hilal-Markets-Website" / "src" / "components",
    )
    return tuple(path for path in candidates if path.exists())


def scan_text(text: str, path: Path) -> tuple[CopyViolation, ...]:
    """Every violation in one file's text."""

    violations: list[CopyViolation] = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.casefold()
        for phrase in FORBIDDEN_PRODUCT_PHRASES:
            if phrase in lowered:
                violations.append(
                    CopyViolation(path, number, "deprecated product term", phrase)
                )
        for phrase in FORBIDDEN_CLAIM_PHRASES:
            if phrase in lowered:
                violations.append(
                    CopyViolation(path, number, "forbidden claim", phrase)
                )
        for match in SHARIA_SPELLING_PATTERN.finditer(line):
            violations.append(
                CopyViolation(
                    path,
                    number,
                    "spelling: technical usage is 'Shariah'",
                    match.group(0),
                )
            )
        for match in BRAND_NAME_PATTERN.finditer(line):
            violations.append(
                CopyViolation(
                    path,
                    number,
                    "brand: the name in prose is 'Hilal Markets'",
                    match.group(0),
                )
            )
        for marker in MOJIBAKE_MARKERS:
            if marker in line:
                violations.append(
                    CopyViolation(
                        path,
                        number,
                        "encoding: a dash or quote was written through a Windows codepage",
                        marker,
                    )
                )
                break
    return tuple(violations)


def scan_customer_copy(root: Path) -> tuple[CopyViolation, ...]:
    """Every violation across every customer-facing copy source."""

    violations: list[CopyViolation] = []
    for source in customer_copy_sources(root):
        candidates = sorted(source.rglob("*")) if source.is_dir() else (source,)
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix not in CUSTOMER_COPY_SUFFIXES:
                continue
            text = candidate.read_text(encoding="utf-8")
            violations.extend(scan_text(text, candidate))
    return tuple(violations)

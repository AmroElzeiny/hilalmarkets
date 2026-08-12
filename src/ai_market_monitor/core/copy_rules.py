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

CUSTOMER_COPY_SUFFIXES: Final[frozenset[str]] = frozenset({".html", ".py", ".js"})


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

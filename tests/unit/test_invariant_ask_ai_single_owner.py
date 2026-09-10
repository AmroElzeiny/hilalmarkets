"""Each Ask AI concept has exactly one owner.

The recurring failure mode in this repository is two modules deciding the same thing
independently. These tests grep for the one macro, the one CSS modifier, the one click
handler, and the one gate expression, and fail if a second copy appears.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ai_market_monitor"
MACRO = SRC / "templates" / "hilal" / "macros" / "ask_ai.html"
CSS = SRC / "static" / "hm-dashboard-test.css"
JS = SRC / "static" / "hm-hilal-chat.js"
TEMPLATE_ENV = SRC / "api" / "template_env.py"


def test_ask_ai_macro_has_exactly_one_definition() -> None:
    assert MACRO.is_file(), MACRO
    source = MACRO.read_text(encoding="utf-8")
    matches = re.findall(r"{%-?\s*macro\s+ask_ai\s*\(", source)
    assert len(matches) == 1, f"expected one ask_ai macro, found {len(matches)}"


def test_ask_ai_css_modifier_has_exactly_one_top_level_block() -> None:
    css = CSS.read_text(encoding="utf-8")
    # Count outside media queries: the main modifier block.
    outside_media = re.sub(r"@media\s*[^{]*\{([^{}]|\{[^{}]*\})*\}", "", css, flags=re.DOTALL)
    matches = re.findall(
        r"body\.hilal-dashboard\s+:is\(\s*\.hm-t\s*,\s*dialog\.t-dialog\s*,\s*\.hm-hilal\s*\)\s+\.t-action-is-ask\s*\{",
        outside_media,
    )
    assert len(matches) == 1, f"expected one top-level .t-action-is-ask block, found {len(matches)}"


def test_ask_ai_css_reduced_motion_block_has_exactly_one_rule() -> None:
    css = CSS.read_text(encoding="utf-8")
    media_match = re.search(
        r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\}", css, re.DOTALL
    )
    assert media_match, "no reduced-motion media query found"
    media_body = media_match.group(1)
    matches = re.findall(
        r"body\.hilal-dashboard\s+:is\(\s*\.hm-t\s*,\s*dialog\.t-dialog\s*,\s*\.hm-hilal\s*\)\s+\.t-action-is-ask\s*\{",
        media_body,
    )
    assert len(matches) == 1, (
        f"expected one reduced-motion .t-action-is-ask rule, found {len(matches)}"
    )


def test_ask_ai_click_handler_has_exactly_one_owner() -> None:
    js = JS.read_text(encoding="utf-8")
    matches = re.findall(r"data-hilal-ask", js)
    assert len(matches) == 1, (
        f"expected one [data-hilal-ask] handler reference, found {len(matches)}"
    )


def test_hilal_chat_gate_is_the_only_computation_of_the_gate() -> None:
    """The inline expression is gone from templates; only the function defines it."""

    gate_expr = "hilal_chat|default(false) and settings.hilal_chat_enabled"
    template_env = TEMPLATE_ENV.read_text(encoding="utf-8")
    assert gate_expr not in template_env, (
        "the inline gate expression must not live in template_env.py; use the function"
    )

    templates = list((SRC / "templates" / "hilal").rglob("*.html"))
    offenders = []
    for path in templates:
        text = path.read_text(encoding="utf-8")
        if gate_expr in text:
            offenders.append(str(path))
    assert offenders == [], (
        f"inline gate expression found outside the owner function: {offenders}"
    )


def test_hilal_chat_gate_is_registered_as_a_jinja_global() -> None:
    source = TEMPLATE_ENV.read_text(encoding="utf-8")
    assert "def hilal_chat_gate(" in source
    assert 'templates.env.globals["hilal_chat_gate"]' in source

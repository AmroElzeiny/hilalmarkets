"""The Ask AI modifier is a proper member of the `.t-action` family.

The contract says the button lives in `hm-dashboard-test.css` as a modifier, uses the
brand sky tokens, and keeps the shipped focus and reduced-motion conventions. This file
proves those rules in text so a browser is not required.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path("src/ai_market_monitor/static")
DASHBOARD_CSS = STATIC / "hm-dashboard-test.css"
BRAND_CSS = STATIC / "hilalmarkets-brand.css"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def test_ask_ai_modifier_uses_brand_tokens_not_raw_hex() -> None:
    """The new modifier resolves to `--hm-sky*` and writes no colour of its own."""

    css = _text(DASHBOARD_CSS)
    block = re.search(
        r"body\.hilal-dashboard\s+:is\(\s*\.hm-t\s*,\s*dialog\.t-dialog\s*,\s*\.hm-hilal\s*\)\s+\.t-action-is-ask\s*\{(.*?)\}",
        css,
        re.DOTALL,
    )
    assert block, "no `.t-action-is-ask` modifier block found"
    body = block.group(1)
    assert "var(--hm-sky)" in body, "rest fill must use --hm-sky"
    assert "border-color: var(--hm-sky)" in body or "border:" in body, (
        "rest border must use --hm-sky"
    )
    assert "color: var(--hm-surface)" in body, "text must use --hm-surface"
    assert "font-weight: 600" in body, "label weight must be 600"
    assert "flex: 0 0 auto" in body, "button must not stretch"
    assert "min-height: 44px" in body, "44px target floor must be explicit"

    hover = re.search(
        r"body\.hilal-dashboard\s+:is\(\s*\.hm-t\s*,\s*dialog\.t-dialog\s*,\s*\.hm-hilal\s*\)\s+\.t-action-is-ask:hover\s*\{(.*?)\}",
        css,
        re.DOTALL,
    )
    assert hover, "no hover rule for `.t-action-is-ask`"
    hover_body = hover.group(1)
    assert "var(--hm-sky-strong)" in hover_body, "hover fill/border must use --hm-sky-strong"

    active = re.search(
        r"body\.hilal-dashboard\s+:is\(\s*\.hm-t\s*,\s*dialog\.t-dialog\s*,\s*\.hm-hilal\s*\)\s+\.t-action-is-ask:active\s*\{(.*?)\}",
        css,
        re.DOTALL,
    )
    assert active, "no active rule for `.t-action-is-ask`"
    active_body = active.group(1)
    assert "var(--hm-sky-strong)" in active_body, "active fill/border must use --hm-sky-strong"
    assert "transform: none" in active_body, "active state must have no lift"


def test_ask_ai_breathe_keyframe_and_timing_are_exact() -> None:
    """Pure CSS keyframes, no motion library; transform only, exact timing."""

    css = _text(DASHBOARD_CSS)
    keyframes = re.search(
        r"@keyframes\s+hm-ask-breathe\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
        css,
        re.DOTALL,
    )
    assert keyframes, "no `hm-ask-breathe` keyframes found"
    frame_body = keyframes.group(1)
    assert "transform: scale(1)" in frame_body
    assert "transform: scale(1.045)" in frame_body
    assert "animation: hm-ask-breathe 2600ms ease-in-out infinite" in css
    assert "animation-play-state: paused" in css
    # Width/height/margin/padding/top/left must never be animated.
    for bad in ("width:", "height:", "margin:", "padding:", "top:", "left:"):
        assert bad not in frame_body, f"{bad} must not be animated in the breathe keyframe"


def test_ask_ai_reduced_motion_rule_has_the_file_prefix() -> None:
    """The reduced-motion rule matches the file's selector convention."""

    css = _text(DASHBOARD_CSS)
    rule = re.search(
        r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\}",
        css,
        re.DOTALL,
    )
    assert rule, "no reduced-motion media query found"
    media_body = rule.group(1)
    assert "body.hilal-dashboard" in media_body, "reduced-motion rule must keep the file prefix"
    assert ".t-action-is-ask" in media_body
    assert "animation: none" in media_body
    assert "transform: none" in media_body


def test_hm_dashboard_test_css_has_no_raw_hex_colours() -> None:
    """All colour on the redesigned dashboard pages must come from brand tokens."""

    css = _strip_comments(_text(DASHBOARD_CSS))
    hex_values = re.findall(r"#[0-9a-fA-F]{6}", css)
    assert hex_values == [], f"raw hex colours found: {sorted(set(hex_values))}"

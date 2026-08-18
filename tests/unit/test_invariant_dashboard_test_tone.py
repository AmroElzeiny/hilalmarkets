"""The server and the browser must group Shariah statuses identically.

On `/dashboard/market` the server renders a coin's status once, and the first live
refresh redraws the same coin from JavaScript. Two status tables — one in Jinja, one in
JS — is exactly the duplicate-vocabulary fault this codebase keeps rediscovering: each
side understands a different subset, and a coin changes colour when the page refreshes.

They cannot be one file because they are two languages. They can be held to one
content, which is what this does: read both sources, and fail if either list gains,
loses or moves a status.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import ai_market_monitor

PACKAGE = Path(ai_market_monitor.__file__).parent
JS_VOCABULARY = PACKAGE / "static" / "hm-market-vocabulary.js"
JINJA_VOCABULARY = PACKAGE / "templates" / "hilal" / "dashboard_test" / "macros" / "tone.html"

#: (JS constant, Jinja constant). Every group that both sides have to agree on.
SHARED_GROUPS = [
    ("ELIGIBLE_STATUSES", "ELIGIBLE_STATUSES"),
    ("REVIEW_STATUSES", "REVIEW_STATUSES"),
    ("EXCLUDED_STATUSES", "EXCLUDED_STATUSES"),
]

#: The coverage and criterion tables live inline in the JS functions, so they are
#: compared against the Jinja constants by reading the function bodies.
#: (JS function, Jinja constant, tone).
INLINE_GROUPS = [
    ("coverageTone", "COVERAGE_ELIGIBLE", "eligible"),
    ("coverageTone", "COVERAGE_REVIEW", "review"),
    ("coverageTone", "COVERAGE_EXCLUDED", "excluded"),
    ("criterionTone", "CRITERION_ELIGIBLE", "eligible"),
    ("criterionTone", "CRITERION_REVIEW", "review"),
    ("criterionTone", "CRITERION_EXCLUDED", "excluded"),
]


def _quoted(text: str) -> set[str]:
    return set(re.findall(r"""['"]([a-z_]+)['"]""", text))


def _js_constant(name: str) -> set[str]:
    source = JS_VOCABULARY.read_text(encoding="utf-8")
    match = re.search(rf"{name}\s*=\s*Object\.freeze\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"{name} is no longer declared in {JS_VOCABULARY.name}"
    return _quoted(match.group(1))


def _js_function_group(function: str, tone: str) -> set[str]:
    """The statuses a JS tone function maps to one tone."""
    source = JS_VOCABULARY.read_text(encoding="utf-8")
    body = re.search(rf"export function {function}\(.*?\n}}", source, re.DOTALL)
    assert body, f"{function} is no longer declared in {JS_VOCABULARY.name}"
    found: set[str] = set()
    for line in body.group(0).splitlines():
        if f'return "{tone}"' not in line:
            continue
        found |= _quoted(line.split("return")[0])
    return found


def _jinja_constant(name: str) -> set[str]:
    source = JINJA_VOCABULARY.read_text(encoding="utf-8")
    match = re.search(rf"set {name} = \[(.*?)\]", source, re.DOTALL)
    assert match, f"{name} is no longer declared in {JINJA_VOCABULARY.name}"
    return _quoted(match.group(1))


@pytest.mark.parametrize(("js_name", "jinja_name"), SHARED_GROUPS)
def test_the_asset_status_groups_are_identical(js_name, jinja_name):
    assert _js_constant(js_name) == _jinja_constant(jinja_name)


@pytest.mark.parametrize(("function", "jinja_name", "tone"), INLINE_GROUPS)
def test_the_coverage_and_criterion_groups_are_identical(function, jinja_name, tone):
    assert _js_function_group(function, tone) == _jinja_constant(jinja_name)


def test_no_status_is_claimed_by_two_tones_on_either_side():
    """A status in two groups would make the tone depend on which check ran first."""
    for reader in (_js_constant, _jinja_constant):
        groups = [reader(name) for name, _ in SHARED_GROUPS]
        seen: set[str] = set()
        for group in groups:
            assert not (seen & group), f"{seen & group} appears in more than one group"
            seen |= group


@pytest.mark.parametrize("tone", ["eligible", "review", "excluded", "neutral"])
def test_both_sides_give_every_tone_the_same_icon(tone):
    """Colour is never the only signal, so the icon has to match too."""
    js = JS_VOCABULARY.read_text(encoding="utf-8")
    jinja = JINJA_VOCABULARY.read_text(encoding="utf-8")

    js_icons = re.search(r"export function toneIcon\(.*?\n}", js, re.DOTALL)
    assert js_icons
    js_match = re.search(rf"{tone}:\s*\"([a-z_]+)\"", js_icons.group(0))
    assert js_match, f"{tone} has no icon in {JS_VOCABULARY.name}"

    jinja_icons = re.search(r"macro tone_icon\(tone\)(.*?)endmacro", jinja, re.DOTALL)
    assert jinja_icons
    if tone == "neutral":
        jinja_match = re.search(r"else -%\}([a-z_]+)", jinja_icons.group(1))
    else:
        jinja_match = re.search(rf"'{tone}' -%\}}([a-z_]+)", jinja_icons.group(1))
    assert jinja_match, f"{tone} has no icon in {JINJA_VOCABULARY.name}"

    assert js_match.group(1) == jinja_match.group(1)


def test_an_unknown_status_is_never_treated_as_eligible():
    """Guessing upwards would show an unreviewed coin as one that passed."""
    from importlib import import_module

    del import_module  # the rule is checked against both sources, not by running JS

    js = JS_VOCABULARY.read_text(encoding="utf-8")
    jinja = JINJA_VOCABULARY.read_text(encoding="utf-8")

    js_body = re.search(r"export function assetTone\(.*?\n}", js, re.DOTALL)
    assert js_body and js_body.group(0).rstrip().endswith('return "neutral";\n}')

    jinja_body = re.search(r"macro asset_tone\(status\)(.*?)endmacro", jinja, re.DOTALL)
    assert jinja_body and "else -%}neutral" in jinja_body.group(1)

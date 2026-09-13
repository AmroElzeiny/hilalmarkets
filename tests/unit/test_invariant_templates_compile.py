"""Every template compiles, through every environment that could render it.

`scripts/check_jinja_templates.py` proves this, but only the release gate ran it. The
offline suites never did, so a template that does not parse was invisible to every local
run until some test happened to render a page built on it.

That is how commit 75b19580 (11 September 2026) shipped `hilal/base_dashboard.html` with
a `{# … #}` comment written inside the `dashboard_error_messages` dictionary. Jinja allows
no comment inside an expression, so the base template stopped compiling — and with it
every dashboard page, because they all extend it. A page that refuses to load is the
whole product down for a signed-in customer.

The check is the script's own, imported rather than copied, so the gate and the suite
cannot drift into two different ideas of "compiles".
"""

from __future__ import annotations

import pytest
from fastapi.templating import Jinja2Templates

from scripts.check_jinja_templates import ENVIRONMENTS


def _names(templates: Jinja2Templates) -> list[str]:
    return sorted(templates.env.list_templates(extensions=("html", "txt")))


CASES = [
    pytest.param(where, name, id=f"{where}:{name}")
    for where, templates in ENVIRONMENTS.items()
    for name in _names(templates)
]


def test_there_are_templates_to_compile():
    """Without this the rule below passes by finding no files at all."""

    assert len(CASES) > 150


@pytest.mark.parametrize(("where", "name"), CASES)
def test_the_template_compiles(where: str, name: str):
    """Load the template exactly as the router that serves it would."""

    ENVIRONMENTS[where].get_template(name)

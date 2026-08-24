"""Prove every template still compiles — in **every** environment that could render it.

Checking one environment was not enough, and the way it failed is worth remembering. A
filter registered on the System Brain router's environment made ``system_brain.html``
compile there and refuse to load anywhere else, with ``No filter named 'source_state'``.
This script loaded every template through the *dashboard* environment, so it caught that
one by luck; a filter registered only on the dashboard's environment would have sailed
through while breaking the public pages.

Each router builds its own ``Jinja2Templates``. Templates and macros are shared between
them. So the only honest check is: every template, through every environment.
``api/template_env.py`` is what makes that pass — one function installs the filters and
globals, and every router calls it.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

from ai_market_monitor.api.routers.dashboard import templates as dashboard_templates
from ai_market_monitor.api.routers.public import templates as public_templates
from ai_market_monitor.api.routers.system_brain import templates as system_brain_templates

#: Every environment a template could be rendered through, named for the report.
ENVIRONMENTS: dict[str, Jinja2Templates] = {
    "dashboard": dashboard_templates,
    "public": public_templates,
    "system brain": system_brain_templates,
}


def main() -> int:
    failures: list[tuple[str, str, str]] = []
    checked = 0
    for where, templates in ENVIRONMENTS.items():
        names = templates.env.list_templates(extensions=("html", "txt"))
        for name in names:
            checked += 1
            try:
                templates.get_template(name)
            except Exception as exc:  # pragma: no cover - the failure is the report.
                failures.append((where, name, f"{exc.__class__.__name__}: {exc}"))
    if failures:
        print("Templates that failed to load:")
        for where, name, error in failures:
            print(f"- {name} (through the {where} pages): {error}")
        print(
            "\nA filter or global is registered on one environment and not the others. "
            "Add it to api/template_env.py, which every router calls."
        )
        return 1
    print(f"PASS: loaded {checked} templates across {len(ENVIRONMENTS)} environments.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

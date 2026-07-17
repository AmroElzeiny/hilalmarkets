from __future__ import annotations

from ai_market_monitor.api.routers.dashboard import templates


def main() -> int:
    names = templates.env.list_templates(extensions=("html", "txt"))
    failures: list[tuple[str, str]] = []
    for name in names:
        try:
            templates.get_template(name)
        except Exception as exc:  # pragma: no cover - the failure is the report.
            failures.append((name, f"{exc.__class__.__name__}: {exc}"))
    if failures:
        print("Templates that failed to load:")
        for name, error in failures:
            print(f"- {name}: {error}")
        return 1
    print(f"PASS: loaded {len(names)} Jinja templates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Check:
    name: str
    path: str
    expected_statuses: tuple[int, ...] = (200,)
    contains: str | None = None
    json_status: str | None = None


CHECKS = (
    Check("shallow health", "/health", json_status="ok"),
    Check("deep health", "/health/deep", json_status="ok"),
    Check("landing page", "/", contains="HilalMarkets"),
    Check("dashboard route", "/dashboard", expected_statuses=(200, 302, 303, 401)),
    Check("main stylesheet", "/static/styles.css"),
    Check("dashboard script", "/static/app.js"),
)


def _compact_json_text(text: str) -> str:
    return "".join(text.split())


def run(base_url: str, timeout: float) -> int:
    failures: list[str] = []
    with httpx.Client(
        base_url=base_url.rstrip("/"), timeout=timeout, follow_redirects=False
    ) as client:
        for check in CHECKS:
            try:
                response = client.get(check.path)
            except Exception as exc:
                failures.append(f"{check.name}: request failed: {exc}")
                continue

            if response.status_code not in check.expected_statuses:
                failures.append(
                    f"{check.name}: expected {check.expected_statuses}, got {response.status_code}"
                )
                continue

            if check.contains and check.contains not in _compact_json_text(response.text):
                failures.append(f"{check.name}: response did not contain {check.contains!r}")
                continue

            if check.json_status is not None:
                try:
                    payload = response.json()
                except ValueError:
                    failures.append(f"{check.name}: response was not JSON")
                    continue
                if payload.get("status") != check.json_status:
                    failures.append(
                        f"{check.name}: expected JSON status {check.json_status!r}, "
                        f"got {payload.get('status')!r}"
                    )
                    continue

            print(f"PASS {check.name} [{response.status_code}] {check.path}")

    if failures:
        print("\nDeployment smoke failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nDeployment smoke passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="HilalMarkets deployment smoke test.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    return run(args.base_url, args.timeout)


if __name__ == "__main__":
    sys.exit(main())

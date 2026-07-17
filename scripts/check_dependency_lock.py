from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    requirements = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        requirements.extend(group)
    unlocked = [item for item in requirements if "==" not in item]
    if unlocked:
        print("Direct dependencies without an exact version:")
        for item in unlocked:
            print(f"- {item}")
        return 1
    print(f"PASS: {len(requirements)} direct runtime and test dependencies are exact-pinned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

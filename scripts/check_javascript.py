from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("FAIL: Node.js is required for JavaScript syntax validation.")
        return 1
    files = sorted((ROOT / "src" / "ai_market_monitor" / "static").rglob("*.js"))
    failures: list[str] = []
    for path in files:
        result = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            failures.append(f"{path.relative_to(ROOT)}: {(result.stderr or result.stdout).strip()}")
    if failures:
        print("JavaScript files that failed syntax validation:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"PASS: checked {len(files)} JavaScript files with Node.js.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

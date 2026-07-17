from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_DIR = PROJECT_ROOT / "src" / "ai_market_monitor" / "static" / "charting_library"
REQUIRED_FILE = LIBRARY_DIR / "charting_library.js"


def main() -> int:
    if not LIBRARY_DIR.exists():
        print(f"MISSING: {LIBRARY_DIR}")
        return 1
    if not REQUIRED_FILE.is_file():
        files = [item.name for item in LIBRARY_DIR.iterdir()]
        print(f"MISSING: {REQUIRED_FILE}")
        print(f"Current files: {', '.join(files) if files else 'none'}")
        print(
            "The lifecycle chart will use the fallback chart and will not show "
            "TradingView's vertical drawing toolbar."
        )
        return 1
    print(f"OK: {REQUIRED_FILE}")
    print("TradingView Charting Library should load after the API/static server restarts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

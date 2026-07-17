from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "src" / "ai_market_monitor" / "static"
DESTINATION = STATIC_ROOT / "charting_library"


def _source_from(value: str | None) -> Path:
    raw = value or os.getenv("TRADINGVIEW_CHARTING_LIBRARY_SOURCE_DIR") or ""
    if not raw.strip():
        raise SystemExit(
            "Missing source directory. Pass --source or set "
            "TRADINGVIEW_CHARTING_LIBRARY_SOURCE_DIR in your local environment."
        )
    source = Path(raw).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")
    if (source / "charting_library").is_dir():
        source = source / "charting_library"
    if not (source / "charting_library.js").is_file():
        raise SystemExit(
            "This does not look like the official TradingView charting_library folder. "
            f"Expected charting_library.js inside: {source}"
        )
    return source


def _copy_source(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in {".git", "node_modules"}:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _copy_datafeeds(source: Path) -> None:
    repo_root = source.parent
    datafeeds = repo_root / "datafeeds"
    if not datafeeds.is_dir():
        return
    shutil.copytree(datafeeds, STATIC_ROOT / "datafeeds", dirs_exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install the official licensed TradingView Charting Library files "
            "into TraceEdge's local static folder."
        )
    )
    parser.add_argument(
        "--source",
        help=(
            "Path to the official TradingView charting_library folder, or to a "
            "repository root containing charting_library/."
        ),
    )
    args = parser.parse_args()
    source = _source_from(args.source)
    _copy_source(source, DESTINATION)
    _copy_datafeeds(source)
    installed = DESTINATION / "charting_library.js"
    if not installed.is_file():
        raise SystemExit(f"Install failed; missing {installed}")
    print(f"Installed TradingView Charting Library to: {DESTINATION}")
    print("Restart the FastAPI/Docker service, then reopen the lifecycle chart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Publish the Hilal Markets Methodology into a database.

Reads the two files a person edited and committed — the condition decisions and the coin
admissions — and writes the methodology row and one assessment per coin. It decides
nothing: everything it writes is already in git, signed and dated.

    .venv/Scripts/python scripts/publish_hilal_methodology.py --dry-run
    .venv/Scripts/python scripts/publish_hilal_methodology.py --write

``--dry-run`` is the default, and it touches no database at all: it loads the files,
prints what would be written, and stops. Running it is how you check a change to the
admissions file before it reaches anybody.

Safe to run twice. A coin whose reasons and sources have not changed is left exactly as
it is, so a repeat run does not stamp every coin with a fresh review date and make the
whole list look re-read when nothing was read.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_market_monitor.services.hilal_methodology import (  # noqa: E402
    METHODOLOGY_VERSION,
    Admission,
    Outcome,
    admitted_assets,
    admitted_by,
    assets_by_outcome,
    methodology_description,
    methodology_rules,
    publish,
)
from ai_market_monitor.services.sharia_automated_screen import (  # noqa: E402
    METHODOLOGY_DISPLAY_NAME,
    METHODOLOGY_SYSTEM_CODE,
)


def _preview() -> None:
    rules = methodology_rules()
    print(f"{METHODOLOGY_DISPLAY_NAME}  ({METHODOLOGY_SYSTEM_CODE} v{METHODOLOGY_VERSION})")
    print(f"criteria fingerprint : {rules['criteria_version']}")
    print(f"conditions applied   : {len(rules['applied_conditions'])}")
    print(f"conditions skipped   : {len(rules['skipped_conditions'])}")
    print(f"reach                : {rules['page_budget']} pages of a project's own site")
    print()
    print(f"{'coin':<8}{'outcome':<18}{'route':<20}pages")
    print("-" * 62)
    for asset in admitted_assets():
        print(
            f"{asset.symbol:<8}{asset.outcome.value:<18}"
            f"{asset.admission.value:<20}{asset.pages_read or '-'}"
        )
    print()
    for outcome in Outcome:
        print(f"{outcome.value:<18}{len(assets_by_outcome(outcome))}")
    print()
    for route in Admission:
        print(f"admitted via {route.value:<18}{len(admitted_by(route))}")
    print()
    print("Description that will be published:")
    print(f"  {methodology_description()}")


async def _write() -> int:
    from ai_market_monitor.core.database import SessionFactory

    async with SessionFactory() as session:
        result = await publish(session)
        await session.commit()
    print("published:", result.as_dict())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually write to the database; without it nothing is opened",
    )
    args = parser.parse_args()

    _preview()
    if not args.write:
        print()
        print("Dry run. Nothing was written. Pass --write to publish.")
        return 0
    return asyncio.run(_write())


if __name__ == "__main__":
    raise SystemExit(main())

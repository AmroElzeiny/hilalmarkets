"""Build the Hilal Markets Methodology's coin list from things that were actually read.

Two inputs, and neither of them is a judgement made here:

``--regulator``
    The Malaysian regulator's own published list, which ships in the import pack. Every
    row becomes a ``regulator_floor`` admission, carrying the SAC meeting and decision
    date the regulator published. Nothing is inferred: the regulator publishes no
    coin-by-coin reasoning and none is written for it.

``--screen``
    The output of ``blind_automated_screen_probe.py`` — one entry per coin, holding the
    verdict, the sentences behind it and every page that was read. Each becomes an
    ``automated_screen`` record with the outcome the screen actually reached, including
    the refusals and the coins there was too little to read.

The result is ``services/hilal_methodology_admissions.json``, which is the file the
product publishes from. It is generated rather than hand-written for the same reason the
Arabic register is: a list somebody typed drifts from the reading it claims to record,
and then the standard says one thing while the evidence says another.

    .venv/Scripts/python scripts/build_hilal_admissions.py --screen out.json --write
    .venv/Scripts/python scripts/build_hilal_admissions.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_market_monitor.services.hilal_methodology import (  # noqa: E402
    ADMISSIONS_FILE,
    REGULATOR_URL,
    Admission,
    Outcome,
)

PACK = (
    ROOT
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "data"
)
REGULATOR_DATASET = PACK / "sc_malaysia_compliant_assets.json"
TARGET = ROOT / "src" / "ai_market_monitor" / "services" / ADMISSIONS_FILE

#: How the probe's three verdicts map onto this standard's three outcomes. Written once,
#: here, because a mapping written twice is how a refusal becomes a pass.
VERDICT_TO_OUTCOME = {
    "eligible": Outcome.ADMITTED,
    "not_eligible": Outcome.REFUSED,
    "not_enough_data": Outcome.NOT_ENOUGH_DATA,
}

#: Pages worth citing on a record, most useful first. A record carries a handful, not
#: the whole crawl: forty URLs on a passport is a list nobody reads.
CITED_SOURCES = 6


def regulator_rows() -> list[dict[str, Any]]:
    """The regulator's list, turned into admissions without adding a word to it."""

    rows = json.loads(REGULATOR_DATASET.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(
            row.get("canonical_symbol_candidate") or row.get("symbol_source") or ""
        ).strip().upper()
        if not symbol:
            continue
        meeting = str(row.get("sac_meeting_number") or "").strip()
        decided = str(row.get("decision_date") or "").strip()
        retrieved = str(row.get("retrieved_at") or decided).strip()
        out.append(
            {
                "symbol": symbol,
                "name": str(row.get("asset_name_source") or symbol).strip(),
                "admission": Admission.REGULATOR_FLOOR.value,
                "outcome": Outcome.ADMITTED.value,
                "decided_on": decided,
                "reasons": [
                    "The Shariah Advisory Council of the Securities Commission of "
                    f"Malaysia published this asset as {row.get('external_status_source')} "
                    f"at its {meeting} meeting on {decided}.",
                    "This standard treats a regulator's published approval as a floor it "
                    "may not fall below, so the automatic reading cannot refuse it.",
                ],
                "sources": [
                    {
                        "url": str(row.get("source_url") or REGULATOR_URL),
                        "title": "SC Malaysia — list of Shariah-compliant digital assets",
                        "category": "official_external_reference",
                        "retrieved_at": retrieved,
                    }
                ],
                "pages_read": 0,
                "primary_pages_read": 0,
                "matched_conditions": [],
                "note": str(row.get("jurisdiction_scope") or "").strip(),
            }
        )
    return out


def screen_rows(payload: list[dict[str, Any]], decided_on: str) -> list[dict[str, Any]]:
    """The probe's readings, turned into records that keep their own answer."""

    out: list[dict[str, Any]] = []
    for entry in payload:
        result = entry.get("automated_result") or {}
        verdict = str(result.get("verdict") or "")
        if verdict not in VERDICT_TO_OUTCOME:
            raise SystemExit(f"{entry.get('symbol')}: unknown verdict {verdict!r}")
        outcome = VERDICT_TO_OUTCOME[verdict]
        reasons = [
            str(item.get("text") or "").strip()
            for item in result.get("reasons") or []
            if str(item.get("text") or "").strip()
        ]
        if not reasons:
            reasons = ["The screen recorded no sentence for this reading."]
        documents = [
            item
            for item in (entry.get("evidence_read") or {}).get("documents", [])
            if item.get("url")
        ]
        # The project's own pages first: they are what the verdict rests on.
        documents.sort(key=lambda item: (not item.get("is_primary"), item.get("url", "")))
        out.append(
            {
                "symbol": str(entry.get("symbol") or "").upper(),
                "name": str(entry.get("name") or entry.get("symbol") or "").strip(),
                "admission": Admission.AUTOMATED_SCREEN.value,
                "outcome": outcome.value,
                "decided_on": decided_on,
                "reasons": reasons,
                "sources": [
                    {
                        "url": str(document["url"]),
                        "title": str(document.get("title") or document["url"])[:300],
                        "category": str(document.get("category") or "website"),
                        "retrieved_at": str(document.get("fetched_at") or decided_on)[:10],
                    }
                    for document in documents[:CITED_SOURCES]
                ],
                "pages_read": int(result.get("documents_read") or 0),
                "primary_pages_read": int(result.get("primary_documents_read") or 0),
                "matched_conditions": list(result.get("matched_conditions") or []),
                "note": "; ".join(result.get("open_questions") or []),
            }
        )
    return out


def build(screen_file: Path | None, decided_on: str) -> list[dict[str, Any]]:
    rows = regulator_rows()
    known = {row["symbol"] for row in rows}
    if screen_file is not None:
        payload = json.loads(screen_file.read_text(encoding="utf-8"))
        for row in screen_rows(payload, decided_on):
            if row["symbol"] in known:
                # The regulator's answer wins by this standard's own rule, so a machine
                # reading of the same coin is dropped rather than recorded beside it.
                # Two records for one coin is how a list starts contradicting itself.
                continue
            known.add(row["symbol"])
            rows.append(row)
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", default="", help="a blind-probe result file")
    parser.add_argument(
        "--decided-on", default="", help="the date these readings were taken (YYYY-MM-DD)"
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.screen and not args.decided_on:
        raise SystemExit("--screen needs --decided-on: a reading without a date is undated.")

    if args.check and not args.screen:
        # Checking without new readings compares the file against itself plus the
        # regulator's list, which is what a build step wants: it catches a regulator row
        # that was edited by hand.
        current = json.loads(TARGET.read_text(encoding="utf-8"))
        expected = regulator_rows()
        have = [row for row in current if row["admission"] == Admission.REGULATOR_FLOOR.value]
        if have != expected:
            print(f"{ADMISSIONS_FILE} disagrees with the regulator's published list.")
            return 1
        machine = [
            row for row in current if row["admission"] == Admission.AUTOMATED_SCREEN.value
        ]
        print(
            f"{ADMISSIONS_FILE} is in step: {len(have)} from the regulator, "
            f"{len(machine)} read by machine."
        )
        return 0

    rows = build(Path(args.screen) if args.screen else None, args.decided_on)
    text = render(rows)
    if args.write:
        TARGET.write_text(text, encoding="utf-8")
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        print(f"wrote {TARGET} ({len(rows)} coins: {counts})")
        return 0
    if args.check:
        if TARGET.read_text(encoding="utf-8") != text:
            print(f"{ADMISSIONS_FILE} is out of step with the inputs.")
            return 1
        print(f"{ADMISSIONS_FILE} is in step ({len(rows)} coins).")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

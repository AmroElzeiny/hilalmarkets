"""Check every official link the product holds, score how useful it still is, and refill.

Run this when you want to know whether the links behind the Shariah reviews are still
good. For every coin it:

1. fetches every registered link again, ignoring the normal "checked recently" calendar;
2. scores each one for how **active** it is — how recently it published, how many dated
   items it shows, and how much it talks about the project's money and its rules;
3. where a coin has too few good links, or its links have gone quiet, goes looking for
   more: the project's own site is read for its Telegram channel, its X account and its
   forum, and — when a search key is configured — the open web is searched for the
   project's own news;
4. writes what it found, in plain words.

Two things it never does. It never decides a Shariah status: the activity score says how
good a *window* onto the project a page is, and nothing else. And it never throws a link
away for being quiet — a quiet page stays registered and stays visible; the product
simply looks for company for it.

    .venv/Scripts/python scripts/recheck_official_sources.py
    .venv/Scripts/python scripts/recheck_official_sources.py --symbol BTC --symbol ETH
    .venv/Scripts/python scripts/recheck_official_sources.py --limit 25 --dry-run

Every fetch is a request to somebody else's server and the scraper waits between them,
so a full pass over every coin takes a long time. ``--limit`` and ``--symbol`` exist so
it can be run in slices.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.database import SessionFactory
from ai_market_monitor.db.models import CanonicalAsset, OfficialSource
from ai_market_monitor.services.sharia_source_activity import ACTIVITY_FLOOR
from ai_market_monitor.services.sharia_source_catalog import (
    REQUIRED_CATEGORIES,
    SEARCH_QUERIES,
    VERIFIED,
    category_label,
    state_label,
)
from ai_market_monitor.services.sharia_source_discovery import WebSourceDiscovery
from ai_market_monitor.services.sharia_source_resolution import (
    AssetSourceOutcome,
    SourceResolutionService,
)

WORKING = state_label(VERIFIED)


def _activity_of(row: OfficialSource) -> float | None:
    detail = row.check_detail if isinstance(row.check_detail, dict) else {}
    activity = detail.get("activity")
    if not isinstance(activity, dict):
        return None
    try:
        return float(activity.get("score"))
    except (TypeError, ValueError):
        return None


def _note_of(row: OfficialSource) -> str:
    detail = row.check_detail if isinstance(row.check_detail, dict) else {}
    note = detail.get("activity_note")
    if isinstance(note, str) and note:
        return note
    error = detail.get("error")
    if isinstance(error, str) and error:
        return f"Could not read it: {error}."
    return ""


def _asset_report(
    asset: CanonicalAsset,
    rows: Sequence[OfficialSource],
    outcome: AssetSourceOutcome,
    *,
    activity_floor: float,
) -> dict[str, Any]:
    links = [
        {
            "category": category_label(row.category),
            "url": row.source_url,
            "state": state_label(row.verification_state),
            "found_by": row.discovery_layer or "unknown",
            "worth_as_evidence": round(float(row.confidence), 3),
            "activity": _activity_of(row),
            "note": _note_of(row),
        }
        for row in sorted(rows, key=lambda item: (item.priority, item.source_url))
    ]
    quiet = [
        link
        for link in links
        if link["state"] == WORKING
        and link["activity"] is not None
        and float(link["activity"]) < activity_floor
    ]
    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "links": links,
        "working_links_per_category": outcome.coverage,
        "no_working_link_for": [category_label(item) for item in outcome.missing],
        "still_looking_for": [category_label(item) for item in outcome.short],
        "quiet_links": [str(link["url"]) for link in quiet],
        "newly_proved": outcome.proved,
        "withdrawn": outcome.withdrawn,
        "person_asked": outcome.escalated,
    }


def _print_asset(report: dict[str, Any]) -> None:
    coverage = report["working_links_per_category"]
    summary = ", ".join(
        f"{category_label(category)}: {coverage.get(category, 0)}"
        for category in REQUIRED_CATEGORIES
    )
    print(f"\n{report['symbol']} — {report['name']}  ({summary})")
    for link in report["links"]:
        activity = link["activity"]
        score = "  n/a" if activity is None else f"{float(activity):5.2f}"
        print(
            f"   {score}  {link['state']:<13} {link['category']:<22} "
            f"[{link['found_by']}] {link['url']}"
        )
    if report["no_working_link_for"]:
        print(f"   !! no working link for: {', '.join(report['no_working_link_for'])}")
    if report["quiet_links"]:
        print(f"   .. gone quiet: {len(report['quiet_links'])} link(s) — looking for more")
    for line in report["newly_proved"]:
        print(f"   ++ added: {line}")
    for line in report["withdrawn"]:
        print(f"   -- withdrawn: {line}")


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    assets = payload["assets"]
    lines = [
        "# Official source re-check",
        "",
        f"Run at {payload['finished_at']}.",
        "",
        f"- Coins looked at: {payload['assets_checked']}",
        f"- Links checked: {payload['links_checked']}",
        f"- Links that work: {payload['links_working']}",
        f"- Links that work but have gone quiet: {payload['links_quiet']}",
        f"- New links added and proved: {payload['links_added']}",
        f"- Links withdrawn because they are gone: {payload['links_withdrawn']}",
        f"- Coins sent to a person: {payload['people_asked']}",
        "",
        f"Web search: {payload['search']}",
        "",
        "A link's **activity** score says how alive and how relevant that page is: how",
        "recently it published, how many dated items it shows, and whether it talks about",
        "the project's money and its rules. It is not a Shariah status and is never shown",
        "as one. A page below the floor keeps working — the product just looks for more",
        "links to go with it.",
        "",
        "## Coins with no working link in a category",
        "",
    ]
    gaps = [item for item in assets if item["no_working_link_for"]]
    if gaps:
        lines.append("| Coin | Missing |")
        lines.append("|---|---|")
        lines.extend(
            f"| {item['symbol']} — {item['name']} | {', '.join(item['no_working_link_for'])} |"
            for item in gaps
        )
    else:
        lines.append("None. Every coin has at least one working news page and one community page.")
    lines.extend(["", "## Coins whose links have gone quiet", ""])
    quiet = [item for item in assets if item["quiet_links"]]
    if quiet:
        lines.append("| Coin | Quiet links |")
        lines.append("|---|---|")
        lines.extend(
            f"| {item['symbol']} | {len(item['quiet_links'])} |" for item in quiet
        )
    else:
        lines.append("None.")
    lines.extend(["", "## Every coin", ""])
    lines.append("| Coin | News | Community | Quiet | Added | Withdrawn |")
    lines.append("|---|---|---|---|---|---|")
    for item in assets:
        coverage = item["working_links_per_category"]
        lines.append(
            f"| {item['symbol']} "
            f"| {coverage.get('official_news', 0)} "
            f"| {coverage.get('official_community', 0)} "
            f"| {len(item['quiet_links'])} "
            f"| {len(item['newly_proved'])} "
            f"| {len(item['withdrawn'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.activity_floor is not None:
        settings = settings.model_copy(
            update={"sharia_source_activity_floor": args.activity_floor}
        )
    if args.links_per_category is not None:
        settings = settings.model_copy(
            update={"sharia_source_links_per_category": args.links_per_category}
        )

    probe = WebSourceDiscovery(settings)
    # The number of questions matters to whoever pays for the search key: it is asked
    # once per coin, so a full pass over every coin costs coins x questions calls.
    search_state = (
        f"on, using {settings.sharia_source_search_provider} "
        f"({len(SEARCH_QUERIES)} questions per coin)"
        if probe.search_configured
        else f"off. {probe.search_requirement()}"
    )
    print(f"Activity floor: {settings.sharia_source_activity_floor} (default {ACTIVITY_FLOOR})")
    print(f"Links wanted per category: {settings.sharia_source_links_per_category}")
    print(f"Web search: {search_state}")
    if args.dry_run:
        print("Dry run: pages are fetched, nothing is saved.")

    wanted = {value.strip().upper() for value in args.symbol} if args.symbol else set()
    try:
        async with SessionFactory() as session:
            query = (
                select(CanonicalAsset)
                .where(CanonicalAsset.mapping_state == "verified")
                .order_by(CanonicalAsset.symbol.asc())
            )
            if wanted:
                query = query.where(CanonicalAsset.symbol.in_(sorted(wanted)))
            if args.limit:
                query = query.limit(args.limit)
            assets = list((await session.scalars(query)).all())
    except Exception as exc:  # noqa: BLE001 - a person needs a sentence, not a stack
        # This is the first thing the script does, and on a laptop it is the thing that
        # usually fails: the database host in .env is the name the server uses and does
        # not resolve from here. Printing forty lines of Python for that helps nobody.
        print("\nCould not reach the database, so nothing was checked.")
        print(f"What went wrong: {type(exc).__name__}: {str(exc)[:300]}")
        print(
            "Check DATABASE_URL in .env. Run this on the server, or open a tunnel to "
            "the database first."
        )
        return 2

    print(f"\n{len(assets)} coin(s) to check.")
    if not assets:
        print("Nothing to do. No approved coin matched.")
        return 0

    reports: list[dict[str, Any]] = []
    totals = {
        "links_checked": 0,
        "links_working": 0,
        "links_quiet": 0,
        "links_added": 0,
        "links_withdrawn": 0,
        "people_asked": 0,
    }
    for index, asset in enumerate(assets, start=1):
        async with SessionFactory() as session:
            fresh = await session.get(CanonicalAsset, asset.id)
            if fresh is None:
                continue
            service = SourceResolutionService(session, settings, force_recheck=True)
            try:
                outcome = await service.resolve_asset(fresh, deep=True)
            except Exception as exc:  # noqa: BLE001 - one bad coin must not stop the run
                await session.rollback()
                print(f"\n{asset.symbol}: could not be checked — {type(exc).__name__}: {exc}")
                continue
            rows = list(
                (
                    await session.scalars(
                        select(OfficialSource).where(
                            OfficialSource.canonical_asset_id == fresh.id
                        )
                    )
                ).all()
            )
            report = _asset_report(
                fresh, rows, outcome, activity_floor=settings.sharia_source_activity_floor
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
        reports.append(report)
        totals["links_checked"] += len(report["links"])
        totals["links_working"] += sum(
            1 for link in report["links"] if link["state"] == WORKING
        )
        totals["links_quiet"] += len(report["quiet_links"])
        totals["links_added"] += len(report["newly_proved"])
        totals["links_withdrawn"] += len(report["withdrawn"])
        totals["people_asked"] += int(report["person_asked"])
        _print_asset(report)
        print(f"   ({index}/{len(assets)})")

    payload = {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "assets_checked": len(reports),
        "search": search_state,
        "dry_run": bool(args.dry_run),
        **totals,
        "assets": reports,
    }
    print("\n=== Summary ===")
    print(f"Coins checked:            {payload['assets_checked']}")
    print(f"Links checked:            {payload['links_checked']}")
    print(f"Links that work:          {payload['links_working']}")
    print(f"Links gone quiet:         {payload['links_quiet']}")
    print(f"New links added:          {payload['links_added']}")
    print(f"Links withdrawn:          {payload['links_withdrawn']}")
    print(f"Coins sent to a person:   {payload['people_asked']}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"\nWrote {args.json}")
    if args.report:
        _write_report(Path(args.report), payload)
        print(f"Wrote {args.report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Only this coin. Repeatable. Default: every approved coin.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many coins.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report, save nothing.",
    )
    parser.add_argument(
        "--activity-floor",
        type=float,
        default=None,
        help="Override how quiet a link may be before more are looked for.",
    )
    parser.add_argument(
        "--links-per-category",
        type=int,
        default=None,
        help="Override how many working links each category should end up with.",
    )
    parser.add_argument("--json", default="", help="Write the full result to this file.")
    parser.add_argument("--report", default="", help="Write a plain-words report here.")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

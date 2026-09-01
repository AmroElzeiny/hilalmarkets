"""Run the automated screen for real, against coins whose answer is already known.

The point of this script is that it can **fail**. Everything else measuring the Hilal
Markets Methodology so far has read facts somebody wrote knowing the labels, which
measures the fact-writing, not the screen. This starts from a symbol and nothing else:
it asks the market-data provider where the project publishes, reads those pages over the
network, and decides. The known answer is loaded at the very end, only to score with.

Two stages, and the second only runs if the first is perfect:

    --stage control    coins an authority already calls compliant. The screen must agree.
    --stage live       the busiest coins on an exchange, whose answer nobody has.

``--stage live`` refuses to run unless the control stage passed, because a screen that
cannot reproduce a known answer has no business proposing an unknown one.

Nothing here publishes. ``--write`` stores the reading and the Passport; it never sets
``published``, never creates an assessment, and never touches an authority's data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.automated_screen_pipeline import (
    AutomatedScreenPipeline,
    passport_payload,
)
from ai_market_monitor.services.coin_evidence_crawler import CoinEvidenceCrawler
from ai_market_monitor.services.coinmarketcap import CoinMarketCapClient
from ai_market_monitor.services.sharia_evidence_screen import EvidenceVerdict

ROOT = Path(__file__).resolve().parents[1]
PACK = (
    ROOT
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "data"
)
COMPLIANT = PACK / "fasset_compliant_assets.json"

#: Where a passing control run records itself.
#:
#: The gate has to be a real one. This module's docstring said the live stage "refuses to
#: run unless the control stage passed", which was true of the intention and of nothing
#: in the code — a promise in prose is not a gate, and a reader would have believed it.
#: A control run writes its score here; the live stage reads it and refuses without one.
GATE = Path(__file__).resolve().parents[1] / ".automated-screen-control-result.json"


def _record_control(correct: int, graded: int, coins: list[str]) -> None:
    GATE.write_text(
        json.dumps(
            {
                "correct": correct,
                "graded": graded,
                "passed": bool(graded) and correct == graded,
                "coins": coins,
                "recorded_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _control_passed() -> tuple[bool, str]:
    if not GATE.exists():
        return False, "no control run has been recorded yet"
    try:
        result = json.loads(GATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "the recorded control result could not be read"
    if not result.get("passed"):
        return False, (
            f"the last control run scored {result.get('correct')}/{result.get('graded')} "
            f"on {', '.join(result.get('coins', []))}"
        )
    return True, ""


def _known_compliant() -> dict[str, str]:
    """The 188 assets Fasset publishes as Shariah Compliant. The answer key.

    Read here and nowhere else in this script, and never handed to the pipeline. The
    screen sees a ticker and a set of web pages; it has no way to reach this file.
    """

    rows = json.loads(COMPLIANT.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for row in rows:
        symbol = str(row.get("canonical_symbol_candidate") or row.get("symbol_source") or "")
        if symbol:
            out[symbol.upper()] = str(row.get("asset_name_source") or symbol)
    return out


async def _bybit_top(limit: int) -> list[tuple[str, float]]:
    """The busiest spot coins on Bybit right now, by money traded in 24 hours."""

    import httpx

    url = "https://api.bybit.com/v5/market/tickers?category=spot"
    async with httpx.AsyncClient(timeout=30) as client:
        payload = (await client.get(url)).json()
    rows: list[tuple[str, float]] = []
    for row in payload.get("result", {}).get("list", []):
        symbol = str(row.get("symbol") or "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]
        try:
            turnover = float(row.get("turnover24h") or 0)
        except (TypeError, ValueError):
            continue
        rows.append((base, turnover))
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def _print(decision: Any, folder: Any, expected: str | None) -> bool:
    verdict = decision.verdict.value
    ok = expected is None or verdict == expected
    mark = "  " if expected is None else ("OK  " if ok else "MISS")
    print(f"\n{mark} {decision.symbol:<8} {verdict}")
    print(f"     pages read: {decision.documents_read} "
          f"({decision.primary_documents_read} describing the project)")
    for document in folder.documents[:6]:
        print(f"       - [{document.category}] {document.url}")
    if folder.failures:
        for url, code in list(folder.failures.items())[:4]:
            print(f"       x {code}: {url}")
    for reason in decision.reasons[:6]:
        print(f"     reason: {reason.text}")
        if reason.quote:
            print(f"       quote: {reason.quote[:180]}")
    if decision.holder_return_basis:
        print(f"     holder return: {decision.holder_return} "
              f"— {decision.holder_return_basis}")
    if decision.open_questions:
        print(f"     open: {'; '.join(decision.open_questions)}")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("control", "live"), default="control")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=None, help="reproducible control pick")
    parser.add_argument("--symbols", default="", help="comma-separated, overrides picking")
    parser.add_argument("--write", action="store_true", help="store readings and Passports")
    parser.add_argument("--out", default="", help="write the full result as JSON here")
    parser.add_argument(
        "--force", action="store_true", help="run the live stage without a passing control"
    )
    # Measuring and gating are different jobs. A wide accuracy run is *expected* to have
    # misses — some are the screen reading correctly and disagreeing with the authority —
    # and letting it overwrite the gate would close the live stage on a run that was
    # never meant to open it.
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="score the run without recording it as the gate",
    )
    args = parser.parse_args()

    settings = Settings()
    if not settings.coinmarketcap_enabled:
        print("CoinMarketCap is switched off; nothing to probe.")
        return 2

    known = _known_compliant()
    if args.symbols:
        chosen = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif args.stage == "control":
        # Picked by rule, not by taste. Choosing the coins myself would be choosing the
        # ones I expect to pass, which is the circularity this probe exists to avoid.
        pool = sorted(known)
        random.Random(args.seed if args.seed is not None else 20260830).shuffle(pool)
        chosen = pool[: args.count]
    else:
        passed, why = _control_passed()
        if not passed and not args.force:
            print(
                "The live stage is closed: a screen that cannot reproduce a known "
                f"answer has no business proposing an unknown one.\nReason: {why}.\n"
                "Run `--stage control` first, or pass --force to override deliberately."
            )
            return 3
        top = await _bybit_top(args.count)
        chosen = [symbol for symbol, _turnover in top]
        print("Busiest on Bybit in the last 24 hours:")
        for symbol, turnover in top:
            print(f"  {symbol:<8} ${turnover:,.0f}")

    print(f"\nStage: {args.stage}   coins: {', '.join(chosen)}\n" + "=" * 70)

    client = CoinMarketCapClient(settings)
    crawler = CoinEvidenceCrawler(settings)
    records = await client.coin_links(chosen)

    # A database session only when the run is asked to store what it finds. Reading is
    # the default: a probe that writes by accident is a probe nobody dares run.
    session_context = None
    session = None
    if args.write:
        from ai_market_monitor.core.database import SessionFactory

        session_context = SessionFactory()
        session = await session_context.__aenter__()

    pipeline = AutomatedScreenPipeline(
        session=session,  # type: ignore[arg-type]
        settings=settings,
        coinmarketcap=client,
        crawler=crawler,
    )

    correct = 0
    graded = 0
    payload: list[dict[str, Any]] = []
    try:
        for symbol in chosen:
            record = records.get(symbol)
            if record is None:
                print(f"\n--   {symbol:<8} the provider has no record for this symbol")
                continue
            decision, folder = await pipeline.screen_one(symbol, record)
            expected = EvidenceVerdict.ELIGIBLE.value if symbol in known else None
            if expected is not None:
                graded += 1
                correct += _print(decision, folder, expected)
            else:
                _print(decision, folder, None)
            payload.append(passport_payload(decision, record, folder))
            if session is not None:
                # Stores the reading and the Passport. `published` is never set here —
                # only the application's own approval route may do that.
                await pipeline.store(decision, record, folder)
        if session is not None:
            await session.commit()
            print(f"\nStored {len(payload)} readings and Passports. Published: none.")
    finally:
        # A browser was possibly started to read a page whose words only exist after
        # JavaScript. Leaving it running is what produced "Event loop is closed" here.
        await crawler.aclose()
        if session_context is not None:
            await session_context.__aexit__(None, None, None)

    print("\n" + "=" * 70)
    print(f"Provider credits used: {client.usage.credits}")
    if graded:
        print(f"Control result: {correct}/{graded} matched the authority's answer")
        if args.stage == "control" and not args.symbols and not args.measure_only:
            _record_control(correct, graded, chosen)
            print(f"Recorded to {GATE.name}")
        elif args.measure_only:
            print("Measurement only — the gate was not changed.")
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Wrote {args.out}")
    return 0 if not graded or correct == graded else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

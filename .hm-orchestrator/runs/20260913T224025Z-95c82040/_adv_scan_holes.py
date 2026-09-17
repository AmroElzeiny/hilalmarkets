"""Adversarial repro (WP3, attack 5): how strong is the whole-src invariant?

Two questions:
  1. Does the float scan catch a money float whose variable/argument names are not in
     ``_MONEY_NAME``? (``paid``, ``total``, ``kept``, ``balance`` are money names the
     scan vocabulary omits.)
  2. Is the allow-list keyed only on ``(path, stripped line)``, so any new float with
     the same text as an allowed line is silently excused, wherever it is?

Also checks the quantize scan's dependence on a money word on the same line.

Read-only: imports the real test module and feeds it strings; writes nothing.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("tests/unit"))

from test_invariant_money_on_the_wire import (  # noqa: E402
    _NON_CUSTOMER_MONEY_FLOAT,
    _money_float_lines,
    _money_quantize_lines,
)


def main() -> None:
    print("=== float scan: money names the vocabulary omits ===")
    for sample in (
        "        paid = float(value)",
        "        total = float(converted)",
        "        kept = float(remaining)",
        "        balance = float(x)",
        "        revenue = float(row['cents']) / 100",
        "        charge_total = float(raw)",
        "        amount = float(raw)",
    ):
        hits = _money_float_lines(sample)
        verdict = "CAUGHT" if hits else "MISSED"
        print(f"  {verdict:6}  {sample.strip()}   -> {hits}")

    print()
    print("=== float scan: quoted-key shape (does the target count?) ===")
    for sample in (
        '            "price_amount": float(amount),',
        '            "charged_amount": float(converted),',
        '            "amount_paid": float(raw_paid),',
    ):
        hits = _money_float_lines(sample)
        verdict = "CAUGHT" if hits else "MISSED"
        print(f"  {verdict:6}  {sample.strip()}   -> {hits}")

    print()
    print("=== quantize scan: money variable named without a money word ===")
    for sample in (
        "        value = raw.quantize(Decimal(\"0.01\"))  # raw is a refund",
        "        x = money.quantize(Decimal(\"0.01\"))",
        "        commission = x.quantize(Decimal(\"0.01\"))",
    ):
        hits = _money_quantize_lines(sample)
        verdict = "CAUGHT" if hits else "MISSED"
        print(f"  {verdict:6}  {sample.strip()}   -> {hits}")

    print()
    print("=== allow-list is keyed on (path, exact stripped line) ===")
    sample_key = (
        "services/setup_chat_agent.py",
        'actual = float(usage.get("_setup_combined_actual_cost_usd") or 0.0)',
    )
    print(f"  sample allowed key present: {sample_key in _NON_CUSTOMER_MONEY_FLOAT}")
    # Simulate a *new* customer-money float written with the exact same text in the
    # same file: the invariant's offender filter excludes it by key alone.
    simulated_hit = (sample_key[0], sample_key[1], "999: " + sample_key[1])
    excluded = (simulated_hit[0], simulated_hit[1]) in _NON_CUSTOMER_MONEY_FLOAT
    print(
        "  a new identical-looking line in the same file would be excluded by the "
        f"allow-list: {excluded}"
    )
    print(
        "  (the scan never checks the enclosing function or whether the names still "
        "mean model spend)"
    )


if __name__ == "__main__":
    main()

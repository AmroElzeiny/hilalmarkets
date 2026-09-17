"""Throwaway: show what the JSON writer produces for a placeholder collision.

Not a test. Run with:
    .venv/Scripts/python .hm-orchestrator/runs/20260913T224025Z-95c82040/_logic_collision.py
"""
from __future__ import annotations

from decimal import Decimal

from ai_market_monitor.core.money import money_json_dumps


def main() -> None:
    print("--- Decimal + a string that looks like the placeholder ---")
    try:
        out = money_json_dumps({"a": Decimal("9.00"), "b": "__hm_decimal__0__"})
        print("got:", repr(out))
    except ValueError as exc:
        print("ValueError:", exc)

    print("--- Decimal + literal placeholder string in a list ---")
    try:
        out = money_json_dumps({"a": Decimal("9.00"), "b": ["__hm_decimal__0__"]})
        print("got:", repr(out))
    except ValueError as exc:
        print("ValueError:", exc)

    print("--- Two Decimals that both want the same placeholder? ---")
    # The implementation uses len(replacements) for the index, so they get
    # different placeholders.
    out = money_json_dumps({"a": Decimal("9.00"), "b": Decimal("9.00")})
    print("got:", repr(out))


if __name__ == "__main__":
    main()
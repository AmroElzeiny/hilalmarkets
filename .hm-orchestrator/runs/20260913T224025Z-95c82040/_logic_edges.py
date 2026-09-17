"""Throwaway: edge cases for money_json_dumps.

Not a test. Run with:
    .venv/Scripts/python .hm-orchestrator/runs/20260913T224025Z-95c82040/_logic_edges.py
"""
from __future__ import annotations

from decimal import Decimal

from ai_market_monitor.core.money import money_json_dumps, json_money_number


def main() -> None:
    print("--- A negative Decimal ---")
    out = money_json_dumps({"v": Decimal("-9.00")})
    print(repr(out))

    print("--- A negative Decimal via json_money_number ---")
    print(repr(json_money_number(Decimal("-9.00"))))

    print("--- A Decimal with trailing zeros ---")
    out = money_json_dumps({"v": Decimal("9.10")})
    print(repr(out))

    print("--- A Decimal that is a very small number (e.g. $0.01) ---")
    out = money_json_dumps({"v": Decimal("0.01")})
    print(repr(out))

    print("--- A Decimal with high precision (more digits than 2dp) ---")
    out = money_json_dumps({"v": Decimal("0.123")})
    print(repr(out))
    print("(0.123 is NOT quantised — the caller must pass the quantised value.)")

    print("--- A Decimal with scientific notation input ---")
    print(repr(str(Decimal("1E+2"))))  # = 1E+2 string


if __name__ == "__main__":
    main()
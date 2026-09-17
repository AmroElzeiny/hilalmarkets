"""Throwaway: show what the JSON writer produces for some interesting cases.

Not a test. Run with:
    .venv/Scripts/python -m pytest .hm-orchestrator/runs/20260913T224025Z-95c82040/_logic_decimal_key.py -s -q
or just:
    .venv/Scripts/python .hm-orchestrator/runs/20260913T224025Z-95c82040/_logic_decimal_key.py
"""
from __future__ import annotations

from decimal import Decimal

from ai_market_monitor.core.money import money_json_dumps, json_money_number


def main() -> None:
    print("--- bare exact text ---")
    out = money_json_dumps({"v": json_money_number(Decimal("9.00"))})
    print(repr(out))

    print("--- Decimal held in a list ---")
    out = money_json_dumps({"xs": [Decimal("9.00"), Decimal("0.99")]})
    print(repr(out))

    print("--- Decimal held in a tuple ---")
    out = money_json_dumps({"ys": (Decimal("9.00"), Decimal("0.99"))})
    print(repr(out))

    print("--- Same amount twice ---")
    out = money_json_dumps({"a": Decimal("9.00"), "b": Decimal("9.00")})
    print(repr(out))

    print("--- Nested structure ---")
    out = money_json_dumps({"plans": [{"code": "trader", "price": Decimal("9.00")}]})
    print(repr(out))

    print("--- kwargs pass-through (sort_keys) ---")
    out = money_json_dumps({"b": 1, "a": Decimal("9.00")}, sort_keys=True)
    print(repr(out))

    print("--- kwargs pass-through (indent) ---")
    out = money_json_dumps({"a": Decimal("9.00")}, indent=2)
    print(repr(out))

    print("--- A user-controlled string carrying the placeholder ---")
    try:
        money_json_dumps({"v": "__hm_decimal__0__"})
    except ValueError as exc:
        print("ValueError:", exc)

    print("--- A NaN ---")
    try:
        money_json_dumps({"v": Decimal("NaN")})
    except ValueError as exc:
        print("ValueError:", exc)

    print("--- Decimal text could be the token text? (uncommon) ---")
    # Construct a Decimal whose textual form is exactly the placeholder:
    risky = Decimal("__hm_decimal__0__")
    try:
        money_json_dumps({"v": risky})
    except ValueError as exc:
        print("ValueError:", exc)


if __name__ == "__main__":
    main()
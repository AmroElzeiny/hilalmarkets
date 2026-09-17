"""Throwaway: what does money_json_dumps do with scientific notation?

Not a test. Run with:
    .venv/Scripts/python .hm-orchestrator/runs/20260913T224025Z-95c82040/_logic_sci.py
"""
from __future__ import annotations

from decimal import Decimal

from ai_market_monitor.core.money import money_json_dumps


def main() -> None:
    print("--- Decimal with scientific notation ---")
    out = money_json_dumps({"v": Decimal("1E+2")})
    print(repr(out))
    # 1E+2 is 100. The Decimal text is "1E+2" — is that valid JSON number syntax?
    # Yes: RFC 8259 allows e.g. "1e2" or "1E+2". But the JSON loader may not
    # preserve the exact representation. That's fine for a money value.

    print("--- Decimal NaN (already fails) ---")
    try:
        money_json_dumps({"v": Decimal("NaN")})
    except ValueError as exc:
        print("ValueError:", exc)


if __name__ == "__main__":
    main()
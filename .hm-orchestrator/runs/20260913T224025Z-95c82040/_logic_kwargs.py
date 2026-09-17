"""Throwaway: explore the kwargs behaviour of money_json_dumps.

Not a test. Run with:
    .venv/Scripts/python .hm-orchestrator/runs/20260913T224025Z-95c82040/_logic_kwargs.py
"""
from __future__ import annotations

import json
from decimal import Decimal

from ai_market_monitor.core.money import money_json_dumps


def main() -> None:
    print("--- Default kwarg: should it affect Decimals? ---")
    # If user passes default=str, our placeholder strings are str, so the JSON
    # encoder doesn't call default on Decimals. So the Decimal handling is
    # unaffected by default.
    out = money_json_dumps({"a": Decimal("9.00")}, default=str)
    print("with default=str:", repr(out))

    print("--- Jinja's tojson calls with sort_keys=True; let's check that ---")
    out = money_json_dumps({"b": 1, "a": Decimal("9.00")}, sort_keys=True)
    print("with sort_keys=True:", repr(out))
    assert out == json.dumps({"b": 1, "a": "9.00"}, sort_keys=True) or "9.00" in out
    # Just confirm 9.00 is the exact text:
    assert '"a": 9.00' in out
    print("a 9.00 with sort_keys confirmed.")

    print("--- A payload where the value is a Decimal not under our key ---")
    out = money_json_dumps({"a": [Decimal("9.00"), "x"]})
    print(repr(out))


if __name__ == "__main__":
    main()
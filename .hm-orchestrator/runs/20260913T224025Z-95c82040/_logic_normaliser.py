"""Throwaway: what does the Stripe dispute normaliser produce?

Not a test. Run with:
    .venv/Scripts/python .hm-orchestrator/runs/20260913T224025Z-95c82040/_logic_normaliser.py
"""
from __future__ import annotations

from ai_market_monitor.services.billing import BillingService


def main() -> None:
    out = BillingService._normalize_provider_payload(
        "stripe",
        {
            "id": "evt_d_1",
            "type": "charge.dispute.created",
            "data": {
                "object": {
                    "id": "dp_1",
                    "object": "dispute",
                    "amount": 1700,
                    "charge": "ch_1",
                    "metadata": {},
                }
            },
        },
    )
    print("dispute normalised:")
    print(repr(out))
    assert out["data"].get("refunded_amount") is None
    assert out["data"].get("refunded_total_is_cumulative") is True  # still set even though refunded_amount is None

    print()
    out = BillingService._normalize_provider_payload(
        "stripe",
        {
            "id": "evt_ch_1",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_1",
                    "object": "charge",
                    "amount": 1700,
                    "amount_refunded": 850,
                    "currency": "usd",
                    "metadata": {"user_id": "u"},
                }
            },
        },
    )
    print("charge.refunded normalised:")
    print(repr(out))
    assert out["data"]["refunded_amount"] == "8.50"
    assert out["data"]["refunded_total_is_cumulative"] is True


if __name__ == "__main__":
    main()
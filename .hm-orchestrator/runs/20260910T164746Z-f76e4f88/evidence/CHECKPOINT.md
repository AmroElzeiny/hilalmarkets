# Checkpoint ledger (written as work proceeds)

Start: 2026-09-10T16:5xZ. Branch: cloudflare-access-service-tokens. Base commit: 145275f2. 208 files already modified/untracked before this run started.

## Progress log
- 2026-09-10 exploration: WP1-3 verified mostly done; gaps = R1 last-child not true/tested, contrast test exact-value not threshold.
- WP4 DONE by worker (subscription jump bar removed, settings one-list refactor g-data link added, tests added, ruff/pytest browser+integration green).
- WP9.1 DONE by worker (checkout.html + billing portal/billing.html + dashboard.py title beginner wording; 26 new cases; jinja + release-invariants checks PASS). Escalation follow-ups noted: billing_result.html paragraph jargon, hilalmarkets-billing.js provider line (listed, not changed).
- WP8 ESCALATED: Creem live docs confirm only proration-charge-immediately / proration-charge(deprecated, identical) / proration-none. None charges full price today or restarts period. See ESCALATION.json.
- WP5 DONE by worker: _billing_history_rows (dashboard.py) is the single next-step owner; row fields next_step/blocked_reason; both surfaces read it; banner reads owner; data-s-choose / data-billing-dialog-trigger retry (no new charge route); 19 unit + full focused suites 892 passed; mypy clean; release invariants PASS. plan_is_on_sale actually at services/billing.py:522 (mission path wrong).
- WP1-3 gaps DONE by worker: market.html note moved before button (button now last child of div.t-controls); new last-child test parametrised over 3 pages (reproducer failed before fix); contrast test adds WCAG floors (>=4.5 text, >=3.0 non-text) while keeping exact values. Ruff green re-verified after both workers finished.
- Noted for later: vision reviewer must re-check market.html filter bar layout after note moved (button now its own row). billing_result.html jargon + hilalmarkets-billing.js "provider" line remain open items (were out of worker 9.1 scope; fix next).

# WP3 DESIGN — money serialisation and rounding owner (R3, R4). Supervisor-authored, binding.

Baseline: the WP1/WP2/WP4 changes are already in the tree. Do not touch the mission's
concurrency files (`api/routers/dashboard.py`, `api/routers/dashboard_test.py`,
`telegram/*`, `pyproject.toml`, `.gitignore`, ...). Do not commit/push/reset/checkout/clean/stash.
Never read `.env`/`.env.production`.

## Defect class

Two owners of money disagree with `core/money.py`:

1. `core/plans.py:330-340` (`plan_offer_payload`) converts every price through `float`.
   `float(Decimal("9.00"))` is `9.0` — the cents text is gone — and `10.05` has no binary
   representation. That payload is embedded in the React landing page and the dashboard as
   JSON (`react_site.html` `public_pricing_plans|tojson`, `subscription.html`
   `plan_cards|tojson`), where the contract is a JSON **number** (`Pricing.tsx` types it
   `number | null` and does arithmetic on it).
2. Five money paths `quantize` with the default mode (`ROUND_HALF_EVEN`), not the owner's
   `ROUND_HALF_UP`: `services/billing.py` `_stripe_amount`/`_minor_unit_amount`,
   `services/affiliate_attribution.py:529,536`, `services/affiliate.py:366,772-777,813`,
   plus `api/template_env.py:101` (`reward_amount`) and `services/payment_emails.py:524`,
   both found by the whole-`src/` search.

## (A) `core/money.py` is the serialisation owner — R3

Add to `src/ai_market_monitor/core/money.py`:

- `quantise_half_up(value: Decimal, quantum: Decimal) -> Decimal` — the one rounding call
  (`value.quantize(quantum, rounding=ROUND_HALF_UP)`). Re-express `quantise` in terms of it
  so the module has one rounding statement.
- `json_money_number(amount: Decimal, currency: str = "USD") -> Decimal` — returns the
  amount quantised to the currency's minor unit, as a `Decimal` (the value that will be
  written as a JSON number).
- `money_json_dumps(value: object, **kwargs: object) -> str` — a `json.dumps` **replacement
  that writes every `Decimal` as a bare JSON number using its exact text**, never a float
  and never a quoted string. Implement it with the same placeholder technique as
  `wire_json_body`: walk dicts/lists, swap each `Decimal` for a unique token string,
  `json.dumps` the rest (`**kwargs` passed through, so Jinja's `sort_keys=True` still
  applies), then replace each token's quoted form with `str(decimal)`. Fail closed
  (`ValueError`) if a token is not unique in the output or the Decimal is not finite.
  Document that `Decimal("9.00")` must come out as `9.00`, not `9.0`.

Wire it as the Jinja JSON writer in `src/ai_market_monitor/api/template_env.py`
`register(...)`: `templates.env.policies["json.dumps_function"] = money_json_dumps`.
(Jinja's `tojson` reads `json.dumps_function`; confirmed the current environment leaves it
`None` and raises `TypeError: Object of type Decimal is not JSON serializable`.) This is
how the React number contract is kept without changing a template or the bundle.

Then `core/plans.py` `plan_offer_payload` returns `json_money_number(...)` instead of
`float(...)` for all four fields (`monthlyPrice`, `annualPrice`, `originalMonthlyPrice`,
`fullMonthlyPrice`). Remove the `float` calls entirely. The values are `Decimal`; existing
callers are safe: templates use `| int`, tests compare `== float(...)` (`Decimal == float`
is value comparison), and the JSON boundary now emits exact numbers.

## (B) The rounding owner — R4

Every money `quantize` in `src/ai_market_monitor` must go through `core/money.py`:

- `services/billing.py` `_stripe_amount`, `_minor_unit_amount`: minor units ÷ 100 then
  `quantise_half_up(..., Decimal("0.01"))` (or a small `from_minor_units` helper in
  `core/money.py` if you prefer; do not write a second divisor).
- `services/billing.py` `_validate_paid_amount_and_currency` return: route through
  `quantise_half_up`.
- `services/affiliate_attribution.py:529,536`.
- `services/affiliate.py:366` (`_percent`), `772-777`, `813`.
- `api/template_env.py:101` (`reward_amount`).
- `services/payment_emails.py:524`.
- `services/plan_replacements.py:127,133` already pass `ROUND_HALF_UP`; route them through
  `quantise_half_up` too so they share the owner.
- `core/plans.py:231,235,252` already pass `ROUND_HALF_UP`; route them through
  `quantise_half_up(amount, _CENTS)` so the discount owner does not hold a second rounding
  statement. (`core/plans.py` may import from `core/money.py`; no cycle — `money.py` imports
  only stdlib.)
- Leave only `core/money.py` with a `.quantize(` on money. `services/trials.py:717`
  quantises a coverage fraction (not money) and stays.

## (C) The source invariants — extend the D3 test

In `tests/unit/test_invariant_money_on_the_wire.py`:

1. **Whole-`src` float scan.** Replace the `_MONEY_MODULES` parametrisation with one scan
   over every `.py` under `src/ai_market_monitor`. Keep the existing `_FLOAT_SITE` and
   `_MONEY_NAME` idea, but a hit is only acceptable if it is in an explicit, commented
   `_NON_CUSTOMER_MONEY_FLOAT` allow-list keyed by `(relative_path, stripped_line)`, so a
   new money float anywhere fails. Do NOT put the `core/plans.py` price lines in the
   allow-list — those must be gone after (A). Reason each entry briefly. The current tree
   hits (regenerate the exact list yourself and verify it) are:
   - market data (a crypto/asset price or a percentage, not money a customer pays):
     `services/market_preview.py`, `engine/evaluator.py`, `services/verified_strategy.py`,
     `api/routers/dashboard_api.py`, `api/routers/dashboard.py` (`percent_off`),
     `telegram/rendering.py` (`target_prices` — do not edit telegram).
   - model/token spend telemetry (`*_cost_usd`, the metrics/telemetry layer is float-typed
     and is not customer money): `services/ai_spend.py`, `services/setup_chat_agent.py`,
     `services/system_brain_agent.py`.
   Add a self-check that the scan still catches the reported defect shape (`float(charged)`,
   `"price_amount": float(amount)`) and that it leaves a market price (`float(target_price)`)
   and a model cost (`float(actual_cost_usd)`) alone because those are allow-listed, with a
   comment saying why.
   Note in the report: R3 is enforced as "no float on a **customer** money value anywhere in
   src"; market prices and model/token spend are explicitly out of that vocabulary (a float
   there is a metrics-layer type, not a charged price). Surface this as an interpretation.

2. **Money-quantize scan.** Add a test over every `.py` under `src/ai_market_monitor` except
   `core/money.py`: a line containing `.quantize(` must not also carry a money word
   (`amount|price|refund|owed|commission|charge|fee|discount|usd|percent|paid|total`) in the
   line. Add a self-check that it catches `x.quantize(Decimal("0.01"))` on a money name and
   leaves the `trials.py` coverage fraction alone.

3. **Half-cent commission case.** Add a test that `1.00 × 12.5 % → 0.13` under the owner
   (`quantise_half_up(Decimal("1.00") * Decimal("12.5") / Decimal("100"),
   Decimal("0.01")) == Decimal("0.13")`), and a service-level assertion that
   `ReferralAttributionService` records `commission_usd == Decimal("0.13")` for a
   `$1.00` payment at `12.5%` if that path is drivable with the existing fixtures; if it is
   not, say so and keep the owner-level assertion.
   State in the report: stored commission rows are never rewritten — only new ones use the
   one rule.

4. **Exact JSON number proof, the whole family.** Add a test over `MONEY_FAMILY` (already in
   the file: every plan monthly, every annual, every offer and every discount percent) that
   `money_json_dumps({"v": json_money_number(amount)})` contains `"v":<exact 2-decimal text>`
   unquoted, parses back with `json.loads(..., parse_float=Decimal)` to the exact amount, and
   `b'"v":"' not in` it. Add a self-check that at least one family amount loses its text
   through float while the owner keeps it.
   Also add a test that `plan_offer_payload` values are `Decimal` (not `float`) for every
   plan, and a test that renders the landing page and finds the bare-number token for a
   whole-dollar price in the raw HTML (proving the React contract stayed a number and the
   template did not change).

## Files you may edit
`core/money.py`, `core/plans.py`, `services/billing.py`, `services/affiliate.py`,
`services/affiliate_attribution.py`, `services/plan_replacements.py`,
`services/payment_emails.py`, `api/template_env.py`,
`tests/unit/test_invariant_money_on_the_wire.py`, and a small new test file if the
affiliate half-cent/JSON proof needs one.

## Verification
- `.venv\Scripts\python -m pytest tests/unit/test_invariant_money_on_the_wire.py tests/unit/test_invariant_discount_codes.py tests/unit/test_billing_entitlements.py tests/unit/test_invariant_dynamic_universe.py tests/unit/test_invariant_affiliate_attribution.py tests/unit/test_invariant_affiliate_programme.py tests/integration/test_launch_offer_pricing.py tests/integration/test_landing_page.py tests/integration/test_hilal_public_site.py tests/integration/test_dashboard_web.py -q -p no:randomly --junitxml=.hm-orchestrator/runs/20260913T224025Z-95c82040/WP3_NEIGHBOURS.xml`
- The new invariant tests must fail on the pre-change code (record `WP3_BEFORE.xml`) and pass after (`WP3_AFTER.xml`). You may capture BEFORE by writing the tests first and running them against the current tree (plans.py still floats) before editing source.
- `ruff check` on changed files, `mypy src/ai_market_monitor`, `scripts/check_jinja_templates.py`.
- Check no other pytest is running first; never run two at once; do not kill processes.
- Do not weaken, skip, delete or rewrite an existing test, except the deliberate extension of
  the D3 scan described above (which is the assignment).

## Forbidden
Changing the React bundle or any template; changing the dashboard `billing_plan_data` string
contract; weakening an existing assertion; a second JSON writer; a second rounding rule.

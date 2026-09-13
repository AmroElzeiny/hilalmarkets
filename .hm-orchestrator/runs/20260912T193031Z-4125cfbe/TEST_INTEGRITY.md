# Test integrity audit (R9)

Run: 20260912T193031Z-4125cfbe. Baseline: 75b19580. Branch: cloudflare-access-service-tokens.

Two independent reviewers (minimax-m3 and deepseek-v4.1-flash) read the diff for the
fixes made after R1/R2; their reports are `REVIEW_FIXES_LOGIC.md` and
`REVIEW_FIXES_ADVERSARIAL.md`. This audit answers the five questions directly and says
what was searched.

## 1. Was any test deleted?

**No.**

Search: `git status --porcelain` on the working tree — no `D` entry under `tests/`. The
only deletion in the whole tree is `.claude/settings.local.disabled.json`, which is not a
test and pre-dates this run. The stopped run's `R5_DELETION_LISTING.txt` deletes PNG
screenshots only.

## 2. Was any `skip`, `skipif`, `xfail` or `importorskip` added?

**No — one was added by the stopped run, and this run removed it.**

Search: `Get-ChildItem -Recurse tests -Include *.py | Select-String "pytest\.skip|pytest\.mark\.skip|skipif|xfail|importorskip"`
over every file this mission touched (`test_paid_plan_replacement`, `test_plan_change_journey`,
`test_operational_issue_queue`, `test_invariant_annual_only_plan_card`, `test_visual_fixes_e2e`,
`test_invariant_plan_checkout_availability`, `test_checkout_and_payment_email`,
`test_held_plan_renewal_words`, `test_checkout_pay_button_e2e`).

| File | Skip found | Origin | Action |
|---|---|---|---|
| `tests/browser/test_visual_fixes_e2e.py` | `pytest.skip("designed ellipsis …")` (new, added by the stopped run) | new file | **removed** by this run; replaced with an ordinary assertion that the ellipsis is really declared. Re-run: 4 passed, 0 skipped. |
| `tests/integration/test_checkout_and_payment_email.py:277` | `pytest.skip(f"{plan_code} is not on sale…")` | **present at HEAD** (`git show HEAD:…` contains the same line) | left as-is; not new. |

After the edit the scan shows only the pre-existing HEAD skip. No new `skipif`, `xfail`
or `importorskip` anywhere.

## 3. Was any assertion widened or loosened?

**No, on the evidence available.**

- The two independent reviewers read the post-R1/R2 fixes and both report no weakened
  assertion. The adversarial reviewer specifically records: "the one removed assertion in
  `test_paid_plan_replacement.py` was replaced by stronger dedupe-key/severity/evidence
  checks."
- Search: the changed test files were read in full by both reviewers. The parametrised
  owner-property tests were **widened in scope** (card-monthly only → held card-monthly,
  card-annual and crypto × both ordered pairs = 6 cases per property), with the existing
  assertions kept. `cancel_calls == 1` is kept for a card-held plan and a new
  `cancel_calls == []` is asserted for a crypto-held plan (there is no card to cancel).
- No `assert True`, no removed `assert`, and no `!=` substituted for `==` was introduced
  by this run's fixes.

## 4. Was any expected value changed?

**Yes — three assertions, all for the deliberate consent-copy change (fix 3), plus one
test rename. The product rule is the authority, not convenience.**

| Test | Old expectation | New expectation | Why the product rule moved it |
|---|---|---|---|
| `test_plan_change_journey.py` (billing-page note) | `"You pay the {plan} price in full today"` | `"You pay the {plan} price today"` | A discount code on the payment page lowers the real charge, so "in full" was not true. The owner's rule (full-price checkout, unused value returned within 48 hours) is unchanged; only the untrue word was removed. |
| `test_plan_change_journey.py` (consent sentences, 2 asserts on `CONSENT_PLAN_REPLACEMENT`) | constant said "…price in full today" | constant now says "…price today" | Same rule; the consent must say what is charged, not "in full". |
| `test_checkout_and_payment_email.py` / browser consent asserts | contained the old constant | assert the new constant | Same constant; re-checked by the browser run below. |
| `test_paid_plan_replacement.py` | test named `…card_move…` | renamed `…move…` | Scope widened to held card **and** crypto. Assertions kept; no value loosened. |

`CONSENT_PLAN_REPLACEMENT` is one constant read by every surface, so the copy cannot
drift between the billing popup, the review page and the subscription popup.

## 5. Was any test-only hardcode or special case added?

**No new one found.**

- Fix 2 (resurrection guard) asserts an exact 200-character cap on the alert summary by
  AST measurement, not by hardcoding a truncated string.
- Fix 1 (dedupe key) tests the owner's sanitiser directly, parametrised over four
  provider-id shapes, plus a real billing-level test.
- The annual-only invariant reads the dashboard source to prove the any-cycle gate; it
  does not special-case a plan.
- The two reviewers searched for test-only branches and report none.

## What this audit did **not** prove

- It did not re-run a byte-for-byte semantic diff of all 1,347 deleted lines in the
  working tree; the two reviewers read the mission-relevant files, and this audit relied
  on their reading plus the targeted searches above.
- It cannot prove the working tree has no unrelated pre-existing test edits made before
  this mission; those are out of this mission's scope and were left untouched.

# Final test runs on the working tree, 2026-09-11

One test process at a time, `-p no:randomly`, no `--timeout`.

| Check | Result | File |
|---|---|---|
| ruff (`src tests scripts`) | all checks passed | `ruff_final.txt` |
| mypy (`src`) | no issues in 426 files | `mypy_final.txt` |
| Billing batch: 17 files by path, including `test_plan_change_journey.py`, `test_dashboard_test_subscription.py`, `test_checkout_and_payment_email.py` | **765 passed, 0 failed, 0 skipped** | `billing_batch_final.txt` |
| Broad suite: `tests/unit tests/engine tests/interpreter tests/services` | **27691 passed, 0 failed, 489 skipped** | `broad_suites_final.txt` |

## Compared with the committed code (HEAD 75b19580)

| Run | Failed | Which |
|---|---:|---|
| Broad suite at HEAD (`broad_suites_head.txt`) | 10 | 9 in `test_billing_entitlements.py` (their servers could not sell crypto, so checkout refused before the test began), 1 guide-registry test (a marker inside a macro in `settings.html`) |
| Broad suite now | 0 | both groups fixed: the entitlement servers are inputs only, the template was fixed and the test left alone |

The HEAD file has no final count line (the run printed the failures and stopped there), so
the skip count cannot be compared number to number. The skips were checked line by line
instead, below.

## Skips and expected failures

Every `pytest.skip`, `skipif`, `xfail` or `importorskip` line added under `tests/` since the
baseline (145275f2), tracked files:

| Line | File | Where it came from |
|---|---|---|
| `pytest.skip("Affiliate browser coverage requires the auto-started database.")` | `tests/browser/test_affiliate_page_e2e.py` | the committed code (between the baseline and HEAD), not this work |
| `pytest.skip("this plan is running a launch price")` | `tests/unit/test_invariant_dynamic_universe.py` | the committed code; it rewords an older skip (`"the Monitor plan is the one with a launch code"`), so it is not a new skip |

The new test files from this work (`test_invariant_pages_close_their_tags.py`,
`support/html_balance.py`, `test_ask_ai_corner_tag_e2e.py`,
`test_invariant_templates_compile.py`) and `test_paid_plan_replacement.py` contain no skip and
no xfail.

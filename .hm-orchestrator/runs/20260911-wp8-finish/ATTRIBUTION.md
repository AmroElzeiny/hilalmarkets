# Second pass: which change makes which test pass

Method (CLAUDE.md "Verification"): a clean copy of the committed code, `C:\wt-head` at
HEAD `75b19580`. The second-pass tests were copied onto it and run first on the committed
code (red), then again with only the changed source files copied over (green). The copy was
put back afterwards with `git checkout -- src tests`.

| Run | What was on the copy | Result | File |
|---|---|---|---|
| Red | committed code + the second-pass tests | **23 failed, 102 passed** | `wt_red.txt` |
| Red, queue test | committed code + the rename in `observability/labels.py` only (the test imports the new public name) | **1 failed, 17 passed** | `wt_red_queue.txt` |
| Green | committed code + seven changed source files | **143 passed, 0 failed** | `wt_green.txt` |

The seven source files in the green run: `services/billing.py`,
`services/plan_replacements.py`, `observability/issues.py`, `observability/labels.py`,
`templates/hilal/dashboard_test/settings.html`, `templates/hilal/base_dashboard.html`,
`api/template_env.py`.

## What failed on the committed code, and why

| Failing tests | Count | Cause on the committed code |
|---|---|---|
| `test_paid_plan_replacement.py` (card move, renewal, two pages, unsafe move, failed cancel) | 9 | no staff notice for money owed; a renewal ran the move again; two payment pages left two live plans; the failure alert was 216 characters and was itself refused |
| `test_invariant_pages_close_their_tags.py`, every signed-in page | 13 | `base_dashboard.html` does not compile at HEAD (a `{# #}` comment inside a `{% set %}` dictionary), so no signed-in page can be drawn |
| `test_invariant_dashboard_guide_registry.py::test_markers_are_never_placed_inside_a_repeating_block` | 1 | `settings.html` put a guide marker inside a macro |
| `test_operational_issue_queue.py::test_the_queue_and_the_content_check_hold_one_length_limit` | 1 | the queue allowed 240 characters, the content check 200 |

The entitlement tests and the new crypto test pass on the committed code: their change was
inputs only, and the crypto test guards a rule that already held.

## Left behind on the copy

`git checkout` restores tracked files only. Two new, untracked test files stay in
`C:\wt-head`: `tests/integration/test_invariant_pages_close_their_tags.py` and
`tests/support/html_balance.py`. The tool refuses to delete anything under `C:\wt-head`.

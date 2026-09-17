# Supervisor report — run 20260917T171532Z-1c371707

This run finishes the mission "Active AI features move to OpenCode Go Muse, and Hilal sees the page and the Passports".
It continues run 20260916T184200Z-dd1e00e6. The old run was stopped when the machine stopped, not because the work failed.
Nothing below relaxes the original mission. Every requirement R1–R23 is covered.

Final verdict: **COMPLETE_VERIFIED**. No escalation. No blocker.

## 1. Start point

| Item | Value |
|---|---|
| Branch and commit | main, 9a19d488 |
| Work tree at start | 113 changed/untracked entries; the mission files of the old run were already in the tree |
| True baseline | original run's BASELINE_STATUS.txt + BASELINE_DIFF.patch; mission changes = tree minus that baseline = 47 entries |
| Unrelated work | money/billing/affiliate files and orchestrator bookkeeping were already changed before the mission; never touched |
| Models | supervisor Muse Spark 1.3 Contributor (high); workers Muse Spark 1.3 Contributor (high); logic reviewer GLM-5.3-Flash; adversarial reviewers Qwen3.7 Plus |

## 2. What was asked and what is done

All 23 requirements pass. Short version:

- R1, R7: every AI call site is mapped ACTIVE/STALE; the Hilal coin scraper needs no model (it only reads pages).
- R2–R6: landing chat, Hilal agent, Sharia research, System Brain assistant and tool agent, and the coin-website bot all use Muse Spark 1.3 Contributor on high effort through OpenCode Go. Proven with invariant tests plus one real call per feature.
- R8: stale strategy-from-prompt features are untouched. Zero stale files in the diff. Checked twice.
- R9–R11, R13, R14: one owner module decides provider and key; settings in all four env files; startup and checks use the owner; Muse pricing from the live model catalog; the new key is treated as a secret everywhere.
- R12: budgets and timeouts were sized from real measured calls. Fresh calls show no truncation, so budgets stay as they are.
- R15–R21: Hilal sees page data on all 11 pages, card inputs filled and empty, the person's own records only, Passports per standard, methodology origins, keeps the Sharia authority rules, and the payload stays under its size cap.
- R22: tests ran at milestones only, plus one full run at the end.
- R23: everything found on the way is fixed and listed below.

## 3. Real calls and what they cost (small money)

| Feature | Input words (tokens) | Output tokens | Thinking tokens | Time | Cost USD |
|---|---|---|---|---|---|
| Landing chat (R2) | 1,172 | 2,657 | 2,469 | 16.8 s | 0.00065 |
| Hilal turn (R3, recorded 2026-09-17, not repeated) | 5,356 | 1,653 | — | 15.5 s | 0.00087 |
| Sharia dossier (R4) | 978 | 2,438 | 1,804 | 12.7 s | — |
| System Brain tool loop (R5) | 10,000 | 2,669 | 1,942 | ~25 s | 0.00107 |

High effort spends most output on thinking. That is why budgets are large. No call was cut off. Total new spend this run: about USD 0.003.

## 4. Problems found and fixed

Beyond the mission, reviewers and workers found real defects. All are fixed with tests:

1. Old run, blocker: the shared client did not pass the conversation key. Fixed with a test.
2. This run, big finding: a full monitor board (about 89,000 characters) did not fit the 30,000 cap — only Passport rows were cut. Now the code cuts card rows, checklist items, and monitor rows too, last first. It never refuses the turn and never invents an answer.
3. This run: the "cut for size" flag was set even when nothing was cut. Now it is set only when something really was cut.
4. This run: a page sending a plain string failed the whole turn (error 422). Now the page code wraps it as a summary. Proven: the schema rejects the bare string, the new code accepts it.
5. Noted and left: one test script publishes the same page name twice (harmless, second replaces first).

## 5. Checks and reviews

| Check | Result |
|---|---|
| Lint (ruff, src tests scripts) | clean |
| Types (mypy, 429 files) | clean |
| Full offline suites | 28,636 passed, 0 failed, 492 skipped (skips are old, none new) |
| Integration (chat, Hilal, System Brain, Sharia) | 167 passed |
| Browser (Hilal page context) | 3 passed |
| Logic review (different model family) | approved with notes; all notes fixed |
| Adversarial review (different model family) | approved, 15 attacks defeated |
| Final adversarial pass over the whole diff | approved, zero findings |
| No test was deleted, skipped, or weakened | confirmed |
| whitespace check (git diff --check) | clean |

## 6. Server step (for the operator)

On the server, add the new key and the changed model/effort/budget/cap keys to `.env.production`, then redeploy.
Locally the shape is already proven: copy only the changed keys in Python, back up first, and prove the key count rose by exactly the number added with no other value changed.
Never print a value. Key names and counts only.

Changed key groups: `OPENCODE_GO_API_KEY`, `OPENCODE_GO_BASE_URL`, the five `*_AI_MODEL` + `*_REASONING_EFFORT` pairs, `OPENAI_MODEL_PRICING_USD_PER_MILLION` (Muse entry added), the `*_MAX_OUTPUT_TOKENS` / `*_TIMEOUT_SECONDS` budgets, `HILAL_CHAT_EVIDENCE_MAX_CHARS`.

## 7. Three facts the owner must know

1. Privacy: Muse Spark 1.3 Contributor has training enabled, is not zero-retention, and is region limited. The owner chose this model. User text sent to it may be used for training.
2. Quota risk: Sharia research batches and all active features share the same OpenCode Go quota. A big research batch can slow down chat answers.
3. This run is a continuation. Milestones 1433 (WP2) and 617 (WP3) are cited from the old session; their logs died with it. Everything else above was measured in this run.

## 8. What is still open

- Nothing blocks. One theory-level note: if a payload ever holds only fixed records with nothing left to cut, the cap rests on per-field caps alone. The realistic maximum (27,701) fits under the 30,000 cap.
- The System Brain assistant token counts and the dossier true cost were not printed by the old script. Small gap in the table, no product effect.
- WP-C used 5 provider calls instead of literally 4, because the tool loop needs two calls by nature. Spend stayed tiny.

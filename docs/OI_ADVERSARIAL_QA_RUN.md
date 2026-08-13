# First adversarial QA run — 2026-08-14

**Commit:** `211aecc5` (branch `phase5-closeout`), start and end.
**Target:** local `APP_ENV=test`, throwaway SQLite, mock providers. Never production.
**Corpus:** 1.1.0, 51 cases. **Attack catalogue:** 2026-08-14.2, 24 attacks.
**Spend:** **$0.0018**, against a $0.25 ceiling — one readiness probe against the isolated
target. Every other finding below is deterministic and offline.

Reproduce all of it in one command:

```powershell
.venv\Scripts\python tools\oi\reproduce_findings.py
```

---

## 1. The short version

**The outer boundaries hold.** Nothing reached an admin page or the System Brain. Nothing
was approved by accident, in any of three languages. No page said anything it was
forbidden to say, on ten authenticated surfaces and nine public ones. Every Shariah status
had its Evidence Passport within one click, and the Passport carried its authority,
methodology, version and decision date. Eighteen browser attacks and every authorization,
copy, approval and Shariah attack in the catalogue found nothing.

**The inner reading of a sentence does not hold.** The biggest problem is one sentence
long: **the product reads the word "not" as if it were not there.**

A trader who types *"don't use 15m"* gets a monitor running on 15m. A trader who types
*"not short"* gets a short strategy. Twenty-five combinations of a rejection phrase and a
setting were tried; **thirteen ended up holding exactly the value the trader refused**
(OI4-003, -004, -005).

Three other things were found: a **question** can silently move the monitored timeframe
(OI4-001); the guard that stops the model inventing capabilities is **switched off for
Arabic** (OI4-006); and asking for something the product says it never does leaves a value
on the draft instead of a refusal (OI4-007).

One thing could not be tested at all: **fault injection says it is available and does not
work**, so nobody can currently prove a provider outage is never shown to a customer as a
Shariah or compiler failure (OI4-008).

| | Count |
|---|---|
| **NEW** findings | 8 |
| **BASELINE** (already failing before this phase) | 0 stable, 1 flaky |
| **BLOCKED ON PRODUCT DECISION** | 2 |

---

## 2. The baseline, and why it is not what the brief expected

The brief said to expect **18 known failures** in `test_dashboard_e2e.py` and
`test_dashboard_guide_e2e.py`. **That is out of date.** Commit `e7aa9e16` — *"Take the
browser suite from eighteen failures to none"* — landed before `211aecc5`.

Two full captures were taken, as required:

| Capture | Tests | Failures | Skips | Time |
|---|---|---|---|---|
| Run 1 | 99 | **1** | 2 | 564 s |
| Run 2 | 99 | **0** | 2 | 626 s |

**Validation case 1 — "the baseline reproduces the same failing set twice" — FAILS.** The
two captures disagree, so there is no stable baseline set.

The one failure was
`tests/browser/test_dashboard_e2e.py::test_setup_observability_desktop_mobile_and_visual_qa`
(a non-brand border colour, `rgb(184, 210, 201)`, on
`article.readiness-candidate.state-confirmation_pending`). Run in isolation three more
times, it passed 3/3. So it is **flaky in full-suite context**, not a stable baseline
failure.

| Set | Contents |
|---|---|
| Stable baseline | *(empty)* |
| Flaky | `test_setup_observability_desktop_mobile_and_visual_qa` |

The harness excuses both — a flaky test is neither a finding this phase caused nor a
baseline it may lean on — and `BaselineSet.is_stable` returns `False`, which is the
honest answer.

The two skips are the same in both runs and are legitimate: no screened asset is seeded,
so there is no Evidence Passport page to guide.

---

## 3. The two open product decisions

Both were re-checked at HEAD, because reporting a stale description of somebody's own
product is worse than not reporting it.

### 3.1 The lifecycles URL — **BLOCKED ON PRODUCT DECISION**

**What the brief said:** three aliases for one handler with no agreed canonical URL.

**What is true at `211aecc5`:** the three-handler problem is **gone**. There is one
handler at `/dashboard/opportunities` (`api/routers/dashboard.py:2589`) and the other two
addresses are 308 permanent redirects to it (`:2616`, `:2621`).

**What is still open:** the *name*. The code constant is `LIFECYCLES_PATH`, the URL says
`opportunities`, and the template is `activity.html`. Three words for one page.

A fourth piece of evidence turned up while probing surfaces: the retired
`/dashboard/setups` route answers **"Latest Setups was removed. Use Lifecycles."**
(`api/routers/dashboard.py:2584`) — so the one message a customer sees when they hit an
old bookmark sends them to a word that is not in the URL they will land on.

Nobody should pick one of those in an engineering pass. Reported, not decided.
**Owner: Product.**

### 3.2 The landing layout — **BLOCKED ON PRODUCT DECISION**

Commit `610cb4ad` lined the hero up with the rest of the page. The reference screenshots
it was measured against **cannot be regenerated** — blocked upstream, and a stated
non-goal of this phase — so any remaining difference cannot be attributed to either side.

**No landing layout defect is reported here.** **Owner: Product / Design.**

---

## 4. Findings — NEW

Ranked worst first. Every one is reproduced by `tools/oi/reproduce_findings.py`.

### OI4-003 — "Don't use 15m" sets the timeframe to 15m — **CRITICAL**

| | |
|---|---|
| Class | `grounding` |
| Confidence | `observed` |
| Where | `engine/turn_fragments.py` — `_EXCLUSION_MARKERS` (line 154) is read only by `_excluded_symbols`; the timeframe reader never consults it |
| Falsified if | The Setup Chat service raises a clarifying question naming the rejected value before anything is stored. It does not — the value is written to canonical state. |

Five ways of saying no, five wrong answers:

| The trader types | Timeframe before | Timeframe after |
|---|---|---|
| `Not 15m.` | 1h | **15m** |
| `Never use 15m.` | 1h | **15m** |
| `Don't use 15m.` | 1h | **15m** |
| `Anything but 15m.` | 1h | **15m** |
| `No 15m.` | 1h | **15m** |

**What it means for a customer.** They tell the product to stop using a timeframe. The
product starts using it. Every alert they then receive comes from the exact chart they
refused, and nothing on the screen says so.

### OI4-004 — "Not short" sets the direction to short — **CRITICAL**

| | |
|---|---|
| Class | `grounding` |
| Confidence | `observed` |
| Where | `engine/turn_fragments.detect_direction` has no negation handling at all |

| The trader types | Direction before | Direction after |
|---|---|---|
| `Not short.` | long | **short** |
| `Never use short.` | long | **short** |
| `Don't use short.` | long | **short** |

**What it means for a customer.** They say they do not want the falling side. The product
watches the falling side. This is the worst version of the same fault, because the
direction decides which half of the market they are told about.

### OI4-005 — "Never use BTCUSDT" adds BTCUSDT — **HIGH**

| | |
|---|---|
| Class | `classification` |
| Confidence | `observed` |
| Where | `engine/turn_fragments.py:154` — `_EXCLUSION_MARKERS` knows "never include" and "not include" but not "never use", "don't use" or "anything but" |

| The trader types | Included | Excluded |
|---|---|---|
| `Not BTCUSDT.` | — | `BTC/USDT` ← **correct** |
| `Never use BTCUSDT.` | **`BTC/USDT`** | — |
| `Anything but BTCUSDT.` | **`BTC/USDT`** | — |

The first row is the important one: this phrasing *is* understood. That proves the other
two are a gap in one word list, not a design choice.

### The root cause behind OI4-003, -004 and -005

There is **one** rejection vocabulary in the codebase, `_EXCLUSION_MARKERS`. It is:

1. read by the symbol reader only;
2. incomplete even there;
3. never consulted by the timeframe reader or the direction reader.

This is the exact failure `CLAUDE.md` names — *"duplicate parsers that disagree… two
modules independently decided what a word means and each understood a different subset"* —
except worse, because two of the three readers have no copy of the vocabulary at all.

The fix is extraction, not patching: one negation vocabulary, one resolution rule, every
canonical-field reader importing it. **This phase does not fix it.**

### OI4-001 — A question changes the monitored timeframe — **HIGH**

| | |
|---|---|
| Class | `classification` |
| Confidence | `observed` |
| Where | `engine/turn_fragments.py` classifies `and is 15m the same as 15 minutes?` as kind `timeframe`, whose `contributes_strategy_state` is `True` |
| Falsified if | The turn is routed read-only before reaching `patches_for_turn`. It is not: `conversation_intent.classify_turn` returns `STRATEGY_EDIT`, `is_read_only=False`. |

```
history: "Watch RSI below 30 on 1h."
trader:  "What does RSI 30 mean, and is 15m the same as 15 minutes?"
result:  base_timeframe 1h -> 15m
```

**What it means for a customer.** A beginner asks what the numbers mean — exactly the
thing this product exists to encourage — and their monitor silently moves to a different
chart. The one turn where they were least likely to be paying attention to the draft.

### OI4-006 — The capability guard is switched off for Arabic — **HIGH**

| | |
|---|---|
| Class | `capability_resolution` |
| Confidence | `observed` |
| Where | `services/openai_interpreter.py:379` — `if candidate_keys:` guards the JSON-schema `enum`, and `CapabilityResolver.resolve_prompt` returns nothing for Arabic |

| Language | Capabilities found | Guard on the model |
|---|---|---|
| English | `['rsi_threshold']` | enum: 1 allowed key |
| Arabic | `[]` | **none — the model may name anything** |
| Egyptian Arabic | `[]` | **none** |
| Arabizi | `[]` | **none** |

All four sentences say the same thing.

**What it means.** The product restricts the model to real, registered capabilities when a
customer writes English, and removes that restriction when they write Arabic. A safety
rail is missing precisely for the audience this product was built for.

**A second, quieter half.** Every non-English case in the committed language-quality corpus
is marked `evaluation_mode: "shadow"`, and
`test_reviewed_deterministic_language_cases_compile_all_labeled_dimensions` only runs on
`deterministic` cases — the three English ones. So Arabic, Egyptian Arabic, Arabizi, mixed
and typo behaviour is **declared in the corpus and never asserted**. That is why this was
invisible.

### OI4-007 — An unsupported request leaves a value behind instead of a refusal — **HIGH**

| | |
|---|---|
| Class | `capability_resolution` |
| Confidence | `observed` |
| Where | `engine/strategy_state.patches_for_turn` writes canonical fields with no reference to `core/product_boundaries` |
| Falsified if | The service records the refusal alongside the patch. `StrategyDraftState.unsupported_capabilities` exists and was empty in every case below. |

| The trader asks for | Registry says | The draft quietly took |
|---|---|---|
| `10x leverage` | **out of scope, permanently** | `base_timeframe=15m`, `include_symbols=('BTC/USDT',)` — leverage dropped in silence |
| `stop loss at 2% and take profit at 5%` | **out of scope** (the product never places an order) | `threshold=5.0` — and note 2% was overwritten by 5% |
| `Apple stock and EURUSD` | **not yet supported** | `include_symbols=('EUR/USD',)` — a forex pair accepted as if it were crypto; Apple dropped |
| `which coin to buy` | **out of scope** (no recommendations) | `direction=long` — taken from the word "buy" |

**What it means for a customer.** They ask for something the product has publicly said it
never does. Instead of being told no, they get a draft that looks like it worked. The
third row is a silent substitution in the plainest sense: a currency pair was accepted as a
crypto market.

### OI4-008 — Fault injection is advertised as available but is not observed — **MEDIUM**

| | |
|---|---|
| Class | `provider` |
| Confidence | `inferred` — the symptom is certain, the cause is not isolated |
| Cost of this probe | **$0.0018** |

An isolated `APP_ENV=test` target was started and probed:

```
Backend health                 PASS   HTTP 200
Evaluator fault-control        PASS   target is an isolated test evaluator with
  isolation                           fault control enabled
```

Then the evaluator's own readiness gate injected `empty_once` and got:

```
EVALUATOR_FAULT_CONTROL_UNAVAILABLE / EvaluatorFaultNotObserved
"The target did not return the exact evidence-bound response for injected fault
 'empty_once'."   http_status=200   target_cost_usd=0.001812
```

So `/health` says fault control is on, the configuration doctor agrees, and the fault
still does not come back marked. `dashboard_api.py:1537` only sets
`X-HM-Eval-Fault-Applied` after the one-shot fault is consumed at the model boundary, and
its own comment anticipates the miss: *"a deterministic branch might not make a model
call."* But a target cost of $0.0018 **was** billed, which suggests a model call did
happen — so "answered deterministically" does not fully explain it.

**Two candidate causes, not distinguished by this run:**

1. the readiness probe message (`"Can you hear me? Reply briefly."`) is now answered by a
   path that does not consume the fault, while still billing something;
2. the fault genuinely does not reach the boundary it is meant to reach.

**What it costs.** The two provider-fault attacks in the catalogue
(`provider.failure_dressed_as_sharia`, `provider.failure_dressed_as_compiler`) **cannot be
run** at this commit. Whether a provider outage is ever shown to a customer as a Shariah,
screening or compiler failure is therefore **NOT VERIFIED** — not "passed".

**It also corrected this harness.** `qa_target.TargetProfile.supports_fault_injection`
originally trusted the `/health` flag. It now documents that the flag means "configured to
accept", never "will be observed", so a silent fault attack is reported as unverified
rather than as a pass.

Reproduce:

```powershell
scripts\run_isolated_setup_chat_smoke.ps1 -PreflightOnly -EnableFaults -Port 8124
```

### OI4-002 — A two-value correction leaves the rejected value in force — **MEDIUM**

| | |
|---|---|
| Class | `composition` |
| Confidence | `inferred` |
| Falsified if | The service asks which timeframe was meant. Not verified — the clarification path is owned by the service layer and was not exercised in this run. |

```
history: "Watch RSI below 30 on 15m."
trader:  "Great, thanks. One change though - use 1h, not 15m."
result:  no patch at all; base_timeframe stays 15m
```

Failing closed on two timeframes is defensible. Failing closed **into the value the trader
just rejected**, with nothing recording that a rejection happened, is not. Lower severity
and lower confidence than the others because the deterministic layer is behaving as
designed here and the remaining question belongs to a layer this run did not measure.

---

## 4b. Four things the harness reported and were **not** findings

The brief judges this phase on noise as well as on findings, so the false ones are listed
too. Each was caught by checking before writing it down, and each changed the harness.

| The harness said | What was actually true | What changed |
|---|---|---|
| `"Not BTCUSDT."` leaves BTC/USDT in the state — a rejected value survived | It was in **`exclude_symbols`**, which is exactly where a rejected symbol belongs. This is the one case the product gets right, and reporting it would have buried the twelve it gets wrong. | The checker now knows `exclude_symbols` is a rejection's correct destination |
| The screened watchlist shows "Halal" without a version or a date | The status is one click from its Evidence Passport, and the Passport carries all four. A list that printed all four on every row would be unreadable. The rule is *reachable and complete*, not *all on one page*. | The test now checks the two halves separately, and passes |
| The dashboard overflows sideways on a phone | At **320 px**. The rest of this repository tests at **390 px** (`test_dashboard_e2e.py:87`), which is the width the product actually targets, and 390 is clean. Attacking a promise nobody made is noise. | The test moved to 390 px |
| Two customer surfaces return 404 | `/dashboard/setups` and `/dashboard/why-no-alert` are **deliberately retired** (`dashboard.py:2584`, `:2868`). They were in the harness's surface list by mistake. | Both removed; the bad-address test now declares its 404s deliberate |

Three of these four would have been confident, well-formatted, completely wrong findings.
The reason none of them reached section 4 is that each was checked against what the product
actually promises before being written down — which is the same discipline that made the
real findings believable.

---

## 5. Findings — BASELINE

| Test | Status |
|---|---|
| `test_setup_observability_desktop_mobile_and_visual_qa` | **Flaky, not baseline.** Failed 1 of 2 full-suite runs; passed 3/3 in isolation. Excused, not attributed to this phase, and not counted as a finding. |

Stable baseline set: **empty.**

---

## 6. The nine validation cases — verbatim

| # | Case | Result |
|---|---|---|
| 1 | Baseline reproduces the same failing set twice | **FAIL** — run 1 had 1 failure, run 2 had 0. Recorded honestly as flaky; `BaselineSet.is_stable` returns `False`. |
| 2 | Seed a boundary defect; harness finds it, classifies it `copy`/`boundary`, gives a repro | **PASS** — 6 seeded phrases including Arabic, all found. `test_a_seeded_boundary_defect_is_found_and_classified`. Plus `test_a_refusal_that_names_a_banned_phrase_is_not_reported` proves it does not cry wolf. |
| 3 | Seed an approval-inference defect; corpus catches it, classifies `authorization` | **PASS** — 5 accidental-approval cases in English, Arabic and Arabizi. `test_approval_is_never_inferred_anywhere_in_the_corpus` found zero violations, so the product holds. The detector is proved live by `test_no_corpus_case_claims_to_grant_approval`. |
| 4 | Point it at a baseline failure; must say BASELINE, not new | **PASS** — `test_a_baseline_failure_is_reported_as_baseline_not_new`, with `test_a_genuinely_new_failure_is_not_excused_by_the_baseline` as the other half. |
| 5 | Point it at the URL-alias inconsistency; must say BLOCKED ON PRODUCT DECISION | **PASS** — 3 phrasings, all blocked. A product decision also wins over a baseline match. |
| 6 | Production target, code fix, candidate promotion — all three must fail | **PASS** — 5 production addresses refused; a loopback address in front of `APP_ENV=production` also refused; `RegressionCandidate.promote()` raises; 5 customer-data paths refused by `conversation_source`. |
| 7 | No synthetic secret reaches a report or evidence file | **PASS** — a deliberately poisoned fixture with an invented seed phrase, API key and bot token. None survives the reader; the evidence store refuses rather than cleans; a `Finding` carrying one cannot be constructed. |
| 8 | A full corpus run stays under its cap and stops when it would exceed it | **PASS** — the whole pass cost **$0.0018** against a $0.25 ceiling, and the corpus itself cost nothing; `SpendCap.reserve` refuses the call that *would* cross the ceiling rather than reporting it afterwards. |
| 9 | ruff, mypy, pytest (non-browser), alembic check, release invariants | **PASS** — see below. |

---

## 7. Exact test output

```
ruff check src tests scripts tools          All checks passed!
mypy src                                    Success: no issues found in 355 source files
pytest tests/unit tests/engine \            8205 passed, 362 skipped, 10 warnings
      tests/interpreter tests/services      in 778.93s (0:12:58)
pytest tests/oi                             754 passed in 70.64s
pytest tests/browser/test_adversarial_qa_e2e.py
                                            18 passed
alembic check                               No new upgrade operations detected.
scripts/check_oi_boundary.py                Boundary intact: ai_market_monitor and hm_oi
                                            do not import each other.
scripts/check_release_invariants.py         PASS: release exposure, route security,
                                            provider, and artifact invariants hold.
scripts/check_oi_command_catalog.py         Command catalog matches the release gate:
                                            38 commands, 25 runnable unattended.
scripts/replay_recorded_turns.py            recorded drafts: 19  readable: 19
  --run v2-recorded-semantic-reconcile      compiled: 8  crashes: 0
                                            blocking findings: 0
```

The last one is the compiler regression probe — it calls no model and no evaluator. It is
included because this phase touched the corpus allowlist, and a probe that shows zero
crashes and zero blocking findings is the cheapest proof that nothing in the compiler
moved.

Browser baseline, both captures:

```
run 1   99 tests, 1 failed, 2 skipped, 564.55s
run 2   99 tests, 0 failed, 2 skipped, 626.50s
```

**Note on `alembic check`.** Run with the repository's default `.env` it fails with
`socket.gaierror: getaddrinfo failed` — the default `DATABASE_URL` points at a Postgres
host that does not resolve on this machine. Against a real database it passes. This is an
environment condition, not a repository defect, and not something this phase caused.

Skips, both runs, unchanged:

```
test_the_passport_guide_targets_resolve_on_a_real_passport
    no screened asset was seeded, so there is no Passport to check
test_the_evidence_passport_guide_explains_the_published_record
    no screened asset was seeded, so no Passport page exists to guide
```

---

## 8. Invariants

| # | Invariant | Verdict |
|---|---|---|
| 1 | Never runs against production, never uses production credentials or data | **HOLDS** — refused by address and by the server's own `APP_ENV`; no override flag exists |
| 2 | Fixes nothing; modifies no product code, template, copy or test outside the harness | **HOLDS** — not one file under `src/ai_market_monitor/` was touched. The only tracked file edited is `src/hm_oi/cli.py`, to add the three free `qa` subcommands. Everything else is new and lives in `src/hm_oi/qa_*.py`, `tests/oi/`, `tests/browser/test_adversarial_qa_e2e.py`, `tests/fixtures/oi_adversarial_qa_*.jsonl`, `tools/oi/` and `docs/`. |
| 3 | Promotes no regression candidate | **HOLDS** — `promote()` raises; asserted |
| 4 | Never activates, approves, publishes a Shariah status or changes billing | **HOLDS** — `builder_permissions.py` unchanged and still in force; nothing in this phase writes to the product |
| 5 | No secret or real customer data in corpus, evidence store or report | **HOLDS** — proved by validation case 7 |
| 6 | Baseline failures excluded from findings and reported separately | **HOLDS** — and the baseline turned out to be unstable, which is reported as its own result |
| 7 | Existing suites remain green and unmodified; the 18 baseline failures neither grow nor are silently "fixed" | **HOLDS with a correction** — there were never 18 at this commit; `e7aa9e16` had already removed them. Nothing was modified to achieve that. |
| 8 | Bounded in runs, wall time and spend | **HOLDS** — one pass, 1800 s ceiling, $0.25 cap, $0.0018 spent |

---

## 9. Regression candidates — proposed, **not promoted**

None of these is in the suite. Promotion is a person's decision, recorded as theirs.

| ID | Would assert | Would live in |
|---|---|---|
| `CAND-001` | Every rejection phrasing × every canonical field: the rejected value is never what the field ends up holding | `tests/unit/test_invariant_negation_vocabulary.py` |
| `CAND-002` | A question-only turn produces no patch to any monitored field, across every question shape | `tests/unit/test_invariant_turn_fragments.py` |
| `CAND-003` | `CapabilityResolver.resolve_prompt` returns the same capability keys for a sentence and its Arabic, Egyptian-Arabic and Arabizi translations | `tests/interpreter/test_prompt_understanding_corpus.py` |
| `CAND-004` | A turn naming an out-of-scope capability records it in `unsupported_capabilities` and patches no monitored field from that clause | `tests/engine/test_invariant_product_boundaries.py` |
| `CAND-005` | Promote the non-English language-quality cases from `shadow` to `deterministic` so they are actually asserted | `tests/unit/test_setup_chat_language_quality.py` |
| `CAND-006` | The evaluator readiness probe uses a message that provably reaches the model boundary, so a missing fault marker means the fault is broken rather than the probe | `tests/evaluator/test_readiness_circuit_breaker.py` |

`CAND-005` is the one that would have caught `OI4-006` two phases ago.

---

## 10. What this phase does **not** make ready

- **Nothing is fixed.** All eight findings are still in the product exactly as found.
- **A pasted seed phrase is still stored and forwarded as typed.** P3 still fails. This
  phase avoided that data; it did not protect it.
- **Neither product decision is answered.** Both are surfaced with their owner named.
- **Fault injection could not be exercised.** An isolated `APP_ENV=test` target *was*
  started and it advertised fault control, but the injected fault never came back marked
  — see OI4-008. The two provider-fault attacks are therefore **NOT VERIFIED**, not
  `passed`. Whether a provider outage is ever shown to a customer as a Shariah, screening
  or compiler failure remains an open question.
- **No paid conversation attack ran.** `copy.claim_via_conversation` was covered in the
  browser against the local model stub, which proves the rendering path and not the real
  model's wording.
- **Arabic quality is not assessed.** Only Arabic *safety* was measured.
- **The landing reference screenshots are still stale**, so landing layout remains
  unmeasurable.

# The adversarial QA harness (OI-4)

**Date:** 2026-08-14
**What it is:** Open Interpreter pointed at a throwaway copy of HilalMarkets, trying to
make the product break its own rules, and writing down what happened.

**What it is not:** it never fixes anything. It changes no product code, no template and
no copy. It never turns one of its own suggestions into a real test. It never runs against
the real product.

---

## 1. Read this first: what "attacker" means here

Every other phase built something that helps. This one tries to break things.

That difference decides the whole design. A helper can share the product's code, its word
lists and its idea of what is correct. An attacker cannot: if it only knows the phrases
the product already refuses, the only thing it can ever discover is that the product
refuses them. So this harness deliberately keeps **its own, wider** list of forbidden
words, and a test checks the relationship in one direction only:

> Every phrase the product refuses, the attacker must also refuse.
> A phrase the attacker refuses and the product does not is **what a finding looks like**.

That is `tests/oi/test_invariant_adversarial_qa.py::test_the_attacker_vocabulary_is_a_superset_of_the_products`.

---

## 2. The precondition gate

| Check | Result | Evidence |
|---|---|---|
| **P1** OI-1 and OI-2 exist and are validated | **PASS** | `docs/OPEN_INTERPRETER_ENGINEERING.md`, `docs/OI_AUTONOMOUS_BUILDER.md`, `src/hm_oi/` (23 modules), `tests/oi/` (8 files). |
| **P2** Prompt 15 has landed | **PASS** | Launch stage `core/launch_stage.py:39`; boundary registry `core/product_boundaries.py:93`; Scanner vs Monitor `core/product_boundaries.py:344`; status banners `observability/banners.py`; Shariah wording `core/copy_rules.py:84`; copy lint `scripts/check_release_invariants.py:188`. |
| **P3** Secret redaction and conversation privacy (prompts 12–13) | **FAIL** | `services/system_brain_privacy.py:17` is real but only the System Brain admin paths call it. `ai_setup_chat.py` never does. No seed-phrase pattern, no bare API-key pattern. Retention, export and delete do not exist. |
| **P4** A local or staging target that is not production | **PASS** | `scripts/run_isolated_setup_chat_smoke.ps1` starts `APP_ENV=test` with its own SQLite file under `test-results/`, its own secret key, mock providers, and every outbound channel off. No production network reachability from this machine. |

**P3 failing is the reason `hm_oi.conversation_source` exists.** Because redaction and
retention are unfinished, this phase may read **no real customer conversation at all**.
That is enforced in code, three ways — by name, by resolved path, and by shape — and there
is deliberately no setting that turns it off. Every corpus used here is committed and
synthetic.

A customer who pastes a wallet seed phrase into Setup Chat today still has it stored as
typed and sent to the provider as typed. **This phase avoids that data. It does not
protect it.** It remains the most important open product finding in the repository.

---

## 3. Target isolation

`src/hm_oi/qa_target.py`. Three refusals, each for its own reason.

| Refusal | What it stops | Where |
|---|---|---|
| **By address** | Any host that is not loopback and not on the staging allowlist. There is no override flag. | `qa_target.py:_address_verdict` |
| **By confession** | A tunnel or port-forward puts the real product behind `127.0.0.1`. `GET /health` reports the server's own `APP_ENV`; if the address says local and the server says production, the server wins. | `qa_target.py:classify_target` |
| **By capability** | Fault injection needs `APP_ENV=test` *plus* two evaluator settings. `/health` reports `evaluator_fault_control_available`, so the harness knows before it sends anything. | `qa_target.py:require_fault_injection` |

The third one matters more than it looks. An attacker that cannot tell *"the product is
broken"* from *"this target does not support what I just tried"* produces findings that
are entirely noise. So a fault attack against a target that cannot take one is recorded as
`skipped_target_does_not_support_it`, with the reason, and **never** as a finding.

**Nothing here starts a server.** `scripts/run_isolated_setup_chat_smoke.ps1` already
builds a throwaway database, throwaway credentials and mock providers, and restores the
caller's environment afterwards. A second launcher would get one of those wrong.

```powershell
# identify a target before sending it anything - free
.venv\Scripts\python -m hm_oi qa target http://127.0.0.1:8124
```

---

## 4. The flow catalogue

`tests/browser/test_adversarial_qa_e2e.py`, inside the existing Playwright harness and the
existing `playwright.config.json`. There is no second browser harness: a second one would
need its own signup fixture, its own model stub and its own idea of a console error, and
the two would disagree within a month.

| Flow | What it attacks |
|---|---|
| Signup → dashboard | The session a customer actually gets |
| Admin routes with a customer session | `authz.admin_from_customer_session` |
| System Brain with a customer session | `authz.system_brain_from_customer_session` |
| Signed-out visitor on customer pages | The simplest authorization boundary |
| Rendered dashboard and public pages | Forbidden promises assembled in JavaScript, which a source scan cannot see |
| Status badges | Status told by colour alone (brand guide §10) |
| Setup Chat | A customer handing the assistant a banned phrase to repeat |
| 320 px viewport | A real phone, where an overflow makes the page unusable |
| Retired wording | "Watch Plan" reaching a painted page |

---

## 5. The boundary attack catalogue

`src/hm_oi/qa_attacks.py`, version `2026-08-14.2`. Twenty-four attacks. Each carries what
it sends, what a correct product does, what a violation looks like, and one command to run
it alone. An entry whose expected outcome lives only in the author's head produces findings
nobody can check, so all four fields are required and a test enforces it.

Failure classes, as the brief names them: `segmentation`, `classification`, `routing`,
`grounding`, `authorization`, `capability_resolution`, `compiler`, `composition`,
`provider`, `ui_state`, `copy`, `boundary`.

**Every class has at least one attack, and a test enforces that too.** The first version of
this catalogue covered seven of the twelve — and the five it missed were exactly where the
corpus was quietly finding the worst problems. A reader comparing the catalogue against the
brief's list would have concluded those five were untested.
`test_every_failure_class_has_somewhere_to_land` now makes that impossible.

```powershell
# what a pass would attempt against this target, and what it would skip and why
.venv\Scripts\python -m hm_oi qa plan http://127.0.0.1:8124
```

---

## 6. The adversarial conversation corpus

`tests/fixtures/oi_adversarial_qa_corpus.jsonl`, version **1.1.0**, 51 cases, all
seventeen required shapes covered.

It is **not** a second copy of the committed language-quality corpus. That one asks *"does
the product understand this sentence?"* — a quality question with a spectrum of answers.
This one asks *"can this sentence make the product do something it must never do?"* —
which has two answers. Mixing them produces a file where a failure could mean either.

### The safety half and the liveness half

Each invariant has two directions, and only one belongs in a test.

**Safety** — the product must never end up holding something the trader did not say. A
question must not change a value. A rejected number must not survive. Approval must not be
inferred. These are owned entirely by the deterministic layer, they hold whatever the model
does, and they are **asserted**.

**Liveness** — the product *should* end up holding what the trader did say. Did the
correction land? Did the Arabic sentence compile? These are owned partly by the model, so
asserting them would fail for reasons that are not defects and would train everyone to
ignore the suite. They are **measured and reported**, never asserted.

`InvariantVerdict` keeps `NOT_APPLICABLE` as a real answer rather than counting it as a
pass, so a corpus that exercises nothing cannot report six green invariants.

### The six invariants

| Invariant | Asserted as |
|---|---|
| Social text is never executable | No fragment the classifier itself called conversation contributes state, and each social span alone moves no monitored field |
| A question is never a mutation | A question-only turn produces no patch to a monitored field |
| A correction targets the correct object | No value the trader named only to reject is held afterwards |
| References resolve correctly | Every value the turn writes is findable in the conversation |
| Unsupported concepts stay unsupported | No registry-unsupported capability becomes resolvable |
| Approval is never inferred | Never approval, on every case, with no not-applicable branch |

### Where they are measured

**On the canonical-state path — `engine/strategy_state.patches_for_turn`.** That is what
decides whether a turn changes the monitored rules. An earlier probe measured on the joined
chat text instead and reported three times as many problems as were real.

### Cost

The corpus is **free**. Every invariant above is deterministic and runs offline with no
model call. Only the handful of catalogue attacks marked `CONVERSATION` reach a provider,
and they do not run without `--allow-paid`. A safety check nobody can afford to run is a
safety check nobody runs.

---

## 7. Evidence schema

`src/hm_oi/qa_evidence.py`, schema `2026-08-14.1`. Every record carries the nine things the
brief requires: the interaction, screenshots, the trace reference, the failure stage,
before/after state, an environment label, a reproduction command, a failure class, and the
regression candidate.

**The gate refuses; it does not clean.** Two checks, for two different problems:

1. **The raw record.** A secret here means something upstream is carrying one. Quietly
   writing `[REDACTED:...]` would produce a tidy file and hide the actual problem, so this
   refuses and names it.
2. **The redacted record.** A secret that survives redaction is a hole in
   `hm_oi.redaction` — fix the pattern, not the record.

Redaction runs between them, and always before truncation, so a length cap cannot cut a key
in half and leave the readable part behind a pattern that no longer matches.

A record is also refused if it contains anything that looks like a real email address or
phone number. The corpora are synthetic, so anything real came from somewhere it should not
have.

---

## 8. Promotion policy

**The harness proposes. It never promotes.**

`RegressionCandidate.promote()` raises `PromotionRefused`. There is no flag. A candidate
becomes a real test when a person reads it and decides, and that decision is recorded as
theirs.

### The true baseline, recorded once so nobody re-derives it

The OI-4 brief expected **18 known browser failures**. There were none. Commit `e7aa9e16`
— *"Take the browser suite from eighteen failures to none"* — landed before `211aecc5`,
so the number was already out of date when the phase started.

Two captures were taken, as the method requires, and a third and fourth after the fixes:

| Capture | Tests | Failures | Skips |
|---|---|---|---|
| OI-4 run 1 | 99 | 1 (flaky) | 2 |
| OI-4 run 2 | 99 | 0 | 2 |
| Closeout run 1 | 117 | 0 | 2 |
| Closeout run 2 | 117 | 0 | 2 |

**The stable baseline set is empty.** The single failure in the first capture was
`test_setup_observability_desktop_mobile_and_visual_qa`, which passed 3/3 in isolation —
flaky, not baseline. Its cause is now fixed: it sampled whether card images had finished
loading at one instant instead of waiting for them, so a busy machine could measure the
page a moment before it became correct. The count rose from 99 to 117 because this
phase's own browser attacks joined the suite.

### The promotion that happened, and who decided it

On **14 August 2026** the operator directed that every OI-4 finding become a permanent
test named to its finding id. That decision is theirs, recorded here, and it is the only
reason these tests exist in the authoritative suite:

| Test file | Covers |
|---|---|
| `tests/unit/test_invariant_oi4_regressions.py` | OI4-001 to OI4-007 |
| `tests/evaluator/test_integration.py` (`test_oi4_008_*`) | OI4-008 |

The evidence recorded with the decision: **60 of the 88 promoted tests fail at
`211aecc5`** and all 88 pass after the fixes. The 28 that pass at `211aecc5` are the
over-refusal guards — they were always meant to pass, and they are there so a future fix
cannot buy an under-refusal cure with an over-refusal disease.

`RegressionCandidate.promote()` still raises. The harness did not promote these; a person
did, and then wrote them.

### The known-violation ledger

`tests/oi/adversarial_known_violations.json` records the invariant violations found at
`211aecc5`, so the suite can stay green while the defects stay visible.

It is a ledger, not an excuse list. `test_the_known_violation_ledger_is_exact` fails in
**both** directions:

- an entry that stops violating fails → somebody fixed it; remove the entry and say so;
- a violation that is not listed fails → something new broke.

Neither can pass quietly. That is the only thing that stops a file like this becoming a
place where problems go to be forgotten.

One invariant is never allowed into the ledger:
`test_approval_is_never_inferred_anywhere_in_the_corpus` has no entries and may never have
any. If approval inference ever appears, the answer is to stop, not to write it down.

---

## 9. Cost controls

`src/hm_oi/qa_harness.py`. Three limits, all refusing rather than warning.

| Limit | Default | Behaviour |
|---|---|---|
| Passes | 1 | No loop that retries until it finds something. An attack that fails one time in twenty is measuring the weather. |
| Wall clock | 1800 s | An attack past the deadline is recorded `not_run_out_of_time` — never `passed`. |
| Spend | $0.25 | Checked **before** each paid call using its estimate. A ceiling you discover you have crossed is not a ceiling. |

---

## 10. Runbook

```powershell
# everything free and offline - corpus, boundary attacks, invariants, gates
tools\oi\hm-oi-qa.ps1 -Stage boundaries
tools\oi\hm-oi-qa.ps1 -Stage corpus

# capture the browser baseline TWICE, because one capture cannot tell a stable
# failure from a flaky one
tools\oi\hm-oi-qa.ps1 -Stage baseline

# the browser attack flows
tools\oi\hm-oi-qa.ps1 -Stage browser

# identify a target, and refuse it if it is production
tools\oi\hm-oi-qa.ps1 -Stage target -BaseUrl http://127.0.0.1:8124

# everything
tools\oi\hm-oi-qa.ps1 -Stage all
```

For the paid attacks, start the isolated target first — it is the only one that accepts an
injected fault:

```powershell
scripts\run_isolated_setup_chat_smoke.ps1 -EnableFaults -PreflightOnly -BudgetUsd 0.25
```

---

## 11. Invariants this phase holds itself to

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Never runs against production | `qa_target.py`, no override flag; three tests |
| 2 | Fixes nothing outside the harness | No file under `src/ai_market_monitor/` was touched at all; the only tracked edit is the `qa` subcommands in `hm_oi/cli.py` |
| 3 | Promotes no regression candidate | `RegressionCandidate.promote()` raises |
| 4 | Never approves, activates, publishes a status or changes billing | `builder_permissions.py` rules, unchanged and still in force |
| 5 | No secret or real customer data in the corpus, evidence or report | `qa_evidence.EvidenceStore.store`, `Finding.__post_init__`, `conversation_source` allowlist |
| 6 | Baseline failures reported separately | `qa_findings.classify`, `BaselineSet` |
| 7 | Existing suites remain green | See the run report |
| 8 | Bounded in runs, wall time and spend | `qa_harness.RunLimits`, `SpendCap` |

---

## 12. What this phase does not make ready

- It does not fix the defects it found. Every one is still in the product.
- It does not protect a pasted seed phrase. P3 is still failing.
- It does not decide either open product question.
- It does not regenerate the landing layout reference screenshots — blocked upstream.
- It gives no verdict on Arabic *quality*, only on Arabic **safety**.
- **It cannot currently test provider faults at all.** The isolated target advertises fault
  control and the injected fault is not observed (finding OI4-008). Until that is settled,
  "a provider outage is never shown as a Shariah or compiler failure" is an untested
  claim, and this harness reports it as `NOT VERIFIED` rather than as a pass.

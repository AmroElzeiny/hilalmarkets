# Routing policy

## Principle

Use the cheapest currently selectable OpenCode Go model that is likely to finish the assigned role correctly.

Only four models are allowed: `muse-spark-1.3-contributor`, `glm-5.3-flash`, `mimo-v2.5`, `qwen3.7-plus`. Each role's fallback is listed in `.hm-orchestrator/models/ROLE_MODEL_MAP.md`; never route outside these four.

The live local OpenCode Go catalog is authoritative.
Never invent a model or reasoning variant.

Routing is:
- cost-first;
- speed-aware;
- capability-gated;
- risk-sensitive.

Do not use a more expensive model merely because a task is large or important.

---

## Standard tier

Default supervisor:

`opencode-go/muse-spark-1.3-contributor`

Use Standard for:
- normal repository analysis;
- localized deterministic bugs;
- routine implementation;
- normal frontend/backend changes;
- low-risk refactors;
- tests;
- documentation coupled to implementation;
- read-only audits;
- ordinary UI work.

Preferred flow:

Supervisor
→ worker
→ focused tests
→ one independent reviewer
→ complete

Do not invoke extra agents unless the mission needs them.

---

## Deep tier

Default supervisor:

`opencode-go/glm-5.3-flash`

Use Deep only when the task materially involves one or more of:

- billing or payment-provider behavior;
- subscriptions, upgrades, downgrades, proration or entitlements;
- authentication or authorization;
- security or privacy boundaries;
- governed Sharia status or authority boundaries;
- persistent state or schema migrations;
- difficult cross-layer state synchronization;
- concurrency, retry or idempotency;
- infrastructure/runtime behavior with material production risk;
- repeated meaningful failure by Standard workers;
- complex architecture where a wrong implementation can silently affect users.

Deep tier does not automatically justify expensive workers.

---

## Default role routing

Standard supervisor:

`opencode-go/muse-spark-1.3-contributor`

Deep supervisor:

`opencode-go/glm-5.3-flash`

Repository explorer:

`opencode-go/mimo-v2.5`

Fast implementation worker:

`opencode-go/muse-spark-1.3-contributor`

Strong cross-file worker:

`opencode-go/muse-spark-1.3-contributor`

Test/debug engineer:

`opencode-go/muse-spark-1.3-contributor`

Independent logic reviewer:

`opencode-go/glm-5.3-flash`

Adversarial/high-risk reviewer:

`opencode-go/qwen3.7-plus`

Direct read:

`opencode-go/mimo-v2.5`

Direct write:

`opencode-go/muse-spark-1.3-contributor`

Vision reviewer:

Use `opencode-go/glm-5.3-flash` (image input tested 2026-09-16) only when actual visual verification is required.

---

## Cost discipline

Expensive models are escalation-only.

Do not automatically route work to:
- Kimi K2.7 Code;
- GLM-5.3 full;
- Qwen Max;
- other high-cost models.

A higher-cost model may be used only when:

1. the cheaper model has failed two meaningful attempts;
2. the required capability is unavailable in the cheaper model;
3. independent review proves the cheaper result materially unreliable;
4. the task demonstrably requires deeper reasoning;
5. Claude explicitly approves the escalation.

Before escalating model cost, record:
- previous model;
- failure evidence;
- why another identical attempt is unlikely to help;
- replacement model;
- expected benefit.

Do not repeat expensive calls simply because an answer was incomplete.

---

## Worker selection

Start implementation with:

`opencode-go/muse-spark-1.3-contributor`

Use the same model for both narrow and multi-file implementation by default.

The distinction between `hm-worker-fast` and `hm-worker-strong` is the role prompt and scope, not necessarily model price.

A task does not require an expensive coding model merely because:
- it touches many files;
- it is business-critical;
- it spans frontend and backend;
- it has a large diff.

Escalate only on evidence.

---

## Repository exploration

Do not invoke `hm-explorer` automatically for every task.

Use it when:
- runtime ownership is unclear;
- duplicate implementations may exist;
- multiple call paths may own the same behavior;
- the relevant tests or contracts are uncertain;
- Claude explicitly requests a root-cause map.

Reuse evidence already established in the same mission.

Do not make every agent rediscover the repository from scratch.

---

## Reviewer policy

### Normal work

Use one independent logic reviewer.

Preferred:

`opencode-go/glm-5.3-flash`

### High-risk work

Use two independent reviewer perspectives when practical:

Logic reviewer:

`opencode-go/glm-5.3-flash`

Adversarial reviewer:

`opencode-go/qwen3.7-plus`

High-risk areas include:
- billing;
- payments;
- subscriptions and entitlements;
- auth;
- security;
- privacy;
- governed Sharia status;
- migrations;
- concurrency/idempotency;
- production-critical state.

Do not use two identical reviewer models merely to satisfy reviewer count.

---

## Governed authority

AI must never invent, infer, override, or silently modify authoritative Sharia status.

When a task touches governed data:
- identify the deterministic source of truth;
- verify that AI remains non-authoritative;
- treat any authority-boundary regression as high severity.

---

## Visual work

Invoke the vision reviewer only when visible UI correctness is part of acceptance.

Visual completion requires:
- Claude's visual contract;
- real screenshots;
- comparison at required viewports;
- actual vision-model inspection.

Do not use the vision model for non-visual tasks.

Do not approve UI from CSS/code inspection alone.

---

## Repair loops

A work package gets at most two meaningful attempts with the same model and approach.

After two meaningful failures:
- change approach;
- change model family;
- or escalate.

Do not repeatedly:
- re-read the entire repository;
- regenerate the same patch;
- rerun substantially identical prompts;
- invoke stronger models without failure evidence.

---

## Context efficiency

Every agent should receive the smallest context sufficient for its assignment.

Prefer:
- relevant files;
- relevant symbols;
- relevant contracts;
- relevant tests;
- compact supervisor evidence.

Avoid repeatedly passing:
- entire repository dumps;
- unrelated documentation;
- full previous transcripts;
- already-established evidence.

The supervisor should summarize and route evidence instead of forcing every worker to rediscover it.

---
## Test cadence rule (owner decision 2026-09-13 — applies to every run)

A run that re-ran the whole offline suite (~28 000 tests, ~20 min each) after every small
step spent 10 hours on one work package. That is not allowed.

1. **After each major stage** (a work package's fix is written): run only the tests that
   stage touches — its new reproducer test file plus the neighbouring test files for the
   modules it changed — and `ruff` on the changed files. Minutes, not the full suite.
2. **Reproducer proof is still required per defect:** the new test file once on the
   unfixed code (must fail) and once on the fixed code (must pass).
3. **The full verification runs once, at the end** of the run, after the last fix:
   `ruff check src tests scripts`, `mypy src`, and the broad offline suites
   (`tests/unit tests/engine tests/interpreter tests/services`, plus `tests/integration`
   when the mission touched money, billing or database code) with `--junitxml`.
4. If the final run fails: fix it, re-run **only the failing files**, then the full
   verification **one** more time. Never run the full suite during a stage.
5. Browser tests: only the named browser file(s) the mission requires, once after the
   stage that needs them and once at the end.

---


## Variant rule

Only use reasoning/effort variants when current live metadata confirms that:
- the exact model supports the variant;
- the exact variant exists;
- its extra cost is justified.

Default to the cheapest normal variant.

**Exception, owner decision 2026-09-16:** `muse-spark-1.3-contributor` always runs on the
`high` variant — in every agent file that uses it and in `run-model.ps1`. Never lower it to
save cost, and never pass a different `--variant` for Muse without the owner's instruction.
See `.hm-orchestrator/models/ROLE_MODEL_MAP.md`.

---

## Live catalog rule

The live OpenCode Go catalog always wins.

If a preferred model is unavailable:

1. choose the cheapest available model with the required capability;
2. preserve reviewer-family independence;
3. record the substitution;
4. do not invent an unavailable model.
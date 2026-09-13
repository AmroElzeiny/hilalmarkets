# HilalMarkets — working rules

## What this product is

A **Halal** crypto-monitoring product built for **beginners and Muslims**. Both halves
of that constrain the work:

- *Halal* — Sharia status is governed, evidence-backed, and assigned only through the
  platform's own review process. Never assign, infer, or imply a Sharia/halal/haram
  status from chat, from a model, or from a heuristic. No leverage, no buy/sell advice,
  no guaranteed returns, no automatic trading.
- *For beginners* — a user will not know what "close-to-close" or "gte" means. Explain
  in plain language, ask one concrete question at a time, and never answer a beginner's
  question with jargon or an internal field name.

### Where the context lives

| What | Where |
|---|---|
| Brand rules — colors, type, tone, voice | `brand guide.md` (project root) — the master. `Hilal-Markets-Website/src/imports/Hilal_Markets_Brand_Rules.md` is a copy; the root file wins. |
| Business goals, audience, positioning, Sharia governance, roadmap | `Notion/HilalMarkets_Notion_Workspace/` (numbered folders `00_`–`10_`) |
| Landing page — the visual source of truth | `Hilal-Markets-Website/src/` |
| Dashboard — the authenticated source of truth | `src/ai_market_monitor/templates/`, `src/ai_market_monitor/static/` |

**Read the brand rules and the relevant Notion folder before any UI, copy, color, or
product-decision work.** Colors, structure, text, UX and UI come **from the existing
landing page and dashboard** — match what is already shipped rather than inventing a
new visual language. If something is missing there, follow `brand guide.md`.

Three of its rules are enforced by `core/copy_rules.py`, not left to review: the name in
prose is **Hilal Markets** (section 4), technical usage says **Shariah** (section 16), and
the forbidden claims list is section 17. Identifiers are exempt by construction — the
patterns only match the word standing alone in prose.

## Fix the defect class, not the reported instance

A bug report names one example. The example is a **symptom**; the scope of the fix is
every code path that can produce that class of error.

> "Bot read the RSI value as 15 while it should be 17" does **not** mean "make this
> read 17". It means: find *why* a value was read that way, fix that cause, and make
> the same mistake impossible for RSI, for every other indicator, and for every other
> value the same reader touches. Prove it with tests across the whole family.

Think past the literal wording of the report. If you find related problems on the way,
fix them too — do not stop to ask — and list them in the final report.

Before fixing anything, search for other implementations of the same reading or
decision. The recurring root cause in this codebase is **duplicate parsers that
disagree** — two modules independently decided what a word means and each understood a
different subset. Confirmed instances: three movement-direction word lists, two
percent-move implementations, two reversion regexes, a comparator table parallel to the
turn classifier's, and a fragment-classification heuristic parallel to
`turn_fragments.classify_fragment`.

The fix is extraction, not patching: one vocabulary, one resolution rule, every caller
importing it.

| Concept | Owner | Never re-implement |
|---|---|---|
| comparison operators | `engine/comparators.py` | operator tables, `>=`/`at least` phrase lists |
| movement direction | `engine/price_movement.py` | up/down word lists |
| operator + level in a clause | `engine/numeric_clause.py` | window scans around a number |
| fragment kind | `engine/turn_fragments.py` | keyword heuristics for "is this a trading instruction" |
| AI value grounding | `engine/grounded_patch.py` | confidence thresholds as a safety check |

Each exposes its regex alternation so callers share the exact vocabulary rather than
hand-writing a subset that drifts.

**Resolution rule for values:** the operator or direction that governs a number is the
one **nearest to its left**, inside the clause that owns it. Scanning a character
window let `(close < open) AND (bearish % change >= 1.0%)` read the `<` and compile a
minimum move as a maximum.

## A setting lives in four files, and they are edited together

Adding, renaming or changing the default of a setting means editing **all four**, in the
same piece of work:

| File | What it is | In git? |
|---|---|---|
| `.env.example` | development example | yes |
| `.env.production.example` | production example | yes |
| `.env` | the real local file | no |
| `.env.production` | the real deployed file | no |

`tests/unit/test_invariant_phase6_launch_audit.py` only compares the two **examples** with
each other. It cannot see the two real files — they are not in git — so nothing will ever
tell you they are stale. A setting added to the examples alone is a setting the running
system does not have, and the default silently applies instead. That is exactly how the
Celery memory limits were written on 22 August 2026 and then not applied to the server.

**Rules for touching the two real files**, because they hold live secrets:

- **Copy only the keys that changed.** Never regenerate a real file from an example, and
  never reorder or reformat it — everything already there stays exactly as it is.
- **Back it up first** (`.bak-<reason>`), then prove afterwards that the key count rose by
  exactly the number added and that **no existing value changed**.
- **Do the edit in Python**, never with a PowerShell file write: `Set-Content` adds a
  byte-order mark and `Get-Content -Raw` decodes with the wrong codepage. Either one
  silently corrupts a secret.
- **Never print a value** from `.env` or `.env.production`. Key names and counts only.
- Changing the deployed file on the server is a separate step from changing it here; say
  so plainly, and give the command.

## A database name is short enough, or it is marked

PostgreSQL refuses any identifier over 63 characters. SQLite does not, so the whole
offline suite runs on a database that accepts names the real one rejects — this class of
bug is only ever found by a deployment that will not start.

SQLAlchemy does two different things with a long name, and the difference is one call:

| How it is written in a migration | What happens |
|---|---|
| `name=op.f("fk_…")` — marked as convention-made | **shortened**: first 55 characters + 4 hex digits of its own hash |
| `name="fk_…"` — a plain string | **validated**: over 63 it raises `IdentifierError` and nothing runs |

The naming convention in `db/base.py` marks every model-side name automatically, so the
models and a marked migration always shorten the same string the same way and agree on
what the constraint is really called. Hand-picking a short name in the migration only is
what creates drift.

**Write every constraint and index name in a migration through `op.f()`.** 83 identifiers
in this schema already exceed the limit. `tests/unit/test_invariant_database_identifiers.py`
checks every migration file and every table against SQLAlchemy's own PostgreSQL validator.

## Tests assert the rule, not the case

Parametrise across the whole family — every operator phrase × every indicator, every
term in a vocabulary individually. A fix that only helps the reported input must fail
the test. See `tests/unit/test_invariant_*.py`.

## Compiler invariants

- **Fail closed.** When meaning cannot be represented, surface it as a blocking issue.
  Never substitute a nearest capability, never fall back to a default level or
  comparator. A refused reading keeps the misunderstanding visible; an invented one
  silently monitors the wrong thing.
- **Never invert.** If a capability expresses only "at least", an upper bound is
  refused, not compiled as its opposite.
- **Never clamp.** `RSI at least 999` is out of domain — refuse it, do not clamp to 100.
- **Only `TRADING_MECHANIC` fragments reach capability resolution.** Approval gating,
  Sharia/labelling policy, rollback requests, open questions and instructions about the
  conversation are separate categories. Emitting them as blocking capability findings
  produces issues no user answer can ever clear.
- **The AI never approves.** It may request approval; only the application's own
  hash-bound route grants it. Wording that *describes* the approval gate
  ("after I say I approve") must never read as granting it.
- **AI fills typed fields only, and every value must be grounded** — findable in the
  user's own text. Confidence is the model's opinion of itself and cannot detect a
  hallucinated threshold; only the source text can.
- **A diagnostic must never become the failure.** Field caps on error/provenance
  payloads truncate; they do not raise. Two HTTP 500 classes came from a length cap
  firing while the compiler was reporting a problem.

## Verification

```bash
.venv/Scripts/python -m ruff check src tests scripts
.venv/Scripts/python -m mypy src
.venv/Scripts/python -m pytest tests/unit tests/engine tests/interpreter tests/services -q -p no:randomly
.venv/Scripts/python scripts/replay_recorded_turns.py --run <RUN_ID>   # compiler regression probe, no model calls
```

`pytest-timeout` is not installed — do not pass `--timeout`.

**Paid API calls are allowed for testing — spend minimally.** Real model and provider
calls are the only way to verify some behaviour, so make them rather than guessing.
Keep them small and few: prefer the deterministic replay probe and the offline suites
first, use the cheapest model that answers the question, run one representative case
rather than a full suite, and reuse cached or recorded responses where they exist.
Never launch a full evaluation run to check a single change.

The working tree usually carries unrelated uncommitted changes. Before claiming a
regression, diff failing test IDs against a clean `git worktree` at `HEAD`, then copy
**only** the files you changed onto that worktree to confirm attribution. Use a short
worktree path (`C:\wt-head`); the repo's nested directories overflow Windows' path
limit otherwise.

## Scope: finish everything in the prompt

Leave no condition, question or requirement from the prompt unsolved. The only
acceptable reason to leave one is a **blocker** — and then say plainly what is blocked
and why. Partially answering a multi-part request, or quietly dropping the hard part,
is a failure even if what was delivered is correct.

### A problem you find is a problem you fix

**Anything you discover while working is part of the mission. Fix it. Do not ask for
permission, do not defer it to "its own pass", do not park it in a report as
"found, not fixed".**

This is not optional and it is not limited to the reported symptom:

- Finding a defect and *describing* it instead of fixing it is an unfinished task, not
  a finding. A list of known-broken things you chose not to touch is a failure of the
  same kind as dropping the hard part of the prompt.
- "It is pre-existing" is not a reason to leave it. Neither is "it was not what I was
  asked about", "it needs its own measured pass", or "it changes a lot of files".
  Those describe *effort*, not blockers.
- Reaching for a workaround instead of the cause is the same failure wearing a
  disguise: declaring an equivalence in a test, widening an assertion, or adding a
  special case, when the honest fix is to remove the duplication or the wrong value.
- The only exception is a **true blocker**: the fix needs a decision that is the user's
  to make (a product or pricing choice), needs access you do not have, or cannot be
  verified with the tools available. Then say in one sentence what is blocked, why, and
  what you need — and fix everything else.

When a fix is large, that is a reason to *sequence* it inside the same piece of work,
not to skip it. Measure before and after, keep the invariant tests green, and report
what moved.

The final report must show, for every extra problem found: what it was, that it is
fixed, and how that was verified. If something is genuinely blocked, it appears with
the blocker named — never as a quiet omission.

## Reporting

**Write for a non-native English speaker. Use very simple words.**

The reader is not a native English speaker and may not be an engineer. Write the way
you would explain it to a smart friend who does not work on this code.

- Short sentences. One idea per sentence.
- Everyday words instead of technical ones. Say "the system saved the wrong number",
  not "the persistence layer serialised an incorrect value".
- If a technical term is unavoidable (a file name, a field name, an error code), say
  it once and then explain it in plain words right after.
- No jargon, no Latin (`i.e.`, `e.g.`, `per se`), no idioms, no clever phrasing.
- Say what it means for the user or the product, not only what the code does.
- A table with plain labels beats a paragraph.

This applies to the summary you write in chat **and** to any report file you generate.
Deep technical detail belongs in code comments and in the invariant documents, not in
the report the user reads.

**Compact and direct.** Lead with what changed and what it means; no preamble, no
restating the request, no narrating the process. Tables and bullets over prose.

State what was measured and how. Distinguish "verified fixed", "unfixed", and
"unverified". Do not claim a pass rate or release gate without the regenerated report
attached. Never present a crash-to-completion change as a score improvement — a crash
had no score to improve on.

Always include: problems found and fixed beyond what was asked, and anything left
unsolved with its reason.

Personal coding orchestration — Claude is architect, OpenCode Go is the default implementer

This section governs my personal engineering workflow in this repository. It does not change product runtime behavior.

Default ownership

For every task that may modify product code, tests, migrations, templates, styles, scripts, configuration examples, or documentation coupled to code:

Claude Code is the architect, scope owner, risk classifier, and final judge.

OpenCode Go is the default implementation workforce.

Claude MUST NOT implement product-file changes directly before delegation.

Claude MAY directly read/search the repository, inspect history/diffs, run read-only diagnostics, create the mission files listed below, and review the final evidence.

Do not silently fall back to Claude implementation if OpenCode is unavailable. Report the unavailable dependency instead.

Claude shell is read/test/orchestration-only. Do not mutate product files through Bash/PowerShell redirection or scripting; after takeover use Edit/Write so file scope remains enforceable.

Claude implementation is allowed only after a valid OpenCode escalation and the takeover gate described below, or when I explicitly tell Claude to bypass delegation for that task.

Required mission workflow

Before delegation:

Read this CLAUDE.md, AGENTS.md, the relevant architecture/contracts/tests, and .agents/commands.json.

Refresh OpenCode Go model knowledge if the snapshot is missing or older than 24 hours:
powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\refresh-models.ps1 -Quiet

Read:

.hm-orchestrator/policy/ROUTING_POLICY.md

.hm-orchestrator/models/LIVE_MODELS.md when present

.hm-orchestrator/models/OCG_CATALOG_2026-09-09.md

.hm-orchestrator/models/MODEL_METADATA_RULES.md

Create .hm-orchestrator/current/MISSION.md.

Divide the mission into 3–8 outcome-based work packages. Do not create dozens of microtasks. The OpenCode supervisor owns microtask decomposition.

Every work package must state:

objective;

repository/runtime authority to inspect;

in-scope behavior;

forbidden changes;

acceptance criteria;

evidence/tests required;

risk and escalation triggers.

Include a requirement coverage list. Every user requirement must receive a stable requirement ID.

Delegate with:
powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\delegate.ps1 -MissionFile .\.hm-orchestrator\current\MISSION.md -Tier Standard
Use -Tier Deep only when architecture, cross-system state, security, schema migration, hard concurrency, difficult parsing/compilation, or repeated worker failure makes the stronger cheap supervisor economical.

Model selection

Claude decides the role and risk tier, not a guessed model name.

Use the live catalog as authority for availability. Never invent a model or variant.
Variants are model/provider-specific. Do not assume low, high, max, or xhigh exists.
Only request a variant when the live OpenCode model metadata for opencode-go confirms it, or when a project policy records a provider-tested override.

Default preference:

- use the cheapest model that has the required capability;
- use `qwen3.8-flash` as the default implementation and test/debug model;
- use `deepseek-v4.1-flash` for read-heavy exploration, deep supervision, or adversarial review when needed;
- use `minimax-m3` for standard supervision and independent logic review;
- use a vision-capable model only when actual screenshot/image verification is required;
- multi-file work does not by itself justify a more expensive model;
- expensive models are escalation-only, never normal defaults.

A higher-cost model may be selected only when:
1. the normal cheap route has failed two meaningful attempts;
2. the required capability is unavailable in the normal models;
3. independent review shows a material quality failure;
4. Claude can state a concrete reason why the expensive model is likely to solve that failure.

Before any expensive-model escalation, record:
- the model that failed;
- the evidence of failure;
- the replacement model;
- why another cheap-model attempt is unlikely to help.

Do not escalate model cost merely because a task is large, important, cross-file, or high-risk.

Claude may direct the supervisor to use any currently available OpenCode Go model through:
powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\run-model.ps1 ...
Read .hm-orchestrator/models/LIVE_MODELS.md before doing so.

Claude must not supervise every worker turn

Do not continuously read worker transcripts.
Do not ask OpenCode for progress on a timer.
The OpenCode supervisor owns execution between event-based checkpoints.

Claude re-enters only for:

architecture contradiction;

scope expansion that changes the mission meaning;

repeated meaningful failure;

conflicting executable evidence;

security/privacy/auth/billing/Sharia/approval/activation/governed-authority risk;

migration or irreversible data decision;

reviewer disagreement that the cheap supervisor cannot resolve;

request to weaken/delete/skip a test;

dependency/package installation;

inability to prove a visual requirement;

explicit ESCALATE_TO_CLAUDE.

Evidence package Claude must require

Claude must not accept "done", "looks good", a green single test, or model confidence as proof.

A completed OpenCode run must provide:

.hm-orchestrator/runs/<run>/SUPERVISOR_REPORT.json

.hm-orchestrator/runs/<run>/SUPERVISOR_REPORT.md

Claude must verify at least:

all requirement IDs are accounted for;

baseline state and changed files are explicit;

defect-class/root-cause reasoning is grounded in code/tests;

a reproducing test exists when fixing a reproducible defect;

focused and adjacent regression evidence is present;

every changed behavior has evidence;

no test was weakened, skipped, deleted, or changed merely to fit the implementation;

serialization/API/schema/state/backward-compatibility effects were checked when relevant;

independent reviewer findings are closed or escalated;

uncertainty is explicit rather than hidden.

Claude should inspect the final diff and the highest-risk evidence, not replay the entire OpenCode conversation.

Claude takeover

If the supervisor emits a valid ESCALATION.json, Claude may run:
powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\grant-takeover.ps1

The takeover gate is temporary and file-scoped.
Claude may edit only the files listed in the escalation.
After resolving them, remove the takeover by running:
powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\revoke-takeover.ps1

Then re-delegate verification/review to OpenCode before declaring completion.

Visual/UI/design tasks

For any task whose correctness depends on visual appearance, Claude owns the visual specification before delegation.

Claude must read the current visual sources of truth required earlier in this file, including the root brand guide and the shipped landing/dashboard implementation relevant to the surface.

Create .hm-orchestrator/current/VISUAL_CONTRACT.md before delegation. It must state exact, testable constraints, including where applicable:

target surface and route/component;

reference implementation/screenshots;

viewport sizes and pixel dimensions;

aspect ratio;

layout grid and max/min widths;

spacing/padding/gaps;

colors as exact tokens/hex values and allowed accent usage;

typography family, weight, size, line-height, casing;

border, radius, shadow, opacity;

icon/logo source and exact treatment;

text hierarchy and maximum line counts;

responsive rules and breakpoints already used by the product;

hover/focus/active/disabled/loading/empty/error states;

crop/contain/cover behavior;

accessibility/contrast/focus requirements;

elements that must not change;

visual acceptance criteria.

Workers implement the contract; they do not invent a new visual direction.

A visual task is not complete until:

code review passes;

required screenshots are produced at defined viewports;

a vision-capable OpenCode Go reviewer checks the screenshots against VISUAL_CONTRACT.md;

mismatches are fixed or explicitly escalated.

If screenshots cannot be produced or a vision-capable reviewer cannot actually inspect them, mark visual verification as unverified and escalate. Never infer a visual pass from CSS alone.

Failure policy

OpenCode gets two normal repair loops per work package.
After two meaningful failures, the supervisor must change strategy/model or escalate; it must not loop indefinitely.

For normal tasks:

one independent reviewer is mandatory.

For high-risk tasks:

two independent reviewers from different model families are mandatory.

For visual tasks:

one code reviewer plus one vision reviewer is mandatory.

Claude is the final judge, but not the default executor.


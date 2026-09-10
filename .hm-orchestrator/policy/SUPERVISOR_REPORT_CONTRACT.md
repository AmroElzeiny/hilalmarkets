# Supervisor report contract

The report is an evidence package for Claude, not a progress summary.

Required files:
- `SUPERVISOR_REPORT.json`
- `SUPERVISOR_REPORT.md`

Artifact ownership:
- `delegate.ps1` alone owns `SUPERVISOR_STDOUT.txt` and `SUPERVISOR_STDOUT_RAW.txt`.
- The supervisor must never create, overwrite, append, rename, or delete those transcript files.
- Optional supervisor narrative, if useful: `SUPERVISOR_NARRATIVE.md`.
- Reports must not describe overwritten/obsolete content as if it still exists.

## Machine shape (authoritative)

- `.hm-orchestrator/policy/supervisor-report.schema.json` is the authority that decides
  whether a report is well-formed.
- The report JSON must have these exact top-level keys (copied verbatim from the schema's
  "required" array): `run_id`, `status`, `baseline`, `models`, `requirements`,
  `work_packages`, `changed_files`, `tests`, `test_integrity`, `hidden_error_audit`,
  `diff_audit`, `reviews`, `uncertainties`, `final_verdict`.
- Types:
  - `status` is one of `complete` / `escalated` / `blocked`.
  - `requirements`, `models`, `work_packages` and `reviews` must be non-empty arrays.
  - `final_verdict` is one of `COMPLETE_VERIFIED` /
    `COMPLETE_WITH_EXPLICIT_UNVERIFIED_ITEM` / `ESCALATE_TO_CLAUDE` / `BLOCKED`.
  - `baseline`, `test_integrity`, `hidden_error_audit` and `diff_audit` are objects.
  - `run_id` is a string; `changed_files` and `uncertainties` are arrays per the schema.
- The numbered sections below describe the Markdown report and the meaning of each part;
  the machine shape above decides validity. Keep all numbering intact.

## Mandatory content

### 1. Identity and baseline
- run_id
- mission path/hash if available
- starting commit
- branch
- initial `git status --porcelain`
- pre-existing changed/untracked files
- supervisor model and effective variant if any
- worker/reviewer model IDs and variants

### 2. Requirement coverage matrix
Every mission R# must appear exactly once with:
- status: PASS | BLOCKED | ESCALATED
- implementation evidence
- test/evidence references
- reviewer confirmation
No unlisted requirement may be silently dropped.

### 3. Root cause / design basis
For defects:
- reproduced symptom;
- failing test/evidence before fix;
- root cause;
- defect class;
- other code paths searched for the same class;
- why the chosen owner/fix is canonical.

For features:
- current behavior;
- contract/source-of-truth inspected;
- design choice;
- compatibility constraints.

### 4. Work-package ledger
For each WP:
- assigned model;
- exact file scope;
- actions completed;
- attempts;
- failures and corrections;
- final state;
- evidence.

### 5. Changed-file ledger
For every changed file:
- why changed;
- key symbols/areas changed;
- behavior affected;
- risk;
- reviewer finding;
- whether it contained pre-existing user changes and how they were preserved.

### 6. Test/evidence ledger
Record command, purpose, before/after where relevant, exit code, pass/fail counts if available.
Include:
- reproducer;
- focused tests;
- adjacent tests;
- lint/static/type checks;
- integration/serialization/API/UI checks when relevant;
- final regression run.

Never claim a test was run if no captured evidence exists.

### 7. Test integrity audit
Explicit answers:
- any test deleted? yes/no
- any test skipped/xfail added? yes/no
- any assertion weakened? yes/no
- any expected value changed? if yes, why is product behavior the authority rather than implementation convenience?
- any test-only special case/hardcode? yes/no

### 8. Cross-layer hidden-error audit
Mark relevant/not relevant and evidence for:
- API request/response shape;
- serialization/deserialization;
- save/edit/reload round trip;
- schema/database/migration;
- backward compatibility;
- async/concurrency/race;
- timeout/retry/idempotency;
- error/exception paths;
- auth/permissions;
- secrets/privacy;
- Sharia/governed authority;
- billing/entitlements;
- caching/state invalidation;
- frontend/backend mismatch;
- accessibility;
- performance regression;
- Windows/Linux/path/encoding differences;
- dependency/version assumptions.

### 9. Diff audit
- final changed files;
- unexpected files;
- generated files;
- TODO/FIXME/debug prints left;
- dead code;
- duplicated parser/rule introduced;
- broad catch/fallback introduced;
- risky code excerpts/line references;
- `git diff --check` result when applicable.

### 10. Independent review
For each reviewer:
- model family;
- what evidence it saw;
- findings by severity;
- false positives resolved;
- remaining disagreement;
- verdict.

Normal task: >=1 reviewer.
High risk: >=2 different model families.
Visual: code reviewer + vision reviewer.

### 11. Visual appendix when applicable
- visual contract hash/path;
- screenshot paths per viewport;
- vision model used;
- exact mismatches found/fixed;
- colors/tokens;
- geometry;
- typography;
- responsive states;
- interaction states;
- accessibility;
- anything visually unverified.

### 12. Uncertainty and hidden-risk register
Every uncertainty must be explicit:
- statement;
- probability/impact qualitatively;
- what evidence reduces it;
- whether it blocks completion.

"Looks correct" is not evidence.

### 13. Final verdict
One of:
- COMPLETE_VERIFIED
- COMPLETE_WITH_EXPLICIT_UNVERIFIED_ITEM
- ESCALATE_TO_CLAUDE
- BLOCKED

A normal completion should be `COMPLETE_VERIFIED`.
Do not call a run verified when material evidence is missing.

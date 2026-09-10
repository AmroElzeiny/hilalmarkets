# Mission — Orchestration smoke test (read-only)

Run ID: assigned by delegate.ps1
Owner: Claude Code (architect and final judge)
Execution owner: OpenCode Go supervisor
Tier: Standard
Risk: normal
Date: 2026-09-09

## User outcome

Prove that the Hilal OpenCode Go orchestration path works end to end — delegation,
rule reading, cheap worker use, and a valid evidence package — **without changing a
single product file**. This is a system test of the orchestration itself, not a code task.

## Runtime/source-of-truth authority

Read-only inspection is authorised for:

- `CLAUDE.md` (repository working rules — the rulebook)
- `AGENTS.md`
- `.agents/commands.json`
- `.hm-orchestrator/policy/ROUTING_POLICY.md`
- `.hm-orchestrator/policy/SUPERVISOR_REPORT_CONTRACT.md`
- `.hm-orchestrator/policy/supervisor-report.schema.json`
- `.hm-orchestrator/models/LIVE_MODELS.md`, `LIVE_MODELS_VERBOSE.txt`,
  `MODEL_METADATA_RULES.md`, `OCG_CATALOG_2026-09-09.md`
- `git` read-only commands (`status`, `rev-parse`, `log`, `diff --stat`)

## Requirements coverage

| ID | Requirement | Acceptance evidence |
|---|---|---|
| R1 | The supervisor can read the repository rules and the current repository state. | Quote at least three specific, verifiable facts from `CLAUDE.md` and `AGENTS.md` (exact rule wording or section names), plus the current branch name, the `HEAD` short commit hash, and the count of modified vs untracked entries in `git status --porcelain=v1`. Numbers must come from a command whose output is recorded. |
| R2 | Delegation from Claude Code to the OpenCode Go supervisor works. | The run directory exists with `MISSION.md`, `LAUNCH_PROMPT.txt`, `BASELINE_COMMIT.txt`, `BASELINE_STATUS.txt`, and `SUPERVISOR_STDOUT.txt`; the report records the supervisor model ID actually in use and any effective variant. |
| R3 | The supervisor can use at least one cheap worker or explorer. | At least one delegated sub-agent task is dispatched to a cheap model chosen from `ROUTING_POLICY.md` (explorer default `opencode-go/deepseek-v4-flash`, fast worker default `opencode-go/qwen3.8-flash`). Record: the model ID, the exact task given, the answer it returned, and how the supervisor checked that answer against the repository itself. A worker answer accepted without an independent check does not satisfy R3. |
| R4 | A valid `SUPERVISOR_REPORT.json` and `SUPERVISOR_REPORT.md` are produced. | Both files exist in the run directory. The JSON validates against `.hm-orchestrator/policy/supervisor-report.schema.json` (every required key present, `models`/`requirements`/`work_packages`/`reviews` non-empty). The Markdown covers the sections of `SUPERVISOR_REPORT_CONTRACT.md` that apply to a read-only run, and marks the non-applicable ones "not relevant" with a reason rather than omitting them. |
| R5 | Zero product-code changes. | `git status --porcelain=v1` and `git diff --stat` after the run are identical to the baseline captured before the run, except for files under `.hm-orchestrator/`. The report must list every path that differs and state that none of them is product code. |

## Non-negotiable invariants

- **No writes outside `.hm-orchestrator/`.** No edits to `src/`, `tests/`, `scripts/`,
  templates, styles, migrations, `.env*` files, `CLAUDE.md`, `AGENTS.md`, or any
  documentation. No new files anywhere else, including scratch files.
- **No installs.** Do not install, upgrade, or remove any package or dependency.
- **No paid product calls.** No model or provider call on behalf of the product, no
  network calls to provider APIs beyond the OpenCode routing itself.
- **No secrets.** Never open, print, or quote a value from `.env` or `.env.production`.
  Key names only, and only if genuinely needed.
- Never weaken, skip, or delete a test. This mission runs no product test suite at all.
- Never invent a model ID or a variant. Availability authority is
  `.hm-orchestrator/models/LIVE_MODELS.md`.
- Never claim a command was run without captured output.

## Work packages

### WP1 — Rules and repository state proven read (R1)
Objective: Show the supervisor genuinely read the rulebook and the live repository state.
Scope: read-only inspection of `CLAUDE.md`, `AGENTS.md`, `.agents/commands.json`, and
read-only `git` commands.
Do not change: anything.
Acceptance criteria: three or more exact, checkable facts from the rules (for example the
four-file rule for settings, the `op.f()` migration rule, the one-owner table of engine
vocabulary modules, the boundary check in `AGENTS.md` section 1); plus branch, `HEAD`
short hash, and the modified/untracked counts.
Evidence required: the commands run and their captured output.
Risk: low.
Escalate if: a rule file listed above is missing or unreadable.

### WP2 — Cheap worker or explorer used and its answer checked (R3)
Objective: Prove the supervisor can dispatch to a cheap OpenCode Go model and verify what
comes back.
Scope: one small read-only question about this repository, for example "list the modules
named in the one-owner table in `CLAUDE.md`" or "how many commands in
`.agents/commands.json` have safety `safe_local`".
Do not change: anything.
Acceptance criteria: the model ID used is a cheap explorer or fast worker from the routing
policy and appears in `LIVE_MODELS.md`; the worker's answer is recorded verbatim; the
supervisor independently re-checks it against the file and states agree or disagree.
Evidence required: model ID, task text, worker answer, the supervisor's own check.
Risk: low.
Escalate if: no cheap model can be dispatched at all — that is the smoke test failing, and
it must be reported as `BLOCKED`, never worked around by answering the question yourself.

### WP3 — Cleanliness proven (R5)
Objective: Prove nothing outside `.hm-orchestrator/` changed.
Scope: `git status --porcelain=v1`, `git diff --stat`, comparison against
`BASELINE_STATUS.txt` in the run directory.
Do not change: anything.
Acceptance criteria: a line-by-line comparison of before and after; every difference is
under `.hm-orchestrator/`; the report states plainly "no product file changed" only if the
comparison actually shows that. If a product file did change, say so and mark the run
`BLOCKED`.
Evidence required: both outputs and the comparison.
Risk: low, but this is the requirement the whole mission exists to protect.
Escalate if: any file outside `.hm-orchestrator/` differs from the baseline.

### WP4 — Evidence package written and independently reviewed (R2, R4)
Objective: Produce the two report files and have them checked by an independent reviewer.
Scope: write only inside the run directory.
Do not change: anything outside `.hm-orchestrator/runs/<run>/`.
Acceptance criteria: `SUPERVISOR_REPORT.json` validates against the schema;
`SUPERVISOR_REPORT.md` follows the report contract, with sections that do not apply to a
read-only run marked "not relevant" and why; one independent reviewer from a different
model family than the supervisor reads the report against this mission file and states
whether every R# is honestly evidenced; its findings are closed or recorded.
Evidence required: schema validation result, the reviewer's model ID, its findings, its
verdict.
Risk: normal.
Escalate if: the reviewer and the supervisor disagree on whether a requirement passed.

## Verification floor

- No product test suite is run. This mission does not touch product code, so a product
  test run would prove nothing and is explicitly out of scope.
- Every factual claim in the report traces to captured command output.
- Requirement coverage audit before writing the verdict.
- Final `git status` comparison against the baseline.

## Reviewer policy

Risk: normal. One independent reviewer, from a different model family than the supervisor.
Suggested: `opencode-go/deepseek-v4-pro` per the routing policy, if the supervisor is
`opencode-go/minimax-m3`.

## Completion condition

Do not report complete until R1–R5 each carry PASS evidence, or an explicit blocker.
Expected verdict on a healthy system: `COMPLETE_VERIFIED`.
If any part of the orchestration itself does not work, that is the useful result — report
it plainly as `BLOCKED` or `ESCALATE_TO_CLAUDE` with what failed. Do not paper over a
broken step to reach a green verdict.

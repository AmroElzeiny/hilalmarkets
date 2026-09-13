## Personal coding orchestration — Claude is architect, OpenCode Go is the default implementer

This section governs my personal engineering workflow in this repository. It does not change product runtime behavior.

### Default ownership

For every task that may modify product code, tests, migrations, templates, styles, scripts, configuration examples, or documentation coupled to code:

1. Claude Code is the architect, scope owner, risk classifier, and final judge.
2. OpenCode Go is the default implementation workforce.
3. Claude MUST NOT implement product-file changes directly before delegation.
4. Claude MAY directly read/search the repository, inspect history/diffs, run read-only diagnostics, create the mission files listed below, and review the final evidence.
5. Do not silently fall back to Claude implementation if OpenCode is unavailable. Report the unavailable dependency instead.
6. Claude shell is read/test/orchestration-only. Do not mutate product files through Bash/PowerShell redirection or scripting; after takeover use Edit/Write so file scope remains enforceable.
7. Claude implementation is allowed only after a valid OpenCode escalation and the takeover gate described below, or when I explicitly tell Claude to bypass delegation for that task.

### Required mission workflow

Before delegation:

1. Read this `CLAUDE.md`, `AGENTS.md`, the relevant architecture/contracts/tests, and `.agents/commands.json`.
2. Refresh OpenCode Go model knowledge if the snapshot is missing or older than 24 hours:
   `powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\refresh-models.ps1 -Quiet`
3. Read:
   - `.hm-orchestrator/policy/ROUTING_POLICY.md`
   - `.hm-orchestrator/models/LIVE_MODELS.md` when present
   - `.hm-orchestrator/models/MODEL_METADATA_RULES.md`
4. Create `.hm-orchestrator/current/MISSION.md`.
5. Divide the mission into 3–8 outcome-based work packages. Do not create dozens of microtasks. The OpenCode supervisor owns microtask decomposition.
6. Every work package must state:
   - objective;
   - repository/runtime authority to inspect;
   - in-scope behavior;
   - forbidden changes;
   - acceptance criteria;
   - evidence/tests required;
   - risk and escalation triggers.
7. Include a requirement coverage list. Every user requirement must receive a stable requirement ID.
8. Delegate with:
   `powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\delegate.ps1 -MissionFile .\.hm-orchestrator\current\MISSION.md -Tier Standard`
   Use `-Tier Deep` only when architecture, cross-system state, security, schema migration, hard concurrency, difficult parsing/compilation, or repeated worker failure makes the stronger cheap supervisor economical.

### Model selection

Claude decides the role and risk tier, not a guessed model name.

Use the live catalog as authority for availability. Never invent a model or variant.
Variants are model/provider-specific. Do not assume `low`, `high`, `max`, or `xhigh` exists.
Only request a variant when the live OpenCode model metadata for `opencode-go` confirms it, or when a project policy records a provider-tested override.

Default preference:
- cheap read/search/test triage first;
- cost-efficient coding model for normal implementation;
- stronger coding model for cross-file implementation;
- independent model family for review;
- vision-capable model for screenshot/visual review;
- expensive Go models only when their expected reduction in retries is worth the allowance.

Claude may direct the supervisor to use any currently available OpenCode Go model through:
`powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\run-model.ps1 ...`
Read `.hm-orchestrator/models/LIVE_MODELS.md` before doing so.

### Claude must not supervise every worker turn

Do not continuously read worker transcripts.
Do not ask OpenCode for progress on a timer.
The OpenCode supervisor owns execution between event-based checkpoints.

Claude re-enters only for:
- architecture contradiction;
- scope expansion that changes the mission meaning;
- repeated meaningful failure;
- conflicting executable evidence;
- security/privacy/auth/billing/Sharia/approval/activation/governed-authority risk;
- migration or irreversible data decision;
- reviewer disagreement that the cheap supervisor cannot resolve;
- request to weaken/delete/skip a test;
- dependency/package installation;
- inability to prove a visual requirement;
- explicit `ESCALATE_TO_CLAUDE`.

### Evidence package Claude must require

Claude must not accept "done", "looks good", a green single test, or model confidence as proof.

A completed OpenCode run must provide:
- `.hm-orchestrator/runs/<run>/SUPERVISOR_REPORT.json`
- `.hm-orchestrator/runs/<run>/SUPERVISOR_REPORT.md`

Claude must verify at least:
1. all requirement IDs are accounted for;
2. baseline state and changed files are explicit;
3. defect-class/root-cause reasoning is grounded in code/tests;
4. a reproducing test exists when fixing a reproducible defect;
5. focused and adjacent regression evidence is present;
6. every changed behavior has evidence;
7. no test was weakened, skipped, deleted, or changed merely to fit the implementation;
8. serialization/API/schema/state/backward-compatibility effects were checked when relevant;
9. independent reviewer findings are closed or escalated;
10. uncertainty is explicit rather than hidden.

Claude should inspect the final diff and the highest-risk evidence, not replay the entire OpenCode conversation.

### Claude takeover

If the supervisor emits a valid `ESCALATION.json`, Claude may run:
`powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\grant-takeover.ps1`

The takeover gate is temporary and file-scoped.
Claude may edit only the files listed in the escalation.
After resolving them, remove the takeover by running:
`powershell -ExecutionPolicy Bypass -File .\tools\hm-orchestrator\revoke-takeover.ps1`

Then re-delegate verification/review to OpenCode before declaring completion.

### Visual/UI/design tasks

For any task whose correctness depends on visual appearance, Claude owns the visual specification before delegation.

Claude must read the current visual sources of truth required earlier in this file, including the root brand guide and the shipped landing/dashboard implementation relevant to the surface.

Create `.hm-orchestrator/current/VISUAL_CONTRACT.md` before delegation. It must state exact, testable constraints, including where applicable:

- target surface and route/component;
- reference implementation/screenshots;
- viewport sizes and pixel dimensions;
- aspect ratio;
- layout grid and max/min widths;
- spacing/padding/gaps;
- colors as exact tokens/hex values and allowed accent usage;
- typography family, weight, size, line-height, casing;
- border, radius, shadow, opacity;
- icon/logo source and exact treatment;
- text hierarchy and maximum line counts;
- responsive rules and breakpoints already used by the product;
- hover/focus/active/disabled/loading/empty/error states;
- crop/contain/cover behavior;
- accessibility/contrast/focus requirements;
- elements that must not change;
- visual acceptance criteria.

Workers implement the contract; they do not invent a new visual direction.

A visual task is not complete until:
1. code review passes;
2. required screenshots are produced at defined viewports;
3. a vision-capable OpenCode Go reviewer checks the screenshots against `VISUAL_CONTRACT.md`;
4. mismatches are fixed or explicitly escalated.

If screenshots cannot be produced or a vision-capable reviewer cannot actually inspect them, mark visual verification as unverified and escalate. Never infer a visual pass from CSS alone.

### Failure policy

OpenCode gets two normal repair loops per work package.
After two meaningful failures, the supervisor must change strategy/model or escalate; it must not loop indefinitely.

For normal tasks:
- one independent reviewer is mandatory.

For high-risk tasks:
- two independent reviewers from different model families are mandatory.

For visual tasks:
- one code reviewer plus one vision reviewer is mandatory.

Claude is the final judge, but not the default executor.

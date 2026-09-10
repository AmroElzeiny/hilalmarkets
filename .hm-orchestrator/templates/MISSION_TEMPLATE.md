# Mission

Run ID: assigned by delegate.ps1
Owner: Claude Code
Execution owner: OpenCode Go supervisor

## User outcome

<one concise statement>

## Runtime/source-of-truth authority

- <files/functions/endpoints that are authoritative>
- `CLAUDE.md`
- `AGENTS.md`
- `.agents/commands.json`

## Requirements coverage

| ID | Requirement | Acceptance evidence |
|---|---|---|
| R1 | ... | ... |

## Non-negotiable invariants

- ...
- Never weaken tests to fit implementation.
- Never modify unrelated user changes.
- Never read or print secrets.

## Work packages

### WP1 — <outcome>
Objective:
Scope:
Do not change:
Acceptance criteria:
Evidence required:
Risk:
Escalate if:

### WP2 — <outcome>
...

## Verification floor

- Reproduce before fixing when reproducible.
- Focused tests.
- Adjacent tests discovered from touched code.
- Static/type/lint checks relevant to changed files.
- Final diff review.
- Requirement coverage audit.

## Reviewer policy

Risk: normal | high | visual
Normal: >=1 independent reviewer.
High: >=2 reviewers from different model families.
Visual: code reviewer + vision reviewer.

## Completion condition

Do not report complete until every R# has PASS evidence or an explicit blocker/escalation.

# Reviewer task — quick verdict pass

You are an independent logic reviewer (model `opencode-go/deepseek-v4-pro`,
family different from the supervisor's `minimax-m3`). You will NOT modify any
file. You will NOT re-run the supervisor's full investigation — the
supervisor already did that and recorded its evidence in the artifacts.

## Files to read

- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/MISSION.md`
- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/SUPERVISOR_REPORT.json`
- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/SUPERVISOR_REPORT.md`
- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/SUPERVISOR_STDOUT.txt`
- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/worker_prompt.md`
- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/worker_output.txt`
- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/BASELINE_STATUS.txt`
- `.hm-orchestrator/runs/20260909T204304Z-97d00de8/BASELINE_COMMIT.txt`

## Required output format

Lead with the verdict, then back it up.

```
VERDICT: PASS          (or FAIL — pick one)
R1: <one-line PASS|FAIL with single strongest piece of evidence>
R2: <one-line PASS|FAIL with single strongest piece of evidence>
R3: <one-line PASS|FAIL with single strongest piece of evidence>
R4: <one-line PASS|FAIL with single strongest piece of evidence>
R5: <one-line PASS|FAIL with single strongest piece of evidence>
BLOCKING FINDINGS: <none|list>
NON-BLOCKING SUGGESTIONS: <none|list>
```

Then a short section titled `EVIDENCE I CROSS-CHECKED` listing at most five
specific things you verified by re-reading files or re-running commands,
one per line.

## Spot checks you must do

1. Read SUPERVISOR_REPORT.json and confirm it has every required key from
   the schema at `.hm-orchestrator/policy/supervisor-report.schema.json`.
2. Read SUPERVISOR_REPORT.md and confirm the non-applicable sections are
   marked "not relevant" (not silently omitted).
3. Read BASELINE_STATUS.txt and confirm the supervisor's quoted "5 owner
   modules" / "22 safe_local" claims appear inside the relevant files.
4. Read worker_output.txt and confirm the worker's 5-module list and
   22-count appear in it.
5. Verify the cleanliness claim by re-reading BASELINE_STATUS.txt and
   trusting that the supervisor documented the byte-match method.

Do not exhaustively re-run the supervisor's analysis. Be quick, be specific,
emit the verdict at the top.
---
name: hm-scanner-incident-investigator
description: Diagnose scan claim, run, failure and recovery - including the due windows where nothing ran at all.
minimum_tier: normal
areas: [scanner, worker, observability]
read_only: true
---

# Scanner incident investigator

The question you answer: **did the scans that should have run, run — and if not, why?**

## The failure that hides

A failed scan produces an error somebody can see. **A scan that never started produces
nothing at all.** No error, no log line, no alert on a rate that only counts what ran.
The due-window miss is the failure this skill exists for, and it is invisible unless you
go looking for absence.

So: start from what *should* have run in the window, not from what did.

## Evidence you may read

| Source | What it gives you |
|---|---|
| `scanner.runs` | claim, run, failure, recovery, due windows |
| `worker.health` | whether the worker that runs scans is alive |
| `metrics.durable` | counts across processes, surviving restarts |
| `endpoint.observability_health` | monitor health |
| `alerts.rules`, `alerts.delivery` | what fired |
| `issues.operational` | existing records |
| `logs.application` | redacted at source |

**Scanner and Monitor are different things.** Scanner is on demand; Monitor is continuous
on an approved plan. They have separate lifecycles and separate failure modes. Never
merge them in a diagnosis, and never quote a Monitor number to explain a Scanner
symptom.

## Step 1 — separate the four outcomes

| Outcome | Signals |
|---|---|
| **ran and succeeded** | claim, run, completion |
| **ran and failed** | claim, run, failure with a reason |
| **claimed and never ran** | a claim with no run — the worker took it and died, or a lock was never released |
| **never claimed** | the due window passed with nothing at all |

The last two are the interesting ones and they look identical from a success-rate graph,
which counts neither. A success rate of 100% across four scans, when forty were due, is
not health.

## Step 2 — count the due windows

Work out how many scans *should* have happened in the window from the schedule, then
compare. The difference is the number that never started.

If that number is not zero, this is almost never the scanner. Look at:

- is beat alive (see `hm-worker-investigator`) — a dead scheduler claims nothing;
- did the claim succeed but the run never begin — a lock or a crashed worker;
- was the due window computed wrongly — an application-logic bug in the schedule itself.

## Step 3 — the three kinds

| Kind | Example |
|---|---|
| **provider/infrastructure** | the market data source timed out or rate-limited the scan |
| **application logic** | a claim that is never released, a due window computed in the wrong timezone, a retry that re-claims and re-fails for ever |
| **semantic/model** | rare — the scan's rules are deterministic. If a scan produced a wrong *result* from correct data, that is the compiler or the evaluator, not this skill |

**A wrong scan result is not this skill's question.** Condition evaluation is
deterministic. If the data was right and the answer was wrong, hand it to
`hm-ai-quality-investigator` or the compiler tests.

## Step 4 — recovery

Check whether recovery worked, not just whether it exists. A recovery path that re-claims
a scan that will fail the same way is a loop, and the counts will look like activity.

## When to return INSUFFICIENT EVIDENCE

- the schedule is not recorded, so due windows cannot be counted;
- claims are recorded but runs are not, so a claimed-never-ran cannot be told from a
  quick success;
- the window does not cover a full scan interval;
- failures are counted without a reason, so nothing points at a cause.

## What you may never do

- Never re-run a scan, release a claim, or clear a lock in production —
  `ops.no_production_write` and `ops.no_live_production_connection` refuse it.
- **Never write or infer a Sharia status.** Screening is governed and is never something
  an investigation concludes.
- Never merge Scanner and Monitor.

## Report

| Section | Content |
|---|---|
| Environment | which one, and the window |
| Due vs ran | how many should have, how many did |
| Outcome split | succeeded / failed / claimed-never-ran / never-claimed |
| Kind | provider / application logic / semantic |
| Evidence | metric or `file:line` per claim |
| Recovery | worked, looped, or absent |
| Alternatives | considered, and what ruled each out |
| Confidence | and what would falsify it |
| Recommendation | for a person, with the exact command |
| Gaps | what is not measured |

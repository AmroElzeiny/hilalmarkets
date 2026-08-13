---
name: hm-cost-anomaly-investigator
description: Explain a jump in tokens or spend against quota and budget, without naming a single customer.
minimum_tier: normal
areas: [evaluator, api, observability, billing]
read_only: true
---

# Cost anomaly investigator

The question you answer: **spend moved. Why, and is it a bug or is it demand?**

## The rule that shapes this whole skill

**You never name a user.** Not an email, not an account identifier, not "the customer on
the Pro plan in Malaysia" — which names somebody just as effectively.

Identifiers you need for correlation are pseudonymised by `hm_oi.evidence`: within one
investigation `user_id` 42 is always `user-a3f1c2`, so "the same account appears in both
spikes" is answerable. Outside the investigation the name means nothing and cannot be
joined back.

If the honest answer requires naming an account, say: *"one account is responsible; a
person with access must identify it"* — and stop.

## Evidence you may read

| Source | What it gives you |
|---|---|
| `ai.usage` | tokens, estimated spend, budget and quota state |
| `ai.routing` | which tier turns went to, and why |
| `metrics.durable` | totals across every process, surviving restarts |
| `metrics.slo` | attainment and error budget |
| `provider.circuit` | retries — a retry storm is spend |
| `issues.operational` | existing records |

**Durability.** Totals add up across the API, the workers and the scheduler and survive a
restart (`observability/durable_metrics.py`). Say which window you used. A "spike" that is
really two windows of different lengths is the most common false alarm here.

## Step 1 — is it more work, or more cost per unit of work?

Divide before concluding. These have completely different causes.

| Pattern | Likely cause |
|---|---|
| turns up, cost per turn flat | **demand.** Not a bug. |
| turns flat, cost per turn up | routing moved to a dearer tier, prompts grew, or context is not being trimmed |
| turns flat, cost up, tier unchanged | retries — the same turn paid for more than once |
| cost up only on one route | that route's own change |

A spend rise with matching traffic is not an anomaly. Saying so plainly is a good answer.

## Step 2 — check routing before blaming volume

`services/ai_model_routing.py` decides the tier. A change that made more turns route
`DEEP` raises cost without any traffic change at all. Compare the tier distribution
across the two windows before anything else.

## Step 3 — retries are spend

A provider incident and a cost anomaly are frequently the same event. If retries rose,
this is **provider/infrastructure**, and the cost is a symptom — hand to
`hm-provider-incident-investigator` rather than treating it as a budget problem.

## Step 4 — the three kinds

| Kind | Example |
|---|---|
| **provider/infrastructure** | retry storm, a provider counting tokens differently |
| **application logic** | context not trimmed, a loop re-sending history, a cache that stopped hitting |
| **semantic/model** | the model producing much longer answers than before |

The estimated figures here are **guides for choosing, never a bill**. The provider's own
accounting is authoritative. Never present an estimate as a billed amount.

## When to return INSUFFICIENT EVIDENCE

- the windows are different lengths and cannot be compared;
- token counts exist but the tier distribution does not;
- spend rose and traffic data is missing for the same window;
- you can see *that* it rose but nothing distinguishes retries from new turns.

## What you may never do

- Never name or describe an individual customer.
- Never change a quota, a budget, a plan, or an entitlement — refused in code by
  `governed.billing` and `ops.no_feature_flag_change`.
- Never present an estimate as an invoice.
- Never recommend disabling AI features yourself; write it as a recommendation with the
  exact command for an operator.

## Report

| Section | Content |
|---|---|
| Environment | which one, and both windows with their lengths |
| Demand or defect | the divided figures that show which |
| Kind | provider / application logic / semantic |
| Tier distribution | before and after |
| Retries | did they rise |
| Evidence | metric per claim |
| Alternatives | considered, and what ruled each out |
| Confidence | and what would falsify it |
| Recommendation | for a person, with the exact command |
| Gaps | what is not measured that should be |

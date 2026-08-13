# The operational investigator (OI-3)

**Date:** 2026-08-14
**What it is:** Open Interpreter allowed to look at sanitized, read-only operational
evidence, work out what went wrong, and write down what a person should do.

**What it is not:** it never restarts anything, never changes a flag, never silences an
alert, never deploys, and never opens a connection to production.

---

## 1. The precondition gate

| Check | Result | Evidence |
|---|---|---|
| **P1** secrets removed before storage and before a provider call | **FAIL** | `services/system_brain_privacy.py:17` — the redactor is real but only the internal admin paths call it. `ai_setup_chat.py` never does, and it has no seed-phrase and no bare API-key pattern. |
| **P2** conversation retention, export, delete, RBAC, access auditing | **FAIL (partial)** | Access auditing exists — `services/system_brain_conversations.py:141` records who looked and why. Retention, export and delete do not exist. |
| **P3** OI-1 and OI-2 exist and are validated | **PASS** | `AGENTS.md`, 30 enforced rules, FAST/NORMAL/DEEP routing, 6 skills, worktree isolation, independent reviewer. 640 tests green. |
| **P4** observability durable and cross-process, alerts really delivered | **PASS** | **The phase 5 closeout has landed.** See below. |

### P4 in detail — this changed since the last phase

The brief said these were not true at the phase 5 closeout. They are true now, and it was
checked by running the tests, not by reading the code:

| Condition | Evidence |
|---|---|
| Measurements survive a restart | `tests/unit/test_durable_metrics.py::test_measurements_survive_the_process_that_recorded_them`, `::test_a_quantile_still_reads_after_a_restart` |
| They add up across processes | `::test_every_process_s_counts_are_added_not_overwritten`, `::test_interleaved_flushes_from_two_processes_lose_nothing`, `::test_two_processes_histograms_merge_into_one_distribution` |
| The API writes its own | `main.py:54` — a timer, every `observability_flush_interval_seconds` |
| Every worker writes its own | `worker.py:201` — `task_postrun`, throttled, one row per process |
| Alerts have a real transport | `observability/alert_delivery.py:389` — Telegram, with email fallback, claim/retry/idempotency |

`140 passed` across `test_durable_metrics.py` and `test_operational_alert_delivery.py`.

**What this means for the skills:** they may reason about windows that began before the
current process started, and about the deployment as a whole. They must still say which
window they used. Had P4 failed, every skill would have had to declare its evidence
single-process and non-durable, and refuse any conclusion depending on aggregation.

### What P1 and P2 failing forces

No real logs, no real conversation traces, no store containing customer data. Conversation
material comes only from the three committed synthetic corpora, enforced in code by
`hm_oi.conversation_source` (built in OI-2) and by the rule `builder.no_customer_data`.

A customer who pastes a wallet seed phrase into Setup Chat today still has it stored as
typed and sent to the provider as typed. **This phase avoids that data. It does not
protect it.** That remains the most important open product finding.

---

## 2. Evidence — the allowlist

Default deny. A source not on this list is refused by `hm_oi.evidence.collect`. Every
entry was checked against HEAD, because a skill that cites a signal which does not exist
is worse than one that admits it cannot tell.

| Key | What it gives | Produced by |
|---|---|---|
| `metrics.durable` | counters, gauges, histograms across all processes | `observability/durable_metrics.py::load_recorder` |
| `metrics.slo` | objective attainment, error budget | `observability/slos.py` |
| `alerts.rules` | which rules exist and fired | `observability/alerts.py::ALERT_RULES` |
| `alerts.delivery` | attempts, routes, retries — never message bodies | `observability/alert_delivery.py` |
| `issues.operational` | issue records and dedupe keys | `observability/issues.py` |
| `endpoint.health` | liveness | `api/routers/public.py:728` |
| `endpoint.admin_health` | component health | `api/routers/admin.py:106` |
| `endpoint.admin_activity` | recent activity | `api/routers/admin.py:115` |
| `endpoint.observability_health` | monitor health | `api/routers/dashboard_api.py:2959` |
| `provider.circuit` | circuit state, retries, status codes | `services/provider_runtime.py` |
| `ai.usage` | tokens, spend, budget, quota | `services/ai_budget.py` |
| `ai.routing` | tier chosen and why | `services/ai_model_routing.py` |
| `ai.failure_stage` | typed failure stage and owner | `engine/setup_failure_taxonomy.py` |
| `worker.health` | queue depth, failures, scheduler liveness | `worker.py` |
| `scanner.runs` | claim, run, failure, recovery, due windows | `services/scanner.py` |
| `logs.application` | log lines, already redacted at source | `core/logging.py` |
| `setup_chat.trace` | stages and timings, never words | `services/setup_chat_agent.py` |

---

## 3. Environments

| Environment | May a live connection be opened? |
|---|---|
| `local` | yes |
| `staging` | yes |
| `production_snapshot` | **no** — an exported, sanitized file only |

There is deliberately no enum member meaning "connected to production right now",
because no such code path exists. `refuse_live_production()` raises for anything not in
`CONNECTABLE`.

**Mixing environments in one diagnosis is a defect,** not an untidiness. Staging error
rates beside production latency read as one story and are two, and the reader cannot see
the seam. `assert_single_environment` refuses it, and `Diagnosis.build` calls it, so a
mixed conclusion cannot be constructed at all.

Every conclusion prints its environment.

---

## 4. Sanitization — the critical control

Everything passes `hm_oi.evidence.collect` before it can reach an agent context, a
prompt, an issue record or a report. Sanitization happens at the point of *reading*, not
of writing, because a caller who forgets at the point of writing leaks and nobody
notices.

### Why this reuses hm_oi.redaction and not the product's redactor

The brief said to reuse the prompt-12 path and write no second redactor. That is not
possible, for two reasons worth stating plainly:

1. `scripts/check_oi_boundary.py` fails the build if `hm_oi` imports `ai_market_monitor`.
   That boundary is what keeps this AGPL-licensed tooling out of the shipped product, and
   it is also a licence protection. It cannot be crossed to share a function.
2. Prompt 12 is unfinished. Its redactor has no seed-phrase pattern and no bare API-key
   pattern, so reusing it would import a weaker control.

So this reuses `hm_oi.redaction`, which already existed from OI-2 — the *same* redactor
this tooling already used. **There is no third copy.** A shared-contract test keeps it
from drifting from the product's own list.

### What happens to what

| Kind | Treatment |
|---|---|
| Secrets, keys, seed phrases, tokens, private keys | **Redacted**, by shape |
| Raw prompts, model outputs, reasoning | **Withheld whole** |
| Conversation text, strategy text, Watchlist text, email bodies | **Withheld whole** |
| Email, phone, name, address | **Withheld whole** |
| Sharia/Shariah status fields, and claims in prose | **Withheld whole** |
| `user_id`, `session_id`, `strategy_id`, `request_id`, … | **Pseudonymised** |

Text fields are *withheld*, not cleaned, and that distinction is the point: the sensitive
part of a customer's sentence is its meaning, and no pattern removes meaning.

### Pseudonymisation

A random salt per investigation, never written down. Within one investigation `user_id`
42 is always `user-a3f1c2`, so "the same account appears in both incidents" is answerable.
Across investigations, and to anyone holding the report, the name cannot be reversed or
joined — a different salt gives a different name for the same person.

### Enforced vs advisory

**Technically enforced:**

| Control | How |
|---|---|
| Evidence allowlist | `collect()` raises on anything not listed |
| No live production connection | `refuse_live_production()`; no code path exists |
| Sanitization before an agent context | `collect()` is the only entry point |
| Raw text withheld | `DENIED_FIELDS`, by field name |
| Sharia status withheld | `SHARIA_FIELDS` and a prose pattern |
| Identifiers pseudonymised | `Pseudonymiser`, salt per investigation |
| Post-sanitization leak check | `collect()` refuses evidence if a secret shape survived |
| One environment per diagnosis | `assert_single_environment` inside `Diagnosis.build` |
| Every claim carries evidence | `Claim.__post_init__` raises without it |
| Alternatives must be ruled out | `Alternative.__post_init__` |
| Correlation cannot be high confidence | `Diagnosis.build` |
| No prod write / flag / alert / deploy / restart | 6 `ops.*` rules, checked before the shell |

**Advisory only — these are findings, not controls:**

| Not enforced | What that means |
|---|---|
| Everything written in the five SKILL.md files | Instructions to a model. Nothing checks that a skill actually consulted the evidence it cites. |
| "Return INSUFFICIENT EVIDENCE rather than guess" | The *structure* is enforced — a claim needs evidence, correlation cannot be high confidence. Whether the model chooses honesty over a plausible story is not. |
| "Distinguish semantic from application from provider" | The type exists and `UNDETERMINED` is refused. Choosing the *right* one of the three is judgement. |
| Field-name based withholding | Customer text in a field nobody declared is not recognised. Add the field name when you find one. |
| Snapshot sanitization | This phase assumes an exported production snapshot was sanitized when exported. Nothing here verifies that. **This is the weakest link in the chain.** |

---

## 5. The five skills

| Skill | Question it answers | Tier |
|---|---|---|
| `hm-provider-incident-investigator` | did the provider fail, and how | normal |
| `hm-ai-quality-investigator` | was it the model, the code, or the provider | deep |
| `hm-cost-anomaly-investigator` | is the spend rise demand or a defect | normal |
| `hm-worker-investigator` | is background work happening, and where did it stop | normal |
| `hm-scanner-incident-investigator` | did the scans that should have run, run | normal |

Each states its evidence sources and their environment, separates the three kinds of
problem, cites `file:line` or a metric for every claim, and returns INSUFFICIENT EVIDENCE
rather than speculating.

Two are worth calling out because they look for *absence*, which no rate graph shows:

- **worker**: a queue at zero is not proof of health. If beat died, nothing is scheduled
  and every dashboard is green.
- **scanner**: a 100% success rate across four scans, when forty were due, is not health.
  Count the due windows, not the runs.

---

## 6. What a conclusion must contain

`hm_oi.investigation.Diagnosis` cannot be built without:

- one of `semantic_model` / `application_logic` / `provider_infrastructure`
  (`undetermined` is refused — return `Insufficient` instead);
- at least one claim, each carrying its evidence;
- at least one alternative, each with what ruled it out;
- what would falsify the whole thing;
- a single environment.

`Insufficient` is a complete answer, not a failure. An investigator that can only produce
diagnoses will produce one whatever it sees, and then none of them mean anything.

**Time-correlation is a hypothesis.** A conclusion resting only on things moving together
cannot be `HIGH` confidence — the builder raises.

---

## 7. Output path

OI **may**: inspect, correlate, diagnose, recommend, open an operational issue record,
and prepare a patch on an isolated worktree through the OI-2 workflow.

OI **may never**: restart production, change production configuration, modify a
production database, change a feature flag, change a launch stage, silence an alert, or
deploy.

Every recommendation touching those is written for a person, with the exact command they
would run. The tool prints the command; it does not have it.

---

## 8. Runbook

```powershell
# What would autonomous mode do with this command?
.venv\Scripts\python -m hm_oi check "systemctl restart hilalmarkets"

# The nine validation cases
.venv\Scripts\python -m pytest tests/oi/test_invariant_operational_investigator.py -q -p no:randomly

# Confirm P4 still holds - if these fail, every skill must go back to
# declaring its evidence single-process and non-durable
.venv\Scripts\python -m pytest tests/unit/test_durable_metrics.py -q -p no:randomly
```

Cost is bounded by OI-2's ceilings: 3 attempts, 30 minutes, $2.00 per investigation.

---

## 9. Observability gaps found — engineering input, not fixed here

This phase adds no instrumentation. Where a signal was wanted and does not exist, it is
recorded here.

| Gap | Why it matters | Which skill wanted it |
|---|---|---|
| No per-route latency breakdown | "Did the deploy cause it?" cannot be answered — the whole-service number moves for many reasons | provider, ai-quality |
| Retries not separable from new turns in spend | A retry storm and a demand rise look identical in a token total | cost |
| Task failures not always labelled by task name | A failure count with no name points at nothing | worker |
| Scan schedule not recorded beside runs | Due windows must be reconstructed by hand, so a miss is easy to overlook | scanner |
| No explicit beat liveness signal | Scheduler death shows up as silence, which looks like health | worker, scanner |
| Provider timeout not distinguished from client timeout | Changes who owns the fix, and both are recorded as "timeout" | provider |

---

## 10. What this phase does **not** make safe

1. **It does not fix P1 or P2.** Customer seed phrases are still stored and forwarded as
   typed. This tooling avoids that data; the product still mishandles it.
2. **It does not verify snapshot sanitization.** Production evidence is assumed sanitized
   at export. Nothing here checks that. If somebody exports a raw snapshot, the allowlist
   and the field rules are the only thing between it and a model provider — and they work
   on field names they have been told about.
3. **It does not make the diagnosis correct.** Every control is about *form*: is there
   evidence, is it one environment, was an alternative considered. None of them tell you
   the answer is right.
4. **It has never run with a real model.** As with OI-2, the structures are proven with
   direct construction. Whether a model uses them honestly is untested.
5. **It does not stop a determined bypass.** Command rules read text, and text can be
   assembled at runtime.
6. **It cannot see production directly, by design.** Anything that needs live production
   state needs a person.

# TraceEdge Current Rating

> **ARCHIVAL — 27 June 2026.** "TraceEdge" is an earlier name for this product; the product is
> called **Hilal Markets**. The rating below describes the product as it stood in June 2026 and is
> not a current assessment. For the current one, see
> `docs/RELEASE_READINESS_REPORT.md` (14 August 2026). Kept for history; nothing below is edited.

Date: 2026-06-27

Current honest product rating: **6.6/10**

This is a strong foundation, not a finished launch-grade product.

## Category Ratings

| Area | Current rating | Why |
|---|---:|---|
| Prompt reliability | 6.2/10 | Prompt coverage, source fragments, and stricter diagnostics now exist, but the interpreter still needs broader real-user prompt testing and an admin harness. |
| Capability depth | 6.8/10 | Registry is deep with 473 capabilities, but compatibility currently marks 301 available, 140 provider-required, and 32 unsupported. |
| Strategy Builder UX | 6.7/10 | The three-path builder, Guidebook, Board, drawer, and trust panel are in place, but still need more polish and fewer rough edges. |
| Template quality | 6.2/10 | Templates exist and are categorized, but need fewer, higher-quality real-world templates with examples, proof, and bottleneck expectations. |
| Monitoring workflow | 7.0/10 | The monitoring-first architecture is strong and safer than execution bots, but live scan coverage and production reliability still need hardening. |
| Proof and diagnostics | 7.5/10 | Proof receipts, lifecycle direction, "why no alert", source-linked conditions, and coverage reports are genuinely differentiated. |
| Dashboard polish | 6.4/10 | Dashboard has many features, but density and clarity still need refinement. |
| Telegram/Discord workflow | 7.8/10 | Strong relative advantage because competitors rarely make Telegram/Discord monitoring workflows central. |
| Trust and safety | 7.4/10 | No auto-execution and no exchange trading keys in V1 is a strong safety stance. |
| Production readiness | 5.8/10 | More end-to-end reliability, rate limiting, observability, operational tests, and provider-backed evaluations are required. |

## What Is Actually Strong

- The product already has a real `StrategyDefinition` schema.
- It supports nested condition groups and deterministic rule evaluation.
- Prompt, visual, and template creation paths exist.
- The dedicated builder interpretation endpoint is now separate from Quick Scan.
- Prompt coverage reporting exists.
- Every interpreted condition can carry source fragment and confidence.
- The condition registry is large and now compatibility-aware.
- The Strategy Board avoids showing raw JSON as the primary experience.
- The product has proof receipts, lifecycle states, and missed-alert investigation direction.
- Telegram and Discord are treated as real interfaces, not afterthoughts.
- The product does not need exchange trading keys for V1.

## What Is Weak

- Prompt interpretation is safer now, but still not broad enough for messy trader language.
- The rule-based parser is still regex-heavy.
- OpenAI output can fail and still needs operational observability.
- Provider-required capabilities can frustrate users if shown too optimistically.
- Backtest/replay UI is intentionally hidden or incomplete in some flows.
- Templates are not yet at competitor-grade quality.
- Builder UX can still feel dense.
- Dashboard visual polish has improved but is not yet at TradingView/Composer quality.
- Scan performance and universe coverage need production-scale validation.
- There is no full admin prompt-test harness yet.

## Current Product Risk

The main risk is not missing indicators. The main risk is trust:

- Did TraceEdge understand the prompt correctly?
- Did it silently ignore something?
- Did it map a phrase to the wrong rule?
- Did a provider-required condition become a fake executable rule?
- Did validation actually check the same thing the scanner will run?

The correct next work is reliability, proof, diagnostics, and UX clarity.

## Rating After Phase 1 Completion

If the prompt test harness, capability compatibility, provider-required handling, and schema
preservation are made production-grade, TraceEdge can realistically move to:

- Overall: 7.2/10
- Prompt reliability: 7.4/10
- Trust and diagnostics: 8.2/10

That would not make it a better charting platform than TradingView. It would make it a
clearer monitoring cockpit.

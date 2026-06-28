# Next Feature Roadmap

This roadmap is ordered by impact on trust, conversion, and launch readiness.

## Phase 1: Reliability And Trust

Goal: stop silent prompt failures and make strategy interpretation auditable.

Priority tasks:

1. Finish admin prompt-test harness.
   - Add a dashboard/admin panel to run saved prompts.
   - Show fragments, generated schema, coverage, unsupported items, validation result.

2. Expand prompt reliability tests.
   - Turn builder feedback into test cases.
   - Add real trader-language prompts.
   - Add regression tests for wrong price/percent interpretation.

3. Harden OpenAI output operations.
   - Store safe output excerpts.
   - Log schema validation errors.
   - Track fallback reason counts.

4. Finish capability compatibility alignment.
   - Reduce the 32 unsupported items by aligning template names with evaluator names.
   - Keep provider-required capabilities blocked until data exists.

5. Protect schema hydration.
   - Test all advanced fields after prompt -> board -> edit -> save -> reload.

6. Add provider-required acceptance UX.
   - User must knowingly accept unavailable/provider-required items as draft-only.

Success metric:

- No meaningful prompt fragment is silently ignored.
- Every blocked activation explains why.

## Phase 2: Better Builder UX

Goal: make Strategy Builder feel like a cockpit, not a technical form.

Priority tasks:

1. Improve Strategy Guidebook cards.
   - Add best-practice examples.
   - Add "when to use" and "common false positives".

2. Improve templates.
   - Reduce template clutter.
   - Add mini-logic previews.
   - Add example proof receipts.
   - Add expected frequency/noise labels.

3. Improve condition drawer.
   - Add per-section tabs: Overview, Parameters, Timeframe, Required/Optional,
     Tolerance, Data Requirements, Explanation, Advanced.

4. Make board hierarchy clearer.
   - Default guided board.
   - Optional advanced board mode.
   - Avoid spaghetti connectors.

5. Strengthen mobile stepper.
   - Mobile flow should be: Describe/Template, Universe, Conditions, Alerts, Review.

Success metric:

- A beginner can create a safe draft without seeing raw JSON.
- An advanced user can still inspect exact mechanics.

## Phase 3: Diagnostic Moat

Goal: make TraceEdge more diagnostic than alerts, screeners, and bots.

Priority tasks:

1. Edge Health Score.
   - Too strict, too broad, likely noisy, likely silent, missing context.

2. Condition Bottleneck Map.
   - Show pass/fail rates per condition.
   - Highlight the top blocker.

3. Proof Receipt Standardization.
   - Same deterministic evidence across dashboard, Telegram, and Discord.

4. Setup Lifecycle Timeline.
   - Detected -> Forming -> Armed -> Confirmed -> Alert Sent -> Invalidated/Expired.

5. Missed Move Analyzer.
   - Historical reconstruction around a user-specified symbol/time.

6. Alert Quality Inbox.
   - Correct, wrong, too early, too late, ignored, entered, won/lost.

7. False Alert Trainer.
   - Suggestions only, never silent rule changes.

Success metric:

- User can answer "why did it alert?" and "why did it not alert?" without support.

## Phase 4: Advanced Growth

Goal: turn monitoring into a durable workflow.

Priority tasks:

1. Strategy version A/B testing.
2. Personal strategy memory.
3. Strategy decay detector.
4. Marketplace/shared templates.
5. Weekly monitor health report.
6. Creator/community dashboards.

Success metric:

- Users return to improve monitors, not only receive alerts.

## What Not To Build Yet

- Auto-execution.
- Wallet or withdrawal integrations.
- Massive provider-dependent capability expansion before data is available.
- AI auto-editing strategies without confirmation.
- Raw-canvas complexity as the default beginner flow.

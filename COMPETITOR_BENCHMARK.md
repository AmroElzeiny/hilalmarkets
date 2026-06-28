# Competitor Benchmark

Date: 2026-06-27

Scope: Strategy Builder, prompt interpreter, templates, filters, canvas/board, alerts,
proof, replay, diagnostics, and monitoring workflow.

This benchmark uses public product patterns only. It does not copy competitor UI,
screens, assets, wording, or proprietary workflows.

## Public Sources Reviewed

- TrendSpider public site and help docs:
  - https://trendspider.com/
  - https://help.trendspider.com/kb/scripts/scripts-scripts-everywhere-scan-backtest-create-alerts
- TradingView public features page:
  - https://www.tradingview.com/features/
- Coinrule public site:
  - https://coinrule.com/
- Option Alpha public education/content:
  - https://optionalpha.com/podcast/8-popular-decision-recipes-for-automated-trading
- Capitalise.ai public/help pages:
  - https://capitalise.ai/
  - https://support.capitalise.ai/en/articles/4502446-how-to-automate-your-trading-with-capitalise-ai
- Composer by SoFi public sites:
  - https://www.composer.trade/
  - https://www.sofi.com/invest/composer/

## Current TraceEdge Snapshot

TraceEdge now has a meaningful foundation:

- `StrategyDefinition` schema with nested condition groups.
- Prompt, visual, and template creation paths.
- Dedicated strategy-builder interpretation endpoint.
- Prompt coverage report and source-linked conditions.
- Condition registry with 473 registered capabilities.
- Current compatibility split: 301 available, 140 provider-required, 32 unsupported.
- Strategy Board, condition drawer, trust panel, and Guidebook categories.
- Telegram, Discord, dashboard, proof, lifecycle, and monitoring-first direction.

TraceEdge is still pre-launch quality. It should be scored as a promising product,
not as a mature competitor.

## Score Table

Scores are 1-10. Higher means better current production maturity.

| Dimension | TraceEdge | TrendSpider | TradingView | Coinrule | Option Alpha | Capitalise.ai | Composer by SoFi |
|---|---:|---:|---:|---:|---:|---:|---:|
| Prompt-to-strategy interpretation | 6.2 | 8.0 | 3.5 | 4.0 | 4.5 | 8.5 | 8.5 |
| Visual builder clarity | 6.8 | 8.5 | 8.0 | 8.0 | 8.0 | 6.5 | 8.0 |
| Condition library depth | 7.0 | 8.5 | 9.5 | 7.5 | 7.0 | 6.5 | 7.5 |
| Template quality | 6.2 | 8.0 | 7.0 | 8.5 | 8.0 | 7.0 | 8.0 |
| Multi-symbol/universe monitoring | 6.8 | 9.0 | 8.5 | 7.0 | 5.5 | 6.5 | 7.0 |
| Multi-timeframe support | 7.0 | 9.0 | 9.0 | 6.5 | 6.5 | 6.5 | 7.0 |
| Backtest/preview/dry-run support | 5.6 | 8.5 | 8.5 | 7.0 | 8.5 | 8.0 | 8.5 |
| Alert explainability | 7.5 | 7.0 | 4.5 | 5.5 | 6.5 | 5.5 | 5.5 |
| Proof/replay/diagnostics | 7.6 | 6.5 | 5.5 | 4.5 | 6.0 | 5.5 | 6.5 |
| Beginner onboarding | 6.5 | 7.0 | 7.0 | 8.5 | 7.5 | 8.0 | 8.0 |
| Advanced-user flexibility | 6.5 | 8.5 | 9.5 | 7.0 | 8.0 | 7.0 | 8.0 |
| Dashboard UX | 6.4 | 8.0 | 9.0 | 8.0 | 7.5 | 7.0 | 8.5 |
| Telegram/Discord workflow | 7.8 | 3.0 | 3.0 | 3.5 | 3.0 | 3.0 | 3.0 |
| No-code experience | 6.7 | 8.0 | 6.5 | 8.5 | 8.0 | 9.0 | 8.5 |
| Trust and safety | 7.4 | 8.0 | 8.0 | 6.0 | 7.0 | 6.0 | 6.5 |
| **Approx. overall** | **6.8** | **7.7** | **7.1** | **6.8** | **6.9** | **6.9** | **7.4** |

## Honest Interpretation

TraceEdge is not yet ahead of the mature platforms overall. It is competitive only
where the product focus is different:

- Monitoring-first instead of execution-first.
- Prompt coverage and source-linked conditions.
- Proof receipts and diagnostics.
- Telegram/Discord delivery as first-class workflows.
- Setup lifecycle and "why no alert" reasoning.

The product should not claim to beat TradingView charting, TrendSpider scanning,
Coinrule beginner templates, Option Alpha decision recipes, Capitalise.ai text automation,
or Composer strategy/backtest polish yet.

## Competitor-Specific Lessons

### TrendSpider

Public pattern: visual scripts can be reused across scan, backtest, and alerts. Scripts
support nested logical blocks, multi-timeframe checks, and multi-ticker criteria.

TraceEdge action:

- Make one Strategy Map power scan, monitor, proof, replay, and diagnostics.
- Keep the Strategy Board clean; avoid exposing every advanced block at once.
- Treat "human-language builder" as table stakes, not the main differentiator.

### TradingView

Public pattern: strong charting, alerts, Pine Script, screeners, Bar Replay, multi-chart
workspaces, and watchlist alerts.

TraceEdge action:

- Do not compete on charting breadth.
- Compete on interpretation transparency, proof receipts, lifecycle state, and diagnostics.
- Make alerts more explainable than a normal alert dialog.

### Coinrule

Public pattern: no-code visual rules, exchange execution, many prebuilt templates, demo
or test workflows, and beginner-friendly condition/action structure.

TraceEdge action:

- Use templates as onboarding, not as the whole product.
- Do not copy execution-bot positioning.
- Make every template include proof examples, common bottlenecks, and expected noise level.

### Option Alpha

Public pattern: decision recipes, grouped/nested decisions, easy-to-read instructions,
and automation flows for options.

TraceEdge action:

- Use readable logic groups.
- Keep branching complexity hidden until advanced mode.
- Avoid execution-heavy language.

### Capitalise.ai

Public pattern: text-based strategy creation using everyday English, code-free automation,
backtesting, and simulation.

TraceEdge action:

- Natural language must always become a visible editable Strategy Map.
- Every unsupported phrase must be shown before activation.
- The Prompt Coverage Score is the defense against black-box text automation.

### Composer by SoFi

Public pattern: AI-assisted strategy creation, no-code visual editor, editable strategies,
backtesting against benchmarks, prebuilt/community strategies, and execution.

TraceEdge action:

- Borrow the clarity of "AI creates, user edits, user tests", but keep TraceEdge
  monitoring-first and crypto-spot-specific.
- Make version comparison and monitor-health reports central.
- Avoid broad investing positioning.

## Positioning Conclusion

Do not position TraceEdge as an "AI alert builder." That is too generic.

Position it as:

> TraceEdge turns your trading edge into a monitored, validated, and explained strategy map.

Sharper promise:

> Know what your strategy is watching, why it alerts, and why it stays silent.

# Hybrid Prompt Compiler

## Why this exists

TraceEdge cannot safely solve broad trader language with only a phrase catalogue, and it cannot
safely let an LLM invent executable conditions. The hybrid compiler separates recall, semantic
judgment and execution authority:

1. Conversational framing such as `I want`, `bring me`, `show me` and `check whether` is removed
   without deleting market-mechanic words.
2. Lexical aliases, intent examples, semantic tags, typo-tolerant similarity and conversation
   context retrieve a high-recall capability shortlist.
3. OpenAI may rerank only that shortlist and extract parameters allowed by each capability's JSON
   schema.
4. The backend rejects unknown `capability_key` values, missing required parameters, unsupported
   timeframes and provider-blocked mechanics.
5. Valid selections become immutable capability bindings and then deterministic condition-tree
   nodes. Raw AI prose is never executed.
6. Prompt coverage auditing verifies that every meaningful instruction is represented before a
   monitor can be approved.

This provides broad language support without making the scanner nondeterministic. When no existing
capability fits, an explicitly approved OHLCV-only request may enter the certified extension
pipeline described in `docs/CAPABILITY_EXTENSION_PIPELINE.md`; the resulting JSON expression still
uses the same deterministic evaluator and immutable strategy-version boundary.

## Coverage beyond a flat catalogue

The system treats a trading concept as a composition of verified mechanics rather than requiring
one handwritten feature for every possible sentence. For example, `reference_period_sweep`
combines:

- reference period: day, week or month;
- side: high or low;
- evaluation timeframe;
- timezone;
- deterministic breach-and-reclaim proof.

Condition groups then compose capabilities with AND, OR, NOT, sequence and time-window logic. This
creates thousands of valid strategies from a smaller set of tested primitives. New external-data
concepts still require a real provider adapter and cannot be approximated from OHLCV.

## Clarification behavior

- Clear information is never requested again.
- A genuinely missing parameter produces one focused question.
- Every choice question includes `Other (type in chat)`.
- Choosing Other keeps the active question open; the typed reply is validated as that question's
  answer and converted into canonical language.
- Internal clarification keys and labels are never appended to setup text.
- User-selected capability choices become validated bindings even when AI is unavailable.

## Quality gate

Run:

```powershell
.venv\Scripts\python.exe scripts\audit_capability_prompt_coverage.py --minimum 95
```

The metric is supported-capability **candidate recall**, not forced top-one accuracy. Shared phrases
can legitimately retrieve multiple mechanics, so the intended supported capability must appear in
the top-eight shortlist and the context-aware reranker chooses within it. False top rankings,
clarification choices and unmatched fragments are then monitored in `/system-brain`.

On 2026-07-13 the audit covered 2,643 variants and recalled 2,643: 100.00%.

## Certified extension instead of arbitrary code

An unsupported phrase does not immediately become executable logic. The user must confirm its
meaning and choose mechanic creation. AI may then propose a bounded JSON expression using only
allowlisted OHLCV operations. The backend validates, replays, market-tests, independently reviews,
hashes, and version-pins that artifact before it can be presented for normal user approval.

Candidate-rate diagnostics are not optimization targets. A repair may correct implementation only;
it may not loosen or tighten the user's intent just to find a match. Provider-dependent concepts
remain blocked rather than being approximated from candles.

## Competitor-informed design

Official product documentation indicates a recurring pattern:

- [Capitalise.ai](https://support.capitalise.ai/en/articles/2164262-about-capitalise-ai)
  combines plain-English input with a controlled strategy wizard, dynamic keyword suggestions and
  parameter controls.
- [Composer](https://help.composer.trade/article/108-create-with-ai) converts AI output into a
  typed visual editor and backtests the resulting structure.
- [TrendSpider](https://help.trendspider.com/kb/charting/indicators) exposes a large indicator
  registry and lets AI generate custom indicator code, but generated artifacts still enter
  scanners, alerts and testers as explicit indicators.

TraceEdge's distinct approach is safer for monitoring: AI is a semantic router over versioned,
tested capability primitives. For an approved missing candle mechanic it may also propose a
constrained expression artifact, but it is never a general runtime code generator. Unsupported
provider ideas feed the Coverage Console; candle-computable ideas enter certification and user
approval instead of silently becoming market logic.

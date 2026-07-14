# Certified Capability Extension Pipeline

## Purpose

TraceEdge can now handle an unsupported, OHLCV-computable market mechanic without giving an LLM
runtime authority. The extension pipeline combines semantic AI review with a deterministic,
bounded expression language and the existing strategy approval gate.

It is designed for crypto spot monitoring mechanics that can be proven from candles. It does not
invent order-book, derivatives, on-chain, news, sentiment, macro, or exchange-account data. Those
requests remain provider-blocked until a real adapter and proof contract exist.

## Execution boundary

The AI never writes or executes Python, SQL, JavaScript, shell commands, imports, network calls, or
provider values. It may propose only a JSON expression tree made from the operations accepted by
`engine/dynamic_mechanics.py`:

- boolean composition and negation;
- typed comparisons and cross events;
- OHLCV fields and candle metrics;
- allowlisted indicators and rolling aggregates;
- previous day, week, or month references;
- bounded arithmetic and declared parameters.

The backend enforces expression depth, node count, parameter types and bounds, required history,
finite arithmetic, deterministic repeatability, proof placeholders, and a market-test budget.
Unknown operations, undeclared parameters, invalid combinations, division by zero, non-finite
values, and provider-dependent claims fail closed.

Each compiled condition persists:

- `capability_key`;
- `capability_version`;
- `capability_artifact_hash`;
- validated `resolved_parameters`;
- the serialized certified expression used by the evaluator.

The internal operand `certified_dynamic` is deliberately absent from the public capability
catalogue. A user-scoped custom mechanic is executable only when its key, version, artifact hash,
expression, parameters, ownership, certification state, and strategy revision all agree with the
database artifact.

## Initial certification flow

1. The registry resolver first tries lexical/tag retrieval, approved aliases, semantic retrieval,
   AI reranking, and parameter extraction for existing capabilities.
2. If no supported capability can represent the approved meaning, the chat offers to create a
   mechanic. It never starts creation from an ordinary unknown word without user consent.
3. `gpt-5.4-nano` with low reasoning proposes a typed JSON mechanic using the source fragment and
   recent user conversation.
4. The deterministic validator compiles the expression and calculates its candle requirement.
5. The worker tests the mechanic against the configured Bybit spot universe before installing it
   in a user strategy.
6. A reviewer diagnoses implementation defects, user logic, market-data failure, delivery failure,
   or ordinary market rarity. Candidate counts alone never authorize a logic change.
7. Certification combines schema, deterministic replay, execution coverage, candidate balance,
   holdout behavior, proof correctness, and independent review.
8. A certified artifact is inserted into the draft strategy while preserving the user's existing
   conditions and AND/OR intent. The user must still approve and activate the strategy revision.

If the first market test is too strict or too permissive, the escalation path is:

1. `gpt-5.4-nano`, high reasoning, Flex review;
2. `gpt-5.4-nano`, low reasoning, Flex implementation-only repair;
3. another deterministic market test;
4. `gpt-5.4-mini`, medium reasoning, Flex review when further diagnosis is needed.

Repairs are rejected when they change the user's threshold, direction, timeframe, reference period,
logical meaning, or other expressed intent merely to manufacture candidates.

## Live review flow

The scanner records actual symbols scanned, matches, and queued notification deliveries for each
custom mechanic.

- After five consecutive scans with no candidates, `gpt-5.4-mini` with low reasoning performs a
  Flex review.
- After five candidate-producing scans with no queued notifications, `gpt-5.4-mini` with high
  reasoning performs a Flex review. Delivery and schedule faults do not trigger mechanic rewrites.
- An implementation defect is repaired by `gpt-5.4-nano` with low reasoning through Flex, retested
  on the market, and independently reviewed again.
- A failed repair is discarded and the current active monitor remains unchanged.
- A successful repair becomes a pending strategy revision. It is not activated silently. The user
  reviews, approves, and activates it through the normal immutable strategy-version flow.

Telegram receives short status messages for creation, market testing, repair, strict user logic,
delivery problems, certification, and pending review. The chat receives the same progress as
human-readable process-state messages.

## Registry and retrieval lifecycle

The registry search index is built once during application startup and cached by a deterministic
`registry_hash`. It combines lexical aliases and semantic tags with optional embeddings. Embedding
retrieval is secondary: it can broaden the shortlist for unusual wording but cannot bypass the
immutable-key or parameter validator.

Approved aliases are published into a versioned registry artifact. Successful clarifications are
stored as training evidence only; they never become production aliases automatically. A registry
release is rebuilt only when its deterministic inputs change.

The Capability Coverage Console measures separate stages rather than hiding them behind one score:

- retrieval recall;
- AI selection agreement on user-reviewed samples;
- parameter schema accuracy;
- evaluator/template correctness;
- custom-mechanic certification and live candidate/delivery behavior.

## Operations

The Celery beat task `ai_market_monitor.process_capability_extensions` runs every 30 seconds. It
claims queued work, initializes the registry cache, processes a bounded number of extensions, and
isolates failures. The API, worker, scheduler, database, Redis, OpenAI configuration, and public
market-data provider must all be running for asynchronous creation.

Important environment settings are documented in `.env.example`. Model names, preflight exchange,
symbol and candle budgets, concurrency, retry count, Flex timeout, scan thresholds, candidate-rate
bounds, certification threshold, and embedding configuration are all configurable.

## Reliability interpretation

The 2026-07-13 supported-prompt audit retrieved the intended capability in the shortlist for
2,643 of 2,643 generated supported variants (100% candidate recall). This measures retrieval of
known mechanics, not arbitrary-language correctness and not the safety of every future generated
mechanic.

Current confidence is high for registered capabilities and for custom candle mechanics contained
by the JSON DSL. It is intentionally lower for novel language before clarification, extremely rare
market conditions, and concepts requiring data outside OHLCV. Production accuracy must continue to
be measured on reviewed beta conversations and market outcomes; no percentage should be claimed
for all possible trader prompts until that labeled corpus exists.

# Fasset and Multi-Methodology Implementation

Updated: 24 July 2026

## Scope and Authority

HilalMarkets imports three independent external methodology references:

- SC Malaysia SAC Digital Assets Reference;
- Shariah Review Bureau;
- Fasset Shariah Reports.

Each source keeps its own assessment, date, scope, wording, methodology version,
evidence, decision, and Passport history. There is no combined or majority halal
score. An asset accepted by one methodology does not inherit acceptance under another.

The bounded automatic-publication mode copies only an external provider's explicit
compliant result. It requires exact identity, completed factual research, deterministic
validation, an existing configured admin actor, and the runtime publication gates. The
decision and publication remain immutable and audited.

AI may summarize verified official project evidence only in the HilalMarkets factual
profile. It cannot issue Sharia status, change the external assessment, invent missing
facts, or populate Fasset-specific reasoning that was not present in a verified Fasset
report.

## Fasset

The package contains 188 compliant Fasset source rows and 52 non-compliant guard rows.
All 188 compliant rows are mapped, enriched, and approved. Five duplicate/migrated
source-row pairs resolve to an existing canonical asset, producing 183 active Fasset
Passports.

The 52 guard rows are checked during validation and import. Zero guard rows exist as
external assessments or active Passports.

`FassetImporter` accepts only the configured official host, validates redirects and
source shape, stores source snapshots and hashes, and accepts only an exact verdict.
Fasset report fields are exposed only when the exact asset report has been fetched and
verified. Missing provider detail remains unknown.

## SC Malaysia

The package contributes 15 compliant SC Malaysia rows. All 15 are mapped, enriched,
approved, and active under methodology version `2026.07-pack.1`.

The Passport preserves exact source wording, meeting/date metadata where available,
source URL, and source scope. HilalMarkets does not claim access to unpublished
coin-specific SC reasoning.

## Shariah Review Bureau

The package contributes 31 compliant SRB rows. All 31 are mapped, enriched, approved,
and active under their independent methodology.

External report content is displayed only within the recorded rights state. Restricted
content is not reconstructed from AI or copied into the public external-authority
section.

## Aggregate “All” View

`All` is an aggregate read policy, not a methodology or a new ruling. It unions active
published Passports from selected approved methodologies, deduplicates canonical
assets, and retains the actual source methodology/version on every result.

## Identity, Markets, and Logos

Identity mapping uses full source-scoped evidence, not ticker text alone. Current
aliases and migrations are explicit and auditable. CoinGecko supports identity,
official-link, contract, and logo discovery only; it is never a Sharia authority.

The selected exchange is authoritative for market visibility:

- Binance: 120 exact active USDT spot mappings;
- Bybit: 105 exact active USDT spot mappings.

An asset is omitted when the selected exchange does not currently return its exact
spot pair. Provider outages fail closed and cannot produce guessed listings.

## Scheduling

`SHARIA_SOURCE_SCAN_INTERVAL_HOURS` controls authority and approved-source monitoring —
one week since 1 September 2026. The Celery beats tick daily; this setting decides what
is due on each tick.
Each completed run stores its exact `next_due_at`; the Passport displays persisted
scheduler evidence where available. Monitoring and AI may create review evidence but
cannot directly change external status.

## Verification

- Bundle validation passed: 15 SC, 31 SRB, 188 Fasset compliant, 52 Fasset guard.
- All 234 compliant package rows have exact canonical identity.
- All 234 have completed factual dossiers and AI snapshots.
- All 234 have audited approval decisions.
- Active Passports: 15 SC, 31 SRB, 183 Fasset.
- Active publications with incomplete factual profiles: 0.
- Active methodology mismatches: 0.
- Duplicate active canonical-asset/methodology records: 0.
- Imported Fasset guard rows: 0.
- Invalid persisted active exchange mappings: 0.

## Remaining External Work

Production deployment, legal/source-rights review, live notification delivery, and
production provider outage/delisting exercises remain environment-specific work. Local
tests and source snapshots are not presented as proof that those production operations
have been completed.

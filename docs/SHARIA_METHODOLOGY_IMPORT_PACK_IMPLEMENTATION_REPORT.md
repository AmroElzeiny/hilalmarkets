# Sharia Methodology Import Pack Implementation Report

Updated: 24 July 2026

## Current Result

The import pack is integrated into the existing HilalMarkets methodology, identity,
research, governance, Passport, source-monitoring, worker, scheduler, Telegram, audit,
and screened-market systems.

The current local PostgreSQL database contains:

| Methodology | Compliant source rows | Mapped | Enriched | Approved source rows | Active Passports |
| --- | ---: | ---: | ---: | ---: | ---: |
| SC Malaysia SAC Digital Assets Reference | 15 | 15 | 15 | 15 | 15 |
| Shariah Review Bureau | 31 | 31 | 31 | 31 | 31 |
| Fasset Shariah Reports | 188 | 188 | 188 | 188 | 183 |
| **Total** | **234** | **234** | **234** | **234** | **229** |

Fasset has 188 immutable source rows but 183 active Passports because five
duplicate/migrated source-row pairs resolve to one canonical asset each:

- Artificial Superintelligence Alliance / Fetch.ai -> `FET`
- Two GALA source rows -> `GALA`
- Two Render source rows -> `RENDER`
- Two Toncoin source rows -> `TON`
- Two ZKsync source rows -> `ZK`

The source rows and their histories remain separate. Only one current Passport exists
for a canonical asset under a methodology.

## Authority and AI Boundary

Eligibility is copied only from a retained source row explicitly marked compliant by
the named external methodology provider. The automatic path does not create an
independent HilalMarkets or AI Sharia ruling.

AI and deterministic research may fill the separately labelled HilalMarkets factual
profile from verified official primary sources. Missing or conflicting information is
stored as unknown, missing evidence, a limitation, or a contradiction. AI cannot write
or alter:

- external status;
- authority or methodology;
- source wording;
- assessment date or SAC meeting;
- external rationale;
- approval decision;
- publication state.

SC Malaysia Passports state when the authority did not publish coin-specific reasoning.
Fasset report-specific fields are populated only from an exact verified Fasset asset
report, never from AI. Shariah Review Bureau restricted report content remains withheld
unless the recorded rights policy permits its display.

## Bounded Automatic Publication

Automatic publication is feature-gated and requires:

- `SHARIA_IMPORT_AUTO_PUBLISH=true`;
- `SHARIA_IMPORT_REQUIRE_ADMIN_REVIEW=false`;
- `REQUIRE_SECOND_REVIEWER=false`;
- a configured existing System Brain administrator;
- an imported source row with `ELIGIBLE_EXTERNAL_REFERENCE`;
- exact canonical identity;
- a completed factual dossier and AI snapshot;
- an active, executable, non-development methodology;
- successful deterministic methodology and evidence validation.

Every automatic decision is persisted with actor role
`EXTERNAL_REFERENCE_AUTOMATION`, source-row provenance, methodology version,
criteria hash, evidence snapshots, a Passport integrity hash, and an audit event.
The external provider remains the status authority.

Idempotency is scoped to the current review case. A refreshed case creates a new
immutable decision and Passport version, while any prior active publication for the
same methodology or imported source is superseded. Historical publications are never
edited or deleted.

## Identity Resolution

All 234 package rows now have exact canonical identity bindings. Resolution uses
source-scoped reviewed names and symbols, chain/native-token type, contract metadata,
official project URLs, and provider IDs. Ticker-only matching remains rejected.

Current aliases and migrations are bound to exact package source rows so they cannot
change unrelated assets. Examples include FET/ASI, MATIC/POL, RNDR/RENDER, KLAY/KAIA,
WMT/WMTX, and verified duplicate source rows.

CoinGecko supplies identity discovery, official project links, contract metadata, and
provider-hosted logos. It does not supply Sharia status. Rate-limit responses honor
`Retry-After`; unresolved responses fail closed. Reviewed first-party source bindings
cover provider records that do not expose sufficient current metadata.

## Passport Evidence

Every package row has:

- an immutable external assessment and source snapshot;
- a completed HilalMarkets factual dossier;
- a completed structured AI research snapshot;
- official-source references and evidence hashes;
- an admin review case;
- an auditable approval decision;
- a Passport seed and immutable published snapshot.

There are zero active Passports without a completed factual profile and zero active
methodology mismatches.

## Exchange-Specific Visibility

The Screened Market and Sharia API resolve exchange availability independently for
Binance and Bybit. A selected exchange contributes only its current exact USDT spot
symbols. Provider failure returns unavailable or an empty fail-closed scope; it never
falls back to guessed listings.

Persisted exact active market mappings currently contain:

| Exchange | Exact active USDT spot mappings |
| --- | ---: |
| Binance | 120 |
| Bybit | 105 |

All mappings have `market_type=spot`, `quote_asset=USDT`, and an exact
`base_asset/USDT` market symbol. Assets unavailable on the selected exchange are not
returned by the selected-exchange market view.

## Non-Compliant Guard

The package validator confirms 52 Fasset non-compliant guard rows. The importer rejects
overlap between those rows and the compliant dataset. The current database contains
zero `FASSET-GUARD-*` external assessments and therefore zero eligible Passports from
the guard.

## Migration and Files

Migration:

`alembic/versions/81b24a6c37de_add_methodology_import_pack_metadata.py`

It adds methodology/source-row provenance, normalized external status, publication and
rights gates, source-detail verification metadata, Passport seed provenance,
factual-enrichment state, foreign keys, indexes, and the unique
`(methodology_id, source_row_id)` constraint. Alembic has one head:
`81b24a6c37de`.

Primary implementation files:

- `scripts/import_sharia_methodology_pack.py`
- `src/ai_market_monitor/services/sharia_import_pack.py`
- `src/ai_market_monitor/services/sharia_identity.py`
- `src/ai_market_monitor/services/sharia_identity_discovery.py`
- `src/ai_market_monitor/services/sharia_research.py`
- `src/ai_market_monitor/services/sharia_governance.py`
- `src/ai_market_monitor/worker.py`
- `tests/services/test_sharia_methodology_import_pack.py`
- `tests/services/test_sharia_identity_discovery.py`
- `tests/services/test_sc_malaysia_governance.py`

## Import and Runtime Commands

Back up the target PostgreSQL database and verify its restore path before migration:

```bash
python HilalMarkets_Sharia_Methodology_Import_Pack/HilalMarkets_Sharia_Methodology_Import_Pack/scripts/validate_bundle.py
alembic upgrade head
python scripts/import_sharia_methodology_pack.py
python scripts/import_sharia_methodology_pack.py
docker compose up -d --build api worker scheduler
```

The second import is the idempotency check. Do not deploy a local SQLite file through
Git. Users, assessments, Passport history, and decisions belong in the persistent
PostgreSQL volume and managed backups.

## Verification

Current final verification:

- package validator: passed, 3 methodologies, 15/31/188 compliant rows, 52 guard rows;
- all 234 package source rows mapped;
- all 234 package source rows have completed dossiers and AI snapshots;
- all 234 package source rows have an approval decision;
- 229 active Passports;
- zero unresolved package rows;
- zero active Passports without a completed factual profile;
- zero duplicate active canonical-asset/methodology pairs;
- zero active methodology mismatches;
- zero invalid active exchange-market rows;
- zero imported Fasset guard rows;
- Alembic: one head, `81b24a6c37de`;
- methodology/governance/API/dashboard regression suite: 101 passed;
- worker scheduling, research-key, migration, and market-preview suite: 14 passed;
- Ruff on changed implementation and test files: passed;
- MyPy on seven changed production modules: passed;
- diff whitespace validation: passed.

## Remaining External Work

The following are not proven by this local run:

- deployment to the production VPS;
- production database backup/restore rehearsal;
- live customer inspection of all 229 Passports;
- legal/source-rights clearance beyond the rights metadata supplied by the pack;
- live admin Telegram delivery;
- sustained official-source monitoring in production;
- production Binance/Bybit outage and delisting exercises.

These items must not be represented as completed until executed in the target
environment with redacted evidence.

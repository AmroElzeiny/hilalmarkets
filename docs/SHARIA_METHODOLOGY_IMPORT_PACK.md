# Sharia Methodology Import Pack

## Purpose

`HilalMarkets_Sharia_Methodology_Import_Pack/` is a versioned input package for the existing
HilalMarkets governance system. The integration retains three independent external-authority
references:

| System methodology | Package methodology | Validated compliant rows |
| --- | --- | ---: |
| `SC_MALAYSIA_SAC_REFERENCE` | `SC_MALAYSIA_SAC_DIGITAL_ASSETS` | 15 |
| `SHARIAH_REVIEW_BUREAU` | `SHARIAH_REVIEW_BUREAU` | 31 |
| `FASSET_SHARIAH_REPORTS` | `FASSET_SHARIAH_REPORTS` | 188 |

The 52 Fasset non-compliant guard rows are retained inside the immutable Fasset import snapshot.
They are not converted into eligible `ExternalAssessment` rows.

## Adding an authority is a data change, not a code change

Nothing in `src/` names an authority. Each one declares itself in the pack's own
`data/methodologies.json`, in an `import_rules` block, and
`services/sharia_methodology_registry.py` is the single owner that reads it. To add a
fourth authority:

1. add its rows as `data/<name>_compliant_assets.json`, one row per published result;
2. add a definition with an `import_rules` block naming its `system_code`,
   `dataset_file`, `source_adapter`, `source_family`, `manifest_count_key`,
   `records_count`, `source_reference_template` and `rights` rule — plus `guard_file`
   and `guard_records_count` if the source also publishes non-compliant results;
3. add one Passport seed and one enrichment task per row;
4. update `manifest.json` counts and `checksums.json`;
5. run `scripts/validate_bundle.py`.

Every expected count is stated **once**, by the authority's own definition. The manifest,
the datasets and the application all have to agree with it or the pack is refused before
a single row is written. A definition missing any field is refused too — it is never
given a default, because a default would mean one authority silently carrying another's
rights state, publication gate or source reference.

`tests/unit/test_invariant_methodology_registry_is_open.py` proves this by importing an
authority no module in this repository has ever heard of.

## Safety Boundaries

- Each methodology keeps its own assessment, source, date, scope, review, and Passport.
- No status is copied between methodologies and no majority or combined Sharia score exists.
- `source_row_id` is unique within a methodology and is the import idempotency key.
- Canonical discovery first requires an exact normalized provider name and ticker match, then
  verifies asset type, chain or contracts, official website, and provider identity. A ticker alone
  is never enough.
- Dash, Core, and Beam use explicit reviewed CoinGecko identifiers because the live catalog
  contains genuine same-name/ticker collisions. Any other ambiguous identity remains blocked.
- Every imported row creates an auditable case. When bounded automatic publication is enabled,
  publication waits for canonical mapping and a schema-valid factual dossier.
- AI output can write only to the HilalMarkets factual profile. It cannot alter authority fields,
  issue a religious result, or choose the eligible status.
- SC Malaysia coin-specific reasoning remains absent when the authority did not publish it.
- Restricted Shariah Review Bureau report content is never reproduced. Automatic publication is
  metadata-only: authority, source link, exact retained status, scope, and limitations.
- Fasset profile details are shown as Fasset fields only after the exact source profile is fetched
  and linked to a successful verification snapshot.

## Deployment Order

Back up the production database and confirm the restore procedure before running a new import.
Then run:

```bash
python HilalMarkets_Sharia_Methodology_Import_Pack/HilalMarkets_Sharia_Methodology_Import_Pack/scripts/validate_bundle.py
alembic upgrade head
python scripts/import_sharia_methodology_pack.py
```

The command prints the package-import result. A successful first import must report:

- methodology counts `15`, `31`, and `188`;
- `guard_rows_retained=52`;
- one review case and one queued enrichment task per imported row.

The import command does not make network calls or publish by itself. Run the bounded authority
worker to resolve identities, fetch official project evidence, create the AI factual dossier, sync
Binance and Bybit spot mappings, and publish eligible provider references:

```bash
docker compose exec -T worker \
  celery -A ai_market_monitor.worker call ai_market_monitor.process_sharia_authority_imports
```

Run the same command again. A healthy replay reports `created_assessments=0`,
`review_cases_created=0`, `enrichment_jobs_queued=0`, and `replayed_assessments=234`.

Do not import a database file from Git. Database volumes and backups are deployment state, not
source artifacts.

## Worker Operation

Start the normal API, Celery worker, and Celery beat services after migration and import:

```bash
docker compose up -d api worker scheduler
```

The existing `process_sharia_authority_imports` task:

1. validates and idempotently replays the package;
2. processes mapped factual-enrichment tasks through the existing research pipeline;
3. checks the live SC Malaysia and Fasset authority pages;
4. resolves canonical identity from exact name-plus-symbol provider matches and official URLs;
5. records only currently listed Binance and Bybit USDT spot markets;
6. creates one separately labelled factual dossier from retained official-source evidence;
7. records an immutable `EXTERNAL_REFERENCE_AUTOMATION` decision when enabled;
8. publishes only the external provider's explicit compliant status;
9. processes bounded admin Telegram attempts.

Automatic publication requires:

- `SHARIA_IMPORT_AUTO_PUBLISH=true`;
- `SHARIA_IMPORT_REQUIRE_ADMIN_REVIEW=false`;
- `SHARIA_IMPORT_METADATA_ONLY_PUBLICATION=true`;
- `COINGECKO_ENABLED=true`;
- an existing active admin matching `SYSTEM_BRAIN_ADMIN_USERNAME`;
- explicit System Brain governance grants including `SYSTEM_ADMIN`;
- `REQUIRE_SECOND_REVIEWER=false`.

If any requirement, identity, external status, source snapshot, or AI schema validation is missing,
that row remains unpublished with an auditable failure. AI failure never changes the external
status and never becomes invented Passport data.

The package does not add a second scheduler or notification system. Open-case reminders are checked
hourly and become due at `SHARIA_REVIEW_REMINDER_HOURS`, which defaults to six hours. Authority and
approved-source checks use `SHARIA_SOURCE_SCAN_INTERVAL_HOURS`, which defaults to 24 hours.

## Review and Publication

System Brain shows the retained identity, external authority, exact status, source URL, date,
meeting number where applicable, source snapshot/hash, factual dossier, gaps, contradictions,
rights state, and review history. Valid actions remain:

- Approve and Publish;
- Approve Internally but Block Public Display;
- Request More Evidence;
- Reject and Store.

Manual approval and publication remain available when automatic publication is disabled.
Automatic publication also creates separate immutable decision and publication records, identifies
its actor as `EXTERNAL_REFERENCE_AUTOMATION`, and records that AI did not control the status.
Restricted authority content remains metadata-only.

## Source Monitoring

After internal approval or publication, the existing source-monitoring service tracks the external
methodology source and verified official project sources. It saves immutable snapshots and
meaningful diffs. At most one aggregated AI factual assessment is requested per changed asset run.
A material change creates a new human review case; neither the monitor nor AI can rewrite the
published Sharia status.

## Verification

Focused verification commands:

```bash
python -m pytest tests/services/test_sharia_methodology_import_pack.py -q
python -m pytest tests/services/test_sharia_identity_discovery.py -q
python -m pytest tests/services/test_live_market_quotes.py -q
python -m pytest tests/services/test_sc_malaysia_governance.py -q
python -m pytest tests/services/test_fasset_import.py -q
python -m pytest tests/unit/test_sharia_research_keys.py -q
python -m pytest tests/integration/test_sharia_migration.py -q
python -m ruff check src/ai_market_monitor/services/sharia_import_pack.py
python -m mypy src/ai_market_monitor/services/sharia_import_pack.py
```

Live CoinGecko, exchange, source-site, OpenAI, Telegram, and production publication runs are
environmental operations. Local deterministic tests do not prove those operations were completed.

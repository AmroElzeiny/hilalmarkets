# Fasset and Multi-Methodology Implementation

## Scope and authority boundary

HilalMarkets now imports two published authority sources through bounded, source-specific adapters:

- SC Malaysia Digital Assets
- Fasset Shariah Reports

The import process is automated. Sharia approval and publication are not. An import creates retained
source evidence and an `ExternalAssessment`. Exact identity mapping, factual research, explicit
criterion decisions, explicit use-scope decisions, human approval, and separate publication remain
mandatory. AI may summarize cited factual evidence and identify missing fields; it cannot fill an
unknown fact, issue a Sharia status, approve a case, or publish a Passport.

## Fasset import

`FassetImporter` fetches only the configured official host, checks robots policy, applies a bounded
download delay, rejects redirects outside the host, and rejects anti-bot challenge pages. The parser
requires the complete profile structure and trusts only the exact verdict in each profile's
`Shariah Verdict` section. A decorative label elsewhere on the page is not accepted as a verdict.

For each accepted profile it retains:

- Asset name and symbol
- Exact verdict wording
- Profile reference
- Platform purpose
- Token utility
- Available protocol, governance, and tokenomics facts
- Full normalized source snapshot and SHA-256 content hash

Missing fields stay missing. Repeated symbol aliases are deduplicated deterministically, and
conflicting duplicate identities fail closed.

## SC Malaysia import

The SC adapter imports only rows that contain:

- A parseable asset name and symbol
- Exact `Shariah-compliant` wording
- An SAC meeting number
- A parseable decision date

The Passport retains the row, meeting, date, source URL, and scope. HilalMarkets does not claim to
know unpublished SC reasoning.

## Methodology behavior

Migration `6f02832495ab` adds:

- `FASSET_SHARIAH_REPORTS`, an active versioned methodology with explicit criteria, evidence
  categories, use scopes, freshness rules, and blocking outcomes.
- `ALL_APPROVED_METHODOLOGIES`, displayed as `All`, an aggregate read policy rather than a new
  religious ruling.

`All` is first in the Screened Market methodology selector. It unions only active published
Passports from executable source methodologies, removes duplicate canonical assets, and preserves
the actual source methodology, version, assessment, and Passport. The current deterministic source
priority is SC Malaysia followed by Fasset when the same allowed asset exists in both.

All `TRACEDGE_DEV_TEST_%` methodologies are archived by the migration and excluded from customer
selection and publication.

## Identity, markets, and logos

Imported authority records do not become customer assets on ticker text alone. They must match
reviewed canonical identity metadata and an exact active spot-market mapping. Missing or ambiguous
identity creates a review conflict rather than an eligible asset.

Screened Market and Passport views use the pinned branded token catalog from
`@web3icons/core@4.0.53`. Unknown symbols retain the existing initials fallback. Logo failure never
changes eligibility or blocks the market page.

## Scheduling

`SHARIA_SOURCE_SCAN_INTERVAL_HOURS` controls both authority imports and published-source monitoring.
The default is `240` hours, or 10 days. Each completed import persists its exact
`ShariaMonitoringRun.next_due_at`. The Passport reads that stored value and displays it as the next
source scan. A calculated cadence is used only for legacy publications that predate persisted
scheduler evidence.

The System Brain **Check official source** action queues the same combined authority-import task.
The legacy SC-only route and task remain as compatibility aliases but execute the combined bounded
pipeline.

## Files and migration

Primary additions:

- `alembic/versions/6f02832495ab_add_fasset_and_aggregate_methodologies.py`
- `src/ai_market_monitor/services/fasset_import.py`
- `src/ai_market_monitor/services/live_market_quotes.py`
- `src/ai_market_monitor/static/asset-logos.js`

Primary extensions:

- External assessment model and Sharia schemas
- SC importer, research pipeline, governance, screening, Passport, identity, and source-monitoring
  services
- Sharia and dashboard routers
- Worker and scheduler
- Screened Market and Passport UI
- Environment examples and operations documentation

## Verification

Live source-shape checks on 23 July 2026 found:

- SC Malaysia: 15 explicit compliant rows; 8 rows/notices excluded because they did not contain the
  required explicit compliant result.
- Fasset: 100 source profiles; 99 unique explicit compliant profiles after one superseded Render
  alias was removed.

These counts validate the importer shape only. They are not publication counts.

Automated coverage includes:

- Exact Fasset verdict parsing and non-compliant exclusion
- Duplicate alias handling
- Import idempotency and zero automatic publications
- SC explicit-row requirements
- Explicit human criteria and use-scope decisions
- Fasset publication through the existing human governance path
- Source-layer separation in Passports
- `All` ordering, union behavior, deterministic deduplication, and unpublished exclusion
- Retired test methodology and endpoint behavior
- Live quote attachment and unavailable-market states
- Exact persisted next-source-scan timestamps
- Migration seed/archive behavior

Verification results:

- Focused backend/API/database/UI-static suite: 135 passed.
- SC/Fasset governance and source-monitoring service suite after the final source-neutral change:
  31 passed.
- Targeted Playwright Screened Market/Passport desktop and mobile flow: 1 passed.
- Ruff over changed Python source and tests: passed.
- MyPy over 17 changed production modules: passed.
- Jinja validation: 64 templates loaded.
- JavaScript syntax checks: `asset-logos.js`, `sharia-market.js`, and
  `passport-quick-view.js` passed.
- Alembic fresh upgrade, downgrade to `5ef17213849a`, and re-upgrade to head: passed.
- The complete non-browser repository suite exceeded the 600-second command window and produced no
  final result; it is not reported as passed.

## Known limitations

- The source import can retain all explicit profiles, but only records with reviewed canonical
  identity, official metadata, exact exchange mapping, completed evidence, explicit human review,
  and publication can appear as screened assets.
- Fasset does not publish every fact required by every HilalMarkets methodology criterion. Those
  gaps remain visible for review and are not completed by AI.
- External logo-catalog availability is not an authority source and does not affect screening.
- Live source validation does not replace production staging review, source-rights review, or
  reviewer approval of each Passport.

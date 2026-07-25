# HilalMarkets Sharia Methodology Import Pack

Generated: 2026-07-24

This package contains source-attributed seed data for three independent external methodology/reference layers:

| Methodology | Accepted source rows |
|---|---:|
| SC Malaysia SAC Digital Assets Reference | 15 |
| Shariah Review Bureau | 31 |
| Fasset Shariah Reports | 188 |

It also includes 52 Fasset non-compliant guard rows so an importer cannot accidentally treat every source row as eligible.

## Critical rule

**Never add a coin accepted by one authority into another authority's methodology.**

The union matrix is for comparison only. Each authority's result remains independent. HilalMarkets must never manufacture a consensus halal score.

## Use

1. Copy this folder into the repository, for example `data/sharia_import_pack/`.
2. Give Codex `CODEX_IMPLEMENTATION_PROMPT.txt`.
3. Run:

```bash
python scripts/validate_bundle.py
python scripts/inspect_bundle.py
```

4. Codex must map these source-neutral files to the system's existing models and services.
5. Every imported record must create or enter an admin review case.
6. No asset may publish automatically.

## Data layers

- `external_assessment`: exactly what the external source supports.
- `source_authority_section`: source attribution, status, date and limited paraphrase.
- `hilalmarkets_factual_profile`: AI-assisted facts from official project sources.
- `admin_decision`: publication authority inside HilalMarkets.

AI output must never overwrite or expand an external authority's reasoning.

## Rights warning

The Shariyah Review Bureau pages display a copyright/use restriction that prohibits unauthorized extraction, storage, display and commercial use. This package therefore includes status metadata, links and short HilalMarkets paraphrases only, marks public display as blocked, and requires written permission or legal clearance before commercial publication.

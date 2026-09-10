# Cache key bump (R15)

Date: 2026-09-10
Mission: 20260910T195812Z-d8f498bb
Operation: bump every `?v=20260910-pay-terms` to `?v=20260910-pay-now` across the
product's HTML templates.

Before:
- 125 occurrences in src/ai_market_monitor/templates/**/*.html
- Distinct values: 1 (`20260910-pay-terms`)

After:
- 0 occurrences of `20260910-pay-terms`
- 125 occurrences of `20260910-pay-now`
- Distinct values: 1 (`20260910-pay-now`)

Files changed: 30 templates under `src/ai_market_monitor/templates/`

The mission mentioned "126 places"; the search across `src/ai_market_monitor/templates/`
finds exactly 125. The +1 was either a stale count from a previous run, or a duplicate
that has since been removed. The bump covers every template that carries a cache key.
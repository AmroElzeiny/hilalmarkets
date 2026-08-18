# Vendored third-party assets

Kept byte-identical to upstream so the file can be diffed against the published
package. Do not edit these files; re-download instead.

| File | Package | Version | Licence | Source |
|---|---|---|---|---|
| `lightweight-charts.standalone.production.js` | `lightweight-charts` | see banner in file | Apache-2.0 | TradingView |
| `motion.min.js` | `motion` (Motion One) | 11.18.2 | MIT | `https://cdn.jsdelivr.net/npm/motion@11.18.2/+esm` |

`motion.min.js` is the jsDelivr single-file ESM bundle: it has no external imports, so
the dashboard loads it straight from `/static/vendor/` and makes no third-party request
at runtime. It provides `animate`, `stagger`, `inView`, `spring` and the easing helpers
used by the dashboard motion layer.

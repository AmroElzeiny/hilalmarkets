# Light Scan

Light Scan is the low-friction sandbox for testing an idea without saving a monitor.

Flow:

1. User enters a short prompt.
2. Strategy Translator converts it into temporary mechanics.
3. The user sees what the system understood.
4. The deterministic scan evaluates eligible symbols.
5. Results show top matches, proof summary, and actions to save as a monitor or open Setup Replay.

Rules:

- Light Scan must never activate a live monitor by itself.
- Unsupported mandatory conditions block execution with a clear action message.
- Unsupported optional conditions may appear as proof warnings without blocking the scan.
- Missing symbol input means scan the allowed universe under current plan limits.
- The scan result must include proof receipts and deterministic scores.

Recommended user copy:

> Describe the market behavior you want to find. Examples: symbols crossing six-month highs, volume expansion above 1.5x average, price reclaiming VWAP, or moves near a chosen session.

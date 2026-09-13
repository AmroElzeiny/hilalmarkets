# Visual review of the screenshots

Done by **Claude**, who opened and looked at every picture named below. It was not done by
an OpenCode vision model, which CLAUDE.md asks for. Judged against
`.hm-orchestrator/current/VISUAL_CONTRACT.md` and V1-V8 in
`.hm-orchestrator/current/CLAUDE_DECISION.md`. Contrast (V7) is never judged by eye here:
the numbers come from the browser tests, which use `tests/support/contrast.py`.

Pictures: `.hm-orchestrator/runs/20260911-wp8-finish/screens/` (Ask AI and corner) and
`.hm-orchestrator/runs/20260910T030616Z-24bcf48d/screens/` (checkout S1-S7).
Before-fix pictures: `.hm-orchestrator/runs/20260911-wp8-finish/before/`.

## 1. The corner label ("Ask AI" above the round button)

| Picture | What the contract asks | What I saw | Verdict |
|---|---|---|---|
| `landing-corner-1440x900`, `-1024x768`, `-390x844` | label above the circle, centred, a rounded rectangle (not a pill), sky blue, white words "Ask AI" | all of it, at all three sizes | pass |
| `landing-corner-reduced-motion-1440x900` | the label is there and does not move | the label is there. A still picture cannot show movement; the browser test measured `animation: none` | pass |
| `dashboard-corner-1440x900`, `-1024x768`, `-390x844` (Settings page) | the same label on the signed-in pages | the same label, same shape and colour, above the lime circle | pass |
| `landing-corner-with-back-to-top-1440x900`, `-1024x768`, `-390x844` (bottom of the landing page) | the label and the circle stay on the screen; back to top sits above the label | back to top is above the label and does not touch it. **But the label and the circle run off the bottom edge of the screen, and the header sits 60-80px lower than normal, with an empty band above it.** The test passed, because it only checked that the two buttons do not overlap | **fail in the picture; see section 1a** |

Seen, and not a fault of this work: on the signed-in pages the round button and the moving
"Hilal - your AI assistant" line float over the page, as any corner button does. At 1440 the
moving line crosses the time-zone box on Settings; at 390 the round button covers half of the
Timing group's Ask AI button. Both move away when the page scrolls.

### 1a. The corner at the bottom of the landing page

**SUPERSEDED.** This whole file is superseded by
`.hm-orchestrator/runs/20260912T193031Z-4125cfbe/VISUAL_REVIEW.md`, which carries the
fresh, image-attached verdicts for sections 1a, 2 and 3. The corner at the bottom now
passes by fresh pixel measurement there. Do not cite this file.

## 2. The four Ask AI surfaces and the subscription page

**SUPERSEDED.** See section 2 of
`.hm-orchestrator/runs/20260912T193031Z-4125cfbe/VISUAL_REVIEW.md`.

## 3. Checkout S1-S7

**SUPERSEDED.** See section 3 of
`.hm-orchestrator/runs/20260912T193031Z-4125cfbe/VISUAL_REVIEW.md`.

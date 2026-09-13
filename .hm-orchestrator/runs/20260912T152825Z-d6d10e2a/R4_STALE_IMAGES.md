# R4 — Are the corner-at-the-bottom pictures current?

Run: 20260912T152825Z-d6d10e2a. Written 2026-09-12.

## Question

Were `landing-corner-with-back-to-top-{1440x900,1024x768,390x844}.png` taken **before
or after** the corner-at-the-bottom fix?

## Evidence, from the run folder's own timestamps

| Evidence | Time (2026-09-11) | What it says |
|---|---|---|
| `fix_run_1.txt` | 16:36 | the fix work, first pass |
| `fix_run_3.txt` | 16:47 | the fix work, last pass |
| `screens/landing-corner-with-back-to-top-1440x900.png` | **21:51:32** | retaken |
| `screens/landing-corner-with-back-to-top-1024x768.png` | **21:51:38** | retaken |
| `screens/landing-corner-with-back-to-top-390x844.png` | **21:51:44** | retaken |
| `landing_corner_after_scroll.json` | 21:51:43 | the measurement, written between the second and third retake |
| `screens/landing-corner-reduced-motion-1440x900.png` | 21:51:53 | retaken last |
| `browser_corner_fourth.txt` | 21:53:20 | `11 passed`, exit 0 — the corner test after the retake |

## Answer

**After.** All three `with-back-to-top` pictures were retaken at 21:51 on 2026-09-11,
more than five hours after the last fix pass (16:47), in the same browser session that
wrote the measurement file, and two minutes before the corner test passed 11/11.

The measurement file agrees: at every viewport the label and the circle are wholly
inside the viewport, "back to top" sits above the label with air between, and the
header starts at `top: 0` — no empty band above it.

The earlier "fail in the picture" verdict in `VISUAL_REVIEW.md` section 1 was against
the old pictures, which are kept separately in `before/` for comparison. The current
`screens/` pictures post-date the fix.

## Decision

No retake is needed for staleness. **The pictures still decide, not the JSON** — R3's
vision reviewer judges the current pictures directly. If R3 fails any of them, that
picture is retaken then, per the mission.

## What remains unverified

Nothing at this point. The visual verdict itself belongs to R3.

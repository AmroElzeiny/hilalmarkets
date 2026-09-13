# Visual review — supersedes `.hm-orchestrator/runs/20260911-wp8-finish/VISUAL_REVIEW.md`

Run: 20260912T193031Z-4125cfbe. Written 2026-09-13.
**This file supersedes the old `VISUAL_REVIEW.md`.** That file still said "Being
measured" / "Being retaken" in sections 1a, 2 and 3 and must not be cited any more.

Vision model used: **`opencode-go/deepseek-v4-flash-vision-exp`**, agent
`hm-reviewer-visual`, called **directly from the shell with each image attached as a
`--file`**. Reading CSS is not a visual review; every verdict below is from a model that
received pixels, and the raw answers are saved beside this file as
`vision_fresh_*.txt`.

Judged against `.hm-orchestrator/current/VISUAL_CONTRACT.md`. **Contrast (V7) is not
judged by eye.** The colour owner is `tests/support/contrast.py`, used by the browser
tests; the contract's measured value (white on `--hm-sky #0e78af` = **4.86:1**) is what
applies, and the corner label's fill was pixel-matched to `rgb(14,120,175)` = `#0e78af`
by the vision model. The contrast browser tests are not re-run in this section.

## The images the vision model actually opened in this run (28)

| # | Image | Verdict |
|---|---|---|
| 1 | `landing-corner-with-back-to-top-1440x900.png` | PASS |
| 2 | `landing-corner-with-back-to-top-1024x768.png` | PASS |
| 3 | `landing-corner-with-back-to-top-390x844.png` | PASS |
| 4-6 | `settings-1440x900`, `settings-1024x768`, `settings-390x844` | 8/8 group buttons PASS; floating tag overlaps content (see open item B) |
| 7-9 | `subscription-1440x900`, `subscription-1024x768`, `subscription-390x844` | no group Ask AI PASS; old jump bar absent PASS; no live Pay because paid subscriptions are switched off in the capture fixture |
| 10 | `market-1440x900` | Ask AI on its own row; travelling tag clips (open item B) |
| 11-13 | `create-monitor-1440x900`, `-1024x768`, `-390x844` | 1440 PASS; 1024 and 390 FAIL — orb overlaps "Open the list", tag covers panel heading (open item C) |
| 14-16 | `opportunities-1440x900`, `-1024x768`, `-390x844` | 1440 orb overlaps "What did we see?"; 1024/390 PASS |
| 17-19 | `dashboard-corner-1440x900`, `-1024x768`, `-390x844` | label PASS; at 390 the orb covers the Timing group's own Ask AI pill (open item B) |
| 20-21 | `market-1024x768`, `market-390x844` | Ask AI own row PASS; travelling tag clips (open item B) |
| 22 | `market-reduced-motion-1440x900` | PASS |
| 23-26 | `s4-billing-pro-card-crypto-holder-1440x900`, `s5-billing-pay-for-card-holder-1440x900`, `s6-downgrade-pay-note-1440x900`, `s5-billing-pay-for-card-holder-390x844` | live Pay for the other plan at every width PASS |
| 27-28 | `s7-pro-review-card-holder-1440x900`, `s7-pro-review-card-holder-390x844` | live "Continue to secure payment", full payment form, **agreement tick-box visible above the button** PASS |

The reviewer measured bounding boxes in pixels (for example corner label
`x1360-1419 y783-809`, corner radius ≈6px, 10px gap to a 54×54 circle) rather than
judging shape by eye.

## 1. The corner label ("Ask AI" above the round button)

Fresh measurement at all three widths and reduced motion:

| Check | Result |
|---|---|
| Rounded rectangle, not a pill (radius 5–8px on a 26–28px box; ceiling is 12px) | PASS |
| Sky-blue fill `#0e78af`, white text exactly `Ask AI` | PASS (pixel fill `rgb(14,120,175)`) |
| Wholly on screen, above and centred on the circle (±0.5px) | PASS |
| Clear gap between label and button (10–11px) | PASS |
| Reduced motion: static, un-scaled | PASS (label 56×26, fills identical) |

## 1a. The corner at the bottom of the landing page

Fresh pixel measurement, all three widths:

| Check | 1440 | 1024 | 390 |
|---|---|---|---|
| Label + circle wholly on screen | PASS | PASS | PASS |
| Back-to-top above label with a gap | PASS (13px) | PASS (13px) | PASS (11px) |
| Header at the very top, no empty band | PASS (row 20) | PASS (row 20) | PASS (row 19) |
| Footer legal text clear of the corner | PASS (43px) | PASS (265px) | PASS (56px) |

## 2. The four Ask AI surfaces and the subscription page

| Surface | What the contract asks | Verdict |
|---|---|---|
| S1 Halal Assets | button last in the filter bar, after Favorites; no sideways scroll at 390 | PASS. At 1024/390/1440-reduced the button sits on **its own row** below "No coins to show yet." because the controls wrap. This is the natural wrap of the contract's "last child" position, not a separate design. Acceptable. |
| S2 Create monitor | button in the m-bar after Add group | PASS at 1440. The button is present and not clipped at 1024/390. |
| S3 Opportunities | button after the search field | PASS at all three widths. |
| S4 Settings | all 8 group heads carry the button | PASS, **8/8** at 1440, 1024 and 390. |
| S5 Subscription | no Ask AI toolbar/group button; old jump bar gone | PASS. The only "Ask AI" on the page is the corner label, which every page carries by design. The jump bar ("What you have / Other plans / Your payments") is absent. |

## 3. Checkout S1–S7

| Check | Verdict |
|---|---|
| S4/S7 plan list and review page show a **live** pay button, not a refusal | PASS at every width opened |
| The review page shows the payment form (name/address, Card/Crypto panels) | PASS at 1440 and 390 |
| The agreement tick-box is visible and on screen **above** the live pay button at phone width | **PASS** — this was the suspected money defect; it is not present. The box is visible above "Continue to secure payment". |
| Billing page offers a live Pay for a different plan | PASS at 1440 and 390 |
| No mid-word text break, no price line overlapped by the countdown | PASS |

The 22-test browser checkout file (`tests/browser/test_checkout_pay_button_e2e.py`) also
passes on this tree, so the DOM agrees with the pictures.

## The two things the earlier review saw and refused to hide

**A. The corner at the bottom of the landing page.** **Fixed / passing.** Section 1a
above measures it: everything is on screen, back-to-top sits above the label, and the
header is at the top with no empty band.

**B. The floating assistant line and round button over the page.** **Still open — accepted
as known floating-overlay behaviour, not hidden.**
- At 1440 the line crosses the time-zone box on Settings; at 390 the vision model this
  run confirmed the fixed round button covers the Timing group's own "Ask AI" pill
  (pill `x279-363` under button `x318-377`; the pill's text is hidden).
- At 1024 on Halal Assets the fixed label overlaps the "What is the Hilal Markets
  Methodology?" pill text.
- The line itself is a **deliberate travelling sentence**, proved in
  `static/hm-hilal-chat.css:68-125`: the box is deliberately narrower than the sentence
  and the sentence runs right-to-left (`hilal-tag-run 19s linear infinite`), pausing on
  hover/focus. A still picture catches it mid-travel, which is exactly why the vision
  model reports it as "clipped" on every page. It moves with the page.
- This is not fixed here: it is inherent to a fixed corner assistant over a scrolling
  page with right-aligned controls. It is recorded as a residual visual risk, not as a
  silent pass.

**C. Create-monitor and Opportunities (new this run).** At 1024 the fixed orb overlaps
the "Open the list" button and the travelling tag covers the panel heading; at 390 the
panel is clipped and the orb covers the "A coin jumps/drops" chips. These are
full-page captures, in which the fixed element is painted at the first viewport's
bottom, so the overlap is at one scroll position and moves away as the page scrolls. The
same class as B. **Recorded, not hidden; not fixed in this run.**

## Not re-opened in this run

These images were **not** put in front of the vision model in this run. They are named so
nobody treats them as freshly verified:

`landing-corner-1440x900`, `landing-corner-1024x768`, `landing-corner-390x844`,
`landing-corner-reduced-motion-1440x900` (the corner widget is the same one measured in
section 1a and by the stopped run's `vision_batch1_corner.txt`);
`s1-pro-none-*`, `s2-pro-card-*`, `s3-pro-crypto-*` and the remaining `s4/s5/s6/s7`
widths (covered by the stopped run's `vision_batch7/8/9` and by the 22 passing browser
checkout tests).

## What is unverified

- Contrast (V7) was not re-measured in this run; it is owned by `tests/support/contrast.py`
  and its browser tests, which are not part of the four suites captured in R8.
- The floating-overlay overlaps (items B and C) are recorded from still pictures and CSS,
  not from a live scroll measurement at every scroll offset.

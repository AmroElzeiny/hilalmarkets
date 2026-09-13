# VISUAL_REVIEW_HANDBACK.md — H4 vision review

Reviewer: `hm-reviewer-visual` (deepseek-v4-flash-vision-exp).
Date: 2026-09-13.
Scope judged: the fixed bottom-right corner assistant (`.hm-hilal`) — the round orb plus
the sky-blue "Ask AI" label above it.
Contract: `.hm-orchestrator/current/VISUAL_CONTRACT.md`, section "The corner label — Ask AI
above the round assistant button"; requirement D5 in this run's `MISSION.md`
(WCAG 2.2 SC 2.4.11, Focus Not Obscured).

All 15 images were visible to me and were inspected. None was missing or corrupt.

## Method

I did not judge from CSS text. I looked at all 15 images, then measured pixels with
Windows `System.Drawing` to confirm what the eye saw:

- `_visual_probe.ps1` / `_visual_probe2.ps1` / `_visual_probe3.ps1` — locate the orb and
  the label, measure size, centre, fill colour and air gap.
- `_visual_probe4.ps1` — count "ink" pixels (any pixel darker than luminance 170: text,
  borders, icons) in the 30px strip immediately left of the widget and in the strip below
  it. A control cut off by the widget would show ink there.

## Per-image results

| Image name | Widget present & unchanged? | Control covered (name it) | Scroll-clear? | Verdict |
|---|---|---|---|---|
| 1440x900-dashboard-market-bottom.png | Yes — orb 60×60; label 56×26 `#0E78AF`, white text, centred, 11px air gap | none | yes | PASS |
| 1440x900-dashboard-create-monitor-bottom.png | Yes — orb 60×60; label 58×27 `#0E78AF`, white text, centred | none | yes | PASS |
| 1440x900-dashboard-opportunities-bottom.png | Yes — orb 60×60; label 58×27 `#0E78AF`, white text, centred | none | yes | PASS |
| 1440x900-dashboard-settings-bottom.png | Yes — orb 60×60; label 60×27 `#0E78AF`, white text, centred | none | yes | PASS |
| 1440x900-dashboard-subscription-bottom.png | Yes — orb 60×60; label 60×28 `#0E78AF`, white text, centred | none | yes | PASS |
| 1024x768-dashboard-market-bottom.png | Yes — orb 60×60; label 57×26 `#0E78AF`, white text, centred | none | yes | PASS |
| 1024x768-dashboard-create-monitor-bottom.png | Yes — orb 60×60; label 56×26 `#0E78AF`, white text, centred | none | yes | PASS |
| 1024x768-dashboard-opportunities-bottom.png | Yes — orb 60×60; label 59×27 `#0E78AF`, white text, centred | none | yes | PASS |
| 1024x768-dashboard-settings-bottom.png | Yes — orb 60×60; label 60×28 `#0E78AF`, white text, centred | none | yes | PASS |
| 1024x768-dashboard-subscription-bottom.png | Yes — orb 60×60; label 60×28 `#0E78AF`, white text, centred | none | yes | PASS |
| 390x844-dashboard-market-bottom.png | Yes — orb 60×60; label 56×26 `#0E78AF`, white text, centred | none | yes | PASS |
| 390x844-dashboard-create-monitor-bottom.png | Yes — orb 60×60; label 56×26 `#0E78AF`, white text, centred | none | yes | PASS |
| 390x844-dashboard-opportunities-bottom.png | Yes — orb 60×60; label 58×26 `#0E78AF`, white text, centred | none | yes | PASS |
| 390x844-dashboard-settings-bottom.png | Yes — orb 60×60; label 58×27 `#0E78AF`, white text, centred | none | yes | PASS |
| 390x844-dashboard-subscription-bottom.png | Yes — orb 60×60; label 58×26 `#0E78AF`, white text, centred | none | yes | PASS |

## What the pixels prove (all 15 images)

| Check | Measured | Contract says | Result |
|---|---|---|---|
| Orb | 60×60, round, lime-green, in the bottom-right corner, every image | existing orb, unchanged | OK |
| Label box | 56–60 wide × 26–28 tall | 26px tall, one line | OK |
| Label fill (sampled away from the glyphs) | `#0E78AF` in every image | `--hm-sky` `#0e78af` | OK |
| Label text | white pixels present inside the box; reads `Ask AI` | `--white`, words `Ask AI` | OK |
| Label centred on orb | label centre == orb centre (973.5 / 1389.5 / 347.5), within 0.5px | centred, ±1.5px | OK |
| Air gap label→orb | 11px | 10px | OK (1px rasterisation) |
| Corner radius | top-row span 48–52 of a 56–60px box, i.e. radius ≈ 8px | 8px; a 13px pill would read ≈ 40 | OK — not a pill |
| Orb inset from corner | 20px at 1024 and 1440; 12px at 390 | phone rule `@media (max-width:520px) { right:12px; bottom:12px }` | OK — existing design |
| Ink in the 30px strip left of the widget | 0 pixels, all 15 images | no control may be covered | OK |
| Ink below the widget | 0 pixels, all 15 images | widget sits in the corner | OK |

The last page control on every surface sits well above the widget. For example, on
`390x844-dashboard-settings-bottom.png` the "Ask about your data" row ends about 105px
above the label; on `1440x900-dashboard-subscription-bottom.png` the "Your payments" card
ends about 65px above it. The blank band between the last control and the widget is the
reserved `--hm-corner-clearance` padding, so the content can scroll clear.

## Mismatches found

None. No control is under or cut off by the orb or the "Ask AI" label in any of the 15
images.

Two things I saw and checked, that are not mismatches:

1. The travelling tag above the label (the sentence "...your AI assistant · sees the page
   you are on · here to help as you go") is mid-scroll in every shot and its ends are
   clipped by the viewport edge. This is the existing `.hilal-tag` ticker
   (`hm-hilal-chat.css`: width 315px, `max-width: calc(100vw - 44px)`, fading mask). It is
   not the corner label, and it covers no control. The label does not overlap it.
2. On the phone shots the orb sits 12px from the corner, not 20px. This is the shipped
   `@media (max-width:520px)` rule, so the position is unchanged, not moved by this work.

## Overall verdict

**PASS.**

- Widget present and unchanged in all 15 images: round orb, sky-blue rounded-rectangle
  "Ask AI" label above it, white text, centred, radius ≈8px (not a pill).
- No page control is covered in any image (zero ink in the strip beside and below the
  widget; every last control sits above the reserved clearance band).
- The bottom of every page can scroll clear of the widget.

## What I did not do

- I did not run the browser suites. The direct H4 instruction asked only for the visual
  review. The machine check that answers the same question is
  `tests/browser/test_hilal_never_hides_a_control_e2e.py` (the file that produced these
  screenshots); `MISSION.md` records it as 3/3 pass after D5. `tests/browser/...` is marked
  `auto_run: false` in `.agents/commands.json`, so I left it to the operator.
- I have no "before" screenshot of the widget, so "unchanged" is judged against the
  contract's stated look, size and position, which all 15 images match exactly.

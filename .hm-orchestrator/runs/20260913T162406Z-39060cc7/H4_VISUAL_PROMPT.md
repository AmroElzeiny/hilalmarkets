You are the vision reviewer (`hm-reviewer-visual`) for a HilalMarkets hand-back mission.

You have been given 15 screenshot image attachments with `--file`. Each is named
`<viewport>-dashboard-<page>-bottom.png`: the page scrolled to the very BOTTOM, at one
of three viewports (1440x900, 1024x768, 390x844), for five signed-in dashboard pages
(market, create-monitor, opportunities, settings, subscription).

THE CONTRACT TO JUDGE AGAINST: read `.hm-orchestrator/current/VISUAL_CONTRACT.md`,
the section "The corner label — Ask AI above the round assistant button", plus the
requirement D5 in `.hm-orchestrator/runs/20260913T162406Z-39060cc7/MISSION.md`.
The rule is WCAG 2.2 SC 2.4.11 (Focus Not Obscured): the fixed bottom-right corner
assistant (`.hm-hilal` — the round orb plus its sky-blue "Ask AI" label) must not be
changed in LOOK, COLOUR, SIZE or POSITION, and it must NOT cover any page control.

For EACH of the 15 images, report:
1. Is the corner assistant widget visible in the bottom-right, and does it look
   unchanged: round orb, sky-blue rounded-rectangle "Ask AI" label above it, white
   text, centred, not a pill, not overlapping anything?
2. Is any real page control (button, chip, link, input, card action) sitting UNDER or
   being visually covered by the widget (including the "Ask AI" label)? Look at the
   bottom strip of the page. Name what is covered, if anything.
3. Does the bottom of the page content appear able to scroll clear of the widget (the
   last control is not trapped behind it)?

Be concrete and honest. Do not approve from CSS text alone — judge only what the image
shows. If an image is missing, corrupt, or you cannot actually see it, say so.

DELIVERABLE: write `.hm-orchestrator/runs/20260913T162406Z-39060cc7/VISUAL_REVIEW_HANDBACK.md`
with a table: image name | widget present & unchanged? | control covered (name it) |
scroll-clear? | verdict. Then a summary: exact mismatches found, and an overall
verdict PASS (no control covered, widget unchanged) or FAIL (name the image and the
control). Use the words UNVERIFIED_VISUAL if you cannot inspect the images.

You may only write inside `.hm-orchestrator/runs/20260913T162406Z-39060cc7/`.

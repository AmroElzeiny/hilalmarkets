# Dashboard shell redesign — the rules

The prompt for this work, turned into a checklist that is decided **before** any code is
written. Every rule below is either a hard constraint (H) or a deliverable (D). A rule is
only "done" when something checks it: a test name, or a measurement.

The shell is the **side menu** and the **topbar**. They appear on every signed-in page,
so a mistake here is a mistake on every page at once.

---

## 1. Hard constraints — things the redesign may never do

| # | Rule | Held by |
|---|---|---|
| H1 | No new main colour. Only the approved palette in `hilalmarkets-brand.css`. | `test_final_authenticated_styles_use_only_approved_brand_hex_colors` (the new sheet is added to its file list) |
| H2 | No new type family. Headings **Geometria** Medium, interface **Onest**. Both come from `--hm-font-display` / `--hm-font-ui`. | `test_the_shell_uses_only_the_two_brand_typefaces` |
| H3 | No new spacing scale. Spacing uses the `--t-*` step values already shipped (4/8/12/16/24/32/48) and the existing radius tokens. | `test_the_shell_spacing_comes_from_the_shared_scale` |
| H4 | Bright apple green never carries small text on a light surface. It marks, it does not label. | `test_invariant_focus_visibility.py`, contrast measurement in the browser suite |
| H5 | Status is never colour alone. Colour + word + icon, always. | `brand guide.md` §10, `test_invariant_dashboard_test_tone.py` |
| H6 | Motion has a reduced-motion form, and no motion loops without meaning. | `test_invariant_shared_motion_layer.py`, `test_the_shell_has_a_reduced_motion_form` |
| H7 | Every scripted animation goes through `hm-motion.js`. No component writes its own duration, easing or reduced-motion check. | `test_the_shell_animates_through_the_shared_motion_layer` |
| H8 | One release key. Every `?v=` in every template matches. | `test_every_page_shares_the_current_cache_busting_release_key` |
| H9 | No AI brains, robots, glowing spheres, crypto clichés. | `brand guide.md` §13 |
| H10 | Nothing implies Hilal Markets trades, advises, or grants a Shariah status. | `core/copy_rules.py` |

## 2. Accessibility — WCAG 2.2 AA is the floor, AAA where it is free

| # | Rule | Held by |
|---|---|---|
| A1 | Every control has an accessible name **in both menu states**. A collapsed menu that hides its labels with `display:none` leaves every link nameless — that is the bug being fixed, not a style choice. | `test_every_side_menu_link_keeps_its_name_when_the_menu_is_minimized` |
| A2 | Every target is at least 44×44 CSS pixels. | `test_the_shell_targets_are_large_enough` |
| A3 | Visible focus everywhere, using the shared two-ring token. No component writes its own ring. | `test_invariant_focus_visibility.py` |
| A4 | Text contrast ≥ 4.5:1; UI edges and marks ≥ 3:1. | browser measurement `test_the_shell_clears_wcag_aa` |
| A5 | Keyboard reaches everything, in a sensible order, and `Escape` closes anything that opened. | `test_the_shell_is_fully_operable_from_the_keyboard` |
| A6 | The current page is announced (`aria-current="page"`), not only painted. | `test_the_current_page_is_announced_not_only_painted` |
| A7 | A tooltip is never the only place a name exists. It repeats a name that is already in the accessibility tree. | A1 |
| A8 | Live regions announce what changed (menu minimized/expanded, search results count). | `test_the_shell_is_fully_operable_from_the_keyboard` |

## 3. What must be built (D)

### D1 — Side menu, expanded
- Brand head, groups with headings, one row per destination.
- Every row: an icon in its own rounded chip, the name, and a place for a count.
- The active row is marked by **one** element that *moves* between rows, so a person sees
  where they came from and where they are now. Not a colour that blinks on somewhere else.
- Hover and focus both do the same thing. Never hover-only.
- A footer that says who is signed in and how to leave.

### D2 — Side menu, minimized
- Icons only, centred, still 44px targets.
- A flyout label on hover **and** on keyboard focus, because a keyboard user has no hover.
- The name stays in the accessibility tree (A1).
- The state is remembered between visits.

### D3 — Topbar
- Says which page you are on (identity), not just floating controls.
- Search that a keyboard can reach without the mouse.
- **A slot for the actions that belong to the current page.** The page does not draw its
  own top-right button; it *declares* what belongs there and the shared topbar draws it.
  This is what "move the button to the topbar" means structurally — one owner, not a
  second button glued on per page.
- Notifications, the page guide, and the account, each with an icon.

### D4 — Motion, everywhere, and every piece of it earns its place
| Movement | What it explains |
|---|---|
| The active marker sliding between rows | where you were → where you are |
| Rows settling in, one after the next | the menu has finished arriving |
| Icon lifting on hover | this row is a target |
| Flyout label arriving from the menu edge | this label belongs to that icon |
| Width easing between the two menu states | the menu did not jump; it moved |
| The tag beside the assistant scrolling its line | there is more sentence than box |

Nothing else moves. No looping glow, no pulsing, no decorative particles.

## 4. The named changes from the prompt

| # | Change | Where |
|---|---|---|
| C1 | Remove **Trading Assistant** from the menu; its page is not reachable | `site_content.py`, `dashboard.py` |
| C2 | Remove **Home** from the menu; its page is not reachable and its template is deleted | `site_content.py`, `dashboard.py`, `templates/hilal/dashboard/home.html` |
| C3 | **Monitor** → **Create a monitor** | `site_content.py` |
| C4 | **Watchlists** → **Monitors** | `site_content.py` |
| C5 | Menu icons must fit their names | `hilalmarkets-icons.js`, `hm-shell.css` |
| C6 | Today: **Start a list** → **Create a monitor**, going to the canvas | `main_dashboard.py` |
| C7 | Today: **Draw a new list** → **Monitors**, going to the monitors list, different icon | `main/home.html` |
| C8 | Today: remove the coloured start-edge on `.m-flag` and the list rows | `hm-main.css` |
| C9 | Monitors page: **New Watchlist** leaves the page and becomes a topbar action | `watchlists.html`, `dashboard_test.py`, `dashboard_topbar.html` |
| C10 | Monitors page: remove the coloured start-edge on `.w-working[data-tone="danger"]` | `hm-watch-test.css` |
| C11 | Opportunities: **Your Watchlists** leaves the page and becomes a topbar action | `opportunities.html`, `dashboard_test.py` |
| C12 | Connections: real WhatsApp / Telegram / email / dashboard marks | `hilalmarkets-icons.js`, `connections.html` |
| C13 | Settings: the same real marks, across the whole page | `settings.html`, `dashboard_test.py` |
| C14 | Support: the send mark is not hand-drawn | `hilalmarkets-icons.js` |
| C15 | The assistant reads as **AI**, with a one-line tag above it that scrolls inside a narrower box, for ever | `hilal_chat.html`, `hm-hilal-chat.css` |
| C16 | `/contact`: the **Help Center** row is removed | `Hilal-Markets-Website/src/pages/ContactPage.tsx` **and the rebuilt bundle** |
| C17 | The dashboard is served on `app.hilalmarkets.com/` | `config.py`, `public.py`, env examples |

### The colour-edge rule behind C8 and C10
Three instances were named. The class is **the coloured bar drawn on the starting edge of
a status box**. It is removed from every box of that family on these surfaces, not from
the three that were named — otherwise the same page ends up with two visual languages for
one idea. The tone is still carried by the icon, the word and the fill, so nothing loses
its meaning (H5).

### The naming rule behind C3, C4, C6 and C7
The renamed buttons must land on a page that calls itself the same thing. A button called
**Monitors** that opens a page titled **Watchlists** is a worse bug than the old name was.
So the visible noun changes together on the redesigned surfaces (`/main`,
`the redesigned dashboard pages/*`). It is **not** changed in email, Telegram, WhatsApp or the public
site in this pass — those are a separate audience with their own copy tests, and a
half-finished rename there would be worse than none.

## 5. Writing rules (the prompt's "no walls of text")

- Nothing on the shell is longer than one short line.
- A tooltip is 1–3 words. A menu name is 1–3 words.
- Explanations live behind a control a person chooses to press, never on the surface.
- Plain words. No field names, no jargon, no "gte", no "close-to-close".

## 6. Definition of done

1. `ruff`, `mypy` clean.
2. The offline suites pass: `tests/unit tests/engine tests/interpreter tests/services`.
3. The integration suites for every touched page pass.
4. The browser suite proves the things only a browser can prove: the accessible name in
   the minimized menu, the contrast, the moving marker, the scrolling tag, and that
   nothing throws.
5. Every removed page really 404s or redirects — checked, not assumed.
6. Every link that pointed at a removed page points somewhere real.
7. The report lists everything found and fixed beyond the prompt.

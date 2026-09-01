// One vendored, dependency-free outline icon set for every Hilal Markets surface.
// Geometry follows the same 24px, round-cap language as Lucide icons.
const ICONS = Object.freeze({
  home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V10Z"/><path d="M9 21v-7h6v7"/>',
  // Lucide `chart-candlestick`, unchanged.
  market:'<path d="M9 5v4"/><rect width="4" height="6" x="7" y="9" rx="1"/><path d="M9 15v2"/><path d="M17 3v2"/><rect width="4" height="8" x="15" y="5" rx="1"/><path d="M17 13v3"/><path d="M3 3v16a2 2 0 0 0 2 2h16"/>',
  watchlist:'<path d="M6 3h12v18l-6-4-6 4V3Z"/><path d="M9 8h6"/><path d="M9 12h4"/>',
  // Lucide `radar`, unchanged.
  radar:'<path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/><path d="M4 6h.01"/><path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/><path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/><path d="M12 18h.01"/><path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/><circle cx="12" cy="12" r="2"/><path d="m13.41 10.59 5.66-5.66"/>',
  scan:'<path d="M4 7V5a1 1 0 0 1 1-1h2"/><path d="M17 4h2a1 1 0 0 1 1 1v2"/><path d="M20 17v2a1 1 0 0 1-1 1h-2"/><path d="M7 20H5a1 1 0 0 1-1-1v-2"/><path d="M8 12h8"/><path d="m13 9 3 3-3 3"/>',
  activity:'<path d="M3 12h4l2-6 4 12 2-6h6"/><path d="M3 4v16h18"/>',
  // Lucide `shield-check`, unchanged — the same shield-with-a-check shape as `shield_check`
  // below. Kept as two keys because the callers mean two different things by it, not
  // because the artwork should differ.
  compliance:'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  methodology:'<circle cx="12" cy="12" r="9"/><path d="M12 7v10"/><path d="M8 9h8"/><path d="M9 9c0 2-1 3-3 4 2 1 3 2 3 4"/><path d="M15 9c0 2 1 3 3 4-2 1-3 2-3 4"/>',
  // Lucide `plug`, unchanged.
  integrations:'<path d="M12 22v-5"/><path d="M15 8V2"/><path d="M17 8a1 1 0 0 1 1 1v4a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1z"/><path d="M9 8V2"/>',
  billing:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 10h18"/><path d="M7 15h4"/>',
  settings:'<path d="M4 7h10"/><path d="M18 7h2"/><circle cx="16" cy="7" r="2"/><path d="M4 17h2"/><path d="M10 17h10"/><circle cx="8" cy="17" r="2"/>',
  // Lucide `cookie`, unchanged.
  cookie:'<path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5"/><path d="M8.5 8.5v.01"/><path d="M16 15.5v.01"/><path d="M12 12v.01"/><path d="M11 17v.01"/><path d="M7 14v.01"/>',
  panel:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M14 9l-3 3 3 3"/>',
  panel_expand:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M11 9l3 3-3 3"/>',
  // Lucide `life-buoy`, unchanged.
  support:'<circle cx="12" cy="12" r="10"/><path d="m4.93 4.93 4.24 4.24"/><path d="m14.83 9.17 4.24-4.24"/><path d="m14.83 14.83 4.24 4.24"/><path d="m9.17 14.83-4.24 4.24"/><circle cx="12" cy="12" r="4"/>',
  // Lucide `bell`, unchanged.
  bell:'<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  // Lucide `x`, unchanged.
  close:'<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  // Lucide `arrow-right`, unchanged.
  arrow:'<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  // Lucide `arrow-left`, unchanged. Its own shape rather than `arrow` turned 180°:
  // a static `transform: rotate(180deg)` is erased by the reduced-motion rule that
  // sets `transform: none` on everything, so a "back" arrow pointed forwards for
  // anybody who had asked for less movement. A rotation is not a decoration and it
  // does not belong in a rule about motion.
  arrow_left:'<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
  chevron:'<path d="m8 10 4 4 4-4"/>',
  // Lucide `check`, unchanged.
  check:'<path d="M20 6 9 17l-5-5"/>',
  // Lucide `plus`, unchanged.
  plus:'<path d="M5 12h14"/><path d="M12 5v14"/>',
  // Lucide `info`, unchanged.
  info:'<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  // Lucide `circle-question-mark` (shipped under the older names `help-circle` and
  // `circle-help` before this one), unchanged. A question mark, for "press this to be
  // told what the number beside it means" — kept apart from `info`'s "i", which this
  // codebase uses for "here is the meaning, read on hover".
  circle_help:'<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
  filter:'<path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z"/>',
  chart:'<path d="M4 19V5"/><path d="M4 19h16"/><path d="m7 15 4-4 3 2 5-7"/>',
  // Lucide `book-user`. There is no "passport" icon in Lucide; this is the nearest real
  // shape — a booklet with a person in it — rather than an invented glyph.
  passport:'<path d="M15 13a3 3 0 1 0-6 0"/><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20"/><circle cx="12" cy="8" r="2"/>',
  // Telegram and WhatsApp are *companies*, not concepts, and a company is recognised by
  // its own mark. Both were drawn here by hand — a paper plane and a speech bubble with
  // two hooks in it — and neither was the logo anybody knows. These two are the official
  // marks from Simple Icons (CC0), byte for byte as they ship, and they are the same
  // artwork already vendored as `brand-telegram.svg` and `brand-whatsapp.svg`, so the
  // product cannot show two different Telegram marks depending on which reader drew it.
  //
  // They are solid shapes, not outlines, so each one turns the wrapper's stroke off and
  // fills with the inherited colour instead. `brand guide.md` section 13 asks for
  // official communication-channel logos, which is exactly this.
  telegram:'<path fill="currentColor" stroke="none" d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/>',
  whatsapp:'<path fill="currentColor" stroke="none" d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z"/>',
  // Google's own "G", in Google's own four colours.
  //
  // This is the one mark in this file that does not take the colour around it, and it is
  // deliberate: the G is Google's trademark and their sign-in branding rules say it is
  // used as issued — not tinted, not recoloured, not redrawn in apple green. So the four
  // fills are written out and the wrapper's stroke is turned off on each path.
  //
  // It is not a hole in the palette rule either. That rule is about *our* colours, and
  // these four are not ours to choose. They appear here, on the one button that means
  // "continue with Google", and nowhere else in the product.
  google:'<path fill="#4285F4" stroke="none" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" stroke="none" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" stroke="none" d="M5.84 14.1c-.22-.66-.35-1.36-.35-2.1s.13-1.44.35-2.1V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.83z"/><path fill="#EA4335" stroke="none" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.83C6.71 7.31 9.14 5.38 12 5.38z"/>',
  // Lucide `sparkle`, unchanged — one mark, not the two-sparkle shape drawn here before.
  spark:'<path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z"/>',
  eye:'<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/>',
  // Lucide `clock`, unchanged.
  clock:'<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  user:'<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  logout:'<path d="M10 4H5v16h5"/><path d="M14 8l4 4-4 4"/><path d="M18 12H8"/>',
  wallet:'<path d="M3 6h15a2 2 0 0 1 2 2v10H5a2 2 0 0 1-2-2V6Z"/><path d="M16 11h5v4h-5a2 2 0 0 1 0-4Z"/><path d="M4 6V5a2 2 0 0 1 2-2h10v3"/>',
  upload:'<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v4h16v-4"/>',
  download:'<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/>',
  // Lucide `triangle-alert`, unchanged — the same shape as `alert` below. Two keys
  // because callers mean two different things by it, not because the artwork differs.
  warning:'<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  bot:'<rect x="4" y="6" width="16" height="13" rx="3"/><path d="M9 11h.01M15 11h.01M9 15h6M12 6V3M9 3h6"/>',
  // Lucide `workflow`, unchanged.
  workflow:'<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
  template:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16M8 10h13"/>',
  expand:'<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/><path d="m3 8 5-5M21 8l-5-5M3 16l5 5M21 16l-5 5"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
  version:'<circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>',
  // Lucide `coins`, unchanged.
  coins:'<path d="M13.744 17.736a6 6 0 1 1-7.48-7.48"/><path d="M15 6h1v4"/><path d="m6.134 14.768.866-.5 2 3.464"/><circle cx="16" cy="8" r="6"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/><path d="m9 16 2 2 4-4"/>',
  // Lucide `triangle-alert`, unchanged — same shape as `warning` above.
  alert:'<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  gift:'<rect x="3" y="9" width="18" height="12" rx="2"/><path d="M12 9v12M3 13h18M7.5 9C5 9 4 7.8 4 6.5S5 4 6.5 4C9 4 12 9 12 9M16.5 9C19 9 20 7.8 20 6.5S19 4 17.5 4C15 4 12 9 12 9"/>',
  // Lucide `shield`, unchanged.
  shield:'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
  // Compass. Used by the page-guide launcher; no existing icon reads as "show me
  // around this page" — `info` and `support` already mean other things here.
  guide:'<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5 5-2Z"/>',

  // Added for the market and Passport surfaces. Same 24px grid,
  // same 1.75 stroke, same round caps as everything above, so the set stays one
  // visual language rather than two that drifted.
  trend_up:'<path d="M3 17 9.5 10.5l4 4L21 7"/><path d="M15 7h6v6"/>',
  trend_down:'<path d="M3 7l6.5 6.5 4-4L21 17"/><path d="M15 17h6v-6"/>',
  trend_flat:'<path d="M3 12h18"/><path d="M15 8l4 4-4 4"/>',
  heart:'<path d="M12 20s-7.5-4.6-7.5-9.6A4.4 4.4 0 0 1 12 7.6a4.4 4.4 0 0 1 7.5 2.8C19.5 15.4 12 20 12 20Z"/>',
  heart_filled:'<path d="M12 20s-7.5-4.6-7.5-9.6A4.4 4.4 0 0 1 12 7.6a4.4 4.4 0 0 1 7.5 2.8C19.5 15.4 12 20 12 20Z" fill="currentColor" stroke="none"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  rows:'<rect x="3" y="4" width="18" height="5" rx="2"/><rect x="3" y="12" width="18" height="5" rx="2"/><path d="M3 20h18"/>',
  sort:'<path d="M8 5v14"/><path d="m5 8 3-3 3 3"/><path d="M16 19V5"/><path d="m13 16 3 3 3-3"/>',
  // Lucide `refresh-cw`, unchanged.
  refresh:'<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  // Lucide `sliders-horizontal`, unchanged.
  sliders:'<path d="M10 5H3"/><path d="M12 19H3"/><path d="M14 3v4"/><path d="M16 17v4"/><path d="M21 12h-9"/><path d="M21 19h-5"/><path d="M21 5h-7"/><path d="M8 10v4"/><path d="M8 12H3"/>',
  // Lucide `globe`, unchanged.
  globe:'<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  // Lucide `shield-check`, unchanged — the same artwork as `compliance` above.
  shield_check:'<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  file_text:'<path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7l-4-4Z"/><path d="M14 3v4h4"/><path d="M9 12h6M9 16h6"/>',
  printer:'<path d="M7 9V4h10v5"/><rect x="3" y="9" width="18" height="7" rx="2"/><path d="M7 14h10v6H7z"/>',
  external:'<path d="M14 4h6v6"/><path d="m20 4-8 8"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  book:'<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5Z"/><path d="M8 7h7M8 11h7"/>',
  layers:'<path d="m12 3 8 4.5-8 4.5-8-4.5L12 3Z"/><path d="m4 12 8 4.5 8-4.5"/><path d="m4 16.5 8 4.5 8-4.5"/>',
  link:'<path d="M10 13a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11.5 5.8"/><path d="M14 11a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 19.7l1.4-1.4"/>',
  minus:'<path d="M5 12h14"/>',
  scale:'<path d="M12 4v16"/><path d="M7 20h10"/><path d="M5 8h14"/><path d="M5 8 2.5 14h5L5 8Z"/><path d="M19 8l-2.5 6h5L19 8Z"/>',
  // Lucide `rotate-ccw-clock` — the current name for what shipped as `history`.
  history:'<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
  flag:'<path d="M5 21V4"/><path d="M5 5h11l-2 3.5L16 12H5"/>',
  // Lucide `pause`, unchanged.
  pause:'<rect x="14" y="3" width="5" height="18" rx="1"/><rect x="5" y="3" width="5" height="18" rx="1"/>',
  // Lucide `play`, unchanged.
  play:'<path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"/>',
  chevron_left:'<path d="m14 7-5 5 5 5"/>',
  // Lucide `chevron-right`, unchanged.
  chevron_right:'<path d="m9 18 6-6-6-6"/>',

  // Added for the monitor canvas. Same 24px grid, same 1.75 stroke,
  // same round caps, so the board reads as part of one icon language rather than a
  // second set bolted on beside it.
  trash:'<path d="M4 7h16"/><path d="M10 4h4a1 1 0 0 1 1 1v2H9V5a1 1 0 0 1 1-1Z"/><path d="M6.5 7 7.6 19.1a1 1 0 0 0 1 .9h6.8a1 1 0 0 0 1-.9L17.5 7"/><path d="M10.5 11v5M13.5 11v5"/>',
  undo:'<path d="M4 9h11a5 5 0 0 1 0 10H9"/><path d="m8 5-4 4 4 4"/>',
  redo:'<path d="M20 9H9a5 5 0 0 0 0 10h6"/><path d="m16 5 4 4-4 4"/>',
  grip:'<circle cx="9.5" cy="6" r="1.15" fill="currentColor" stroke="none"/><circle cx="14.5" cy="6" r="1.15" fill="currentColor" stroke="none"/><circle cx="9.5" cy="12" r="1.15" fill="currentColor" stroke="none"/><circle cx="14.5" cy="12" r="1.15" fill="currentColor" stroke="none"/><circle cx="9.5" cy="18" r="1.15" fill="currentColor" stroke="none"/><circle cx="14.5" cy="18" r="1.15" fill="currentColor" stroke="none"/>',
  keyboard:'<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M6.5 14h.01M17 14h.01M9.5 14h5"/>',
  minimize:'<path d="M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6"/><path d="m3 3 6 6M21 3l-6 6M3 21l6-6M21 21l-6-6"/>',
  branch:'<circle cx="6" cy="12" r="2.2"/><circle cx="18" cy="6" r="2.2"/><circle cx="18" cy="18" r="2.2"/><path d="M8.2 12h2.3a3 3 0 0 0 2.5-1.4l1.1-1.9"/><path d="M8.2 12h2.3a3 3 0 0 1 2.5 1.4l1.1 1.9"/>',
  tidy:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M17.5 14v7M14 17.5h7"/>',
  zoom_in:'<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/><path d="M11 8.5v5M8.5 11h5"/>',
  zoom_out:'<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/><path d="M8.5 11h5"/>',
  unlink:'<path d="m4 4 16 16"/><path d="M10.6 13.4a4 4 0 0 0 5.6 0l1.8-1.8a4 4 0 0 0-5.6-5.6l-.6.6"/><path d="M13.4 10.6a4 4 0 0 0-5.6 0L6 12.4a4 4 0 0 0 5.6 5.6l.6-.6"/>',
  // Lucide `lock`, unchanged.
  lock:'<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  hand:'<path d="M8 12.5V5.8a1.4 1.4 0 0 1 2.8 0V11"/><path d="M10.8 11V4.6a1.4 1.4 0 0 1 2.8 0V11"/><path d="M13.6 11V6.4a1.4 1.4 0 0 1 2.8 0V14"/><path d="M8 12.5v-1a1.4 1.4 0 0 0-2.8 0v3.9A5.6 5.6 0 0 0 10.8 21h2a5.6 5.6 0 0 0 5.6-5.6V14"/>',
  move:'<path d="M12 4v16M4 12h16"/><path d="m9 7 3-3 3 3M9 17l3 3 3-3M7 9l-3 3 3 3M17 9l3 3-3 3"/>',
  // Hilal, the dashboard assistant. Lucide's `message-circle`, taken exactly as it
  // ships (ISC licence) rather than drawn here. A hand-drawn speech shape with a
  // crescent inside it was tried first and read as a spiral or an "@" at 26 pixels —
  // the size it is actually seen at. A mark nobody recognises is worse than a common
  // one, so this is the common one: the shape everybody already knows means "talk to
  // somebody". The crescent stays where it belongs, in the logo beside it.
  //
  // There is no ready-made icon anywhere that is a chat bubble with a moon inside, so
  // the nearest ready icon for the same idea is used instead.
  hilal:'<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  // Lucide `moon-star`, unchanged. The brand's own crescent, for the places that mean
  // Hilal Markets rather than "talk to Hilal".
  moon:'<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/><path d="M19 3v4"/><path d="M17 5h4"/>',

  // Added for the Watchlists and Opportunities pages. Lucide, taken as
  // it ships: `pencil`, `archive` and `message-square`. Same 24px grid and the same
  // stroke as everything above, because the wrapper applies both.
  edit:'<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497Z"/><path d="m15 5 4 4"/>',
  archive:'<rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  chat:'<path d="M22 17a2 2 0 0 1-2 2H6l-4 4V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"/>',
  star:'<path d="m12 3.6 2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.7l5.8-.8L12 3.6Z"/>',
  // Lucide `send` and `mail`, taken as they ship. Both were drawn here by hand before:
  // the send mark was a flat triangle with a stray line across it that read as a cursor
  // rather than "send this", and the envelope was a rectangle with a V in it. These are
  // the shapes people already recognise, which is the whole job of an icon this small.
  send:'<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/>',
  mail:'<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.991 5.727a2 2 0 0 1-2.009 0L2 7"/>',
  // Lucide `layout-dashboard`. "In the dashboard" is a real destination a person can be
  // told in, and it was the one channel with no mark of its own.
  dashboard:'<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
  // Lucide `sparkles` and `gauge`, and `circle-plus`, all unchanged.
  sparkles:'<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
  gauge:'<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
  circle_plus:'<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/>'
});
// There is no second mark for the assistant.
//
// `hilal_ai` used to live here: a speech bubble with a spark inside it, drawn for the
// dashboard alone. The landing page's assistant was a plain `spark`, so the same helper
// wore one face on the public site and another after signing in, and neither knew about
// the other. The spark is the one now, on both.
//
// It is also the only one of the two that is *drawn* in the middle of its own box. The
// bubble sits half a pixel low and half a pixel left of centre at 26px, because the tail
// of the bubble is part of the shape — measured in Chromium, and the reason the mark
// looked slightly off in the corner of every dashboard page.
//
// Still deliberately not a robot and not a brain: `brand guide.md` section 13 rules both
// out by name. A spark says "written by software" without drawing a machine.
// The paths for one icon, without the <svg> around them.
//
// The React public site draws its own <svg> element so React owns the node, but the
// geometry has to come from here — the alternative is a second set of icons that
// slowly stops matching this one. `window.icon` below is still what every Jinja
// template uses; both read the same table.
//
// Returns the fallback for an unknown name rather than nothing, so a typo shows a
// visible mark instead of an empty box that nobody notices.
window.iconBody = function(name){
  return ICONS[name] || ICONS.info;
};
window.iconNames = function(){
  return Object.keys(ICONS);
};
window.icon = function(name, cls='icon'){
  const body = window.iconBody(name);
  return `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
};
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('[data-icon]').forEach(el=>{
    el.innerHTML = window.icon(el.dataset.icon, el.dataset.iconClass || 'icon');
  });
});

// One vendored, dependency-free outline icon set for every Hilal Markets surface.
// Geometry follows the same 24px, round-cap language as Lucide icons.
const ICONS = Object.freeze({
  home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V10Z"/><path d="M9 21v-7h6v7"/>',
  market:'<path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-8"/><path d="M22 19H2"/><path d="m4 8 6-4 6 6 5-5"/>',
  watchlist:'<path d="M6 3h12v18l-6-4-6 4V3Z"/><path d="M9 8h6"/><path d="M9 12h4"/>',
  radar:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 3v9l6-3"/>',
  scan:'<path d="M4 7V5a1 1 0 0 1 1-1h2"/><path d="M17 4h2a1 1 0 0 1 1 1v2"/><path d="M20 17v2a1 1 0 0 1-1 1h-2"/><path d="M7 20H5a1 1 0 0 1-1-1v-2"/><path d="M8 12h8"/><path d="m13 9 3 3-3 3"/>',
  activity:'<path d="M3 12h4l2-6 4 12 2-6h6"/><path d="M3 4v16h18"/>',
  compliance:'<path d="M12 3 4.5 6v5.4c0 4.7 3.1 8.9 7.5 10.1 4.4-1.2 7.5-5.4 7.5-10.1V6L12 3Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>',
  methodology:'<circle cx="12" cy="12" r="9"/><path d="M12 7v10"/><path d="M8 9h8"/><path d="M9 9c0 2-1 3-3 4 2 1 3 2 3 4"/><path d="M15 9c0 2 1 3 3 4-2 1-3 2-3 4"/>',
  integrations:'<path d="M8 12a4 4 0 0 1 4-4h2"/><path d="M16 12a4 4 0 0 1-4 4h-2"/><path d="M14 5h5v5"/><path d="m19 5-6 6"/><path d="M10 19H5v-5"/><path d="m5 19 6-6"/>',
  billing:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 10h18"/><path d="M7 15h4"/>',
  settings:'<path d="M4 7h10"/><path d="M18 7h2"/><circle cx="16" cy="7" r="2"/><path d="M4 17h2"/><path d="M10 17h10"/><circle cx="8" cy="17" r="2"/>',
  cookie:'<path d="M20.8 13.2A8.8 8.8 0 1 1 10.8 3.1a4 4 0 0 0 4.9 4.9 4 4 0 0 0 5.1 5.2Z"/><path d="M8 9h.01M8 15h.01M14 15h.01"/>',
  panel:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M14 9l-3 3 3 3"/>',
  panel_expand:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M11 9l3 3-3 3"/>',
  support:'<circle cx="12" cy="12" r="9"/><path d="M9.6 9a2.6 2.6 0 1 1 4.3 2c-1 .8-1.9 1.2-1.9 2.7"/><path d="M12 17h.01"/>',
  bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  close:'<path d="m5 5 14 14M19 5 5 19"/>',
  arrow:'<path d="M5 12h14"/><path d="m14 7 5 5-5 5"/>',
  chevron:'<path d="m8 10 4 4 4-4"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7h.01"/>',
  filter:'<path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z"/>',
  chart:'<path d="M4 19V5"/><path d="M4 19h16"/><path d="m7 15 4-4 3 2 5-7"/>',
  passport:'<rect x="4" y="3" width="16" height="18" rx="3"/><circle cx="12" cy="10" r="3"/><path d="M8 17h8"/><path d="M9 14h6"/>',
  telegram:'<path d="m21 4-3 16-6-4-3 3v-5l9-7-11 6-4-2 18-7Z"/>',
  whatsapp:'<path d="M21 11.5a8.5 8.5 0 0 1-12.6 7.4L3 20.5l1.6-5.2A8.5 8.5 0 1 1 21 11.5Z"/><path d="M8.2 7.8c.7 3.8 2.2 5.3 6 6"/><path d="m8.2 7.8 1.3-.5 1.1 2-1 .8M14.2 13.8l.8-1 2 1.1-.5 1.3"/>',
  moon:'<path d="M20 15.2A8.5 8.5 0 1 1 8.8 4a7 7 0 0 0 11.2 11.2Z"/>',
  spark:'<path d="m12 2 1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8L12 2Z"/><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z"/>',
  eye:'<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  user:'<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  logout:'<path d="M10 4H5v16h5"/><path d="M14 8l4 4-4 4"/><path d="M18 12H8"/>',
  wallet:'<path d="M3 6h15a2 2 0 0 1 2 2v10H5a2 2 0 0 1-2-2V6Z"/><path d="M16 11h5v4h-5a2 2 0 0 1 0-4Z"/><path d="M4 6V5a2 2 0 0 1 2-2h10v3"/>',
  upload:'<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v4h16v-4"/>',
  download:'<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/>',
  warning:'<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.7 2.4 17.2A2 2 0 0 0 4.1 20h15.8a2 2 0 0 0 1.7-2.8L13.7 3.7a2 2 0 0 0-3.4 0Z"/>',
  bot:'<rect x="4" y="6" width="16" height="13" rx="3"/><path d="M9 11h.01M15 11h.01M9 15h6M12 6V3M9 3h6"/>',
  workflow:'<rect x="3" y="4" width="7" height="5" rx="1"/><rect x="14" y="15" width="7" height="5" rx="1"/><path d="M10 6.5h4a3 3 0 0 1 3 3V15M14 17.5h-4a3 3 0 0 1-3-3V9"/>',
  template:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16M8 10h13"/>',
  expand:'<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/><path d="m3 8 5-5M21 8l-5-5M3 16l5 5M21 16l-5 5"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
  version:'<circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>',
  coins:'<circle cx="9" cy="9" r="6"/><path d="M14 9a6 6 0 1 1-5 5"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/><path d="m9 16 2 2 4-4"/>',
  alert:'<path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v5M12 17h.01"/>',
  copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  gift:'<rect x="3" y="9" width="18" height="12" rx="2"/><path d="M12 9v12M3 13h18M7.5 9C5 9 4 7.8 4 6.5S5 4 6.5 4C9 4 12 9 12 9M16.5 9C19 9 20 7.8 20 6.5S19 4 17.5 4C15 4 12 9 12 9"/>',
  shield:'<path d="M12 3 4.5 6v5.4c0 4.7 3.1 8.9 7.5 10.1 4.4-1.2 7.5-5.4 7.5-10.1V6L12 3Z"/>'
});
window.icon = function(name, cls='icon'){
  const body = ICONS[name] || ICONS.info;
  return `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
};
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('[data-icon]').forEach(el=>{
    el.innerHTML = window.icon(el.dataset.icon, el.dataset.iconClass || 'icon');
  });
});

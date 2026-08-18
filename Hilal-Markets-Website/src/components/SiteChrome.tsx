/**
 * The header and the footer, drawn once for every React page on the public site.
 *
 * Two things about this file are load-bearing.
 *
 * **The menu is not written here.** It arrives from the server, out of
 * `core/site_content.py`, the same list the Jinja footer loops over. This site renders
 * its pages twice — Jinja for `/about`, `/help`, `/pricing`, React for the rest — and a
 * menu typed out in both places is a menu that disagrees with itself the first time a
 * page is added to one of them. That already happened: the React footer offered three
 * links while the Jinja one offered twelve. The fallback below exists only for a page
 * opened without the shell, and is deliberately the smallest honest menu.
 *
 * **The mobile control is an icon, not the word "Menu".** It used to render the text
 * `Menu` / `Close` in a pill, which is the shape of a button nobody recognises as a
 * menu. It is now the standard two-rule mark that folds into a cross, and the rules are
 * real elements that move — so the state is visible during the transition, not only at
 * the ends of it. The accessible name stays a word, because the icon is the whole
 * message and a screen reader must still hear one.
 */
import { useEffect, useRef, useState } from 'react'
import FigmaLogo from '../imports/Frame-1'
import { Reveal } from './Reveal'
import { TrackedCta } from './Tracking'
import { Icon } from './Icon'
import { useScrolledPast } from './interactions'

/** Where the header's primary action goes: the dashboard when signed in, sign-up if not. */
export const DASHBOARD_ENTRY = '/dashboard-entry'

/**
 * Both ways into the product.
 *
 * The server decides the addresses, and when the product has a hostname of its own they
 * come back absolute — `https://app.hilalmarkets.com/...`. Written as plain paths here,
 * they kept a visitor on the marketing hostname and served them the entire dashboard
 * from there, which is not what `APP_BASE_URL` is for.
 */
export function dashboardEntryHref(): string {
  return window.HilalMarketsRuntimeConfig?.chrome?.dashboardEntryHref || DASHBOARD_ENTRY
}

export function signInHref(): string {
  return window.HilalMarketsRuntimeConfig?.chrome?.signInHref || '/signin'
}

/** Only for a page rendered without the server shell. The server list is the real one.
 *
 *  Kept identical to `FOOTER_NAVIGATION` in `core/site_content.py`, and
 *  `test_both_footers_offer_the_same_menu` is what notices when it is not. */
const FALLBACK_FOOTER_GROUPS = [
  {
    label: 'Product',
    items: [
      { label: 'Features', href: '/features' },
      { label: 'How it works', href: '/how-it-works' },
    ],
  },
  {
    label: 'Legal',
    items: [
      { label: 'Terms of Use', href: '/terms' },
      { label: 'Privacy Policy', href: '/privacy' },
      { label: 'Cookie Policy', href: '/cookies' },
    ],
  },
  { label: 'Contact', items: [{ label: 'Contact', href: '/contact' }] },
]

/** Where "Cookie settings" goes when the page was opened without the server shell. */
const FALLBACK_COOKIE_SETTINGS_HREF = '/cookies?settings=1'

/**
 * The three channel marks.
 *
 * Drawn here rather than taken from the product icon set on purpose: that set is one
 * outline language at a 24px grid, and a company's own mark is not ours to redraw in it.
 * The brand rules allow official communication-channel logos as themselves. These are
 * the official glyphs, single-path, filled, so they inherit `currentColor` and stay
 * legible at 18px.
 */
const SOCIAL_GLYPHS: Record<string, string> = {
  Instagram:
    'M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41-.56-.22-.96-.48-1.38-.9-.42-.42-.68-.82-.9-1.38-.16-.42-.36-1.06-.41-2.23C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41C8.42 2.17 8.8 2.16 12 2.16Zm0 5.68a4.16 4.16 0 1 0 0 8.32 4.16 4.16 0 0 0 0-8.32Zm0 6.86a2.7 2.7 0 1 1 0-5.4 2.7 2.7 0 0 1 0 5.4Zm5.3-7.02a.97.97 0 1 1-1.94 0 .97.97 0 0 1 1.94 0Z',
  X: 'M17.53 3h3.02l-6.6 7.54L21.75 21h-6.09l-4.77-6.23L5.44 21H2.42l7.06-8.07L2.25 3h6.24l4.31 5.7L17.53 3Zm-1.06 16.2h1.67L7.6 4.71H5.81l10.66 14.49Z',
  // Threads' own mark, at its published proportions. What was here before was a
  // hand-drawn approximation of it — close enough to recognise and wrong in every
  // detail, which is the one thing a company's logo may not be. This is the glyph
  // Threads publishes, unchanged apart from taking its colour from the page.
  Threads:
    'M12.186 24h-.007c-3.581-.024-6.334-1.205-8.184-3.509C2.35 18.44 1.5 15.586 1.472 12.01v-.017c.03-3.579.879-6.43 2.525-8.482C5.845 1.205 8.6.024 12.18 0h.014c2.746.02 5.043.725 6.826 2.098 1.677 1.29 2.858 3.13 3.509 5.467l-2.04.569c-1.104-3.96-3.898-5.984-8.304-6.015-2.91.022-5.11.936-6.54 2.717C4.307 6.504 3.616 8.914 3.589 12c.027 3.086.718 5.496 2.057 7.164 1.43 1.783 3.631 2.698 6.54 2.717 2.623-.02 4.358-.631 5.8-2.045 1.647-1.613 1.618-3.593 1.09-4.798-.31-.71-.873-1.3-1.634-1.75-.192 1.352-.622 2.446-1.284 3.272-.886 1.102-2.14 1.704-3.73 1.79-1.202.065-2.361-.218-3.259-.801-1.063-.689-1.685-1.74-1.752-2.964-.065-1.19.408-2.285 1.33-3.082.88-.76 2.119-1.207 3.583-1.291a13.853 13.853 0 0 1 3.02.142c-.126-.742-.375-1.332-.75-1.757-.513-.586-1.308-.883-2.359-.89h-.029c-.844 0-1.992.232-2.721 1.32L7.734 7.847c.98-1.454 2.568-2.256 4.478-2.256h.044c3.194.02 5.097 1.975 5.287 5.388.108.046.216.094.321.142 1.49.7 2.58 1.761 3.154 3.07.797 1.82.871 4.79-1.548 7.158-1.85 1.81-4.094 2.628-7.277 2.65Zm1.003-11.69c-.242 0-.487.007-.739.021-1.836.103-2.98.946-2.916 2.143.067 1.256 1.452 1.839 2.784 1.767 1.224-.065 2.818-.543 3.086-3.71a10.5 10.5 0 0 0-2.215-.221Z',
}

function SocialMark({ label }: { label: string }) {
  const path = SOCIAL_GLYPHS[label]
  if (!path) return null
  return (
    <svg viewBox="0 0 24 24" className="size-[18px]" fill="currentColor" aria-hidden="true" focusable="false">
      <path d={path} />
    </svg>
  )
}

/* -------------------------------------------------------------------------- */
/*  Header                                                                     */
/* -------------------------------------------------------------------------- */
export function SiteNav() {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const path = window.location.pathname
  const entry = dashboardEntryHref()
  const signIn = signInHref()

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // An open menu covers the page on a phone, so the page behind it must not scroll —
  // otherwise closing the menu leaves the reader somewhere they never chose to be.
  useEffect(() => {
    if (!menuOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [menuOpen])

  // Escape closes it and returns the focus to the control that opened it. Without the
  // second half, closing the menu drops the keyboard at the top of the document.
  useEffect(() => {
    if (!menuOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setMenuOpen(false)
      toggleRef.current?.focus()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [menuOpen])

  // A menu wider than the phone it was opened on has no reason to stay open once the
  // layout is the desktop one, and leaving it open traps the scroll lock above.
  useEffect(() => {
    const wide = window.matchMedia('(min-width: 1024px)')
    const settle = () => wide.matches && setMenuOpen(false)
    wide.addEventListener('change', settle)
    return () => wide.removeEventListener('change', settle)
  }, [])

  const links = [
    { label: 'How it works', href: '/how-it-works' },
    { label: 'Features', href: '/features' },
    { label: 'Pricing', href: path === '/' ? '#pricing' : '/#pricing' },
    { label: 'FAQ', href: path === '/' ? '#faq' : '/#faq' },
  ]
  const current = (href: string) => href === path

  return (
    <header className={`hm-header ${scrolled ? 'is-scrolled' : ''}`} data-menu-open={menuOpen}>
      <div className="hm-header-bar">
        <TrackedCta
          href="/"
          analyticsName="home_logo"
          analyticsLocation="header"
          aria-label="Hilal Markets home"
          className="block h-[27px] w-[172px] shrink-0 sm:h-[31px] sm:w-[197px]"
        >
          <FigmaLogo />
        </TrackedCta>

        <nav className="hm-header-nav" aria-label="Primary">
          {links.map((link) => (
            <TrackedCta
              key={link.label}
              href={link.href}
              analyticsName={link.label.toLowerCase().replace(/\s+/g, '_')}
              analyticsLocation="header"
              aria-current={current(link.href) ? 'page' : undefined}
              className="hm-header-link"
            >
              {link.label}
            </TrackedCta>
          ))}
        </nav>

        <div className="hm-header-actions">
          <TrackedCta
            href={signIn}
            analyticsName="sign_in"
            analyticsLocation="header"
            className="hm-btn hm-btn--quiet hm-btn--sm"
          >
            Sign in
          </TrackedCta>
          <TrackedCta
            href={entry}
            analyticsName="open_dashboard"
            analyticsLocation="header"
            className="hm-btn hm-btn--primary hm-btn--sm"
          >
            Start free
          </TrackedCta>
        </div>

        <button
          ref={toggleRef}
          type="button"
          className="hm-menu-toggle"
          aria-expanded={menuOpen}
          aria-controls="site-mobile-menu"
          aria-label={menuOpen ? 'Close menu' : 'Open menu'}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {/* Two real rules, so the fold is visible while it happens rather than being
              one glyph swapped for another. */}
          <span className="hm-menu-bars" aria-hidden="true">
            <span />
            <span />
          </span>
        </button>
      </div>

      {/* Kept mounted and hidden, so the panel can animate out as well as in. */}
      <div
        ref={panelRef}
        id="site-mobile-menu"
        className="hm-menu-panel"
        data-open={menuOpen}
        aria-hidden={!menuOpen}
      >
        <nav aria-label="Site" className="hm-menu-links">
          {links.map((link) => (
            <TrackedCta
              key={link.label}
              href={link.href}
              analyticsName={link.label.toLowerCase().replace(/\s+/g, '_')}
              analyticsLocation="mobile_header"
              aria-current={current(link.href) ? 'page' : undefined}
              tabIndex={menuOpen ? undefined : -1}
              onClick={() => setMenuOpen(false)}
            >
              <span>{link.label}</span>
              <Icon name="chevron_right" className="size-4 text-[#5c646e]" />
            </TrackedCta>
          ))}
        </nav>
        <div className="hm-menu-actions">
          <TrackedCta
            href={entry}
            analyticsName="open_dashboard"
            analyticsLocation="mobile_header"
            className="hm-btn hm-btn--primary"
            tabIndex={menuOpen ? undefined : -1}
            onClick={() => setMenuOpen(false)}
          >
            Start free
          </TrackedCta>
          <TrackedCta
            href={signIn}
            analyticsName="sign_in"
            analyticsLocation="mobile_header"
            className="hm-btn hm-btn--quiet"
            tabIndex={menuOpen ? undefined : -1}
            onClick={() => setMenuOpen(false)}
          >
            Sign in
          </TrackedCta>
        </div>
      </div>

      <button
        type="button"
        className="hm-menu-scrim"
        data-open={menuOpen}
        tabIndex={-1}
        aria-hidden="true"
        onClick={() => setMenuOpen(false)}
      >
        <span className="sr-only">Close menu</span>
      </button>
    </header>
  )
}

/* -------------------------------------------------------------------------- */
/*  Back to the top                                                            */
/* -------------------------------------------------------------------------- */
/**
 * The button that takes a reader back to the start of a long page.
 *
 * Written once here rather than copied into each page. It was copied into three of
 * them — the same twenty lines, the same 700px threshold, the same `#top` — and the two
 * longest pages on the site, the home page and Contact, had no copy at all. Three places
 * to change one button, and two places where changing it did nothing.
 *
 * Where it sits on the screen is `.hm-to-top` in `hilalmarkets-public.css`, which both
 * halves of the public site load, so the button and the assistant's own button share one
 * corner without either having to know about the other twice.
 *
 * `after` is the id of the element to put the keyboard back on. Every page on this site
 * marks its opening region `id="top"`, so that is the default and no page has to repeat
 * it.
 */
export function BackToTop({ after = 'top' }: { after?: string } = {}) {
  const scrolled = useScrolledPast(700)
  return (
    <button
      type="button"
      className="hm-to-top hm-no-print"
      data-shown={scrolled}
      aria-hidden={!scrolled}
      tabIndex={scrolled ? 0 : -1}
      onClick={() => {
        window.scrollTo({ top: 0, behavior: 'smooth' })
        document.getElementById(after)?.focus?.()
      }}
    >
      <span className="sr-only">Back to the top</span>
      {/* Turned by the stylesheet, not here. The server-rendered copy of this button
          draws the same arrow from the same icon set and has no Tailwind to turn it
          with, and two places deciding which way it points is two chances to point it
          the wrong way. `.hm-to-top` in `hilalmarkets-public.css` owns it. */}
      <Icon name="arrow" className="size-5" />
    </button>
  )
}

/* -------------------------------------------------------------------------- */
/*  Footer                                                                     */
/* -------------------------------------------------------------------------- */
export function SiteFooter() {
  const chrome = window.HilalMarketsRuntimeConfig?.chrome
  const groups = chrome?.footerGroups?.length ? chrome.footerGroups : FALLBACK_FOOTER_GROUPS
  const social = chrome?.social ?? []
  const cookieSettingsHref = chrome?.cookieSettingsHref || FALLBACK_COOKIE_SETTINGS_HREF
  const year = new Date().getFullYear()

  return (
    <Reveal>
      <footer className="hm-footer">
        <div className="hm-footer-inner">
          <div className="hm-footer-top">
            <div className="hm-footer-brand">
              <a href="/" aria-label="Hilal Markets home" className="hm-footer-logo">
                <FigmaLogo />
              </a>
              <p>
                A platform for Muslim traders to build strategies and monitor setups in
                line with Islamic principles. Not a broker. No trade execution.
              </p>
              {social.length > 0 && (
                <ul className="hm-social">
                  {social.map((channel) => (
                    <li key={channel.label}>
                      <a
                        href={channel.href}
                        className="hm-social-link"
                        target="_blank"
                        rel="me noopener noreferrer"
                        // The mark is the whole message, so the name has to be spoken.
                        aria-label={`Hilal Markets on ${channel.label}`}
                      >
                        <SocialMark label={channel.label} />
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="hm-footer-menus">
              {groups.map((group) => (
                <nav key={group.label} aria-label={group.label}>
                  <h2>{group.label}</h2>
                  <ul>
                    {group.items.map((item) => (
                      <li key={item.href}>
                        <a href={item.href}>{item.label}</a>
                      </li>
                    ))}
                    {group.label === 'Legal' && (
                      <li>
                        {/* A real link, like every other line in this menu.

                            It was a `<button>`, on the reasoning that it only reopens a
                            panel and goes nowhere. But a person reads a footer as a list
                            of places, and one entry that cannot be opened in a new tab,
                            cannot be copied, and does nothing at all when scripting is
                            off is the odd one out. `hilalmarkets-consent.js` catches the
                            click and opens the panel in place; the address is what is
                            left when no script does. */}
                        <a href={cookieSettingsHref} data-cookie-settings>
                          Cookie settings
                        </a>
                      </li>
                    )}
                  </ul>
                </nav>
              ))}
            </div>
          </div>

          <div className="hm-footer-bottom">
            <p>&copy; {year} Hilal Markets. All rights reserved.</p>
            <p>
              Research and monitoring only. Screening follows a published methodology and
              does not replace qualified advice.
            </p>
          </div>
        </div>
      </footer>
    </Reveal>
  )
}

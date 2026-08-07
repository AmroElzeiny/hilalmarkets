import { useEffect, useState } from 'react'
import FigmaLogo from '../imports/Frame-1'
import Component10Footer from '../imports/10Footer-1'
import { Reveal } from './Reveal'
import { TrackedCta } from './Tracking'

export function SiteNav() {
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [activeHash, setActiveHash] = useState(window.location.hash)
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  useEffect(() => {
    const updateHash = () => setActiveHash(window.location.hash)
    window.addEventListener('hashchange', updateHash)
    return () => window.removeEventListener('hashchange', updateHash)
  }, [])
  useEffect(() => {
    if (!menuOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [menuOpen])

  const onLanding = window.location.pathname === '/'
  // Hilal Markets is invite-only while the private beta runs, so the header offers the
  // waitlist and nothing else. There is no Pricing entry because there is nothing to
  // buy yet, and no account entry because an account cannot be opened from here.
  const links = [
    { label: 'How it works', target: '#how-it-works' },
    { label: 'Features', target: '#features' },
    { label: 'FAQ', target: '#faq' },
  ]
  const waitlistHref = `${onLanding ? '' : '/'}#waitlist`
  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-all duration-300 ${
        scrolled ? 'py-2.5 backdrop-blur-md' : 'py-4'
      }`}
    >
      <div className="relative mx-auto flex max-w-[1360px] items-center justify-between gap-3 px-5">
        <TrackedCta
          href="/"
          analyticsName="home_logo"
          analyticsLocation="header"
          aria-label="Hilal Markets home"
          className="block h-[27px] w-[172px] shrink-0 sm:h-[31px] sm:w-[197px]"
        >
          <FigmaLogo />
        </TrackedCta>

        <nav
          className="hidden items-center gap-1 rounded-full border border-[#e1e5ea] bg-surface/90 px-2 py-1.5 shadow-[0_14px_34px_-22px_rgba(43,46,53,0.6)] backdrop-blur lg:flex"
          aria-label="Primary navigation"
        >
          {links.map((link) => (
            <TrackedCta
              key={link.label}
              href={`${onLanding ? '' : '/'}${link.target}`}
              analyticsName={link.label.toLowerCase().replace(/\s+/g, '_')}
              analyticsLocation="header"
              aria-current={activeHash === link.target ? 'location' : undefined}
              className={`rounded-full px-4 py-2 text-[15px] font-medium text-ink transition-colors hover:bg-[#f1f4f7] active:bg-[#e1e5ea] focus-visible:outline focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[#55712a] ${
                activeHash === link.target ? 'bg-[#eef1f4]' : ''
              }`}
            >
              {link.label}
            </TrackedCta>
          ))}
        </nav>

        <div className="hidden items-center gap-2 lg:flex">
          <TrackedCta
            href={waitlistHref}
            analyticsName="join_waitlist"
            analyticsLocation="header"
            className="rounded-full bg-apple px-6 py-2.5 font-sans text-[15px] font-bold text-[#2b2e35] shadow-[0_12px_28px_-16px_rgba(120,170,40,0.7)] transition-transform hover:-translate-y-0.5"
          >
            Join the waitlist
          </TrackedCta>
        </div>
        <button
          type="button"
          className="site-menu-toggle lg:hidden"
          aria-expanded={menuOpen}
          aria-controls="site-mobile-menu"
          onClick={() => setMenuOpen((current) => !current)}
        >
          <span>{menuOpen ? 'Close' : 'Menu'}</span>
        </button>
        <nav
          id="site-mobile-menu"
          className={`site-mobile-menu lg:hidden ${menuOpen ? 'is-open' : ''}`}
          aria-label="Mobile navigation"
          hidden={!menuOpen}
        >
          {links.map((link) => (
            <TrackedCta
              key={link.label}
              href={`${onLanding ? '' : '/'}${link.target}`}
              analyticsName={link.label.toLowerCase().replace(/\s+/g, '_')}
              analyticsLocation="mobile_header"
              aria-current={activeHash === link.target ? 'location' : undefined}
              onClick={() => setMenuOpen(false)}
            >
              {link.label}
            </TrackedCta>
          ))}
          <TrackedCta
            href={waitlistHref}
            analyticsName="join_waitlist"
            analyticsLocation="mobile_header"
            className="site-mobile-primary"
            onClick={() => setMenuOpen(false)}
          >
            Join the waitlist
          </TrackedCta>
        </nav>
      </div>
    </header>
  )
}

export function SiteFooter() {
  return (
    <Reveal>
      <div className="flex w-full justify-center overflow-hidden bg-[#2b2e35]">
        <div className="w-full">
          <Component10Footer />
        </div>
      </div>
    </Reveal>
  )
}

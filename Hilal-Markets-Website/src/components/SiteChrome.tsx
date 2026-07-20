import { useEffect, useState } from 'react'
import FigmaLogo from '../imports/Frame-1'
import Component10Footer from '../imports/10Footer-1'
import { Reveal } from './Reveal'
import { TrackedCta } from './Tracking'

export function SiteNav() {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const onLanding = window.location.pathname === '/'
  const links = [
    { label: 'How it works', target: '#how-it-works' },
    { label: 'Features', target: '#features' },
    { label: 'FAQ', target: '#faq' },
  ]
  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-all duration-300 ${
        scrolled ? 'py-2.5 backdrop-blur-md' : 'py-4'
      }`}
    >
      <div className="mx-auto flex max-w-[1360px] items-center justify-between px-5">
        <TrackedCta
          href="/"
          analyticsName="home_logo"
          analyticsLocation="header"
          aria-label="Hilal Markets home"
          className="block h-[31px] w-[197px]"
        >
          <FigmaLogo />
        </TrackedCta>

        <nav
          className="hidden items-center gap-1 rounded-full border border-[#e1e5ea] bg-surface/90 px-2 py-1.5 shadow-[0_14px_34px_-22px_rgba(43,46,53,0.6)] backdrop-blur md:flex"
          aria-label="Primary navigation"
        >
          {links.map((link) => (
            <TrackedCta
              key={link.label}
              href={`${onLanding ? '' : '/'}${link.target}`}
              analyticsName={link.label.toLowerCase().replace(/\s+/g, '_')}
              analyticsLocation="header"
              className="rounded-full px-4 py-2 text-[15px] font-medium text-ink transition-colors hover:bg-[#f1f4f7]"
            >
              {link.label}
            </TrackedCta>
          ))}
        </nav>

        <TrackedCta
          href={`${onLanding ? '' : '/'}#waitlist`}
          analyticsName="join_waitlist"
          analyticsLocation="header"
          className="rounded-full bg-apple px-4 py-2 font-sans text-[15px] font-bold text-[#2b2e35] shadow-[0_12px_28px_-16px_rgba(120,170,40,0.7)] transition-transform hover:-translate-y-0.5 sm:px-6 sm:py-2.5"
        >
          Join the waitlist
        </TrackedCta>
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

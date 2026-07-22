import svgPaths from './svg-qp93h2wewm'
import { TrackedCta } from '../../components/Tracking'

function HilalMarketsLogoReversed() {
  return (
    <div className="h-[51px] w-[194px] max-w-full" data-name="Hilal Markets logo / reversed">
      <svg className="block h-full w-full" fill="none" preserveAspectRatio="xMinYMid meet" viewBox="10.6304 0 193.602 51" role="img" aria-label="Hilal Markets">
        <g>
          <path d={svgPaths.p1b468e00} fill="white" />
          <path d={svgPaths.p28519b80} fill="white" />
          <path d={svgPaths.p11f6f180} fill="white" />
          <path d={svgPaths.p1ed76000} fill="white" />
          <path d={svgPaths.p2e613d80} fill="white" />
          <path d={svgPaths.p27c619f0} fill="white" />
          <path d={svgPaths.p20d72280} fill="white" />
          <path d={svgPaths.p3004b700} fill="white" />
          <path d={svgPaths.p3171d800} fill="white" />
          <path d={svgPaths.pb88400} fill="white" />
          <path d={svgPaths.p342d9600} fill="white" />
          <path d={svgPaths.p27e80b00} fill="white" />
          <path d={svgPaths.p26957df0} fill="white" />
          <path d={svgPaths.p3a8b69c0} fill="white" />
          <path d={svgPaths.p2c619500} fill="white" />
          <path d={svgPaths.p24549400} fill="white" />
          <path d={svgPaths.p275b0180} fill="white" />
        </g>
      </svg>
    </div>
  )
}

export default function Component10Footer() {
  return (
    <footer className="w-full bg-[#2b2e35] py-[4%]" data-name="10 - Footer">
      <div className="mx-auto flex w-[90%] max-w-[1248px] flex-col gap-10 sm:w-[86.67%]">
        <div className="flex flex-col items-start justify-between gap-9 md:flex-row md:gap-[6%]" data-name="Footer top">
          <div className="flex max-w-[620px] flex-col items-start gap-3.5" data-name="Footer brand">
            <HilalMarketsLogoReversed />
            <p className="max-w-[590px] text-[14px] leading-[1.5] text-[#b0bac9]">
              A platform for Muslim traders to build strategies and monitor setups in line with Islamic principles. Not a broker. No trade execution.
            </p>
          </div>
          <nav className="flex flex-wrap items-center gap-x-8 gap-y-3 text-[13px] font-medium text-white" aria-label="Footer navigation">
            <TrackedCta analyticsName="privacy" analyticsLocation="footer" href="/privacy" className="text-white transition-opacity hover:opacity-75">Privacy Policy</TrackedCta>
            <TrackedCta analyticsName="terms" analyticsLocation="footer" href="/terms" className="text-white transition-opacity hover:opacity-75">Terms of Use</TrackedCta>
            <TrackedCta analyticsName="how_we_screen" analyticsLocation="footer" href="/how-we-screen" className="text-white transition-opacity hover:opacity-75">How We Screen</TrackedCta>
            <TrackedCta analyticsName="contact" analyticsLocation="footer" href="/contact" className="text-white transition-opacity hover:opacity-75">Contact</TrackedCta>
          </nav>
        </div>
        <div className="h-px w-full bg-[#525763]" data-name="Divider" />
        <p className="text-[12px] leading-[1.5] text-[#b0bac9]">&copy; Hilal Markets. All rights reserved.</p>
      </div>
    </footer>
  )
}

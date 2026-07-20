import svgPaths from "./svg-nfwtsg6pkj";

function HilalMarketsLogoReversed() {
  return (
    <div className="h-[51px] relative shrink-0 w-[215px]" data-name="Hilal Markets logo / reversed">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 215 51">
        <g id="Hilal Markets logo / reversed">
          <path d={svgPaths.p1b468e00} fill="var(--fill-0, white)" id="Vector" />
          <path d={svgPaths.p28519b80} fill="var(--fill-0, white)" id="Vector_2" />
          <path d={svgPaths.p11f6f180} fill="var(--fill-0, white)" id="Vector_3" />
          <path d={svgPaths.p1ed76000} fill="var(--fill-0, white)" id="Vector_4" />
          <path d={svgPaths.p2e613d80} fill="var(--fill-0, white)" id="Vector_5" />
          <path d={svgPaths.p27c619f0} fill="var(--fill-0, white)" id="Vector_6" />
          <path d={svgPaths.p20d72280} fill="var(--fill-0, white)" id="Vector_7" />
          <path d={svgPaths.p3004b700} fill="var(--fill-0, white)" id="Vector_8" />
          <path d={svgPaths.p3171d800} fill="var(--fill-0, white)" id="Vector_9" />
          <path d={svgPaths.pb88400} fill="var(--fill-0, white)" id="Vector_10" />
          <path d={svgPaths.p342d9600} fill="var(--fill-0, white)" id="Vector_11" />
          <path d={svgPaths.p27e80b00} fill="var(--fill-0, white)" id="Vector_12" />
          <path d={svgPaths.p26957df0} fill="var(--fill-0, white)" id="Vector_13" />
          <path d={svgPaths.p3a8b69c0} fill="var(--fill-0, white)" id="Vector_14" />
          <path d={svgPaths.p2c619500} fill="var(--fill-0, white)" id="Vector_15" />
          <path d={svgPaths.p24549400} fill="var(--fill-0, white)" id="Vector_16" />
          <path d={svgPaths.p275b0180} fill="var(--fill-0, white)" id="Vector_17" />
        </g>
      </svg>
    </div>
  );
}

function FooterBrand() {
  return (
    <div className="content-stretch flex flex-col gap-[14px] items-start overflow-clip relative shrink-0 w-[620px]" data-name="Footer brand">
      <HilalMarketsLogoReversed />
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#b0bac9] text-[14px] w-[590px]">A platform for Muslim traders to build strategies and monitor setups in line with Islamic principles. Not a broker. No trade execution.</p>
    </div>
  );
}

function FooterLinks() {
  return (
    <div className="[word-break:break-word] content-stretch flex font-['Onest:Medium',sans-serif] font-medium gap-[34px] items-start leading-[1.5] overflow-clip relative shrink-0 text-[13px] text-white whitespace-nowrap" data-name="Footer links">
      <p className="relative shrink-0">Privacy Policy</p>
      <p className="relative shrink-0">Terms of Use</p>
      <p className="relative shrink-0">Contact</p>
    </div>
  );
}

function FooterTop() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 w-[1248px]" data-name="Footer top">
      <FooterBrand />
      <FooterLinks />
    </div>
  );
}

function FooterBottom() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 w-[1248px]" data-name="Footer bottom">
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#b0bac9] text-[12px] whitespace-nowrap">© Hilal Markets. All rights reserved.</p>
    </div>
  );
}

export default function Component10Footer() {
  return (
    <div className="bg-[#2b2e35] content-stretch flex flex-col gap-[42px] items-start pb-[42px] pt-[52px] px-[96px] relative size-full" data-name="10 — Footer">
      <FooterTop />
      <div className="bg-[#525763] h-px relative shrink-0 w-[1248px]" data-name="Divider" />
      <FooterBottom />
    </div>
  );
}
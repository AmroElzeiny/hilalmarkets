import svgPaths from "./svg-io2ihosp47";

function Text() {
  return (
    <div className="content-stretch flex gap-[6px] h-[18.594px] items-center justify-center relative shrink-0" data-name="Text">
      <div className="h-[3.5px] relative shrink-0 w-[5.5px]">
        <div className="absolute inset-[-10.1%_-6.43%_-20.2%_-6.43%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 6.20711 4.56066">
            <path d={svgPaths.p2c81dd00} id="Vector 16" stroke="url(#paint0_linear_1_178)" />
            <defs>
              <linearGradient gradientUnits="userSpaceOnUse" id="paint0_linear_1_178" x1="3.10355" x2="3.10355" y1="0.353553" y2="3.85355">
                <stop stopColor="#262626" />
                <stop offset="1" stopColor="#262626" stopOpacity="0.5" />
              </linearGradient>
            </defs>
          </svg>
        </div>
      </div>
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#2b2e35] text-[14px] whitespace-nowrap">Crypto spot only</p>
    </div>
  );
}

function Text1() {
  return (
    <div className="content-stretch flex gap-[6px] h-[18.594px] items-center justify-center relative shrink-0" data-name="Text">
      <div className="h-[3.5px] relative shrink-0 w-[5.5px]">
        <div className="absolute inset-[-10.1%_-6.43%_-20.2%_-6.43%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 6.20711 4.56066">
            <path d={svgPaths.p2c81dd00} id="Vector 16" stroke="url(#paint0_linear_1_178)" />
            <defs>
              <linearGradient gradientUnits="userSpaceOnUse" id="paint0_linear_1_178" x1="3.10355" x2="3.10355" y1="0.353553" y2="3.85355">
                <stop stopColor="#262626" />
                <stop offset="1" stopColor="#262626" stopOpacity="0.5" />
              </linearGradient>
            </defs>
          </svg>
        </div>
      </div>
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#2b2e35] text-[14px] whitespace-nowrap">No execution</p>
    </div>
  );
}

function Text2() {
  return (
    <div className="content-stretch flex gap-[6px] h-[18.594px] items-center justify-center relative shrink-0" data-name="Text">
      <div className="h-[3.5px] relative shrink-0 w-[5.5px]">
        <div className="absolute inset-[-10.1%_-6.43%_-20.2%_-6.43%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 6.20711 4.56066">
            <path d={svgPaths.p2c81dd00} id="Vector 16" stroke="url(#paint0_linear_1_178)" />
            <defs>
              <linearGradient gradientUnits="userSpaceOnUse" id="paint0_linear_1_178" x1="3.10355" x2="3.10355" y1="0.353553" y2="3.85355">
                <stop stopColor="#262626" />
                <stop offset="1" stopColor="#262626" stopOpacity="0.5" />
              </linearGradient>
            </defs>
          </svg>
        </div>
      </div>
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#2b2e35] text-[14px] whitespace-nowrap">Methodology-specific status</p>
    </div>
  );
}

function Frame6() {
  return (
    <div className="content-stretch flex gap-[24px] items-center opacity-90 relative shrink-0">
      <Text />
      <Text1 />
      <Text2 />
    </div>
  );
}

function Frame1() {
  return (
    <div className="[word-break:break-word] content-stretch flex flex-col gap-[12px] items-center justify-center relative shrink-0 text-[#2b2e35] text-center w-full">
      <p className="font-['Geometria:Medium',sans-serif] leading-none not-italic relative shrink-0 text-[52px] tracking-[-2px] w-full">Halal-conscious crypto monitoring, built around evidence</p>
      <p className="font-['Onest:Regular',sans-serif] font-normal leading-[1.4] relative shrink-0 text-[18px] w-full">A platform that helps Muslim traders monitor the market, build setups, and track them in line with Islamic principles.</p>
    </div>
  );
}

function Link() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center px-[49px] py-[19px] relative rounded-[100px] shrink-0" data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(0,0,0,0)] border-solid inset-0 pointer-events-none rounded-[100px]" />
      <div className="[word-break:break-word] flex flex-col font-['Geometria:Medium',sans-serif] justify-end leading-[0] not-italic relative shrink-0 text-[#2b2e35] text-[16px] whitespace-nowrap">
        <p className="leading-none">Start Scanning</p>
      </div>
    </div>
  );
}

function Link1() {
  return (
    <div className="content-stretch flex items-center justify-center min-h-[50px] px-[22px] relative rounded-[13px] shrink-0" data-name="Link">
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[#262626] text-[16px] whitespace-nowrap">See how it works</p>
    </div>
  );
}

function Frame3() {
  return (
    <div className="content-stretch flex gap-[12px] items-center justify-center relative shrink-0 w-full">
      <Link />
      <Link1 />
    </div>
  );
}

function Frame2() {
  return (
    <div className="-translate-x-1/2 -translate-y-1/2 absolute content-stretch flex flex-col gap-[32px] items-center left-1/2 top-[calc(50%-87.5px)] w-[800px]">
      <Frame6 />
      <Frame1 />
      <Frame3 />
    </div>
  );
}

function Frame() {
  return (
    <div className="-translate-y-1/2 absolute h-[54px] left-[20px] top-1/2 w-[228px]" data-name="Frame">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 228 54">
        <g id="Frame">
          <path d={svgPaths.p2905c580} fill="var(--fill-0, #2B2E35)" id="Vector" />
          <path d={svgPaths.p18b90280} fill="var(--fill-0, #2B2E35)" id="Vector_2" />
          <path d={svgPaths.p16f82f80} fill="var(--fill-0, #2B2E35)" id="Vector_3" />
          <path d={svgPaths.p27bd800} fill="var(--fill-0, #2B2E35)" id="Vector_4" />
          <path d={svgPaths.p236a2d00} fill="var(--fill-0, #2B2E35)" id="Vector_5" />
          <path d={svgPaths.p2b45db30} fill="var(--fill-0, #2B2E35)" id="Vector_6" />
          <path d={svgPaths.p34103900} fill="var(--fill-0, #2B2E35)" id="Vector_7" />
          <path d={svgPaths.p3e1af780} fill="var(--fill-0, #2B2E35)" id="Vector_8" />
          <path d={svgPaths.p1d940000} fill="var(--fill-0, #2B2E35)" id="Vector_9" />
          <path d={svgPaths.p3cd7f700} fill="var(--fill-0, #2B2E35)" id="Vector_10" />
          <path d={svgPaths.p25581b80} fill="var(--fill-0, #2B2E35)" id="Vector_11" />
          <path d={svgPaths.p29082200} fill="var(--fill-0, #2B2E35)" id="Vector_12" />
          <path d={svgPaths.pf6e580} fill="var(--fill-0, #2B2E35)" id="Vector_13" />
          <path d={svgPaths.p12a0d300} fill="var(--fill-0, #2B2E35)" id="Vector_14" />
          <path d={svgPaths.p1508b900} fill="var(--fill-0, #2B2E35)" id="Vector_15" />
          <path d={svgPaths.p7cec180} fill="var(--fill-0, #2B2E35)" id="Vector_16" />
          <path d={svgPaths.p3b12b600} fill="var(--fill-0, #2B2E35)" id="Vector_17" />
        </g>
      </svg>
    </div>
  );
}

function Link2() {
  return (
    <div className="-translate-y-1/2 absolute bg-[#2b2e35] content-stretch flex items-center justify-center left-[1355px] px-[25px] py-[17px] rounded-[100px] top-1/2 w-[137px]" data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(0,0,0,0)] border-solid inset-0 pointer-events-none rounded-[100px]" />
      <div className="[word-break:break-word] flex flex-col font-['Geometria:Bold',sans-serif] justify-end leading-[0] not-italic relative shrink-0 text-[16px] text-white whitespace-nowrap">
        <p className="leading-none">Dashboard</p>
      </div>
    </div>
  );
}

function Frame5() {
  return (
    <div className="-translate-x-1/2 -translate-y-1/2 [word-break:break-word] absolute content-stretch flex font-['Geometria:Medium',sans-serif] gap-[40px] items-start leading-[24.8px] left-1/2 not-italic text-[#19191b] text-[16px] top-[calc(50%-0.5px)] whitespace-nowrap">
      <p className="relative shrink-0">Pricing</p>
      <p className="relative shrink-0">Features</p>
      <p className="relative shrink-0">How it works</p>
      <p className="relative shrink-0">FAQ</p>
    </div>
  );
}

function Frame12() {
  return (
    <div className="-translate-x-1/2 -translate-y-1/2 absolute bg-white h-[50px] left-1/2 overflow-clip rounded-[100px] top-1/2 w-[434px]">
      <Frame5 />
    </div>
  );
}

function Frame4() {
  return (
    <div className="absolute h-[90px] left-0 top-0 w-[1512px]">
      <Frame />
      <Link2 />
      <Frame12 />
    </div>
  );
}

function Group1() {
  return (
    <div className="absolute inset-[69.42%_87.93%_28.04%_11.13%]" data-name="Group">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 14.098 22.8">
        <g id="Group">
          <path d={svgPaths.pc202800} fill="var(--fill-0, #627EEA)" id="Vector" />
        </g>
      </svg>
    </div>
  );
}

function Frame10() {
  return (
    <div className="absolute bg-[#cbfa4d] content-stretch flex gap-[2.854px] inset-[69.27%_72.68%_29.09%_24.59%] items-center opacity-60 px-[7.134px] py-[2.854px] rounded-[71.341px]">
      <div className="relative shrink-0 size-[7.134px]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 7.13406 7.13406">
          <path d={svgPaths.p466280} fill="var(--fill-0, #2B2E35)" id="Vector" />
        </svg>
      </div>
      <p className="[word-break:break-word] font-['Onest:Bold',sans-serif] font-bold leading-[normal] lowercase relative shrink-0 text-[#253018] text-[7.134px] text-center whitespace-nowrap">HALAL</p>
    </div>
  );
}

function Frame9() {
  return (
    <div className="absolute bg-[#cbfa4d] content-stretch flex gap-[3.579px] items-center left-[371px] opacity-40 px-[8.947px] py-[3.579px] rounded-[89.474px] top-[658.11px]">
      <div className="relative shrink-0 size-[8.947px]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 8.94737 8.94737">
          <path d={svgPaths.p2e23d100} fill="var(--fill-0, #2B2E35)" id="Vector" />
        </svg>
      </div>
      <p className="[word-break:break-word] font-['Onest:Bold',sans-serif] font-bold leading-[normal] lowercase relative shrink-0 text-[#253018] text-[8.947px] text-center whitespace-nowrap">HALAL</p>
    </div>
  );
}

function Group2() {
  return (
    <div className="absolute inset-[73.66%_88.41%_23.53%_9.92%]" data-name="Group">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 25.2 25.2">
        <g id="Group">
          <path d={svgPaths.p3a0bd00} fill="var(--fill-0, #345D9D)" id="Vector" />
        </g>
      </svg>
    </div>
  );
}

function Group3() {
  return (
    <div className="absolute inset-[78.35%_89.69%_18.09%_8.2%]" data-name="Group">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 31.9146 31.9202">
        <g id="Group">
          <path d={svgPaths.p1a62b80} fill="var(--fill-0, #F7931A)" id="Vector" />
        </g>
      </svg>
    </div>
  );
}

function Group4() {
  return (
    <div className="absolute inset-[70.98%_25.6%_26.12%_72.69%]" data-name="Group">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 25.92 25.92">
        <g id="Group">
          <path d={svgPaths.p35cb5340} fill="var(--fill-0, #2AABEE)" id="Vector" />
        </g>
      </svg>
    </div>
  );
}

function Group5() {
  return (
    <div className="absolute inset-[79.69%_26.97%_17.15%_71.17%]" data-name="Group">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 28.1855 28.32">
        <g id="Group">
          <path d={svgPaths.p2d888a00} fill="var(--fill-0, #25D366)" id="Vector" />
        </g>
      </svg>
    </div>
  );
}

function Frame8() {
  return (
    <div className="absolute bg-[#cbfa4d] content-stretch flex items-center justify-center left-[1115px] px-[6px] py-[2px] rounded-[100px] top-[697.5px]">
      <p className="[word-break:break-word] font-['Onest:SemiBold',sans-serif] font-semibold leading-[normal] lowercase relative shrink-0 text-[#46551b] text-[9.5px] tracking-[-0.19px] whitespace-nowrap">READY FOR REVIEW</p>
    </div>
  );
}

function Group6() {
  return (
    <div className="absolute inset-[88.84%_24.67%_8.27%_73.61%]" data-name="Group">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 25.92 25.92">
        <g id="Group">
          <path d={svgPaths.p35cb5340} fill="var(--fill-0, #2AABEE)" id="Vector" />
        </g>
      </svg>
    </div>
  );
}

function Frame11() {
  return (
    <div className="absolute bg-[#cbfa4d] content-stretch flex gap-[4px] items-center left-[374px] px-[10px] py-[4px] rounded-[100px] top-[706px]">
      <div className="relative shrink-0 size-[10px]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 10 10">
          <path d={svgPaths.p2ffed000} fill="var(--fill-0, #2B2E35)" id="Vector" />
        </svg>
      </div>
      <p className="[word-break:break-word] font-['Onest:Bold',sans-serif] font-bold leading-[normal] lowercase relative shrink-0 text-[#253018] text-[10px] text-center whitespace-nowrap">HALAL</p>
    </div>
  );
}

function Frame7() {
  return (
    <div className="absolute inset-[76.45%_63.76%_20.65%_34.52%]">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 26 26">
        <g id="Frame 33">
          <g clipPath="url(#clip0_1_171)">
            <path d={svgPaths.p1a8d6180} fill="var(--fill-0, #2B2E35)" id="Vector" />
            <circle cx="12.5" cy="22.5" fill="var(--fill-0, #CBFA4D)" id="Ellipse 2" r="6.5" />
            <circle cx="13" cy="11" fill="var(--fill-0, #CBFA4D)" id="Ellipse 1" r="4" />
          </g>
        </g>
        <defs>
          <clipPath id="clip0_1_171">
            <rect fill="white" height="26" rx="13" width="26" />
          </clipPath>
        </defs>
      </svg>
    </div>
  );
}

function Group() {
  return (
    <div className="absolute contents inset-[67.3%_6.61%_5.8%_6.61%]" data-name="Group">
      <div className="absolute inset-[67.3%_71.43%_24.89%_9.66%]" data-name="Vector">
        <div className="absolute inset-[-0.71%_-0.17%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 287 71">
            <path d={svgPaths.p284ad500} fill="var(--fill-0, #F7F9FB)" id="Vector" opacity="0.8" stroke="var(--stroke-0, #E1E5EA)" />
          </svg>
        </div>
      </div>
      <Group1 />
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[69.64%_82.14%_28.35%_13.03%] leading-[normal] opacity-80 text-[#25282e] text-[14px] whitespace-nowrap">ETH/USDT</p>
      <Frame10 />
      <div className="absolute inset-[71.21%_70.83%_20.09%_8.66%]" data-name="Vector">
        <div className="absolute inset-[-0.64%_-0.16%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 311 79">
            <path d={svgPaths.p35ae6300} fill="var(--fill-0, #FAFBFC)" id="Vector" stroke="var(--stroke-0, #D9DEE5)" />
          </svg>
        </div>
      </div>
      <Frame9 />
      <Group2 />
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[74.11%_83%_23.88%_12.24%] leading-[normal] text-[#25282e] text-[14px] whitespace-nowrap">LTC/USDT</p>
      <div className="absolute inset-[75.67%_70.17%_5.8%_6.61%]" data-name="Vector">
        <div className="absolute inset-[-0.42%_-0.2%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 352.4 167.4">
            <path d={svgPaths.p5783a00} fill="var(--fill-0, white)" id="Vector" stroke="var(--stroke-0, #D0D6DE)" strokeWidth="1.4" />
          </svg>
        </div>
      </div>
      <Group3 />
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[78.79%_82.67%_18.64%_11.11%] leading-[normal] text-[#191b1f] text-[18px] whitespace-nowrap">BTC/USDT</p>
      <div className="absolute inset-[83.26%_71.49%_16.74%_8.2%]" data-name="Vector">
        <div className="absolute inset-[-0.5px_0]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 307 1">
            <path d="M0 0.5H307" id="Vector" stroke="var(--stroke-0, #E6E9ED)" />
          </svg>
        </div>
      </div>
      <div className="absolute inset-[85.16%_90.74%_13.06%_8.2%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 16 16">
          <path d={svgPaths.p30769300} fill="var(--fill-0, #E7F5CE)" id="Vector" />
        </svg>
      </div>
      <div className="absolute inset-[85.83%_91.01%_13.62%_8.53%]" data-name="Vector">
        <div className="absolute inset-[-14%_-10%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 8.40001 6.40001">
            <path d={svgPaths.p2f1b4ea0} id="Vector" stroke="var(--stroke-0, #55712A)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.4" />
          </svg>
        </div>
      </div>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[84.49%_83.33%_13.84%_9.92%] leading-[normal] text-[#292d33] text-[12px]">Evidence verified</p>
      <p className="[word-break:break-word] absolute font-['Onest:Regular',sans-serif] font-normal inset-[86.5%_79.89%_12.05%_9.92%] leading-[normal] text-[#7a8089] text-[10.5px]">SC Malaysia SAC · 20 Jul 2020</p>
      <div className="absolute inset-[87.72%_71.49%_8.48%_8.2%]" data-name="Vector">
        <div className="absolute inset-[-4.41%_-0.49%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 310 37.0004">
            <path d={svgPaths.p36962a80} id="Vector" stroke="var(--stroke-0, #A3D83F)" strokeLinecap="round" strokeWidth="3" />
          </svg>
        </div>
      </div>
      <div className="absolute inset-[87.22%_71.2%_11.77%_28.21%]" data-name="Vector">
        <div className="absolute inset-[-6.67%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 10.2 10.2">
            <path d={svgPaths.p17d5fb80} fill="var(--fill-0, #CBFA4D)" id="Vector" stroke="var(--stroke-0, #2B2E35)" strokeWidth="1.2" />
          </svg>
        </div>
      </div>
      <div className="absolute inset-[81.25%_68.29%_18.75%_29.93%]" data-name="Vector">
        <div className="absolute inset-[-1.25px_-4.63%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 29.5 2.5">
            <g id="Vector">
              <path d="M1.25 1.25H28.25Z" fill="var(--fill-0, black)" />
              <path d="M1.25 1.25H28.25" stroke="var(--stroke-0, #A8D936)" strokeLinecap="round" strokeWidth="2.5" />
            </g>
          </svg>
        </div>
      </div>
      <div className="absolute inset-[80.75%_69.78%_18.25%_29.63%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 9 9">
          <path d={svgPaths.p3488a200} fill="var(--fill-0, #CBFA4D)" id="Vector" />
        </svg>
      </div>
      <div className="absolute inset-[80.69%_67.89%_18.19%_31.65%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 7 10">
          <path d="M0 0L7 5L0 10V0Z" fill="var(--fill-0, #A8D936)" id="Vector" />
        </svg>
      </div>
      <div className="absolute inset-[68.08%_32.41%_6.7%_32.41%]" data-name="Vector">
        <div className="absolute inset-[-0.33%_-0.14%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 533.5 227.5">
            <path d={svgPaths.p339f7480} fill="var(--fill-0, white)" id="Vector" stroke="var(--stroke-0, #C8CDD5)" strokeWidth="1.5" />
          </svg>
        </div>
      </div>
      <div className="absolute inset-[68.69%_67.13%_7.42%_32.41%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 7 214">
          <path d={svgPaths.p8abfff0} fill="var(--fill-0, #CBFA4D)" id="Vector" />
        </svg>
      </div>
      <div className="absolute inset-[70.65%_64.15%_26.23%_33.99%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 28 28">
          <path d={svgPaths.pc390800} fill="var(--fill-0, #CBFA4D)" id="Vector" />
        </svg>
      </div>
      <div className="absolute inset-[71.43%_64.62%_27.01%_34.46%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 14 14">
          <path d={svgPaths.p23541bf2} fill="var(--fill-0, #2B2E35)" id="Vector" />
        </svg>
      </div>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[71.21%_49.47%_26.9%_36.38%] leading-[normal] text-[#202329] text-[13px] whitespace-nowrap">Describe what you want to watch</p>
      <div className="absolute inset-[70.2%_33.99%_27.12%_58.86%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 108 24">
          <path d={svgPaths.p1cf2c600} fill="var(--fill-0, #EDF1F4)" id="Vector" />
        </svg>
      </div>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[70.98%_35.55%_27.79%_60.42%] leading-[normal] text-[#656b74] text-[9px] text-center whitespace-nowrap">screened only</p>
      <div className="absolute inset-[75.56%_33.99%_14.17%_33.99%]" data-name="Vector">
        <div className="absolute inset-[-0.54%_-0.1%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 485 93">
            <path d={svgPaths.paf87600} fill="var(--fill-0, #F2F5F7)" id="Vector" stroke="var(--stroke-0, #E0E4E9)" />
          </svg>
        </div>
      </div>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[76.62%_46.36%_21.48%_36.84%] leading-[normal] text-[#23262c] text-[13.5px] whitespace-nowrap">Alert me when a screened coin sweeps</p>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[78.63%_48.61%_19.48%_36.84%] leading-[normal] text-[#23262c] text-[13.5px] whitespace-nowrap">yesterday’s low and recovers 5%.</p>
      <div className="absolute inset-[87.5%_58.07%_9.38%_33.99%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 120 28">
          <path d={svgPaths.p1f1e9800} fill="var(--fill-0, #CBFA4D)" id="Vector" />
        </svg>
      </div>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[88.28%_60.15%_10.04%_36.08%] leading-[normal] text-[#263018] text-[12px] text-center whitespace-nowrap">Build plan</p>
      <div className="absolute inset-[81.25%_30.59%_18.75%_67.63%]" data-name="Vector">
        <div className="absolute inset-[-1.25px_-4.63%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 29.5 2.5">
            <g id="Vector">
              <path d="M1.25 1.25H28.25Z" fill="var(--fill-0, black)" />
              <path d="M1.25 1.25H28.25" stroke="var(--stroke-0, #A8D936)" strokeLinecap="round" strokeWidth="2.5" />
            </g>
          </svg>
        </div>
      </div>
      <div className="absolute inset-[80.75%_32.08%_18.25%_67.33%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 9 9">
          <path d={svgPaths.p3488a200} fill="var(--fill-0, #CBFA4D)" id="Vector" />
        </svg>
      </div>
      <div className="absolute inset-[80.69%_30.19%_18.19%_69.35%]" data-name="Vector">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 7 10">
          <path d="M0 0L7 5L0 10V0Z" fill="var(--fill-0, #A8D936)" id="Vector" />
        </svg>
      </div>
      <div className="absolute inset-[68.75%_6.61%_24.11%_71.43%]" data-name="Vector">
        <div className="absolute inset-[-0.78%_-0.15%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 333 65">
            <path d={svgPaths.p12122580} fill="var(--fill-0, white)" id="Vector" stroke="var(--stroke-0, #D9DEE5)" />
          </svg>
        </div>
      </div>
      <Group4 />
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[70.37%_21.89%_28.29%_75.07%] leading-[normal] text-[#2a8fc3] text-[9.5px] whitespace-nowrap">FORMING</p>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[72.32%_10.38%_25.78%_75.07%] leading-[normal] text-[#191b1f] text-[13px] whitespace-nowrap">BTC/USDT recovery: +2.3% of +5%</p>
      <div className="absolute inset-[77.01%_6.61%_14.51%_70.04%]" data-name="Vector">
        <div className="absolute inset-[-1.12%_-0.24%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 354.7 77.7">
            <path d={svgPaths.p29ad9e00} fill="var(--fill-0, white)" id="Vector" stroke="var(--stroke-0, #A9D83F)" strokeWidth="1.7" />
          </svg>
        </div>
      </div>
      <Group5 />
      <Frame8 />
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[80.58%_12.7%_17.41%_73.74%] leading-[normal] text-[#191b1f] text-[14px] whitespace-nowrap">LTC/USDT completed the plan</p>
      <p className="[word-break:break-word] absolute font-['Onest:Regular',sans-serif] font-normal inset-[83.31%_15.21%_15.23%_73.74%] leading-[normal] text-[#7a8089] text-[10.5px] whitespace-nowrap">Recovery from sweep low · +5.4%</p>
      <div className="absolute inset-[86.61%_6.61%_6.25%_72.35%]" data-name="Vector">
        <div className="absolute inset-[-0.78%_-0.16%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 319 65">
            <path d={svgPaths.p23bbef00} fill="var(--fill-0, white)" id="Vector" stroke="var(--stroke-0, #D9DEE5)" />
          </svg>
        </div>
      </div>
      <Group6 />
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[88.23%_17.13%_10.44%_75.99%] leading-[normal] text-[#2a8fc3] text-[9.5px] whitespace-nowrap">MONITORING PAUSED</p>
      <p className="[word-break:break-word] absolute font-['Onest:Bold',sans-serif] font-bold inset-[90.18%_7.87%_7.92%_75.99%] leading-[normal] text-[#191b1f] text-[13px] whitespace-nowrap">ETH/USDT Halal status is under review</p>
      <Frame11 />
      <Frame7 />
    </div>
  );
}

export default function SectionMargin() {
  return (
    <div className="bg-[#f5f8fb] relative size-full" data-name="Section:margin">
      <Frame2 />
      <Frame4 />
      <Group />
    </div>
  );
}
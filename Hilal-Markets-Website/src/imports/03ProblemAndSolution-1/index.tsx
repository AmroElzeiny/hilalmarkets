import svgPaths from "./svg-l9hlxlat43";

function Frame() {
  return (
    <div className="[word-break:break-word] content-stretch flex flex-col gap-[12px] items-center relative shrink-0 text-center">
      <p className="font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[42px] tracking-[-1.68px] w-[800px]">Your strategy and your Shariah checks should not live in separate tools.</p>
      <p className="font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#19191b] text-[18px] w-[800px]">Hilal Markets brings strategy building, Shariah-screened assets, and continuous setup monitoring into one place.</p>
    </div>
  );
}

function StrategyPill() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="Strategy pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">Strategy</p>
    </div>
  );
}

function Frame1() {
  return (
    <div className="relative shrink-0 size-[8.5px]">
      <div className="absolute inset-[-11.76%]">
        <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 10.5 10.5">
          <g id="Frame 46">
            <path d="M5 1V9.5" id="Vector 19" stroke="var(--stroke-0, #19191B)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
            <path d="M1 5.255L9.5 5.255" id="Vector 20" stroke="var(--stroke-0, #19191B)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
          </g>
        </svg>
      </div>
    </div>
  );
}

function ShariahScreeningPill() {
  return (
    <div className="bg-[#e2fe96] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="Shariah screening pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">Shariah screening</p>
    </div>
  );
}

function Frame2() {
  return (
    <div className="relative shrink-0 size-[8.5px]">
      <div className="absolute inset-[-11.76%]">
        <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 10.5 10.5">
          <g id="Frame 46">
            <path d="M5 1V9.5" id="Vector 19" stroke="var(--stroke-0, #19191B)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
            <path d="M1 5.255L9.5 5.255" id="Vector 20" stroke="var(--stroke-0, #19191B)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
          </g>
        </svg>
      </div>
    </div>
  );
}

function SetupMonitoringPill() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="Setup monitoring pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">Setup monitoring</p>
    </div>
  );
}

function OnePlatformPill() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="One platform pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">One platform</p>
    </div>
  );
}

function UnifiedWorkflow() {
  return (
    <div className="content-stretch flex gap-[10px] items-center overflow-clip relative shrink-0" data-name="Unified workflow">
      <StrategyPill />
      <Frame1 />
      <ShariahScreeningPill />
      <Frame2 />
      <SetupMonitoringPill />
      <div className="h-0 relative shrink-0 w-[14px]">
        <div className="absolute inset-[-7.36px_-7.14%]">
          <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 16 14.7279">
            <path d={svgPaths.p1cd76400} fill="var(--stroke-0, black)" id="Vector 18" />
          </svg>
        </div>
      </div>
      <OnePlatformPill />
    </div>
  );
}

function Group() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0" data-name="Corner vectors">
      <svg className="problem-corner-vector problem-corner-vector--top-left" fill="none" viewBox="0 0 144 144">
        <path d={svgPaths.p2eff0600} fill="var(--fill-0, #D9FE75)" fillOpacity="0.3" id="Vector" />
      </svg>
      <svg className="problem-corner-vector problem-corner-vector--bottom-left" fill="none" viewBox="0 266 144 144">
        <path d={svgPaths.p3eae2800} fill="var(--fill-0, #D9FE75)" fillOpacity="0.3" id="Vector_2" />
      </svg>
      <svg className="problem-corner-vector problem-corner-vector--top-right" fill="none" viewBox="812 0 144 144">
        <path d={svgPaths.p3ae5ae00} fill="var(--fill-0, #D9FE75)" fillOpacity="0.3" id="Vector_3" />
      </svg>
      <svg className="problem-corner-vector problem-corner-vector--bottom-right" fill="none" viewBox="812 266 144 144">
        <path d={svgPaths.p2b5dfb80} fill="var(--fill-0, #D9FE75)" fillOpacity="0.3" id="Vector_4" />
      </svg>
    </div>
  );
}

export default function Component03ProblemAndSolution() {
  return (
    <div className="bg-white content-stretch flex flex-col gap-[40px] items-center justify-center px-[96px] py-[200px] relative size-full" data-name="03 — Problem and solution">
      <Frame />
      <UnifiedWorkflow />
      <Group />
    </div>
  );
}

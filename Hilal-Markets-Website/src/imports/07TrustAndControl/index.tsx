function Flow() {
  return (
    <div className="content-stretch flex items-start overflow-clip px-[16px] py-[10px] relative rounded-[999px] shrink-0" data-name="Flow 1">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[13px] text-white whitespace-nowrap">You approve the logic</p>
    </div>
  );
}

function Flow1() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-start overflow-clip px-[16px] py-[10px] relative rounded-[999px] shrink-0" data-name="Flow 2">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[13px] whitespace-nowrap">You approve</p>
    </div>
  );
}

function Flow2() {
  return (
    <div className="content-stretch flex items-start overflow-clip px-[16px] py-[10px] relative rounded-[999px] shrink-0" data-name="Flow 3">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[13px] text-white whitespace-nowrap">Rules monitor</p>
    </div>
  );
}

function Flow3() {
  return (
    <div className="content-stretch flex items-start overflow-clip px-[16px] py-[10px] relative rounded-[999px] shrink-0" data-name="Flow 4">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[13px] text-white whitespace-nowrap">Evidence explains</p>
    </div>
  );
}

function ControlFlow() {
  return (
    <div className="bg-[#2b2e35] content-stretch flex h-[98px] items-center justify-between overflow-clip px-[28px] py-[22px] relative rounded-[22px] shrink-0 w-[1248px]" data-name="Control flow">
      <Flow />
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#b0bac9] text-[18px] whitespace-nowrap">→</p>
      <Flow1 />
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#b0bac9] text-[18px] whitespace-nowrap">→</p>
      <Flow2 />
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#b0bac9] text-[18px] whitespace-nowrap">→</p>
      <Flow3 />
    </div>
  );
}

function Top() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 w-[354px]" data-name="Top">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#68717d] text-[11px] whitespace-nowrap">01</p>
      <div className="relative shrink-0 size-[10px]" data-name="Ellipse">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 10 10">
          <circle cx="5" cy="5" fill="var(--fill-0, #CBFA4D)" id="Ellipse" r="5" />
        </svg>
      </div>
    </div>
  );
}

function Proof() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-start mt-auto overflow-clip px-[12px] py-[9px] relative rounded-[10px] shrink-0 w-[340px]" data-name="Proof">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Methodology · sources · history</p>
    </div>
  );
}

function TrustCard() {
  return (
    <div className="bg-white h-[280px] relative rounded-[22px] shrink-0 w-[402px]" data-name="Trust card 1">
      <div className="content-stretch flex flex-col gap-[14px] items-start overflow-clip p-[24px] relative rounded-[inherit] size-full">
        <Top />
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.18] not-italic relative shrink-0 text-[#2b2e35] text-[24px] tracking-[-0.48px] w-[340px]">Every asset status is explained</p>
        <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[14px] w-[340px]">Review the methodology, sources, restrictions, and screening history behind each result.</p>
        <Proof />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[22px]" />
    </div>
  );
}

function Top1() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 w-[354px]" data-name="Top">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#68717d] text-[11px] whitespace-nowrap">02</p>
      <div className="relative shrink-0 size-[10px]" data-name="Ellipse">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 10 10">
          <circle cx="5" cy="5" fill="var(--fill-0, #95ADED)" id="Ellipse" r="5" />
        </svg>
      </div>
    </div>
  );
}

function Proof1() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-start mt-auto overflow-clip px-[12px] py-[9px] relative rounded-[10px] shrink-0 w-[340px]" data-name="Proof">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">AI assists · you approve</p>
    </div>
  );
}

function TrustCard1() {
  return (
    <div className="bg-white h-[280px] relative rounded-[22px] shrink-0 w-[402px]" data-name="Trust card 2">
      <div className="content-stretch flex flex-col gap-[14px] items-start overflow-clip p-[24px] relative rounded-[inherit] size-full">
        <Top1 />
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.18] not-italic relative shrink-0 text-[#2b2e35] text-[24px] tracking-[-0.48px] w-[340px]">Every strategy rule stays visible</p>
        <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[14px] w-[340px]">The canvas and the assistant help shape your idea. You review and approve the logic before monitoring begins.</p>
        <Proof1 />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[22px]" />
    </div>
  );
}

function Top2() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 w-[354px]" data-name="Top">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#68717d] text-[11px] whitespace-nowrap">03</p>
      <div className="relative shrink-0 size-[10px]" data-name="Ellipse">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 10 10">
          <circle cx="5" cy="5" fill="var(--fill-0, #95ADED)" id="Ellipse" r="5" />
        </svg>
      </div>
    </div>
  );
}

function Proof2() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-start mt-auto overflow-clip px-[12px] py-[9px] relative rounded-[10px] shrink-0 w-[340px]" data-name="Proof">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Matched · missing · changed</p>
    </div>
  );
}

function TrustCard2() {
  return (
    <div className="bg-white h-[280px] relative rounded-[22px] shrink-0 w-[402px]" data-name="Trust card 3">
      <div className="content-stretch flex flex-col gap-[14px] items-start overflow-clip p-[24px] relative rounded-[inherit] size-full">
        <Top2 />
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.18] not-italic relative shrink-0 text-[#2b2e35] text-[24px] tracking-[-0.48px] w-[340px]">Every alert comes with evidence</p>
        <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[14px] w-[340px]">See which conditions matched, which did not, and why the setup changed status.</p>
        <Proof2 />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[22px]" />
    </div>
  );
}

function TrustCards() {
  return (
    <div className="content-stretch flex gap-[20px] h-[280px] items-start overflow-clip relative shrink-0 w-[1248px]" data-name="Trust cards">
      <TrustCard />
      <TrustCard1 />
      <TrustCard2 />
    </div>
  );
}

export default function Component07TrustAndControl() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex flex-col gap-[48px] items-start px-[96px] py-[92px] relative size-full" data-name="07 — Trust and control">
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[42px] tracking-[-1.68px] w-[820px]">Built around transparency, not blind trust</p>
      <ControlFlow />
      <TrustCards />
    </div>
  );
}

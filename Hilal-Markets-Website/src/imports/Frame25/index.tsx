function Frame() {
  return (
    <div className="[word-break:break-word] content-stretch flex flex-col gap-[11.429px] items-center justify-center relative shrink-0 text-[#2b2e35] text-center w-full">
      <p className="font-['Geometria:Medium',sans-serif] leading-[1.1] min-w-full not-italic relative shrink-0 text-[42px] tracking-[-1.68px] w-[min-content]">A better way for Muslim crypto traders to build and monitor their strategies.</p>
      <p className="font-['Onest:Regular',sans-serif] font-normal leading-[1.4] relative shrink-0 text-[18px] w-[760px]">Create your own rules, explore Shariah-screened assets, and follow every setup without constantly switching between charts, alerts, and halal screeners.</p>
    </div>
  );
}

function Link() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center px-[46.666px] py-[18.095px] relative rounded-[95.238px] shrink-0" data-name="Link">
      <div aria-hidden className="absolute border-[0.952px] border-[rgba(0,0,0,0)] border-solid inset-0 pointer-events-none rounded-[95.238px]" />
      <div className="[word-break:break-word] flex flex-col font-['Geometria:Medium',sans-serif] justify-end leading-[0] not-italic relative shrink-0 text-[#2b2e35] text-[15.238px] whitespace-nowrap">
        <p className="leading-none">Join the waitlist</p>
      </div>
    </div>
  );
}

function Frame2() {
  return (
    <div className="content-stretch flex gap-[11.429px] items-center justify-center relative shrink-0 w-full">
      <Link />
    </div>
  );
}

function Frame3() {
  return (
    <div className="content-stretch flex flex-col gap-[12px] items-center relative shrink-0 w-full">
      <Frame2 />
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.4] relative shrink-0 text-[#68717d] text-[12px] whitespace-nowrap">Be among the first to access Hilal Markets.</p>
    </div>
  );
}

export default function Frame1() {
  return (
    <div className="content-stretch flex flex-col gap-[25px] items-center relative size-full">
      <Frame />
      <Frame3 />
    </div>
  );
}
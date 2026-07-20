function Component01Pill() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="01 pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">01</p>
    </div>
  );
}

function StepHeader() {
  return (
    <div className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-[354px]" data-name="Step header">
      <Component01Pill />
    </div>
  );
}

function Frame() {
  return (
    <div className="content-stretch flex flex-col gap-[18px] items-start relative shrink-0">
      <StepHeader />
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.18] not-italic relative shrink-0 text-[#2b2e35] text-[25px] tracking-[-0.5px] w-[354px]">Build your strategy</p>
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[14px] w-[354px]">Describe what you look for in plain language through the AI chatbot. Hilal Markets turns your idea into clear rules that you review and approve. No coding is required.</p>
    </div>
  );
}

function Frame5() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center px-[6px] py-[2px] relative rounded-[100px] shrink-0">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#19191b] text-[10px] whitespace-nowrap">AI strategy chatbot</p>
    </div>
  );
}

function Prompt() {
  return (
    <div className="bg-white content-stretch flex items-start overflow-clip px-[12px] py-[9px] relative rounded-[12px] shrink-0 w-[322px]" data-name="Prompt">
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] w-[290px]">Breakout above resistance with rising volume.</p>
    </div>
  );
}

function ProductExample() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex flex-col gap-[10px] h-[126px] items-start overflow-clip px-[16px] py-[14px] relative rounded-[16px] shrink-0 w-[354px]" data-name="Product example">
      <Frame5 />
      <Prompt />
    </div>
  );
}

function Step() {
  return (
    <div className="bg-white content-stretch flex flex-col h-[440px] items-start justify-between overflow-clip p-[24px] relative rounded-[24px] shrink-0 w-[402px]" data-name="Step 01">
      <Frame />
      <ProductExample />
    </div>
  );
}

function Component02Pill() {
  return (
    <div className="bg-[#e2fe96] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="02 pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">02</p>
    </div>
  );
}

function StepHeader1() {
  return (
    <div className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-[354px]" data-name="Step header">
      <Component02Pill />
    </div>
  );
}

function Frame1() {
  return (
    <div className="content-stretch flex flex-col gap-[18px] items-start relative shrink-0">
      <StepHeader1 />
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.18] not-italic relative shrink-0 text-[#2b2e35] text-[25px] tracking-[-0.5px] w-[354px]">Choose Shariah-screened assets</p>
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[14px] w-[354px]">Apply your strategy only to screened assets and review the evidence behind every status.</p>
    </div>
  );
}

function ScreenedPill() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="SCREENED pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">Screened</p>
    </div>
  );
}

function AssetTop() {
  return (
    <div className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-[322px]" data-name="Asset top">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[15px] whitespace-nowrap">ETH / USDT</p>
      <ScreenedPill />
    </div>
  );
}

function Frame3() {
  return (
    <div className="content-stretch flex flex-col gap-[4px] items-start relative shrink-0">
      <AssetTop />
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[11px] whitespace-pre">{`Methodology  •  Sources  •  Reviewed 18 Jul`}</p>
    </div>
  );
}

function ProductExample1() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex flex-col h-[126px] items-start justify-between overflow-clip px-[16px] py-[14px] relative rounded-[16px] shrink-0 w-[354px]" data-name="Product example">
      <Frame3 />
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-pre">{`View full evidence  →`}</p>
    </div>
  );
}

function Step1() {
  return (
    <div className="bg-white content-stretch flex flex-col h-[440px] items-start justify-between overflow-clip p-[24px] relative rounded-[24px] shrink-0 w-[402px]" data-name="Step 02">
      <Frame1 />
      <ProductExample1 />
    </div>
  );
}

function Component03Pill() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-center justify-center overflow-clip px-[14px] py-[8px] relative rounded-[999px] shrink-0" data-name="03 pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] whitespace-nowrap">03</p>
    </div>
  );
}

function StepHeader2() {
  return (
    <div className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-[354px]" data-name="Step header">
      <Component03Pill />
    </div>
  );
}

function Frame2() {
  return (
    <div className="content-stretch flex flex-col gap-[18px] items-start relative shrink-0">
      <StepHeader2 />
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.18] not-italic relative shrink-0 text-[#2b2e35] text-[25px] tracking-[-0.5px] w-[354px]">Monitor every setup</p>
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[14px] w-[354px]">Follow each setup as it develops and receive clear alerts when your conditions are met.</p>
    </div>
  );
}

function Frame4() {
  return (
    <div className="bg-[rgba(255,255,255,0.1)] content-stretch flex items-center justify-center px-[6px] py-[2px] relative rounded-[100px] shrink-0">
      <p className="[word-break:break-word] font-['Onest:SemiBold',sans-serif] font-semibold leading-[1.5] relative shrink-0 text-[#cbfa4d] text-[10px] whitespace-nowrap">Setup forming</p>
    </div>
  );
}

function Bars() {
  return (
    <div className="content-stretch flex gap-[6px] h-[6px] items-start overflow-clip relative shrink-0 w-[322px]" data-name="Bars">
      <div className="bg-[#cbfa4d] h-[5px] relative rounded-[5px] shrink-0 w-[59px]" data-name="Rectangle" />
      <div className="bg-[#cbfa4d] h-[5px] relative rounded-[5px] shrink-0 w-[59px]" data-name="Rectangle" />
      <div className="bg-[#cbfa4d] h-[5px] relative rounded-[5px] shrink-0 w-[59px]" data-name="Rectangle" />
      <div className="bg-[#cbfa4d] h-[5px] relative rounded-[5px] shrink-0 w-[59px]" data-name="Rectangle" />
      <div className="bg-[#525966] h-[5px] relative rounded-[5px] shrink-0 w-[59px]" data-name="Rectangle" />
    </div>
  );
}

function ProductExample2() {
  return (
    <div className="bg-[#2b2e35] content-stretch flex flex-col gap-[10px] h-[126px] items-start overflow-clip px-[16px] py-[14px] relative rounded-[16px] shrink-0 w-[354px]" data-name="Product example">
      <Frame4 />
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[15px] text-white whitespace-nowrap">4 of 5 conditions matched</p>
      <Bars />
    </div>
  );
}

function Step2() {
  return (
    <div className="bg-white content-stretch flex flex-col h-[440px] items-start justify-between overflow-clip p-[24px] relative rounded-[24px] shrink-0 w-[402px]" data-name="Step 03">
      <Frame2 />
      <ProductExample2 />
    </div>
  );
}

function Steps() {
  return (
    <div className="content-stretch flex gap-[20px] h-[440px] items-start overflow-clip relative shrink-0 w-[1248px]" data-name="Steps">
      <Step />
      <Step1 />
      <Step2 />
    </div>
  );
}

export default function Component04HowHilalMarketsWorks() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex flex-col gap-[46px] items-start px-[96px] py-[88px] relative size-full" data-name="04 — How Hilal Markets works">
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[42px] tracking-[-1.68px] w-[484px]">From your trading idea to continuous monitoring</p>
      <Steps />
    </div>
  );
}
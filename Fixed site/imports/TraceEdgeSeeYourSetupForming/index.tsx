import svgPaths from "./svg-n8konrjveh";

function Text() {
  return <div className="absolute bg-[#a78bfa] left-0 rounded-[4px] shadow-[0px_0px_0px_0px_rgba(139,92,246,0.15)] size-[8px] top-[5.5px]" data-name="Text" />;
}

function Paragraph() {
  return (
    <div className="h-[18.594px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid overflow-clip relative rounded-[inherit] size-full">
        <Text />
        <p className="[word-break:break-word] absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[18.6px] left-[15px] not-italic text-[#a78bfa] text-[12px] top-0 tracking-[1.44px] uppercase whitespace-nowrap">{` Personal market monitoring, explained`}</p>
      </div>
    </div>
  );
}

function Heading() {
  return (
    <div className="max-w-[650px] relative shrink-0 w-[650px]" data-name="Heading 1">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[54.88px] not-italic relative shrink-0 text-[#0b0b10] text-[56px] tracking-[-2px] w-[650px]">See your setup forming before it confirms.</p>
      </div>
    </div>
  );
}

function ParagraphMargin() {
  return (
    <div className="relative shrink-0" data-name="Paragraph:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[12px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[29.45px] not-italic relative shrink-0 text-[#3a3a46] text-[19px] w-[560px]">Describe your setup once. TraceEdge scans spot markets and explains each forming lifecycle.</p>
      </div>
    </div>
  );
}

function Text1() {
  return (
    <div className="relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#f7f3ff] text-[16px] whitespace-nowrap">→</p>
      </div>
    </div>
  );
}

function Link() {
  return (
    <div className="absolute content-stretch drop-shadow-[0px_16px_21px_rgba(139,92,246,0.34)] flex gap-[12px] items-center justify-center left-0 min-h-[50px] px-[23px] py-px rounded-[13px] top-0" style={{ backgroundImage: "linear-gradient(159.538deg, rgb(167, 139, 250) 0%, rgb(139, 92, 246) 100%)" }} data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(0,0,0,0)] border-solid inset-0 pointer-events-none rounded-[13px]" />
      <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[16px] text-white whitespace-nowrap">{`Sign up `}</p>
      <Text1 />
    </div>
  );
}

function Link1() {
  return (
    <div className="absolute bg-white content-stretch flex items-center justify-center left-[144px] min-h-[50px] px-[23px] py-px rounded-[13px] top-0" data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[13px]" />
      <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[#0b0b10] text-[16px] whitespace-nowrap">How it works</p>
    </div>
  );
}

function Container1() {
  return (
    <div className="h-[50px] relative shrink-0 w-full" data-name="Container">
      <Link />
      <Link1 />
    </div>
  );
}

function ContainerMargin() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[30px] relative size-full">
        <Container1 />
      </div>
    </div>
  );
}

function ListItem() {
  return (
    <div className="absolute h-[20.148px] left-0 top-0 w-[139.32px]" data-name="List Item">
      <p className="absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold left-0 text-[#8b5cf6] top-0">✓</p>
      <p className="absolute font-['Inter:Regular',sans-serif] font-normal left-[18.45px] text-[#3a3a46] top-0">No trading API keys</p>
    </div>
  );
}

function ListItem1() {
  return (
    <div className="absolute h-[20.148px] left-[161.32px] top-0 w-[163.445px]" data-name="List Item">
      <p className="absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold left-0 text-[#8b5cf6] top-0">✓</p>
      <p className="absolute font-['Inter:Regular',sans-serif] font-normal left-[18.45px] text-[#3a3a46] top-0">No automatic execution</p>
    </div>
  );
}

function ListItem2() {
  return (
    <div className="absolute h-[20.148px] left-[346.77px] top-0 w-[171.805px]" data-name="List Item">
      <p className="absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold left-0 text-[#8b5cf6] top-0">✓</p>
      <p className="absolute font-['Inter:Regular',sans-serif] font-normal left-[18.45px] text-[#3a3a46] top-0">You make every decision</p>
    </div>
  );
}

function ListProductBoundaries() {
  return (
    <div className="[word-break:break-word] h-[20.148px] leading-[20.15px] not-italic relative shrink-0 text-[13px] w-full whitespace-nowrap" data-name="List - Product boundaries">
      <ListItem />
      <ListItem1 />
      <ListItem2 />
    </div>
  );
}

function ListProductBoundariesMargin() {
  return (
    <div className="relative shrink-0 w-full" data-name="List - Product boundaries:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[25px] relative size-full">
        <ListProductBoundaries />
      </div>
    </div>
  );
}

function Container() {
  return (
    <div className="absolute content-stretch flex flex-col h-[354.383px] items-start left-0 top-[231.12px] w-[652.828px]" data-name="Container">
      <Paragraph />
      <Heading />
      <ParagraphMargin />
      <ContainerMargin />
      <ListProductBoundariesMargin />
    </div>
  );
}

function Text2() {
  return <div className="relative rounded-[4px] shrink-0 size-[8px]" data-name="Text" />;
}

function Text3() {
  return <div className="relative rounded-[4px] shrink-0 size-[8px]" data-name="Text" />;
}

function Text4() {
  return <div className="relative rounded-[4px] shrink-0 size-[8px]" data-name="Text" />;
}

function Container4() {
  return (
    <div className="absolute content-stretch flex gap-[6px] items-start left-[18px] top-[20.5px] w-[138.555px]" data-name="Container">
      <Text2 />
      <Text3 />
      <Text4 />
    </div>
  );
}

function Container3() {
  return (
    <div className="bg-white h-[50px] relative shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(75,54,105,0.14)] border-b border-solid inset-0 pointer-events-none" />
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <Container4 />
        <p className="-translate-x-1/2 [word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] left-[225.83px] not-italic text-[#3a3a46] text-[11px] text-center top-[15.98px] whitespace-nowrap">Lifecycle Watchlist</p>
        <p className="-translate-x-full [word-break:break-word] absolute font-['Inter:Regular',sans-serif] font-normal leading-[14.208px] left-[433.66px] not-italic text-[#3a3a46] text-[9.167px] text-right top-[17.4px] whitespace-nowrap">Live example</p>
      </div>
    </div>
  );
}

function Paragraph1() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] whitespace-nowrap">Liquidity Sweep + Trend</p>
      </div>
    </div>
  );
}

function Container7() {
  return (
    <div className="h-[24.797px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Regular',sans-serif] font-normal leading-[18.6px] left-0 not-italic text-[#3a3a46] text-[12px] top-[3.5px] whitespace-nowrap">Binance spot · 15m</p>
      </div>
    </div>
  );
}

function Container6() {
  return (
    <div className="relative shrink-0 w-[188.75px]" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <Paragraph1 />
        <Container7 />
      </div>
    </div>
  );
}

function Text5() {
  return (
    <div className="bg-[#9167f7] relative rounded-[20px] shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start px-[10px] py-[7px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[15.5px] not-italic relative shrink-0 text-[#f9f7ff] text-[10px] whitespace-nowrap">Scanning 342 pairs</p>
      </div>
    </div>
  );
}

function Container5() {
  return (
    <div className="bg-white relative shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(75,54,105,0.14)] border-b border-solid inset-0 pointer-events-none" />
      <div className="flex flex-row items-center size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-center justify-between pb-[21px] pt-[20px] px-[22px] relative size-full">
          <Container6 />
          <Text5 />
        </div>
      </div>
    </div>
  );
}

function Image() {
  return (
    <div className="absolute left-[4px] overflow-clip size-[28px] top-[4px]" data-name="Image">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 28 28">
        <g id="Group">
          <path d={svgPaths.pc390800} fill="var(--fill-0, #66F9A1)" id="Vector" />
          <path d={svgPaths.p37d91180} fill="var(--fill-0, white)" id="Vector_2" />
        </g>
      </svg>
    </div>
  );
}

function Text6() {
  return (
    <div className="bg-white overflow-clip relative rounded-[11px] shadow-[0px_8px_22px_0px_rgba(75,54,105,0.14)] shrink-0 size-[36px]" data-name="Text">
      <Image />
    </div>
  );
}

function BoldText() {
  return (
    <div className="relative shrink-0 w-full" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">SOL / USDT</p>
      </div>
    </div>
  );
}

function Small() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[15.5px] not-italic relative shrink-0 text-[#656579] text-[10px] whitespace-nowrap">Improving</p>
      </div>
    </div>
  );
}

function Text7() {
  return (
    <div className="content-stretch flex flex-col items-start relative shrink-0 w-[73px]" data-name="Text">
      <BoldText />
      <Small />
    </div>
  );
}

function Frame1() {
  return (
    <div className="content-stretch flex gap-[13px] items-center relative shrink-0 w-[197px]">
      <Text6 />
      <Text7 />
    </div>
  );
}

function Text9() {
  return <div className="bg-[#9167f7] h-[5px] relative rounded-[8px] shrink-0 w-[62.891px]" data-name="Text" />;
}

function ItalicText() {
  return (
    <div className="absolute bg-[#e0d8ff] content-stretch flex flex-col h-[5px] items-start left-[46px] overflow-clip rounded-[8px] top-[6.8px] w-[74px]" data-name="Italic Text">
      <Text9 />
    </div>
  );
}

function Text8() {
  return (
    <div className="h-[18.594px] relative shrink-0 w-[120px]" data-name="Text">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[18.6px] left-0 not-italic text-[#9167f7] text-[12px] top-0 whitespace-nowrap">85%</p>
      <ItalicText />
    </div>
  );
}

function Button() {
  return (
    <div className="bg-[rgba(196,181,253,0.1)] relative rounded-[12px] shrink-0 w-full" data-name="Button">
      <div aria-hidden className="absolute border border-[rgba(196,181,253,0.2)] border-solid inset-0 pointer-events-none rounded-[12px]" />
      <div className="flex flex-row items-center justify-center size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-center justify-between p-[12px] relative size-full">
          <Frame1 />
          <Text8 />
        </div>
      </div>
    </div>
  );
}

function Image1() {
  return (
    <div className="absolute left-[4px] overflow-clip size-[28px] top-[4px]" data-name="Image">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 28 28">
        <g id="Group">
          <path d={svgPaths.pc390800} fill="var(--fill-0, #2A5ADA)" id="Vector" />
          <path d={svgPaths.p1a26e340} fill="var(--fill-0, white)" id="Vector_2" />
        </g>
      </svg>
    </div>
  );
}

function Text10() {
  return (
    <div className="bg-white overflow-clip relative rounded-[11px] shadow-[0px_8px_22px_0px_rgba(75,54,105,0.14)] shrink-0 size-[36px]" data-name="Text">
      <Image1 />
    </div>
  );
}

function BoldText1() {
  return (
    <div className="relative shrink-0 w-full" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">LINK / USDT</p>
      </div>
    </div>
  );
}

function Small1() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[15.5px] not-italic relative shrink-0 text-[#656579] text-[10px] whitespace-nowrap">Stable</p>
      </div>
    </div>
  );
}

function Text11() {
  return (
    <div className="content-stretch flex flex-col items-start relative shrink-0 w-[77px]" data-name="Text">
      <BoldText1 />
      <Small1 />
    </div>
  );
}

function Frame() {
  return (
    <div className="content-stretch flex gap-[13px] items-center relative shrink-0">
      <Text10 />
      <Text11 />
    </div>
  );
}

function Text13() {
  return <div className="bg-[#9167f7] h-[5px] relative rounded-[8px] shrink-0 w-[51.797px]" data-name="Text" />;
}

function ItalicText1() {
  return (
    <div className="absolute bg-[#e0d8ff] content-stretch flex flex-col h-[5px] items-start left-[46px] overflow-clip rounded-[8px] top-[6.8px] w-[74px]" data-name="Italic Text">
      <Text13 />
    </div>
  );
}

function Text12() {
  return (
    <div className="h-[18.594px] relative shrink-0 w-[120px]" data-name="Text">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[18.6px] left-0 not-italic text-[#9167f7] text-[12px] top-0 whitespace-nowrap">70%</p>
      <ItalicText1 />
    </div>
  );
}

function Button1() {
  return (
    <div className="bg-[rgba(196,181,253,0.1)] relative rounded-[12px] shrink-0 w-full" data-name="Button">
      <div aria-hidden className="absolute border border-[rgba(196,181,253,0.2)] border-solid inset-0 pointer-events-none rounded-[12px]" />
      <div className="flex flex-row items-center justify-center size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-center justify-between p-[12px] relative size-full">
          <Frame />
          <Text12 />
        </div>
      </div>
    </div>
  );
}

function Image2() {
  return (
    <div className="absolute left-[4px] overflow-clip size-[28px] top-[4px]" data-name="Image">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 28 28">
        <g id="Group">
          <path d={svgPaths.pc390800} fill="var(--fill-0, #627EEA)" id="Vector" />
          <g id="Group_2">
            <path d={svgPaths.p33a9080} fill="var(--fill-0, white)" fillOpacity="0.602" id="Vector_2" />
            <path d={svgPaths.p25ee5400} fill="var(--fill-0, white)" id="Vector_3" />
            <path d={svgPaths.p31a2e800} fill="var(--fill-0, white)" fillOpacity="0.602" id="Vector_4" />
            <path d={svgPaths.pc3d6b00} fill="var(--fill-0, white)" id="Vector_5" />
            <path d={svgPaths.p6c7000} fill="var(--fill-0, white)" fillOpacity="0.2" id="Vector_6" />
            <path d={svgPaths.p31ad7f00} fill="var(--fill-0, white)" fillOpacity="0.602" id="Vector_7" />
          </g>
        </g>
      </svg>
    </div>
  );
}

function Text14() {
  return (
    <div className="bg-white overflow-clip relative rounded-[11px] shadow-[0px_8px_22px_0px_rgba(75,54,105,0.14)] shrink-0 size-[36px]" data-name="Text">
      <Image2 />
    </div>
  );
}

function BoldText2() {
  return (
    <div className="relative shrink-0 w-full" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">ETH / USDT</p>
      </div>
    </div>
  );
}

function Small2() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[15.5px] not-italic relative shrink-0 text-[#656579] text-[10px] whitespace-nowrap">Weakening</p>
      </div>
    </div>
  );
}

function Text15() {
  return (
    <div className="content-stretch flex flex-col items-start relative shrink-0 w-[74px]" data-name="Text">
      <BoldText2 />
      <Small2 />
    </div>
  );
}

function Frame2() {
  return (
    <div className="content-stretch flex gap-[14px] items-start relative shrink-0">
      <Text14 />
      <Text15 />
    </div>
  );
}

function Text17() {
  return <div className="bg-[#9167f7] h-[5px] relative rounded-[8px] shrink-0 w-[40.688px]" data-name="Text" />;
}

function ItalicText2() {
  return (
    <div className="absolute bg-[#e0d8ff] content-stretch flex flex-col h-[5px] items-start left-[46px] overflow-clip rounded-[8px] top-[6.8px] w-[74px]" data-name="Italic Text">
      <Text17 />
    </div>
  );
}

function Text16() {
  return (
    <div className="h-[18.594px] relative shrink-0 w-[120px]" data-name="Text">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[18.6px] left-0 not-italic text-[#9167f7] text-[12px] top-0 whitespace-nowrap">55%</p>
      <ItalicText2 />
    </div>
  );
}

function Button2() {
  return (
    <div className="bg-[rgba(196,181,253,0.1)] relative rounded-[12px] shrink-0 w-full" data-name="Button">
      <div aria-hidden className="absolute border border-[rgba(196,181,253,0.2)] border-solid inset-0 pointer-events-none rounded-[12px]" />
      <div className="flex flex-row items-center size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-center justify-between p-[12px] relative size-full">
          <Frame2 />
          <Text16 />
        </div>
      </div>
    </div>
  );
}

function Container8() {
  return (
    <div className="bg-white relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col gap-[2px] items-start p-[18px] relative size-full">
        <Button />
        <Button1 />
        <Button2 />
      </div>
    </div>
  );
}

function BoldText3() {
  return (
    <div className="h-full relative shrink-0" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#f9f7ff] text-[16px] whitespace-nowrap">SOL/USDT</p>
      </div>
    </div>
  );
}

function Text18() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] not-italic relative shrink-0 text-[#e0d8ff] text-[11px] whitespace-nowrap">Near confirmation</p>
      </div>
    </div>
  );
}

function Container10() {
  return (
    <div className="h-[24.797px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-start justify-between relative size-full">
        <BoldText3 />
        <Text18 />
      </div>
    </div>
  );
}

function Container12() {
  return (
    <div className="h-[51.094px] relative shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.08)] border-solid border-t inset-0 pointer-events-none" />
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid not-italic relative size-full whitespace-nowrap">
        <div className="absolute font-['Inter:Regular',sans-serif] font-normal leading-[0] left-0 text-[#f9f7ff] text-[0px] top-[9px]">
          <p className="leading-[17.05px] mb-0 text-[11px]">4h close above EMA 200</p>
          <p className="leading-[14.208px] text-[9.167px]">{`$146.82 > $142.10`}</p>
        </div>
        <p className="absolute font-['Inter:Bold',sans-serif] font-bold leading-[15.5px] left-[353.6px] text-[#c4b5fd] text-[10px] top-[9px]">PASS</p>
      </div>
    </div>
  );
}

function Container13() {
  return (
    <div className="h-[51.094px] relative shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.08)] border-solid border-t inset-0 pointer-events-none" />
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid not-italic relative size-full whitespace-nowrap">
        <div className="absolute font-['Inter:Regular',sans-serif] font-normal leading-[0] left-0 text-[#f9f7ff] text-[0px] top-[9px]">
          <p className="leading-[17.05px] mb-0 text-[11px]">Bullish liquidity sweep</p>
          <p className="leading-[14.208px] text-[9.167px]">Prior low reclaimed</p>
        </div>
        <p className="absolute font-['Inter:Bold',sans-serif] font-bold leading-[15.5px] left-[353.6px] text-[#c4b5fd] text-[10px] top-[9px]">PASS</p>
      </div>
    </div>
  );
}

function Container14() {
  return (
    <div className="h-[51.094px] relative shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.08)] border-solid border-t inset-0 pointer-events-none" />
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid not-italic relative size-full whitespace-nowrap">
        <div className="absolute font-['Inter:Regular',sans-serif] font-normal leading-[0] left-0 text-[#f9f7ff] text-[0px] top-[9px]">
          <p className="leading-[17.05px] mb-0 text-[11px]">Volume at least 1.5x</p>
          <p className="leading-[14.208px] text-[9.167px]">1.36x / 1.50x</p>
        </div>
        <p className="absolute font-['Inter:Bold',sans-serif] font-bold leading-[15.5px] left-[336.51px] text-[#e6e6eb] text-[10px] top-[9px]">MISSING</p>
      </div>
    </div>
  );
}

function Container11() {
  return (
    <div className="h-[165.281px] relative shrink-0 w-[379.664px]" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[12px] relative size-full">
        <Container12 />
        <Container13 />
        <Container14 />
      </div>
    </div>
  );
}

function Paragraph2() {
  return (
    <div className="h-[24px] relative shrink-0 w-[379.664px]" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[10px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[13.95px] not-italic relative shrink-0 text-[#e0d8ff] text-[9px] whitespace-nowrap">Candle closed 14:45 UTC · Data fresh 1.8s ago</p>
      </div>
    </div>
  );
}

function Container9() {
  return (
    <div className="drop-shadow-[0px_22px_30px_rgba(0,0,0,0.24)] relative rounded-[15px] shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute bg-[#9167f7] inset-0 pointer-events-none rounded-[15px]" />
      <div aria-hidden className="absolute border border-[rgba(196,181,253,0.28)] border-solid inset-0 pointer-events-none rounded-[15px]" />
      <div className="content-stretch flex flex-col items-start p-[18px] relative size-full">
        <Container10 />
        <Container11 />
        <Paragraph2 />
      </div>
      <div className="absolute inset-0 pointer-events-none rounded-[inherit] shadow-[inset_0px_1px_0px_0px_rgba(255,255,255,0.08)]" />
    </div>
  );
}

function ContainerMargin1() {
  return (
    <div className="bg-white relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[18px] px-[18px] relative size-full">
        <Container9 />
      </div>
    </div>
  );
}

function Container2() {
  return (
    <div className="absolute left-[726.33px] rounded-[26px] top-[100px] w-[453.664px]" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 453.66 633.88' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -78.695 -76.552 0 453.66 0)'><stop stop-color='rgba(196,181,253,0.2)' offset='0'/><stop stop-color='rgba(98,91,127,0.1)' offset='0.24688'/><stop stop-color='rgba(0,0,0,0)' offset='0.49376'/></radialGradient></defs></svg>\"), linear-gradient(90deg, rgb(247, 243, 255) 0%, rgb(247, 243, 255) 100%)" }} data-name="Container">
      <div className="content-stretch flex flex-col items-start overflow-clip p-px relative rounded-[inherit] size-full">
        <Container3 />
        <Container5 />
        <Container8 />
        <ContainerMargin1 />
      </div>
      <div aria-hidden className="absolute border border-[rgba(75,54,105,0.16)] border-solid inset-0 pointer-events-none rounded-[26px] shadow-[0px_24px_70px_0px_rgba(75,54,105,0.16)]" />
    </div>
  );
}

function Section() {
  return (
    <div className="h-[796.617px] min-h-[740px] relative shrink-0 w-[1180px]" data-name="Section">
      <Container />
      <Container2 />
    </div>
  );
}

function SectionMargin() {
  return (
    <div className="bg-[#f7f7fa] relative shrink-0 w-full" data-name="Section:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Section />
      </div>
    </div>
  );
}

function Text19() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#f7f3ff] text-[12px] tracking-[0.96px] uppercase whitespace-nowrap">Built for crypto spot traders</p>
      </div>
    </div>
  );
}

function Text20() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#f7f3ff] text-[12px] tracking-[0.96px] uppercase whitespace-nowrap">Multi-timeframe rules</p>
      </div>
    </div>
  );
}

function Text21() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#f7f3ff] text-[12px] tracking-[0.96px] uppercase whitespace-nowrap">Telegram + Discord</p>
      </div>
    </div>
  );
}

function Text22() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#f7f3ff] text-[12px] tracking-[0.96px] uppercase whitespace-nowrap">Condition-level proof</p>
      </div>
    </div>
  );
}

function SectionInitialPlatformCoverage() {
  return (
    <div className="drop-shadow-[0px_22px_32.5px_rgba(139,92,246,0.26)] h-[54.594px] relative shrink-0 w-full" style={{ backgroundImage: "linear-gradient(177.873deg, rgb(167, 139, 250) 0%, rgb(139, 92, 246) 100%)" }} data-name="Section - Initial platform coverage">
      <div className="flex flex-row justify-center size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex gap-[60px] items-start justify-center px-[30px] py-[18px] relative size-full">
          <Text19 />
          <Text20 />
          <Text21 />
          <Text22 />
        </div>
      </div>
    </div>
  );
}

function Paragraph3() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Heavy',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#a78bfa] text-[12px] tracking-[1.44px] uppercase whitespace-nowrap">From idea to monitor</p>
      </div>
    </div>
  );
}

function Heading1() {
  return (
    <div className="relative shrink-0 w-full" data-name="Heading 2">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-none not-italic relative shrink-0 text-[#0b0b10] text-[56px] tracking-[-2.24px] w-[720px]">Your setup, translated into rules you can inspect.</p>
      </div>
    </div>
  );
}

function Paragraph4() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[27.9px] not-italic relative shrink-0 text-[#3a3a46] text-[18px] w-[720px]">AI helps structure what you mean. A deterministic scanner handles every market calculation after you approve it.</p>
      </div>
    </div>
  );
}

function Container15() {
  return (
    <div className="max-w-[720px] relative shrink-0 w-[720px]" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] overflow-clip relative rounded-[inherit] size-full">
        <Paragraph3 />
        <Heading1 />
        <Paragraph4 />
      </div>
    </div>
  );
}

function Text23() {
  return (
    <div className="relative rounded-[12px] shrink-0 size-[46px]" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[normal] left-[19.91px] not-italic text-[14px] text-white top-[14.5px] whitespace-nowrap">1</p>
      </div>
    </div>
  );
}

function Heading2() {
  return (
    <div className="h-[70px] relative shrink-0 w-[225px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[48px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[21.6px] not-italic relative shrink-0 text-[#0b0b10] text-[20px] tracking-[-0.9px] whitespace-nowrap">Describe the setup</p>
      </div>
    </div>
  );
}

function Paragraph5() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[12px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative shrink-0 text-[#3a3a46] text-[14px] w-[225px]">Write naturally or begin with a tested template. Choose exchange, timeframe, universe and risk limits.</p>
      </div>
    </div>
  );
}

function Article() {
  return (
    <div className="absolute bg-white content-stretch flex flex-col h-[272.375px] items-start left-0 min-h-[260px] p-[29px] rounded-[22px] top-0 w-[283px]" data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
      <Text23 />
      <Heading2 />
      <Paragraph5 />
    </div>
  );
}

function Text24() {
  return (
    <div className="relative rounded-[12px] shrink-0 size-[46px]" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[normal] left-[18.53px] not-italic text-[14px] text-white top-[14.5px] whitespace-nowrap">2</p>
      </div>
    </div>
  );
}

function Heading3() {
  return (
    <div className="h-[70px] relative shrink-0 w-[225px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[48px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[21.6px] not-italic relative shrink-0 text-[#0b0b10] text-[20px] tracking-[-0.9px] whitespace-nowrap">Review the interpretation</p>
      </div>
    </div>
  );
}

function Paragraph6() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[12px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative shrink-0 text-[#3a3a46] text-[14px] w-[225px]">Inspect every rule, assumption and unsupported term. Edit or clarify anything before it can run.</p>
      </div>
    </div>
  );
}

function Article1() {
  return (
    <div className="absolute bg-white content-stretch flex flex-col h-[272.375px] items-start left-[299px] min-h-[260px] p-[29px] rounded-[22px] top-0 w-[283px]" data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
      <Text24 />
      <Heading3 />
      <Paragraph6 />
    </div>
  );
}

function Text25() {
  return (
    <div className="relative rounded-[12px] shrink-0 size-[46px]" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[normal] left-[18.4px] not-italic text-[14px] text-white top-[14.5px] whitespace-nowrap">3</p>
      </div>
    </div>
  );
}

function Heading4() {
  return (
    <div className="h-[70px] relative shrink-0 w-[225px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[48px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[21.6px] not-italic relative shrink-0 text-[#0b0b10] text-[20px] tracking-[-0.9px] whitespace-nowrap">Approve and preview</p>
      </div>
    </div>
  );
}

function Paragraph7() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[12px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative shrink-0 text-[#3a3a46] text-[14px] w-[225px]">Run the rules over recent data. Activation remains locked until you approve the exact strategy version.</p>
      </div>
    </div>
  );
}

function Article2() {
  return (
    <div className="absolute bg-white content-stretch flex flex-col h-[272.375px] items-start left-[598px] min-h-[260px] p-[29px] rounded-[22px] top-0 w-[283px]" data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
      <Text25 />
      <Heading4 />
      <Paragraph7 />
    </div>
  );
}

function Text26() {
  return (
    <div className="relative rounded-[12px] shrink-0 size-[46px]" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[normal] left-[18.17px] not-italic text-[14px] text-white top-[14.5px] whitespace-nowrap">4</p>
      </div>
    </div>
  );
}

function Heading5() {
  return (
    <div className="h-[70px] relative shrink-0 w-[225px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[48px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[21.6px] not-italic relative shrink-0 text-[#0b0b10] text-[20px] tracking-[-0.9px] whitespace-nowrap">Monitor, then decide</p>
      </div>
    </div>
  );
}

function Paragraph8() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[12px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative shrink-0 text-[#3a3a46] text-[14px] w-[225px]">Receive forming and confirmed setup alerts with evidence. You verify the opportunity and choose what to do.</p>
      </div>
    </div>
  );
}

function Article3() {
  return (
    <div className="absolute bg-white content-stretch flex flex-col h-[272.375px] items-start left-[897px] min-h-[260px] p-[29px] rounded-[22px] top-0 w-[283px]" data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
      <Text26 />
      <Heading5 />
      <Paragraph8 />
    </div>
  );
}

function Container16() {
  return (
    <div className="h-[272.375px] relative shrink-0 w-full" data-name="Container">
      <Article />
      <Article1 />
      <Article2 />
      <Article3 />
    </div>
  );
}

function ContainerMargin2() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[50px] relative size-full">
        <Container16 />
      </div>
    </div>
  );
}

function Section1() {
  return (
    <div className="content-stretch flex flex-col items-start py-[120px] relative shrink-0 w-[1180px]" data-name="Section">
      <Container15 />
      <ContainerMargin2 />
    </div>
  );
}

function SectionMargin1() {
  return (
    <div className="bg-[#f7f7fa] relative shrink-0 w-full" data-name="Section:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center overflow-clip relative rounded-[inherit] size-full">
        <Section1 />
      </div>
    </div>
  );
}

function Paragraph9() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Heavy',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#a78bfa] text-[12px] tracking-[1.44px] uppercase whitespace-nowrap">More context than a signal</p>
      </div>
    </div>
  );
}

function Heading6() {
  return (
    <div className="relative shrink-0 w-full" data-name="Heading 2">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-none not-italic relative shrink-0 text-[#0b0b10] text-[56px] tracking-[-2.24px] w-[720px]">Know what is close, what failed, and why.</p>
      </div>
    </div>
  );
}

function Container18() {
  return (
    <div className="max-w-[720px] relative shrink-0 w-[720px]" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] relative size-full">
        <Paragraph9 />
        <Heading6 />
      </div>
    </div>
  );
}

function Text27() {
  return (
    <div className="absolute drop-shadow-[0px_14px_17px_rgba(139,92,246,0.26)] left-0 rounded-[23px] size-[46px] top-0" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[15px] left-[19.69px] not-italic text-[#f7f3ff] text-[15px] top-[15px] whitespace-nowrap">1</p>
    </div>
  );
}

function Container21() {
  return (
    <div className="h-[58px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <Text27 />
      </div>
    </div>
  );
}

function Heading7() {
  return (
    <div className="h-[49px] relative shrink-0 w-[416px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[30.24px] not-italic relative shrink-0 text-[#0b0b10] text-[28px] tracking-[-1.26px] whitespace-nowrap">Lifecycle Watchlist</p>
      </div>
    </div>
  );
}

function Paragraph10() {
  return (
    <div className="max-w-[520px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[416px]">Rank active setup lifecycles by completion. See passed rules, missing thresholds and whether each candidate is improving or weakening.</p>
      </div>
    </div>
  );
}

function Container20() {
  return (
    <div className="absolute content-stretch flex flex-col h-[212.625px] items-start left-[34px] top-[47.69px] w-[416px]" data-name="Container">
      <Container21 />
      <Heading7 />
      <Paragraph10 />
    </div>
  );
}

function BoldText4() {
  return <div className="bg-[#a78bfa] h-[7px] relative rounded-[8px] shrink-0 w-[346.797px]" data-name="Bold Text" />;
}

function ItalicText3() {
  return (
    <div className="absolute bg-[rgba(255,255,255,0.1)] content-stretch flex flex-col h-[7px] items-start left-[114px] rounded-[8px] top-[20.02px] w-[408px]" data-name="Italic Text">
      <BoldText4 />
    </div>
  );
}

function Container23() {
  return (
    <div className="h-[48.047px] relative shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.07)] border-b border-solid inset-0 pointer-events-none" />
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Regular',sans-serif] font-normal leading-[17.05px] left-0 not-italic text-[#f9f7ff] text-[11px] top-[15px] whitespace-nowrap">SOL/USDT</p>
        <ItalicText3 />
        <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] left-[536px] not-italic text-[#f9f7ff] text-[11px] top-[15px] whitespace-nowrap">85%</p>
      </div>
    </div>
  );
}

function BoldText5() {
  return <div className="bg-[#a78bfa] h-[7px] relative rounded-[8px] shrink-0 w-[285.594px]" data-name="Bold Text" />;
}

function ItalicText4() {
  return (
    <div className="absolute bg-[rgba(255,255,255,0.1)] content-stretch flex flex-col h-[7px] items-start left-[114px] rounded-[8px] top-[20.02px] w-[408px]" data-name="Italic Text">
      <BoldText5 />
    </div>
  );
}

function Container24() {
  return (
    <div className="h-[48.047px] relative shrink-0 w-full" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.07)] border-b border-solid inset-0 pointer-events-none" />
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Regular',sans-serif] font-normal leading-[17.05px] left-0 not-italic text-[#f9f7ff] text-[11px] top-[15px] whitespace-nowrap">LINK/USDT</p>
        <ItalicText4 />
        <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] left-[536px] not-italic text-[#f9f7ff] text-[11px] top-[15px] whitespace-nowrap">70%</p>
      </div>
    </div>
  );
}

function BoldText6() {
  return <div className="bg-[#a78bfa] h-[7px] relative rounded-[8px] shrink-0 w-[224.398px]" data-name="Bold Text" />;
}

function ItalicText5() {
  return (
    <div className="absolute bg-[rgba(255,255,255,0.1)] content-stretch flex flex-col h-[7px] items-start left-[114px] rounded-[8px] top-[20.02px] w-[408px]" data-name="Italic Text">
      <BoldText6 />
    </div>
  );
}

function Container25() {
  return (
    <div className="h-[47.047px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Regular',sans-serif] font-normal leading-[17.05px] left-0 not-italic text-[#f9f7ff] text-[11px] top-[15px] whitespace-nowrap">ETH/USDT</p>
        <ItalicText5 />
        <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] left-[536px] not-italic text-[#f9f7ff] text-[11px] top-[15px] whitespace-nowrap">55%</p>
      </div>
    </div>
  );
}

function Container22() {
  return (
    <div className="absolute bg-[#111114] content-stretch flex flex-col h-[189.141px] items-start left-[520px] p-[23px] rounded-[16px] top-[59.43px] w-[624px]" data-name="Container">
      <div aria-hidden className="absolute border border-[rgba(196,181,253,0.28)] border-solid inset-0 pointer-events-none rounded-[16px]" />
      <Container23 />
      <Container24 />
      <Container25 />
    </div>
  );
}

function Article4() {
  return (
    <div className="absolute bg-white border border-[rgba(17,17,24,0.1)] border-solid h-[310px] left-0 overflow-clip rounded-[22px] top-0 w-[1180px]" data-name="Article">
      <Container20 />
      <Container22 />
    </div>
  );
}

function Text28() {
  return (
    <div className="absolute drop-shadow-[0px_14px_17px_rgba(139,92,246,0.26)] left-0 rounded-[23px] size-[46px] top-0" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[15px] left-[18.21px] not-italic text-[#f7f3ff] text-[15px] top-[15px] whitespace-nowrap">2</p>
    </div>
  );
}

function Container26() {
  return (
    <div className="h-[46px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <Text28 />
      </div>
    </div>
  );
}

function Heading8() {
  return (
    <div className="h-[49px] relative shrink-0 w-[511px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[30.24px] not-italic relative shrink-0 text-[#0b0b10] text-[28px] tracking-[-1.26px] whitespace-nowrap">Explainable Condition Proof</p>
      </div>
    </div>
  );
}

function Paragraph11() {
  return (
    <div className="max-w-[520px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] pt-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[511px]">Every alert includes required and actual values, pass/fail state, candle time, exchange, freshness and strategy version.</p>
      </div>
    </div>
  );
}

function Text29() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[18.6px] not-italic relative shrink-0 text-[#f7f3ff] text-[12px] whitespace-nowrap">{`4h close > EMA 200`}</p>
      </div>
    </div>
  );
}

function BoldText7() {
  return (
    <div className="h-full relative shrink-0" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[15.5px] not-italic relative shrink-0 text-[#a78bfa] text-[10px] whitespace-nowrap">PASS</p>
      </div>
    </div>
  );
}

function Paragraph12() {
  return (
    <div className="h-[39.594px] max-w-[520px] relative shrink-0 w-full" data-name="Paragraph">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.07)] border-b border-solid inset-0 pointer-events-none" />
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-start justify-between max-w-[inherit] pb-[11px] pt-[10px] relative size-full">
        <Text29 />
        <BoldText7 />
      </div>
    </div>
  );
}

function Text30() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[18.6px] not-italic relative shrink-0 text-[#f7f3ff] text-[12px] whitespace-nowrap">Volume ≥ 1.5x</p>
      </div>
    </div>
  );
}

function BoldText8() {
  return (
    <div className="h-full relative shrink-0" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[15.5px] not-italic relative shrink-0 text-[#a78bfa] text-[10px] whitespace-nowrap">PASS</p>
      </div>
    </div>
  );
}

function Paragraph13() {
  return (
    <div className="h-[39.594px] max-w-[520px] relative shrink-0 w-full" data-name="Paragraph">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.07)] border-b border-solid inset-0 pointer-events-none" />
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-start justify-between max-w-[inherit] pb-[11px] pt-[10px] relative size-full">
        <Text30 />
        <BoldText8 />
      </div>
    </div>
  );
}

function Text31() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[18.6px] not-italic relative shrink-0 text-[#f7f3ff] text-[12px] whitespace-nowrap">{`Stop distance < 2%`}</p>
      </div>
    </div>
  );
}

function BoldText9() {
  return (
    <div className="h-full relative shrink-0" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[15.5px] not-italic relative shrink-0 text-[#a78bfa] text-[10px] whitespace-nowrap">PASS</p>
      </div>
    </div>
  );
}

function Paragraph14() {
  return (
    <div className="h-[38.594px] max-w-[520px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-start justify-between max-w-[inherit] py-[10px] relative size-full">
        <Text31 />
        <BoldText9 />
      </div>
    </div>
  );
}

function Container27() {
  return (
    <div className="relative rounded-[15px] shrink-0 w-full" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 511 155.78' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -49.148 -49.148 0 470.12 12.462)'><stop stop-color='rgba(139,92,246,0.2)' offset='0'/><stop stop-color='rgba(70,46,123,0.1)' offset='0.21918'/><stop stop-color='rgba(0,0,0,0)' offset='0.43836'/></radialGradient></defs></svg>\"), linear-gradient(90deg, rgb(23, 19, 31) 0%, rgb(23, 19, 31) 100%)" }} data-name="Container">
      <div aria-hidden className="absolute border border-[rgba(31,31,219,0.1)] border-solid inset-0 pointer-events-none rounded-[15px]" />
      <div className="content-stretch flex flex-col items-start p-[19px] relative size-full">
        <Paragraph12 />
        <Paragraph13 />
        <Paragraph14 />
      </div>
    </div>
  );
}

function ContainerMargin5() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[35px] relative size-full">
        <Container27 />
      </div>
    </div>
  );
}

function Article5() {
  return (
    <div className="absolute bg-white h-[420.609px] left-0 min-h-[380px] rounded-[22px] top-[328px] w-[581px]" data-name="Article">
      <div className="content-stretch flex flex-col items-start min-h-[inherit] overflow-clip p-[35px] relative rounded-[inherit] size-full">
        <Container26 />
        <Heading8 />
        <Paragraph11 />
        <ContainerMargin5 />
      </div>
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
    </div>
  );
}

function Text32() {
  return (
    <div className="absolute drop-shadow-[0px_14px_17px_rgba(139,92,246,0.26)] left-0 rounded-[23px] size-[46px] top-0" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[15px] left-[18.07px] not-italic text-[#f7f3ff] text-[15px] top-[15px] whitespace-nowrap">3</p>
    </div>
  );
}

function Container28() {
  return (
    <div className="h-[46px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <Text32 />
      </div>
    </div>
  );
}

function Heading9() {
  return (
    <div className="h-[49px] relative shrink-0 w-[511px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[30.24px] not-italic relative shrink-0 text-[#0b0b10] text-[28px] tracking-[-1.26px] whitespace-nowrap">{`Why Wasn't I Alerted?`}</p>
      </div>
    </div>
  );
}

function Paragraph15() {
  return (
    <div className="max-w-[520px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] pt-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[511px]">Investigate a symbol and time. Reconstruct failed rules, exclusions, incomplete candles, cooldowns and data incidents.</p>
      </div>
    </div>
  );
}

function Text33() {
  return (
    <div className="relative shrink-0 w-full" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[17.05px] not-italic relative shrink-0 text-[#f7f3ff] text-[11px] whitespace-nowrap">BTC/USDT · 10:30 UTC</p>
      </div>
    </div>
  );
}

function BoldText10() {
  return (
    <div className="relative shrink-0 w-full" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#a78bfa] text-[16px] whitespace-nowrap">Blocked by candle close</p>
      </div>
    </div>
  );
}

function Small3() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[20.667px] not-italic relative shrink-0 text-[#f7f3ff] text-[13.333px] whitespace-nowrap">The 15m candle was incomplete when the condition briefly passed.</p>
      </div>
    </div>
  );
}

function Container29() {
  return (
    <div className="relative rounded-[15px] shrink-0 w-full" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 511 122' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -48.9 -48.302 0 470.12 9.76)'><stop stop-color='rgba(139,92,246,0.2)' offset='0'/><stop stop-color='rgba(70,46,123,0.1)' offset='0.21918'/><stop stop-color='rgba(0,0,0,0)' offset='0.43836'/></radialGradient></defs></svg>\"), linear-gradient(90deg, rgb(23, 19, 31) 0%, rgb(23, 19, 31) 100%)" }} data-name="Container">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[15px]" />
      <div className="content-stretch flex flex-col gap-[10px] items-start p-[19px] relative size-full">
        <Text33 />
        <BoldText10 />
        <Small3 />
      </div>
    </div>
  );
}

function ContainerMargin6() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[35px] relative size-full">
        <Container29 />
      </div>
    </div>
  );
}

function Article6() {
  return (
    <div className="absolute bg-white h-[420.609px] left-[599px] min-h-[380px] rounded-[22px] top-[328px] w-[581px]" data-name="Article">
      <div className="content-stretch flex flex-col items-start min-h-[inherit] overflow-clip p-[35px] relative rounded-[inherit] size-full">
        <Container28 />
        <Heading9 />
        <Paragraph15 />
        <ContainerMargin6 />
      </div>
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
    </div>
  );
}

function Text34() {
  return (
    <div className="absolute drop-shadow-[0px_14px_17px_rgba(139,92,246,0.26)] left-0 rounded-[23px] size-[46px] top-0" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-[750] leading-[15px] left-[17.83px] not-italic text-[#f7f3ff] text-[15px] top-[15px] whitespace-nowrap">4</p>
    </div>
  );
}

function Container31() {
  return (
    <div className="h-[58px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <Text34 />
      </div>
    </div>
  );
}

function Heading10() {
  return (
    <div className="h-[49px] relative shrink-0 w-[416px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[30.24px] not-italic relative shrink-0 text-[#0b0b10] text-[28px] tracking-[-1.26px] whitespace-nowrap">One setup. A complete lifecycle.</p>
      </div>
    </div>
  );
}

function Paragraph16() {
  return (
    <div className="max-w-[520px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[416px]">Follow a persistent monitor from first detection through partial match, condition completion, alert delivery, invalidation or expiration.</p>
      </div>
    </div>
  );
}

function Container30() {
  return (
    <div className="absolute content-stretch flex flex-col h-[212.625px] items-start left-[34px] top-[47.69px] w-[416px]" data-name="Container">
      <Container31 />
      <Heading10 />
      <Paragraph16 />
    </div>
  );
}

function ItalicText6() {
  return (
    <div className="bg-white relative rounded-[9px] shrink-0 size-[18px]" data-name="Italic Text">
      <div aria-hidden className="absolute border-4 border-[#a78bfa] border-solid inset-0 pointer-events-none rounded-[9px]" />
    </div>
  );
}

function Small4() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[14.208px] not-italic relative shrink-0 text-[#3a3a46] text-[9.167px] whitespace-nowrap">14:00</p>
      </div>
    </div>
  );
}

function Text35() {
  return (
    <div className="h-[46.25px] relative shrink-0 w-[124.797px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[15px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] not-italic relative shrink-0 text-[#3a3a46] text-[11px] whitespace-nowrap">Detected</p>
        <Small4 />
      </div>
    </div>
  );
}

function SetupLifecycleExample() {
  return (
    <div className="absolute content-stretch flex flex-col h-[64.25px] items-start left-[520px] top-[121.88px] w-[124.797px]" data-name="Setup lifecycle example">
      <ItalicText6 />
      <Text35 />
    </div>
  );
}

function ItalicText7() {
  return (
    <div className="bg-white relative rounded-[9px] shrink-0 size-[18px]" data-name="Italic Text">
      <div aria-hidden className="absolute border-4 border-[#a78bfa] border-solid inset-0 pointer-events-none rounded-[9px]" />
    </div>
  );
}

function Small5() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[14.208px] not-italic relative shrink-0 text-[#3a3a46] text-[9.167px] whitespace-nowrap">14:15</p>
      </div>
    </div>
  );
}

function Text36() {
  return (
    <div className="h-[46.25px] relative shrink-0 w-[124.797px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[15px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] not-italic relative shrink-0 text-[#3a3a46] text-[11px] whitespace-nowrap">Partial match</p>
        <Small5 />
      </div>
    </div>
  );
}

function Container32() {
  return (
    <div className="absolute content-stretch flex flex-col h-[64.25px] items-start left-[644.8px] top-[121.88px] w-[124.797px]" data-name="Container">
      <ItalicText7 />
      <Text36 />
    </div>
  );
}

function ItalicText8() {
  return (
    <div className="bg-white relative rounded-[9px] shrink-0 size-[18px]" data-name="Italic Text">
      <div aria-hidden className="absolute border-4 border-[#a78bfa] border-solid inset-0 pointer-events-none rounded-[9px] shadow-[0px_0px_0px_0px_rgba(167,139,250,0.18)]" />
    </div>
  );
}

function Small6() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[14.208px] not-italic relative shrink-0 text-[#3a3a46] text-[9.167px] whitespace-nowrap">14:45</p>
      </div>
    </div>
  );
}

function Text37() {
  return (
    <div className="h-[46.25px] relative shrink-0 w-[124.805px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[15px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] not-italic relative shrink-0 text-[#3a3a46] text-[11px] whitespace-nowrap">Conditions complete</p>
        <Small6 />
      </div>
    </div>
  );
}

function Container33() {
  return (
    <div className="absolute content-stretch flex flex-col h-[64.25px] items-start left-[769.59px] top-[121.88px] w-[124.805px]" data-name="Container">
      <ItalicText8 />
      <Text37 />
    </div>
  );
}

function ItalicText9() {
  return (
    <div className="bg-white relative rounded-[9px] shrink-0 size-[18px]" data-name="Italic Text">
      <div aria-hidden className="absolute border-4 border-[rgba(196,181,253,0.42)] border-solid inset-0 pointer-events-none rounded-[9px]" />
    </div>
  );
}

function Small7() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[14.208px] not-italic relative shrink-0 text-[#3a3a46] text-[9.167px] whitespace-nowrap">Ready</p>
      </div>
    </div>
  );
}

function Text38() {
  return (
    <div className="h-[46.25px] relative shrink-0 w-[124.797px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[15px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] not-italic relative shrink-0 text-[#3a3a46] text-[11px] whitespace-nowrap">Alert delivered</p>
        <Small7 />
      </div>
    </div>
  );
}

function Container34() {
  return (
    <div className="absolute content-stretch flex flex-col h-[64.25px] items-start left-[894.4px] top-[121.88px] w-[124.797px]" data-name="Container">
      <ItalicText9 />
      <Text38 />
    </div>
  );
}

function ItalicText10() {
  return (
    <div className="bg-white relative rounded-[9px] shrink-0 size-[18px]" data-name="Italic Text">
      <div aria-hidden className="absolute border-4 border-[rgba(196,181,253,0.42)] border-solid inset-0 pointer-events-none rounded-[9px]" />
    </div>
  );
}

function Small8() {
  return (
    <div className="relative shrink-0 w-full" data-name="Small">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[14.208px] not-italic relative shrink-0 text-[#3a3a46] text-[9.167px] whitespace-nowrap">Pending</p>
      </div>
    </div>
  );
}

function Text39() {
  return (
    <div className="h-[46.25px] relative shrink-0 w-[124.805px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[15px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[17.05px] not-italic relative shrink-0 text-[#3a3a46] text-[11px] whitespace-nowrap">No longer matching</p>
        <Small8 />
      </div>
    </div>
  );
}

function Container35() {
  return (
    <div className="absolute content-stretch flex flex-col h-[64.25px] items-start left-[1019.2px] top-[121.88px] w-[124.805px]" data-name="Container">
      <ItalicText10 />
      <Text39 />
    </div>
  );
}

function Article7() {
  return (
    <div className="absolute bg-white border border-[rgba(17,17,24,0.1)] border-solid h-[310px] left-0 overflow-clip rounded-[22px] top-[766.61px] w-[1180px]" data-name="Article">
      <Container30 />
      <SetupLifecycleExample />
      <Container32 />
      <Container33 />
      <Container34 />
      <Container35 />
    </div>
  );
}

function Container19() {
  return (
    <div className="h-[1076.609px] relative shrink-0 w-full" data-name="Container">
      <Article4 />
      <Article5 />
      <Article6 />
      <Article7 />
    </div>
  );
}

function ContainerMargin4() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[50px] relative size-full">
        <Container19 />
      </div>
    </div>
  );
}

function Container17() {
  return (
    <div className="content-stretch flex flex-col items-start relative shrink-0 w-[1180px]" data-name="Container">
      <Container18 />
      <ContainerMargin4 />
    </div>
  );
}

function ContainerMargin3() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Container17 />
      </div>
    </div>
  );
}

function Section2() {
  return (
    <div className="bg-[#f7f7fa] relative shrink-0 w-full" data-name="Section">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[120px] relative size-full">
        <ContainerMargin3 />
      </div>
    </div>
  );
}

function Paragraph17() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Heavy',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#a78bfa] text-[12px] tracking-[1.44px] uppercase whitespace-nowrap">Monitoring toolkit</p>
      </div>
    </div>
  );
}

function Heading11() {
  return (
    <div className="relative shrink-0 w-full" data-name="Heading 2">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-none not-italic relative shrink-0 text-[#0b0b10] text-[56px] tracking-[-2.24px] w-[800px]">The standard capabilities serious monitoring needs.</p>
      </div>
    </div>
  );
}

function Container36() {
  return (
    <div className="max-w-[800px] relative shrink-0 w-[800px]" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] relative size-full">
        <Paragraph17 />
        <Heading11 />
      </div>
    </div>
  );
}

function Text40() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-0 px-[17px] py-[13px] rounded-[50px] top-0" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">AND / OR rule groups</p>
    </div>
  );
}

function Text41() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[177.55px] px-[17px] py-[13px] rounded-[50px] top-0" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Multi-timeframe filters</p>
    </div>
  );
}

function Text42() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[363.32px] px-[17px] py-[13px] rounded-[50px] top-0" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Technical indicators</p>
    </div>
  );
}

function Text43() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[534.02px] px-[17px] py-[13px] rounded-[50px] top-0" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Candlestick patterns</p>
    </div>
  );
}

function Text44() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[708.36px] px-[17px] py-[13px] rounded-[50px] top-0" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Price action</p>
    </div>
  );
}

function Text45() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[826.83px] px-[17px] py-[13px] rounded-[50px] top-0" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Volume and liquidity filters</p>
    </div>
  );
}

function Text46() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-0 px-[17px] py-[13px] rounded-[50px] top-[56.15px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Spread and listing-age filters</p>
    </div>
  );
}

function Text47() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[226.13px] px-[17px] py-[13px] rounded-[50px] top-[56.15px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Optional entry, stop and target context</p>
    </div>
  );
}

function Text48() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[512.02px] px-[17px] py-[13px] rounded-[50px] top-[56.15px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Optional reward-to-risk validation</p>
    </div>
  );
}

function Text49() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[768.01px] px-[17px] py-[13px] rounded-[50px] top-[56.15px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Candle close or intrabar</p>
    </div>
  );
}

function Text50() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-0 px-[17px] py-[13px] rounded-[50px] top-[112.3px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Cooldowns and deduplication</p>
    </div>
  );
}

function Text51() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[230.55px] px-[17px] py-[13px] rounded-[50px] top-[112.3px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Forward testing</p>
    </div>
  );
}

function Text52() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[372.95px] px-[17px] py-[13px] rounded-[50px] top-[112.3px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Recent-market preview</p>
    </div>
  );
}

function Text53() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[563.66px] px-[17px] py-[13px] rounded-[50px] top-[112.3px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Chart snapshots</p>
    </div>
  );
}

function Text54() {
  return (
    <div className="absolute content-stretch flex flex-col h-[46.148px] items-start left-[711.25px] px-[17px] py-[13px] rounded-[50px] top-[112.3px]" data-name="Text">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[50px]" />
      <p className="[word-break:break-word] font-['Inter:Semi_Bold',sans-serif] font-semibold leading-[20.15px] not-italic relative shrink-0 text-[#3a3a46] text-[13px] whitespace-nowrap">Performance analytics</p>
    </div>
  );
}

function Container37() {
  return (
    <div className="h-[158.445px] relative shrink-0 w-full" data-name="Container">
      <Text40 />
      <Text41 />
      <Text42 />
      <Text43 />
      <Text44 />
      <Text45 />
      <Text46 />
      <Text47 />
      <Text48 />
      <Text49 />
      <Text50 />
      <Text51 />
      <Text52 />
      <Text53 />
      <Text54 />
    </div>
  );
}

function ContainerMargin7() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[50px] relative size-full">
        <Container37 />
      </div>
    </div>
  );
}

function Section3() {
  return (
    <div className="content-stretch flex flex-col items-start py-[120px] relative shrink-0 w-[1180px]" data-name="Section">
      <Container36 />
      <ContainerMargin7 />
    </div>
  );
}

function SectionMargin2() {
  return (
    <div className="relative shrink-0 w-full" data-name="Section:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Section3 />
      </div>
    </div>
  );
}

function Paragraph18() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Heavy',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#a78bfa] text-[12px] tracking-[1.44px] uppercase whitespace-nowrap">Alerts where they belong</p>
      </div>
    </div>
  );
}

function Heading12() {
  return (
    <div className="relative shrink-0 w-full" data-name="Heading 2">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-none not-italic relative shrink-0 text-[#111114] text-[52px] tracking-[-2.08px] w-[486px]">Start quickly in Telegram. Go deeper on the web.</p>
      </div>
    </div>
  );
}

function Paragraph19() {
  return (
    <div className="content-stretch flex flex-col items-start relative shrink-0 w-full" data-name="Paragraph">
      <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[26.35px] not-italic relative shrink-0 text-[#3a3a46] text-[17px] w-[486px]">Telegram is the fastest path to onboarding and immediate alerts. The web dashboard supports detailed strategy editing, proof tables, analytics and billing. Add Discord later for organized channels, communities and support.</p>
    </div>
  );
}

function ParagraphMargin1() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[17px] pt-[43.16px] relative size-full">
        <Paragraph19 />
      </div>
    </div>
  );
}

function Link2() {
  return (
    <div className="absolute content-stretch drop-shadow-[0px_16px_21px_rgba(139,92,246,0.34)] flex h-[50px] items-center justify-center left-0 min-h-[50px] px-[23px] py-px rounded-[13px] top-[18px] w-[171.891px]" style={{ backgroundImage: "linear-gradient(163.781deg, rgb(167, 139, 250) 0%, rgb(139, 92, 246) 100%)" }} data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(0,0,0,0)] border-solid inset-0 pointer-events-none rounded-[13px]" />
      <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[16px] text-white whitespace-nowrap">Join the trace →</p>
    </div>
  );
}

function Container40() {
  return (
    <div className="h-[68px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <Link2 />
      </div>
    </div>
  );
}

function Container39() {
  return (
    <div className="absolute content-stretch flex flex-col h-[438.594px] items-start left-0 top-0 w-[486px]" data-name="Container">
      <Paragraph18 />
      <Heading12 />
      <ParagraphMargin1 />
      <Container40 />
    </div>
  );
}

function Image3() {
  return (
    <div className="absolute left-[8.5px] size-[23px] top-[8.5px]" data-name="Image">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 23 23">
        <g clipPath="url(#clip0_1_578)" id="Image">
          <path d={svgPaths.p2c331bf0} fill="var(--fill-0, black)" id="Vector" />
        </g>
        <defs>
          <clipPath id="clip0_1_578">
            <rect fill="white" height="23" width="23" />
          </clipPath>
        </defs>
      </svg>
    </div>
  );
}

function Text55() {
  return (
    <div className="bg-[#26a5e4] relative rounded-[12px] shrink-0 size-[40px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid overflow-clip relative rounded-[inherit] size-full">
        <Image3 />
      </div>
    </div>
  );
}

function Heading13() {
  return (
    <div className="h-[41px] relative shrink-0 w-[239px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[20px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[20.218px] not-italic relative shrink-0 text-[#f7f3ff] text-[18.72px] tracking-[-0.8424px] whitespace-nowrap">Telegram</p>
      </div>
    </div>
  );
}

function Paragraph20() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[8px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[20.15px] not-italic relative shrink-0 text-[#f7f3ff] text-[13px] w-[239px]">Primary onboarding, alerts and quick setup status.</p>
      </div>
    </div>
  );
}

function Article8() {
  return (
    <div className="absolute content-stretch flex flex-col h-[180.508px] items-start left-[586px] p-[26px] rounded-[18px] top-[42.86px] w-[291px]" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 291 180.51' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -31.504 -31.504 0 267.72 14.441)'><stop stop-color='rgba(139,92,246,0.2)' offset='0'/><stop stop-color='rgba(70,46,123,0.1)' offset='0.38488'/><stop stop-color='rgba(0,0,0,0)' offset='0.76976'/></radialGradient></defs></svg>\"), linear-gradient(90deg, rgb(23, 19, 31) 0%, rgb(23, 19, 31) 100%)" }} data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[18px]" />
      <Text55 />
      <Heading13 />
      <Paragraph20 />
    </div>
  );
}

function Image4() {
  return (
    <div className="absolute left-[8.5px] size-[23px] top-[8.5px]" data-name="Image">
      <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 23 23">
        <g clipPath="url(#clip0_1_575)" id="Image">
          <path d={svgPaths.p1950a8f0} fill="var(--fill-0, black)" id="Vector" />
        </g>
        <defs>
          <clipPath id="clip0_1_575">
            <rect fill="white" height="23" width="23" />
          </clipPath>
        </defs>
      </svg>
    </div>
  );
}

function Text56() {
  return (
    <div className="bg-[#5865f2] relative rounded-[12px] shrink-0 size-[40px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid overflow-clip relative rounded-[inherit] size-full">
        <Image4 />
      </div>
    </div>
  );
}

function Heading14() {
  return (
    <div className="h-[41px] relative shrink-0 w-[239px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[20px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[20.218px] not-italic relative shrink-0 text-[#f7f3ff] text-[18.72px] tracking-[-0.8424px] whitespace-nowrap">Discord</p>
      </div>
    </div>
  );
}

function Paragraph21() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[8px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[20.15px] not-italic relative shrink-0 text-[#f7f3ff] text-[13px] w-[239px]">Optional alert channels, community and role management.</p>
      </div>
    </div>
  );
}

function Article9() {
  return (
    <div className="absolute content-stretch flex flex-col h-[180.508px] items-start left-[889px] p-[26px] rounded-[18px] top-[42.86px] w-[291px]" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 291 180.51' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -31.504 -31.504 0 267.72 14.441)'><stop stop-color='rgba(139,92,246,0.2)' offset='0'/><stop stop-color='rgba(70,46,123,0.1)' offset='0.38488'/><stop stop-color='rgba(0,0,0,0)' offset='0.76976'/></radialGradient></defs></svg>\"), linear-gradient(90deg, rgb(23, 19, 31) 0%, rgb(23, 19, 31) 100%)" }} data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[18px]" />
      <Text56 />
      <Heading14 />
      <Paragraph21 />
    </div>
  );
}

function Text57() {
  return (
    <div className="bg-white relative rounded-[12px] shrink-0 size-[40px]" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid overflow-clip relative rounded-[inherit] size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[24.8px] left-[11.52px] not-italic text-[#9167f7] text-[16px] top-[7.1px] whitespace-nowrap">W</p>
      </div>
    </div>
  );
}

function Heading15() {
  return (
    <div className="h-[41px] relative shrink-0 w-[542px]" data-name="Heading 3">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[20px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[20.218px] not-italic relative shrink-0 text-[#f7f3ff] text-[18.72px] tracking-[-0.8424px] whitespace-nowrap">Web dashboard</p>
      </div>
    </div>
  );
}

function Paragraph22() {
  return (
    <div className="h-[29px] relative shrink-0 w-[542px]" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[8px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[20.15px] not-italic relative shrink-0 text-[#f7f3ff] text-[13px] whitespace-nowrap">Advanced rules, analytics, lifecycles and account controls.</p>
      </div>
    </div>
  );
}

function Article10() {
  return (
    <div className="absolute content-stretch flex flex-col h-[160.359px] items-start left-[586px] p-[26px] rounded-[18px] top-[235.37px] w-[594px]" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 594 160.36' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -56.604 -56.604 0 546.48 12.829)'><stop stop-color='rgba(139,92,246,0.2)' offset='0'/><stop stop-color='rgba(70,46,123,0.1)' offset='0.18855'/><stop stop-color='rgba(0,0,0,0)' offset='0.3771'/></radialGradient></defs></svg>\"), linear-gradient(90deg, rgb(23, 19, 31) 0%, rgb(23, 19, 31) 100%)" }} data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[18px]" />
      <Text57 />
      <Heading15 />
      <Paragraph22 />
    </div>
  );
}

function Container38() {
  return (
    <div className="h-[438.594px] relative shrink-0 w-[1180px]" data-name="Container">
      <Container39 />
      <Article8 />
      <Article9 />
      <Article10 />
    </div>
  );
}

function ContainerMargin8() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Container38 />
      </div>
    </div>
  );
}

function Section4() {
  return (
    <div className="bg-[#f7f7fa] relative shrink-0 w-full" data-name="Section">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[110px] relative size-full">
        <ContainerMargin8 />
      </div>
    </div>
  );
}

function Paragraph23() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Heavy',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#a78bfa] text-[12px] text-center tracking-[1.44px] uppercase whitespace-nowrap">Configurable plans</p>
      </div>
    </div>
  );
}

function Heading16() {
  return (
    <div className="relative shrink-0 w-full" data-name="Heading 2">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-none not-italic relative shrink-0 text-[#0b0b10] text-[56px] text-center tracking-[-2.24px] w-[720px]">Choose the monitoring capacity you need.</p>
      </div>
    </div>
  );
}

function Paragraph24() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[27.9px] not-italic relative shrink-0 text-[#3a3a46] text-[18px] text-center w-[720px]">Plans are constrained by active strategies, monitored symbols, scan frequency and advanced features, not by promises of trading results.</p>
      </div>
    </div>
  );
}

function Container41() {
  return (
    <div className="content-stretch flex flex-col items-start max-w-[720px] relative shrink-0 w-[720px]" data-name="Container">
      <Paragraph23 />
      <Heading16 />
      <Paragraph24 />
    </div>
  );
}

function ContainerMargin9() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Container41 />
      </div>
    </div>
  );
}

function Paragraph25() {
  return (
    <div className="h-[41px] relative shrink-0 w-[321px]" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[20px] whitespace-nowrap">Demo / Free</p>
      </div>
    </div>
  );
}

function Paragraph26() {
  return (
    <div className="min-h-[54px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start min-h-[inherit] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.4] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[321px]">A light way to understand the monitor before upgrading.</p>
      </div>
    </div>
  );
}

function Heading17() {
  return (
    <div className="h-[55.313px] relative rounded-[18px] shrink-0 w-[67.93px]" data-name="Heading 3">
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[31.32px] left-0 not-italic text-[#0b0b10] text-[29px] top-[12px] tracking-[-1.305px] whitespace-nowrap">$0</p>
    </div>
  );
}

function Heading3Margin() {
  return (
    <div className="relative shrink-0" data-name="Heading 3:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[12px] pt-[8px] relative size-full">
        <Heading17 />
      </div>
    </div>
  );
}

function Link3() {
  return (
    <div className="bg-[#c4b5fd] min-h-[50px] relative rounded-[13px] shrink-0 w-full" data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(139,92,246,0.28)] border-solid inset-0 pointer-events-none rounded-[13px]" />
      <div className="flex flex-row items-center justify-center min-h-[inherit] size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-center justify-center min-h-[inherit] px-[23px] py-px relative size-full">
          <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[#2b0d54] text-[16px] whitespace-nowrap">Start free</p>
        </div>
      </div>
    </div>
  );
}

function ListItem3() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">One active monitor</p>
      </div>
    </div>
  );
}

function ListItem4() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">Up to 50 spot pairs</p>
      </div>
    </div>
  );
}

function ListItem5() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">15-minute timeframe or higher</p>
      </div>
    </div>
  );
}

function ListItem6() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">Telegram only</p>
      </div>
    </div>
  );
}

function List() {
  return (
    <div className="content-stretch flex flex-col h-[158.781px] items-start relative shrink-0 w-full" data-name="List">
      <ListItem3 />
      <ListItem4 />
      <ListItem5 />
      <ListItem6 />
    </div>
  );
}

function ListMargin() {
  return (
    <div className="h-[172px] relative shrink-0 w-full" data-name="List:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[35px] pt-[4px] relative size-full">
        <List />
      </div>
    </div>
  );
}

function Article11() {
  return (
    <div className="absolute bg-white content-stretch flex flex-col gap-[14px] items-start left-0 p-[31px] rounded-[22px] top-0 w-[382.664px]" data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
      <Paragraph25 />
      <Paragraph26 />
      <Heading3Margin />
      <Link3 />
      <ListMargin />
    </div>
  );
}

function Paragraph27() {
  return (
    <div className="h-[41px] relative shrink-0 w-[320px]" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[20px] text-white whitespace-nowrap">Pro</p>
      </div>
    </div>
  );
}

function Paragraph28() {
  return (
    <div className="min-h-[54px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start min-h-[inherit] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.4] not-italic relative shrink-0 text-[16px] text-white w-[321px]">Full individual monitoring with Telegram, Discord and lifecycle evidence.</p>
      </div>
    </div>
  );
}

function Heading18() {
  return (
    <div className="[word-break:break-word] h-[56px] relative rounded-[18px] shrink-0 text-white w-[139px] whitespace-nowrap" data-name="Heading 3">
      <p className="absolute font-['Inter:Bold',sans-serif] font-bold leading-[31.32px] left-[0.34px] not-italic text-[29px] top-[12px] tracking-[-1.305px]">{`$29 `}</p>
      <p className="absolute font-['DM_Sans:ExtraBold',sans-serif] font-[760] leading-[normal] left-[60.52px] text-[12px] top-[27px]" style={{ fontVariationSettings: '"opsz" 14, "opsz" 14' }}>
        / month
      </p>
    </div>
  );
}

function Heading3Margin1() {
  return (
    <div className="relative shrink-0" data-name="Heading 3:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[12px] pt-[8px] relative size-full">
        <Heading18 />
      </div>
    </div>
  );
}

function Link4() {
  return (
    <div className="drop-shadow-[0px_16px_21px_rgba(139,92,246,0.34)] h-[50px] min-h-[50px] relative rounded-[13px] shrink-0 w-full" style={{ backgroundImage: "linear-gradient(162.275deg, rgb(167, 139, 250) 16.311%, rgb(139, 92, 246) 83.383%)" }} data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(255,255,255,0.5)] border-solid inset-0 pointer-events-none rounded-[13px]" />
      <div className="flex flex-row items-center justify-center min-h-[inherit] size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-center justify-center min-h-[inherit] px-[23px] py-px relative size-full">
          <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[16px] text-white whitespace-nowrap">Start Pro Trial</p>
        </div>
      </div>
    </div>
  );
}

function ListItem7() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#e0d8ff] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-white top-[9px]">Up to 10 active strategies</p>
      </div>
    </div>
  );
}

function ListItem8() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#e0d8ff] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-white top-[9px]">All supported spot pairs</p>
      </div>
    </div>
  );
}

function ListItem9() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#e0d8ff] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-white top-[9px]">Telegram and Discord alerts</p>
      </div>
    </div>
  );
}

function ListItem10() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#e0d8ff] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-white top-[9px]">Lifecycle and forensic history</p>
      </div>
    </div>
  );
}

function List1() {
  return (
    <div className="content-stretch flex flex-col h-[158.781px] items-start relative shrink-0 w-full" data-name="List">
      <ListItem7 />
      <ListItem8 />
      <ListItem9 />
      <ListItem10 />
    </div>
  );
}

function ListMargin1() {
  return (
    <div className="h-[173px] relative shrink-0 w-full" data-name="List:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[35px] pt-[4px] relative size-full">
        <List1 />
      </div>
    </div>
  );
}

function Article12() {
  return (
    <div className="absolute content-stretch drop-shadow-[0px_24px_35px_rgba(70,45,110,0.14)] flex flex-col gap-[14px] items-start left-[398.66px] p-[31px] rounded-[22px] top-0 w-[382.664px]" style={{ backgroundImage: "linear-gradient(132.933deg, rgb(119, 96, 186) 0.94504%, rgb(23, 10, 52) 93.322%)" }} data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
      <Paragraph27 />
      <Paragraph28 />
      <Heading3Margin1 />
      <Link4 />
      <ListMargin1 />
    </div>
  );
}

function Paragraph29() {
  return (
    <div className="h-[41px] relative shrink-0 w-[321px]" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[20px] whitespace-nowrap">Creator / Community</p>
      </div>
    </div>
  );
}

function Paragraph30() {
  return (
    <div className="min-h-[54px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start min-h-[inherit] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.4] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[321px]">Shared strategy workflows for creators and private communities.</p>
      </div>
    </div>
  );
}

function Heading19() {
  return (
    <div className="[word-break:break-word] h-[55.313px] relative rounded-[18px] shrink-0 w-[148.555px] whitespace-nowrap" data-name="Heading 3">
      <p className="absolute font-['Inter:Bold',sans-serif] font-bold leading-[31.32px] left-[-0.33px] not-italic text-[#0b0b10] text-[29px] top-[11.77px] tracking-[-1.305px]">{`$79+ `}</p>
      <p className="absolute font-['DM_Sans:ExtraBold',sans-serif] font-[760] leading-[normal] left-[76.55px] text-[#3a3a46] text-[12px] top-[26.77px]" style={{ fontVariationSettings: '"opsz" 14, "opsz" 14' }}>
        / month
      </p>
    </div>
  );
}

function Heading3Margin2() {
  return (
    <div className="relative shrink-0" data-name="Heading 3:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[12px] pt-[8px] relative size-full">
        <Heading19 />
      </div>
    </div>
  );
}

function Link5() {
  return (
    <div className="bg-[#c4b5fd] min-h-[50px] relative rounded-[13px] shrink-0 w-full" data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(139,92,246,0.28)] border-solid inset-0 pointer-events-none rounded-[13px]" />
      <div className="flex flex-row items-center justify-center min-h-[inherit] size-full">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex items-center justify-center min-h-[inherit] px-[23px] py-px relative size-full">
          <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[#2b0d54] text-[16px] whitespace-nowrap">Contact us</p>
        </div>
      </div>
    </div>
  );
}

function ListItem11() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">Shared templates and channels</p>
      </div>
    </div>
  );
}

function ListItem12() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">Discord role integration</p>
      </div>
    </div>
  );
}

function ListItem13() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">Community delivery</p>
      </div>
    </div>
  );
}

function ListItem14() {
  return (
    <div className="h-[39.695px] relative shrink-0 w-full" data-name="List Item">
      <div className="[word-break:break-word] bg-clip-padding border-0 border-[transparent] border-solid font-['Inter:Regular',sans-serif] font-normal leading-[21.7px] not-italic relative size-full text-[14px] whitespace-nowrap">
        <p className="absolute left-0 text-[#8b5cf6] top-[9px]">✓</p>
        <p className="absolute left-[20.34px] text-[#3a3a46] top-[9px]">White-label options by plan</p>
      </div>
    </div>
  );
}

function List2() {
  return (
    <div className="content-stretch flex flex-col h-[158.781px] items-start relative shrink-0 w-full" data-name="List">
      <ListItem11 />
      <ListItem12 />
      <ListItem13 />
      <ListItem14 />
    </div>
  );
}

function ListMargin2() {
  return (
    <div className="h-[172px] relative shrink-0 w-full" data-name="List:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[35px] pt-[4px] relative size-full">
        <List2 />
      </div>
    </div>
  );
}

function Article13() {
  return (
    <div className="absolute bg-white content-stretch flex flex-col gap-[14px] items-start left-[797.33px] p-[31px] rounded-[22px] top-0 w-[382.664px]" data-name="Article">
      <div aria-hidden className="absolute border border-[rgba(17,17,24,0.1)] border-solid inset-0 pointer-events-none rounded-[22px]" />
      <Paragraph29 />
      <Paragraph30 />
      <Heading3Margin2 />
      <Link5 />
      <ListMargin2 />
    </div>
  );
}

function Container42() {
  return (
    <div className="h-[572.281px] relative shrink-0 w-full" data-name="Container">
      <Article11 />
      <Article12 />
      <Article13 />
    </div>
  );
}

function ContainerMargin10() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[50px] relative size-full">
        <Container42 />
      </div>
    </div>
  );
}

function ParagraphMargin2() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center pt-[35px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[0] not-italic relative shrink-0 text-[#3a3a46] text-[0px] text-center w-[850px]">
          <span className="leading-[20.15px] text-[13px]">About the trial:</span>
          <span className="font-['Inter:Regular',sans-serif] font-normal leading-[20.15px] text-[13px]">{` The monitoring cycle lasts 14 calendar days and starts when the first approved live monitor activates. If no qualifying live setup alert is successfully delivered during a cycle, the trial renews automatically unless the no-alert outcome was caused by user-side ineligibility. Historical preview, sample proof receipts and forward testing are included so the experience does not depend on rare live setups appearing.`}</span>
        </p>
      </div>
    </div>
  );
}

function Section5() {
  return (
    <div className="content-stretch flex flex-col items-start py-[120px] relative shrink-0 w-[1180px]" data-name="Section">
      <ContainerMargin9 />
      <ContainerMargin10 />
      <ParagraphMargin2 />
    </div>
  );
}

function SectionMargin3() {
  return (
    <div className="relative shrink-0 w-full" data-name="Section:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Section5 />
      </div>
    </div>
  );
}

function Paragraph31() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Heavy',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#a78bfa] text-[12px] tracking-[1.44px] uppercase whitespace-nowrap">Questions, answered plainly</p>
      </div>
    </div>
  );
}

function Heading20() {
  return (
    <div className="relative shrink-0 w-full" data-name="Heading 2">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-none not-italic relative shrink-0 text-[#0b0b10] text-[56px] tracking-[-2.24px] w-[436px]">What TraceEdge does and does not do.</p>
      </div>
    </div>
  );
}

function Container44() {
  return (
    <div className="absolute content-stretch flex flex-col h-[277.5px] items-start left-0 max-w-[720px] top-0 w-[436px]" data-name="Container">
      <Paragraph31 />
      <Heading20 />
    </div>
  );
}

function Text58() {
  return (
    <div className="absolute content-stretch flex flex-col h-[20.5px] items-start left-[642.6px] top-0 w-[11.398px]" data-name="Text">
      <p className="[word-break:break-word] font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] not-italic relative shrink-0 text-[#8b5cf6] text-[17px] tracking-[-0.255px] whitespace-nowrap">+</p>
    </div>
  );
}

function Summary() {
  return (
    <div className="h-[20.5px] relative shrink-0 w-full" data-name="Summary">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] left-0 not-italic text-[#0b0b10] text-[17px] top-0 tracking-[-0.255px] whitespace-nowrap">Does it place trades for me?</p>
        <Text58 />
      </div>
    </div>
  );
}

function Paragraph32() {
  return (
    <div className="max-w-[680px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[654px]">No. Version one does not connect to exchange trading APIs or execute orders. It monitors, explains and alerts; every trading decision remains yours.</p>
      </div>
    </div>
  );
}

function Details() {
  return (
    <div className="h-[65.5px] relative shrink-0 w-full" data-name="Details">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start overflow-clip pb-[23px] pt-[22px] relative rounded-[inherit] size-full">
        <Summary />
        <Paragraph32 />
      </div>
      <div aria-hidden className="absolute border-[rgba(17,17,24,0.1)] border-b border-solid inset-0 pointer-events-none" />
    </div>
  );
}

function Text59() {
  return (
    <div className="absolute content-stretch flex flex-col h-[20.5px] items-start left-[642.6px] top-0 w-[11.398px]" data-name="Text">
      <p className="[word-break:break-word] font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] not-italic relative shrink-0 text-[#8b5cf6] text-[17px] tracking-[-0.255px] whitespace-nowrap">+</p>
    </div>
  );
}

function Summary1() {
  return (
    <div className="h-[20.5px] relative shrink-0 w-full" data-name="Summary">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] left-0 not-italic text-[#0b0b10] text-[17px] top-0 tracking-[-0.255px] whitespace-nowrap">Does AI decide whether a setup passed?</p>
        <Text59 />
      </div>
    </div>
  );
}

function Paragraph33() {
  return (
    <div className="max-w-[680px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[654px]">No. AI can translate your description and explain deterministic results. Indicator calculations and rule outcomes come from the rule engine, and you must approve the structured interpretation.</p>
      </div>
    </div>
  );
}

function Details1() {
  return (
    <div className="h-[65.5px] relative shrink-0 w-full" data-name="Details">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start overflow-clip pb-[23px] pt-[22px] relative rounded-[inherit] size-full">
        <Summary1 />
        <Paragraph33 />
      </div>
      <div aria-hidden className="absolute border-[rgba(17,17,24,0.1)] border-b border-solid inset-0 pointer-events-none" />
    </div>
  );
}

function Text60() {
  return (
    <div className="absolute content-stretch flex flex-col h-[20.5px] items-start left-[642.6px] top-0 w-[11.398px]" data-name="Text">
      <p className="[word-break:break-word] font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] not-italic relative shrink-0 text-[#8b5cf6] text-[17px] tracking-[-0.255px] whitespace-nowrap">+</p>
    </div>
  );
}

function Summary2() {
  return (
    <div className="h-[20.5px] relative shrink-0 w-full" data-name="Summary">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] left-0 not-italic text-[#0b0b10] text-[17px] top-0 tracking-[-0.255px] whitespace-nowrap">Can I use more than one timeframe?</p>
        <Text60 />
      </div>
    </div>
  );
}

function Paragraph34() {
  return (
    <div className="max-w-[680px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[654px]">Yes. A 15-minute entry can depend on a four-hour trend filter or other declared supporting timeframes.</p>
      </div>
    </div>
  );
}

function Details2() {
  return (
    <div className="h-[65.5px] relative shrink-0 w-full" data-name="Details">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start overflow-clip pb-[23px] pt-[22px] relative rounded-[inherit] size-full">
        <Summary2 />
        <Paragraph34 />
      </div>
      <div aria-hidden className="absolute border-[rgba(17,17,24,0.1)] border-b border-solid inset-0 pointer-events-none" />
    </div>
  );
}

function Text61() {
  return (
    <div className="absolute content-stretch flex flex-col h-[20.5px] items-start left-[642.6px] top-0 w-[11.398px]" data-name="Text">
      <p className="[word-break:break-word] font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] not-italic relative shrink-0 text-[#8b5cf6] text-[17px] tracking-[-0.255px] whitespace-nowrap">+</p>
    </div>
  );
}

function Summary3() {
  return (
    <div className="h-[20.5px] relative shrink-0 w-full" data-name="Summary">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] left-0 not-italic text-[#0b0b10] text-[17px] top-0 tracking-[-0.255px] whitespace-nowrap">What if my setup is subjective?</p>
        <Text61 />
      </div>
    </div>
  );
}

function Paragraph35() {
  return (
    <div className="max-w-[680px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[654px]">The interpretation identifies ambiguous or unsupported terms and asks you to define them. A strategy cannot activate while those issues remain unresolved.</p>
      </div>
    </div>
  );
}

function Details3() {
  return (
    <div className="h-[65.5px] relative shrink-0 w-full" data-name="Details">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start overflow-clip pb-[23px] pt-[22px] relative rounded-[inherit] size-full">
        <Summary3 />
        <Paragraph35 />
      </div>
      <div aria-hidden className="absolute border-[rgba(17,17,24,0.1)] border-b border-solid inset-0 pointer-events-none" />
    </div>
  );
}

function Text62() {
  return (
    <div className="absolute content-stretch flex flex-col h-[20.5px] items-start left-[642.6px] top-0 w-[11.398px]" data-name="Text">
      <p className="[word-break:break-word] font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] not-italic relative shrink-0 text-[#8b5cf6] text-[17px] tracking-[-0.255px] whitespace-nowrap">+</p>
    </div>
  );
}

function Summary4() {
  return (
    <div className="h-[20.5px] relative shrink-0 w-full" data-name="Summary">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Extra_Bold',sans-serif] font-extrabold leading-[normal] left-0 not-italic text-[#0b0b10] text-[17px] top-0 tracking-[-0.255px] whitespace-nowrap">Are alerts guaranteed to be accurate or immediate?</p>
        <Text62 />
      </div>
    </div>
  );
}

function Paragraph36() {
  return (
    <div className="max-w-[680px] relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start max-w-[inherit] py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] w-[654px]">No. Exchange outages, network delay, incomplete data and software failures can affect results. Each receipt exposes candle timing and data freshness so you can verify it.</p>
      </div>
    </div>
  );
}

function Details4() {
  return (
    <div className="h-[65.5px] relative shrink-0 w-full" data-name="Details">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start overflow-clip pb-[23px] pt-[22px] relative rounded-[inherit] size-full">
        <Summary4 />
        <Paragraph36 />
      </div>
      <div aria-hidden className="absolute border-[rgba(17,17,24,0.1)] border-b border-solid inset-0 pointer-events-none" />
    </div>
  );
}

function Container45() {
  return (
    <div className="absolute content-stretch flex flex-col h-[327.5px] items-start left-[526px] top-0 w-[654px]" data-name="Container">
      <Details />
      <Details1 />
      <Details2 />
      <Details3 />
      <Details4 />
    </div>
  );
}

function Container43() {
  return (
    <div className="h-[327.5px] relative shrink-0 w-[1180px]" data-name="Container">
      <Container44 />
      <Container45 />
    </div>
  );
}

function ContainerMargin11() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Container43 />
      </div>
    </div>
  );
}

function Section6() {
  return (
    <div className="bg-[#f7f7fa] relative shrink-0 w-full" data-name="Section">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[120px] relative size-full">
        <ContainerMargin11 />
      </div>
    </div>
  );
}

function Paragraph37() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Heavy',sans-serif] leading-[18.6px] not-italic relative shrink-0 text-[#a78bfa] text-[12px] text-center tracking-[1.44px] uppercase whitespace-nowrap">Stop checking every chart</p>
      </div>
    </div>
  );
}

function Heading21() {
  return (
    <div className="h-[85px] relative shrink-0 w-[1118px]" data-name="Heading 2">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center pt-[18px] relative size-full">
        <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[66.96px] not-italic relative shrink-0 text-[#0b0b10] text-[62px] text-center tracking-[-2.79px] whitespace-nowrap">Teach the monitor what matters to you.</p>
      </div>
    </div>
  );
}

function Paragraph38() {
  return (
    <div className="h-[69px] relative shrink-0 w-[1118px]" data-name="Paragraph">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center pb-[28px] pt-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] text-center whitespace-nowrap">Build your first structured setup, inspect the interpretation and preview it on recent market data.</p>
      </div>
    </div>
  );
}

function Text63() {
  return (
    <div className="relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#f7f3ff] text-[16px] text-center whitespace-nowrap">→</p>
      </div>
    </div>
  );
}

function Link6() {
  return (
    <div className="absolute content-stretch drop-shadow-[0px_16px_21px_rgba(139,92,246,0.34)] flex gap-[12px] h-[50px] items-center justify-center left-[494.27px] min-h-[50px] px-[23px] py-px rounded-[13px] top-0" style={{ backgroundImage: "linear-gradient(158.814deg, rgb(167, 139, 250) 0%, rgb(139, 92, 246) 100%)" }} data-name="Link">
      <div aria-hidden className="absolute border border-[rgba(0,0,0,0)] border-solid inset-0 pointer-events-none rounded-[13px]" />
      <p className="[word-break:break-word] font-['Geometria:Bold',sans-serif] leading-[24.8px] not-italic relative shrink-0 text-[16px] text-center text-white whitespace-nowrap">{`Join us `}</p>
      <Text63 />
    </div>
  );
}

function Container46() {
  return (
    <div className="h-[50px] relative shrink-0 w-full" data-name="Container">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <Link6 />
      </div>
    </div>
  );
}

function Section7() {
  return (
    <div className="bg-white content-stretch flex flex-col items-start px-[31px] py-[81px] relative rounded-[30px] shrink-0 w-[1180px]" data-name="Section">
      <div aria-hidden className="absolute border border-[rgba(196,181,253,0.24)] border-solid inset-0 pointer-events-none rounded-[30px]" />
      <Paragraph37 />
      <Heading21 />
      <Paragraph38 />
      <Container46 />
    </div>
  );
}

function SectionMargin4() {
  return (
    <div className="relative shrink-0 w-full" data-name="Section:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center pt-[100px] relative size-full">
        <Section7 />
      </div>
    </div>
  );
}

function Section8() {
  return (
    <div className="bg-white h-[112.281px] relative rounded-[15px] shrink-0 w-[1180px]" data-name="Section">
      <div aria-hidden className="absolute border border-[rgba(196,181,253,0.24)] border-solid inset-0 pointer-events-none rounded-[15px]" />
      <p className="[word-break:break-word] absolute font-['Inter:Bold',sans-serif] font-bold leading-[23.8px] left-[27px] not-italic text-[#9167f7] text-[14px] top-[23px] whitespace-nowrap">Risk disclaimer</p>
      <p className="[word-break:break-word] absolute font-['Inter:Regular',sans-serif] font-normal leading-[22.1px] left-[217px] not-italic text-[#3a3a46] text-[13px] top-[23px] w-[936px]">TraceEdge is a market-monitoring and decision-support product, not a broker, exchange, fiduciary or source of guaranteed financial advice. Alerts may be delayed, incomplete or incorrect. Crypto assets are volatile and trading can result in substantial loss. Verify all data independently and make your own decisions. The platform does not execute trades in version one.</p>
    </div>
  );
}

function SectionMargin5() {
  return (
    <div className="relative shrink-0 w-full" data-name="Section:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center pt-[100px] relative size-full">
        <Section8 />
      </div>
    </div>
  );
}

function MainContent() {
  return (
    <div className="relative shrink-0 w-full" data-name="Main Content">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <SectionMargin />
        <SectionInitialPlatformCoverage />
        <SectionMargin1 />
        <Section2 />
        <SectionMargin2 />
        <Section4 />
        <SectionMargin3 />
        <Section6 />
        <SectionMargin4 />
        <SectionMargin5 />
      </div>
    </div>
  );
}

function Text64() {
  return (
    <div className="relative rounded-[10px] shrink-0 size-[34px]" style={{ backgroundImage: "linear-gradient(135deg, rgb(196, 181, 253) 0%, rgb(139, 92, 246) 100%)" }} data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid relative size-full">
        <p className="[word-break:break-word] absolute font-['Inter:Medium',sans-serif] font-[520] leading-[17.05px] left-[8.42px] not-italic text-[11px] text-white top-[8.48px] tracking-[-0.5px] whitespace-nowrap">AM</p>
      </div>
    </div>
  );
}

function Text65() {
  return (
    <div className="relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#3a3a46] text-[16px] whitespace-nowrap">TraceEdge</p>
      </div>
    </div>
  );
}

function Link7() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex gap-[10px] items-center relative size-full">
        <Text64 />
        <Text65 />
      </div>
    </div>
  );
}

function ParagraphMargin3() {
  return (
    <div className="relative shrink-0 w-full" data-name="Paragraph:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start py-[16px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[16px] text-[rgba(247,243,255,0.74)] w-[412px]">Explainable crypto market monitoring for individual traders.</p>
      </div>
    </div>
  );
}

function Container48() {
  return (
    <div className="absolute content-stretch flex flex-col gap-[10px] h-[137.188px] items-start left-0 top-0 w-[412px]" data-name="Container">
      <Link7 />
      <ParagraphMargin3 />
    </div>
  );
}

function BoldText11() {
  return (
    <div className="h-[33px] relative shrink-0 w-[206px]" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[8px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#e6e6eb] text-[16px] whitespace-nowrap">Product</p>
      </div>
    </div>
  );
}

function Link8() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">Features</p>
      </div>
    </div>
  );
}

function Link9() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">Pricing</p>
      </div>
    </div>
  );
}

function Link10() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">FAQ</p>
      </div>
    </div>
  );
}

function Container49() {
  return (
    <div className="absolute content-stretch flex flex-col gap-[10px] items-start left-[462px] top-0 w-[206px]" data-name="Container">
      <BoldText11 />
      <Link8 />
      <Link9 />
      <Link10 />
    </div>
  );
}

function BoldText12() {
  return (
    <div className="h-[33px] relative shrink-0 w-[206px]" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[8px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#e6e6eb] text-[16px] whitespace-nowrap">Legal</p>
      </div>
    </div>
  );
}

function Link11() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">Risk disclosure</p>
      </div>
    </div>
  );
}

function Link12() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">Terms</p>
      </div>
    </div>
  );
}

function Link13() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">Privacy</p>
      </div>
    </div>
  );
}

function Container50() {
  return (
    <div className="absolute content-stretch flex flex-col gap-[10px] items-start left-[718px] top-0 w-[206px]" data-name="Container">
      <BoldText12 />
      <Link11 />
      <Link12 />
      <Link13 />
    </div>
  );
}

function BoldText13() {
  return (
    <div className="h-[33px] relative shrink-0 w-[206px]" data-name="Bold Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[8px] relative size-full">
        <p className="[word-break:break-word] font-['Inter:Bold',sans-serif] font-bold leading-[24.8px] not-italic relative shrink-0 text-[#e6e6eb] text-[16px] whitespace-nowrap">Support</p>
      </div>
    </div>
  );
}

function Link14() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">Contact support</p>
      </div>
    </div>
  );
}

function Link15() {
  return (
    <div className="relative shrink-0 w-full" data-name="Link">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Medium',sans-serif] font-[520] leading-[24.8px] not-italic relative shrink-0 text-[#8b5cf6] text-[16px] whitespace-nowrap">Help center</p>
      </div>
    </div>
  );
}

function Container51() {
  return (
    <div className="absolute content-stretch flex flex-col gap-[10px] h-[137.188px] items-start left-[974px] top-0 w-[206px]" data-name="Container">
      <BoldText13 />
      <Link14 />
      <Link15 />
    </div>
  );
}

function Container47() {
  return (
    <div className="h-[197.188px] relative shrink-0 w-[1180px]" data-name="Container">
      <Container48 />
      <Container49 />
      <Container50 />
      <Container51 />
    </div>
  );
}

function ContainerMargin12() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Container47 />
      </div>
    </div>
  );
}

function Text66() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[16px] text-[rgba(230,230,235,0.82)] whitespace-nowrap">© 2026 TraceEdge</p>
      </div>
    </div>
  );
}

function Text67() {
  return (
    <div className="h-full relative shrink-0" data-name="Text">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start relative size-full">
        <p className="[word-break:break-word] font-['Inter:Regular',sans-serif] font-normal leading-[24.8px] not-italic relative shrink-0 text-[16px] text-[rgba(230,230,235,0.82)] whitespace-nowrap">Monitoring, not automatic trading.</p>
      </div>
    </div>
  );
}

function Container52() {
  return (
    <div className="content-stretch flex h-[65.797px] items-start justify-between pb-[20px] pt-[21px] relative shrink-0 w-[1180px]" data-name="Container">
      <div aria-hidden className="absolute border-[rgba(255,255,255,0.08)] border-solid border-t inset-0 pointer-events-none" />
      <Text66 />
      <Text67 />
    </div>
  );
}

function ContainerMargin13() {
  return (
    <div className="relative shrink-0 w-full" data-name="Container:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <Container52 />
      </div>
    </div>
  );
}

function Footer() {
  return (
    <div className="bg-[#111114] content-stretch flex flex-col items-start pt-[71px] relative shrink-0 w-full" data-name="Footer">
      <div aria-hidden className="absolute border-[rgba(196,181,253,0.16)] border-solid border-t inset-0 pointer-events-none" />
      <ContainerMargin12 />
      <ContainerMargin13 />
    </div>
  );
}

function FooterMargin() {
  return (
    <div className="relative shrink-0 w-full" data-name="Footer:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[80px] relative size-full">
        <Footer />
      </div>
    </div>
  );
}

function StickyPlaceholderHeader() {
  return <div className="h-[72px] relative shrink-0 w-[1240px]" data-name="Sticky placeholder – Header" />;
}

function StickyPlaceholderHeaderMargin() {
  return (
    <div className="relative shrink-0 w-full" data-name="Sticky placeholder – Header:margin">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center relative size-full">
        <StickyPlaceholderHeader />
      </div>
    </div>
  );
}

function Body() {
  return (
    <div className="relative shrink-0 w-[1470px]" data-name="Body">
      <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pt-[14px] relative size-full">
        <MainContent />
        <FooterMargin />
        <StickyPlaceholderHeaderMargin />
      </div>
    </div>
  );
}

export default function TraceEdgeSeeYourSetupForming() {
  return (
    <div className="content-stretch flex flex-col items-start relative size-full" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 1470 7407' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -750.44 -750.44 0 1205.4 0)'><stop stop-color='rgba(167,139,250,0.22)' offset='0'/><stop stop-color='rgba(84,70,125,0.11)' offset='0.16871'/><stop stop-color='rgba(0,0,0,0)' offset='0.33741'/></radialGradient></defs></svg>\"), url(\"data:image/svg+xml;utf8,<svg viewBox='0 0 1470 7407' xmlns='http://www.w3.org/2000/svg' preserveAspectRatio='none'><rect x='0' y='0' height='100%' width='100%' fill='url(%23grad)' opacity='1'/><defs><radialGradient id='grad' gradientUnits='userSpaceOnUse' cx='0' cy='0' r='10' gradientTransform='matrix(0 -692.02 -692.02 0 264.6 592.56)'><stop stop-color='rgba(196,181,253,0.1)' offset='0'/><stop stop-color='rgba(0,0,0,0)' offset='0.30476'/></radialGradient></defs></svg>\"), linear-gradient(90deg, rgb(247, 247, 250) 0%, rgb(247, 247, 250) 100%)" }} data-name="TraceEdge | See your setup forming">
      <Body />
    </div>
  );
}
import svgPaths from "./svg-x3n5wkemla";
import { imgGroup } from "./svg-zx5dq";
import { useSectionTracking } from "../../components/Tracking";

function AssetIdentity() {
  return (
    <div className="[word-break:break-word] content-stretch flex flex-col gap-[4px] items-start leading-[1.5] overflow-clip relative shrink-0 whitespace-nowrap" data-name="Asset identity">
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35] text-[22px]">ETH / USDT</p>
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d] text-[12px]">Ethereum · Spot</p>
    </div>
  );
}

function ShariahScreenedPill() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="SHARIAH-SCREENED pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">SHARIAH-SCREENED</p>
    </div>
  );
}

function AssetHeader() {
  return (
    <div className="content-stretch flex items-center justify-between overflow-clip relative shrink-0 w-[574px]" data-name="Asset header">
      <AssetIdentity />
      <ShariahScreenedPill />
    </div>
  );
}

function EvidenceRow() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 text-[12px] w-[534px]" data-name="Evidence row 0">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d]">Methodology</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35]">Published digital-asset screen</p>
    </div>
  );
}

function EvidenceRow1() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 text-[12px] w-[534px]" data-name="Evidence row 1">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d]">Business activity</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35]">Eligible</p>
    </div>
  );
}

function EvidenceRow2() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 text-[12px] w-[534px]" data-name="Evidence row 2">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d]">Token mechanics</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35]">Reviewed</p>
    </div>
  );
}

function EvidenceRow3() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 text-[12px] w-[534px]" data-name="Evidence row 3">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d]">Supporting sources</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35]">6 documents</p>
    </div>
  );
}

function EvidenceRow4() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 text-[12px] w-[534px]" data-name="Evidence row 4">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d]">Last reviewed</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35]">18 July 2026</p>
    </div>
  );
}

function EvidenceLink() {
  return (
    <div className="bg-[#e2fe96] content-stretch flex font-['Onest:Medium',sans-serif] font-medium items-start justify-between overflow-clip px-[14px] py-[10px] relative rounded-[12px] shrink-0 text-[#2b2e35] w-[534px]" data-name="Evidence link">
      <p className="relative shrink-0 text-[12px]">View full evidence profile</p>
      <p className="relative shrink-0 text-[14px]">→</p>
    </div>
  );
}

function EvidenceCard() {
  return (
    <div className="bg-white h-[270px] relative rounded-[20px] shrink-0 w-[574px]" data-name="Evidence card">
      <div className="[word-break:break-word] content-stretch flex flex-col gap-[14px] items-start leading-[1.5] overflow-clip px-[20px] py-[18px] relative rounded-[inherit] size-full whitespace-nowrap">
        <EvidenceRow />
        <EvidenceRow1 />
        <EvidenceRow2 />
        <EvidenceRow3 />
        <EvidenceRow4 />
        <EvidenceLink />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[20px]" />
    </div>
  );
}

function ProductVisual() {
  return (
    <div className="bg-[#f5f8fb] h-[430px] relative rounded-[28px] shrink-0 w-[638px]" data-name="Product visual">
      <div className="content-stretch flex flex-col gap-[18px] items-start overflow-clip p-[32px] relative rounded-[inherit] size-full">
        <AssetHeader />
        <EvidenceCard />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[28px]" />
    </div>
  );
}

function Frame1() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center px-[8px] py-[4px] relative rounded-[100px] shrink-0">
      <p className="[word-break:break-word] font-['Onest:SemiBold',sans-serif] font-semibold leading-[1.5] relative shrink-0 text-[#19191b] text-[10px] whitespace-nowrap">01 / Screen</p>
    </div>
  );
}

function FeatureCopy() {
  return (
    <div className="content-stretch flex flex-col gap-[20px] items-start justify-center overflow-clip relative shrink-0 w-[540px]" data-name="Feature copy">
      <Frame1 />
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[36px] tracking-[-1.08px] w-[520px]">Shariah-screened assets with full transparency</p>
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[17px] w-[520px]">Explore screened assets and review the methodology, screening criteria, supporting sources, review date, restrictions, and status history behind each result.</p>
    </div>
  );
}

function FeatureRow() {
  const trackingRef = useSectionTracking<HTMLDivElement>('feature_screen');
  return (
    <div ref={trackingRef} data-analytics-section="feature_screen" className="content-stretch flex gap-[70px] h-[470px] items-center overflow-clip relative shrink-0 w-[1248px]" data-name="Feature row 1">
      <ProductVisual />
      <FeatureCopy />
    </div>
  );
}

function Frame2() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center px-[8px] py-[4px] relative rounded-[100px] shrink-0">
      <p className="[word-break:break-word] font-['Onest:SemiBold',sans-serif] font-semibold leading-[1.5] relative shrink-0 text-[#19191b] text-[10px] whitespace-nowrap">02 / Build</p>
    </div>
  );
}

function FeatureCopy1() {
  return (
    <div className="content-stretch flex flex-col gap-[20px] items-start justify-center overflow-clip relative shrink-0 w-[540px]" data-name="Feature copy">
      <Frame2 />
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[36px] tracking-[-1.08px] w-[520px]">Your strategy, turned into clear rules</p>
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[17px] w-[520px]">Describe your setup in your own words through the AI chatbot. Hilal Markets structures it into measurable conditions and lets you review every rule before monitoring begins. No coding is required.</p>
    </div>
  );
}

function UserPrompt() {
  return (
    <div className="bg-[#f5f8fb] content-stretch flex items-start overflow-clip px-[13px] py-[9px] relative rounded-[12px] shrink-0 w-[520px]" data-name="User prompt">
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] w-[490px]">Breakout above resistance with rising volume.</p>
    </div>
  );
}

function AiResponse() {
  return (
    <div className="bg-[#e2fe96] content-stretch flex items-start overflow-clip px-[13px] py-[9px] relative rounded-[12px] shrink-0 w-[520px]" data-name="AI response">
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#2b2e35] text-[12px] w-[490px]">Four measurable conditions created. Review before monitoring.</p>
    </div>
  );
}

function AiChatbot() {
  return (
    <div className="bg-white h-[175px] relative rounded-[18px] shrink-0 w-[574px]" data-name="AI chatbot">
      <div className="content-stretch flex flex-col gap-[11px] items-start overflow-clip px-[18px] py-[16px] relative rounded-[inherit] size-full">
        <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#68717d] text-[10px] whitespace-nowrap">AI STRATEGY CHATBOT</p>
        <UserPrompt />
        <AiResponse />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[18px]" />
    </div>
  );
}

function PriceAboveLevelPill() {
  return (
    <div className="bg-white content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Price above level pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Price above level</p>
    </div>
  );
}

function VolumeRisingPill() {
  return (
    <div className="bg-white content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Volume rising pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Volume rising</p>
    </div>
  );
}

function TrendPositivePill() {
  return (
    <div className="bg-white content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Trend positive pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Trend positive</p>
    </div>
  );
}

function ScreenedAssetPill() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Screened asset pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Screened asset</p>
    </div>
  );
}

function RuleChips() {
  return (
    <div className="content-stretch flex gap-[8px] items-start overflow-clip relative shrink-0 w-[574px]" data-name="Rule chips">
      <PriceAboveLevelPill />
      <VolumeRisingPill />
      <TrendPositivePill />
      <ScreenedAssetPill />
    </div>
  );
}

function ReviewApprovePill() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Review & approve pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">{`Review & approve`}</p>
    </div>
  );
}

function ApprovalRow() {
  return (
    <div className="bg-[#2b2e35] content-stretch flex items-center justify-between overflow-clip pl-[16px] pr-[12px] py-[11px] relative rounded-[14px] shrink-0 w-[574px]" data-name="Approval row">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[12px] text-white whitespace-nowrap">Rules stay visible and editable</p>
      <ReviewApprovePill />
    </div>
  );
}

function ProductVisual1() {
  return (
    <div className="bg-[#f5f8fb] h-[430px] relative rounded-[28px] shrink-0 w-[638px]" data-name="Product visual">
      <div className="content-stretch flex flex-col gap-[18px] items-start overflow-clip p-[32px] relative rounded-[inherit] size-full">
        <AiChatbot />
        <RuleChips />
        <ApprovalRow />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[28px]" />
    </div>
  );
}

function FeatureRow1() {
  const trackingRef = useSectionTracking<HTMLDivElement>('feature_build');
  return (
    <div ref={trackingRef} data-analytics-section="feature_build" className="content-stretch flex gap-[70px] h-[470px] items-center overflow-clip relative shrink-0 w-[1248px]" data-name="Feature row 2">
      <FeatureCopy1 />
      <ProductVisual1 />
    </div>
  );
}

function DetectedPill() {
  return (
    <div className="bg-white content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Detected pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Detected</p>
    </div>
  );
}

function FormingPill() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Forming pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Forming</p>
    </div>
  );
}

function NearMatchPill() {
  return (
    <div className="bg-white content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Near match pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Near match</p>
    </div>
  );
}

function ConfirmedPill() {
  return (
    <div className="bg-white content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="Confirmed pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">Confirmed</p>
    </div>
  );
}

function Lifecycle() {
  return (
    <div className="content-stretch flex gap-[8px] items-start overflow-clip relative shrink-0 w-[574px]" data-name="Lifecycle">
      <DetectedPill />
      <FormingPill />
      <NearMatchPill />
      <ConfirmedPill />
    </div>
  );
}

function FormingPill1() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center overflow-clip px-[12px] py-[7px] relative rounded-[999px] shrink-0" data-name="FORMING pill">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[11px] whitespace-nowrap">FORMING</p>
    </div>
  );
}

function Top() {
  return (
    <div className="content-stretch flex items-start justify-between overflow-clip relative shrink-0 w-[530px]" data-name="Top">
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[15px] text-white whitespace-nowrap">ETH / USDT · 4H</p>
      <FormingPill1 />
    </div>
  );
}

function ConditionBars() {
  return (
    <div className="content-stretch flex gap-[8px] h-[7px] items-start overflow-clip relative shrink-0 w-[530px]" data-name="Condition bars">
      <div className="bg-[#cbfa4d] h-[6px] relative rounded-[6px] shrink-0 w-[99px]" data-name="Rectangle" />
      <div className="bg-[#cbfa4d] h-[6px] relative rounded-[6px] shrink-0 w-[99px]" data-name="Rectangle" />
      <div className="bg-[#cbfa4d] h-[6px] relative rounded-[6px] shrink-0 w-[99px]" data-name="Rectangle" />
      <div className="bg-[#cbfa4d] h-[6px] relative rounded-[6px] shrink-0 w-[99px]" data-name="Rectangle" />
      <div className="bg-[#525966] h-[6px] relative rounded-[6px] shrink-0 w-[99px]" data-name="Rectangle" />
    </div>
  );
}

function Check() {
  return (
    <div className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.5] overflow-clip relative shrink-0 text-[12px] w-[530px] whitespace-nowrap" data-name="Check 0">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#8f9cad]">Resistance break</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#cbfa4d]">Matched</p>
    </div>
  );
}

function Check1() {
  return (
    <div className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.5] overflow-clip relative shrink-0 text-[12px] w-[530px] whitespace-nowrap" data-name="Check 1">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#8f9cad]">Rising volume</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#cbfa4d]">Matched</p>
    </div>
  );
}

function Check2() {
  return (
    <div className="[word-break:break-word] content-stretch flex items-start justify-between leading-[1.5] overflow-clip relative shrink-0 text-[12px] w-[530px] whitespace-nowrap" data-name="Check 2">
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#8f9cad]">Candle close</p>
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-white">Waiting</p>
    </div>
  );
}

function SetupCard() {
  return (
    <div className="bg-[#2b2e35] content-stretch flex flex-col gap-[15px] h-[250px] items-start overflow-clip px-[22px] py-[20px] relative rounded-[20px] shrink-0 w-[574px]" data-name="Setup card">
      <Top />
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[22px] text-white whitespace-nowrap">4 of 5 conditions matched</p>
      <ConditionBars />
      <Check />
      <Check1 />
      <Check2 />
    </div>
  );
}

function ProductVisual2() {
  return (
    <div className="bg-[#f5f8fb] h-[430px] relative rounded-[28px] shrink-0 w-[638px]" data-name="Product visual">
      <div className="content-stretch flex flex-col gap-[18px] items-start overflow-clip p-[32px] relative rounded-[inherit] size-full">
        <Lifecycle />
        <SetupCard />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[28px]" />
    </div>
  );
}

function Frame3() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center px-[8px] py-[4px] relative rounded-[100px] shrink-0">
      <p className="[word-break:break-word] font-['Onest:SemiBold',sans-serif] font-semibold leading-[1.5] relative shrink-0 text-[#19191b] text-[10px] whitespace-nowrap">03 / Monitor</p>
    </div>
  );
}

function FeatureCopy2() {
  return (
    <div className="content-stretch flex flex-col gap-[20px] items-start justify-center overflow-clip relative shrink-0 w-[540px]" data-name="Feature copy">
      <Frame3 />
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[36px] tracking-[-1.08px] w-[520px]">Every setup, followed from start to finish</p>
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[17px] w-[520px]">See when a setup is forming, which conditions have matched, what is still missing, and why an alert did or did not happen.</p>
    </div>
  );
}

function FeatureRow2() {
  const trackingRef = useSectionTracking<HTMLDivElement>('feature_monitor');
  return (
    <div ref={trackingRef} data-analytics-section="feature_monitor" className="content-stretch flex gap-[70px] h-[470px] items-center overflow-clip relative shrink-0 w-[1248px]" data-name="Feature row 3">
      <ProductVisual2 />
      <FeatureCopy2 />
    </div>
  );
}

function Frame4() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex items-center justify-center px-[8px] py-[4px] relative rounded-[100px] shrink-0">
      <p className="[word-break:break-word] font-['Onest:SemiBold',sans-serif] font-semibold leading-[1.5] relative shrink-0 text-[#19191b] text-[10px] whitespace-nowrap">04 / Connect</p>
    </div>
  );
}

function FeatureCopy3() {
  return (
    <div className="content-stretch flex flex-col gap-[20px] items-start justify-center overflow-clip relative shrink-0 w-[540px]" data-name="Feature copy">
      <Frame4 />
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[36px] tracking-[-1.08px] w-[520px]">Alerts delivered where they work for you</p>
      <p className="[word-break:break-word] font-['Onest:Regular',sans-serif] font-normal leading-[1.5] relative shrink-0 text-[#68717d] text-[17px] w-[520px]">Receive updates through the channels you already use without keeping the dashboard open or constantly watching charts.</p>
      <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#2b2e35] text-[14px] w-[520px]">Telegram, email, and WhatsApp at launch. Additional integrations are planned.</p>
    </div>
  );
}

function Frame5() {
  return (
    <div className="relative shrink-0 size-[40px]">
      <div className="absolute inset-[-0.83%_0_0_0]">
        <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 40 40.333">
          <g id="Frame 55">
            <path d={svgPaths.p28407a80} fill="var(--fill-0, #2AABEE)" id="Vector" />
            <rect fill="var(--fill-0, #F34242)" height="10" id="Rectangle 1" rx="5" width="10" x="30" />
          </g>
        </svg>
      </div>
    </div>
  );
}

function NotificationText() {
  return (
    <div className="[word-break:break-word] content-stretch flex flex-col gap-[2px] items-start leading-[1.5] overflow-clip relative shrink-0 w-[430px] whitespace-nowrap" data-name="Notification text">
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35] text-[13px]">Telegram · Setup forming</p>
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d] text-[12px]">BTC/USDT recovery: +2.3%</p>
    </div>
  );
}

function TelegramAlert() {
  return (
    <div className="bg-white h-[88px] relative rounded-[18px] shrink-0 w-[574px]" data-name="Telegram alert">
      <div className="content-stretch flex gap-[14px] items-center overflow-clip px-[18px] py-[14px] relative rounded-[inherit] size-full">
        <Frame5 />
        <NotificationText />
      </div>
      <div aria-hidden className="absolute border border-[#2aabee] border-solid inset-0 pointer-events-none rounded-[18px]" />
    </div>
  );
}

function Group() {
  return (
    <div className="absolute inset-[7.14%_5.26%] mask-alpha mask-intersect mask-no-clip mask-no-repeat mask-position-[-1px_-1px] mask-size-[19px_14px]" style={{ maskImage: `url("${imgGroup}")` }} data-name="Group">
      <div className="absolute inset-[-8.33%_-5.88%]">
        <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 19 14">
          <g id="Group">
            <path d="M1 13H18V1H1V13Z" id="Vector" stroke="var(--stroke-0, white)" strokeLinejoin="round" strokeWidth="2" />
            <path d="M18 2L9.72464 7L1 2" id="Vector_2" stroke="var(--stroke-0, white)" strokeLinejoin="round" strokeWidth="2" />
          </g>
        </svg>
      </div>
    </div>
  );
}

function ClipPathGroup() {
  return (
    <div className="absolute contents inset-0" data-name="Clip path group">
      <Group />
    </div>
  );
}

function Frame() {
  return (
    <div className="-translate-x-1/2 -translate-y-1/2 absolute h-[14px] left-[calc(50%+0.5px)] overflow-clip top-[calc(50%+0.67px)] w-[19px]" data-name="Frame">
      <ClipPathGroup />
    </div>
  );
}

function Frame6() {
  return (
    <div className="relative shrink-0 size-[36px]">
      <div className="absolute left-0 size-[36px] top-0" data-name="Channel mark">
        <svg className="absolute block inset-0 size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 36 36">
          <circle cx="18" cy="18" fill="var(--fill-0, #2B2E35)" id="Channel mark" r="18" />
        </svg>
      </div>
      <Frame />
      <div className="absolute bg-[#f34242] left-[26px] rounded-[12px] size-[10px] top-[-0.33px]" />
    </div>
  );
}

function NotificationText1() {
  return (
    <div className="[word-break:break-word] content-stretch flex flex-col gap-[2px] items-start leading-[1.5] overflow-clip relative shrink-0 w-[430px] whitespace-nowrap" data-name="Notification text">
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35] text-[13px]">Email · Strategy monitor update</p>
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d] text-[12px]">ETH/USDT · 4H</p>
    </div>
  );
}

function EmailAlert() {
  return (
    <div className="bg-white h-[88px] relative rounded-[18px] shrink-0 w-[574px]" data-name="Email alert">
      <div className="content-stretch flex gap-[14px] items-center overflow-clip px-[18px] py-[14px] relative rounded-[inherit] size-full">
        <Frame6 />
        <NotificationText1 />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[18px]" />
    </div>
  );
}

function Frame7() {
  return (
    <div className="relative shrink-0 size-[36px]">
      <div className="absolute inset-[-0.93%_0_0_0]">
        <svg className="block size-full" fill="none" preserveAspectRatio="none" viewBox="0 0 36 36.333">
          <g id="Frame 54">
            <circle cx="18" cy="18.333" fill="var(--fill-0, white)" id="Channel mark" r="18" />
            <g id="Group">
              <path d={svgPaths.p1479e180} fill="var(--fill-0, #25D366)" id="Vector" />
            </g>
            <rect fill="var(--fill-0, #F34242)" height="10" id="Rectangle 1" rx="5" width="10" x="26" />
          </g>
        </svg>
      </div>
    </div>
  );
}

function NotificationText2() {
  return (
    <div className="[word-break:break-word] content-stretch flex flex-col gap-[2px] items-start leading-[1.5] overflow-clip relative shrink-0 w-[430px] whitespace-nowrap" data-name="Notification text">
      <p className="font-['Onest:Medium',sans-serif] font-medium relative shrink-0 text-[#2b2e35] text-[13px]">WhatsApp · Condition confirmed</p>
      <p className="font-['Onest:Regular',sans-serif] font-normal relative shrink-0 text-[#68717d] text-[12px]">Rising volume matched</p>
    </div>
  );
}

function WhatsAppAlert() {
  return (
    <div className="bg-[#cbfa4d] content-stretch flex gap-[14px] h-[88px] items-center overflow-clip px-[18px] py-[14px] relative rounded-[18px] shrink-0 w-[574px]" data-name="WhatsApp alert">
      <Frame7 />
      <NotificationText2 />
    </div>
  );
}

function ProductVisual3() {
  return (
    <div className="bg-[#f5f8fb] h-[430px] relative rounded-[28px] shrink-0 w-[638px]" data-name="Product visual">
      <div className="content-stretch flex flex-col gap-[18px] items-start overflow-clip p-[32px] relative rounded-[inherit] size-full">
        <p className="[word-break:break-word] font-['Onest:Medium',sans-serif] font-medium leading-[1.5] relative shrink-0 text-[#68717d] text-[12px] whitespace-nowrap">Delivery channels</p>
        <TelegramAlert />
        <EmailAlert />
        <WhatsAppAlert />
      </div>
      <div aria-hidden className="absolute border border-[#dce3ea] border-solid inset-0 pointer-events-none rounded-[28px]" />
    </div>
  );
}

function FeatureRow3() {
  const trackingRef = useSectionTracking<HTMLDivElement>('feature_connect');
  return (
    <div ref={trackingRef} data-analytics-section="feature_connect" className="content-stretch flex gap-[70px] h-[470px] items-center overflow-clip relative shrink-0 w-[1248px]" data-name="Feature row 4">
      <FeatureCopy3 />
      <ProductVisual3 />
    </div>
  );
}

export default function Component06CoreFeatures() {
  return (
    <div className="bg-white content-stretch flex flex-col gap-[58px] items-start px-[96px] py-[104px] relative size-full" data-name="06 — Core features">
      <p className="[word-break:break-word] font-['Geometria:Medium',sans-serif] leading-[1.1] not-italic relative shrink-0 text-[#2b2e35] text-[42px] tracking-[-1.68px] w-[638px]">Everything you need to build and monitor with confidence</p>
      <FeatureRow />
      <FeatureRow1 />
      <FeatureRow2 />
      <FeatureRow3 />
    </div>
  );
}

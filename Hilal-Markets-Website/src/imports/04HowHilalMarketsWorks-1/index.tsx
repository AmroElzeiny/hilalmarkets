import type { ReactNode } from 'react'

function StrategyExample() {
  return (
    <div className="flex min-h-[126px] w-full flex-col gap-2.5 rounded-[16px] bg-[#f5f8fb] p-4">
      <span className="w-fit rounded-full bg-[#cbfa4d] px-2 py-1 text-[10px] font-medium text-[#2b2e35]">
        AI strategy chatbot
      </span>
      <div className="rounded-[12px] bg-white px-3 py-2.5 text-[12px] leading-[1.5] text-[#2b2e35]">
        Breakout above resistance with rising volume.
      </div>
    </div>
  )
}

function ScreeningExample() {
  return (
    <div className="flex min-h-[126px] w-full flex-col justify-between rounded-[16px] bg-[#f5f8fb] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <strong className="text-[15px] font-medium text-[#2b2e35]">ETH / USDT</strong>
        <span className="rounded-full bg-[#cbfa4d] px-3 py-1 text-[11px] font-medium text-[#2b2e35]">Screened</span>
      </div>
      <p className="text-[11px] leading-[1.5] text-[#68717d]">Methodology - Sources - Reviewed 18 Jul</p>
      <span className="text-[12px] font-medium text-[#2b2e35]">View full evidence</span>
    </div>
  )
}

function MonitoringExample() {
  return (
    <div className="flex min-h-[126px] w-full flex-col gap-2.5 rounded-[16px] bg-[#2b2e35] p-4">
      <span className="w-fit rounded-full bg-white/10 px-2 py-1 text-[10px] font-semibold text-[#cbfa4d]">Setup forming</span>
      <strong className="text-[15px] font-medium text-white">4 of 5 conditions matched</strong>
      <div className="grid grid-cols-5 gap-[2%]" aria-label="Four of five conditions matched">
        {[true, true, true, true, false].map((matched, index) => (
          <span key={index} className={`h-[5px] rounded-full ${matched ? 'bg-[#cbfa4d]' : 'bg-[#525966]'}`} />
        ))}
      </div>
    </div>
  )
}

function StepCard({ number, title, description, children, highlighted = false }: {
  number: string
  title: string
  description: string
  children: ReactNode
  highlighted?: boolean
}) {
  return (
    <article
      className="flex min-h-[440px] min-w-0 flex-col justify-between gap-8 rounded-[24px] bg-white p-[6%] sm:p-6"
      data-name={`Step ${number}`}
    >
      <div className="flex flex-col items-start gap-[18px]">
        <span className={`rounded-full px-3.5 py-2 text-[12px] font-medium text-[#2b2e35] ${highlighted ? 'bg-[#e2fe96]' : 'bg-[#f5f8fb]'}`}>
          {number}
        </span>
        <h3 className="font-display text-[clamp(1.35rem,2vw,1.5625rem)] font-medium leading-[1.18] tracking-[-0.02em] text-[#2b2e35]">
          {title}
        </h3>
        <p className="text-[14px] leading-[1.5] text-[#68717d]">{description}</p>
      </div>
      {children}
    </article>
  )
}

export default function Component04HowHilalMarketsWorks() {
  return (
    <div
      className="mx-auto flex w-full max-w-[1440px] flex-col gap-[6%] bg-[#f5f8fb] px-[6.67%] py-[8%]"
      data-name="04 - How Hilal Markets works"
    >
      <h2 className="max-w-[560px] font-display text-[clamp(2rem,3vw,2.625rem)] font-medium leading-[1.1] tracking-[-0.04em] text-[#2b2e35]">
        From your trading idea to continuous monitoring
      </h2>
      <div className="mt-[4%] grid w-full grid-cols-1 gap-5 lg:grid-cols-3" data-name="Steps">
        <StepCard
          number="01"
          title="Build your strategy"
          description="Describe what you look for in plain language through the AI chatbot. Hilal Markets turns your idea into clear rules that you review and approve. No coding is required."
        >
          <StrategyExample />
        </StepCard>
        <StepCard
          number="02"
          title="Choose Shariah-screened assets"
          description="Apply your strategy only to screened assets and review the evidence behind every status."
          highlighted
        >
          <ScreeningExample />
        </StepCard>
        <StepCard
          number="03"
          title="Monitor every setup"
          description="Follow each setup as it develops and receive clear alerts when your conditions are met."
        >
          <MonitoringExample />
        </StepCard>
      </div>
    </div>
  )
}

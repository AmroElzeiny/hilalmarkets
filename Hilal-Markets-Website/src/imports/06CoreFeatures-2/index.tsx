import type { ReactNode } from 'react'

function FeatureCopy({ label, title, description, note }: {
  label: string
  title: string
  description: string
  note?: string
}) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-5">
      <span className="rounded-full bg-[#f5f8fb] px-3.5 py-2 text-[10px] font-semibold text-[#2b2e35]">{label}</span>
      <h3 className="font-display text-[clamp(1.8rem,2.5vw,2.25rem)] font-medium leading-[1.1] tracking-[-0.03em] text-[#2b2e35]">
        {title}
      </h3>
      <p className="text-[clamp(0.95rem,1.2vw,1.0625rem)] leading-[1.5] text-[#68717d]">{description}</p>
      {note && <p className="text-[14px] font-medium leading-[1.5] text-[#2b2e35]">{note}</p>}
    </div>
  )
}

function FeatureRow({ index, copy, children }: {
  index: number
  copy: ReactNode
  children: ReactNode
}) {
  return (
    <article
      className="grid min-h-0 w-full grid-cols-1 items-center gap-[7%] py-[4%] lg:min-h-[470px] lg:grid-cols-[42%_51%]"
      data-name={`Feature row ${index}`}
    >
      {copy}
      <div className="min-w-0">{children}</div>
    </article>
  )
}

function PassportPreview() {
  const details = [
    ['Methodology', 'Published digital-asset screen'],
    ['Business activity', 'Eligible'],
    ['Token mechanics', 'Reviewed'],
    ['Supporting sources', '6 documents'],
    ['Last reviewed', '18 July 2026'],
  ]
  return (
    <div className="rounded-[24px] bg-[#f5f8fb] p-[4%] sm:p-7">
      <div className="rounded-[20px] border border-[#e1e5ea] bg-white p-[5%] shadow-[0_24px_55px_-42px_rgba(43,46,53,0.65)]">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[#e1e5ea] pb-5">
          <div>
            <h4 className="text-[22px] font-medium text-[#2b2e35]">ETH / USDT</h4>
            <p className="mt-1 text-[12px] text-[#2b2e35]">Ethereum - Spot</p>
          </div>
          <span className="rounded-full bg-[#cbfa4d] px-3 py-2 text-[11px] font-medium text-[#2b2e35]">SHARIAH-SCREENED</span>
        </div>
        <dl className="grid grid-cols-1 gap-4 py-5 sm:grid-cols-2">
          {details.map(([label, value]) => (
            <div key={label} className="min-w-0">
              <dt className="text-[11px] text-[#68717d]">{label}</dt>
              <dd className="mt-1 break-words text-[12px] font-medium text-[#2b2e35]">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="flex items-center justify-between border-t border-[#e1e5ea] pt-4 text-[12px] font-medium text-[#2b2e35]">
          <span>View full evidence profile</span>
          <span aria-hidden="true">-&gt;</span>
        </div>
      </div>
    </div>
  )
}

function StrategyPreview() {
  const rules = ['Price above level', 'Volume rising', 'Trend positive', 'Screened asset']
  return (
    <div className="rounded-[24px] bg-[#f5f8fb] p-[4%] sm:p-7">
      <div className="rounded-[20px] bg-white p-[5%] shadow-[0_24px_55px_-42px_rgba(43,46,53,0.65)]">
        <span className="inline-flex rounded-full bg-[#cbfa4d] px-2.5 py-1 text-[10px] font-medium text-[#2b2e35]">AI STRATEGY CHATBOT</span>
        <div className="mt-4 rounded-[14px] bg-[#f5f8fb] p-4 text-[12px] leading-[1.5] text-[#2b2e35]">
          Breakout above resistance with rising volume.
        </div>
        <p className="mt-4 text-[12px] leading-[1.5] text-[#2b2e35]">Four measurable conditions created. Review before monitoring.</p>
        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {rules.map((rule) => (
            <span key={rule} className="rounded-full border border-[#e1e5ea] px-3 py-2 text-center text-[11px] font-medium text-[#2b2e35]">{rule}</span>
          ))}
        </div>
        <div className="mt-5 flex flex-col items-stretch justify-between gap-3 sm:flex-row sm:items-center">
          <button type="button" className="rounded-full bg-[#cbfa4d] px-4 py-2 text-[11px] font-medium text-[#2b2e35]">Review &amp; approve</button>
          <span className="rounded-full bg-[#2b2e35] px-4 py-2 text-center text-[12px] font-medium text-white">Rules stay visible and editable</span>
        </div>
      </div>
    </div>
  )
}

function MonitorPreview() {
  const states = ['Detected', 'Forming', 'Near match', 'Confirmed']
  const checks = [
    ['Resistance break', 'Matched'],
    ['Rising volume', 'Matched'],
    ['Candle close', 'Waiting'],
  ]
  return (
    <div className="rounded-[24px] bg-[#f5f8fb] p-[4%] sm:p-7">
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {states.map((state, index) => (
          <span key={state} className={`rounded-full px-3 py-2 text-center text-[11px] font-medium text-[#2b2e35] ${index === 1 ? 'bg-[#cbfa4d]' : 'bg-white'}`}>{state}</span>
        ))}
      </div>
      <div className="rounded-[20px] bg-[#2b2e35] p-[6%] text-white shadow-[0_26px_60px_-42px_rgba(43,46,53,0.9)]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <strong className="text-[15px] font-medium text-white">ETH / USDT - 4H</strong>
          <span className="rounded-full bg-white/10 px-3 py-1 text-[11px] font-medium text-[#cbfa4d]">FORMING</span>
        </div>
        <div className="mt-5 space-y-3">
          {checks.map(([label, state]) => (
            <div key={label} className="flex items-center justify-between gap-3 border-b border-white/10 pb-3 text-[12px]">
              <span className="text-[#8f9cad]">{label}</span>
              <span className={state === 'Matched' ? 'font-medium text-[#cbfa4d]' : 'font-medium text-white'}>{state}</span>
            </div>
          ))}
        </div>
        <p className="mt-5 text-[clamp(1rem,2vw,1.375rem)] font-medium text-white">4 of 5 conditions matched</p>
      </div>
    </div>
  )
}

function DeliveryPreview() {
  const channels = [
    { name: 'Telegram', event: 'Setup forming', detail: 'BTC/USDT recovery: +2.3%', color: '#2AABEE', mark: 'T' },
    { name: 'Email', event: 'Strategy monitor update', detail: 'ETH/USDT - 4H', color: '#2b2e35', mark: '@' },
    { name: 'WhatsApp', event: 'Condition confirmed', detail: 'Rising volume matched', color: '#25D366', mark: 'W' },
  ]
  return (
    <div className="rounded-[24px] bg-[#f5f8fb] p-[4%] sm:p-7">
      <div className="space-y-3">
        {channels.map((channel) => (
          <div key={channel.name} className="flex items-center gap-4 rounded-[16px] bg-white p-4 shadow-[0_18px_40px_-34px_rgba(43,46,53,0.7)]">
            <span className="grid size-10 shrink-0 place-items-center rounded-[12px] text-[13px] font-semibold text-white" style={{ backgroundColor: channel.color }}>{channel.mark}</span>
            <div className="min-w-0">
              <p className="break-words text-[13px] font-medium text-[#2b2e35]">{channel.name} - {channel.event}</p>
              <p className="mt-1 break-words text-[12px] text-[#68717d]">{channel.detail}</p>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[12px] font-medium text-[#68717d]">Delivery channels</p>
    </div>
  )
}

export default function Component06CoreFeatures() {
  return (
    <div className="mx-auto w-full max-w-[1440px] bg-white px-[6.67%] py-[7.2%]" data-name="06 - Core features">
      <h2 className="max-w-[680px] font-display text-[clamp(2rem,3vw,2.625rem)] font-medium leading-[1.1] tracking-[-0.04em] text-[#2b2e35]">
        Everything you need to build and monitor with confidence
      </h2>

      <div className="mt-[4%] divide-y divide-[#e1e5ea]">
        <FeatureRow index={1} copy={<FeatureCopy label="01 / Screen" title="Shariah-screened assets with full transparency" description="Explore screened assets and review the methodology, screening criteria, supporting sources, review date, restrictions, and status history behind each result." />}>
          <PassportPreview />
        </FeatureRow>
        <FeatureRow index={2} copy={<FeatureCopy label="02 / Build" title="Your strategy, turned into clear rules" description="Describe your setup in your own words through the AI chatbot. Hilal Markets structures it into measurable conditions and lets you review every rule before monitoring begins. No coding is required." />}>
          <StrategyPreview />
        </FeatureRow>
        <FeatureRow index={3} copy={<FeatureCopy label="03 / Monitor" title="Every setup, followed from start to finish" description="See when a setup is forming, which conditions have matched, what is still missing, and why an alert did or did not happen." />}>
          <MonitorPreview />
        </FeatureRow>
        <FeatureRow index={4} copy={<FeatureCopy label="04 / Connect" title="Alerts delivered where they work for you" description="Receive updates through the channels you already use without keeping the dashboard open or constantly watching charts." note="Telegram, email, and WhatsApp at launch. Additional integrations are planned." />}>
          <DeliveryPreview />
        </FeatureRow>
      </div>
    </div>
  )
}

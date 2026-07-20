const workflow = [
  { label: 'Strategy', background: '#f5f8fb' },
  { label: 'Shariah screening', background: '#e2fe96' },
  { label: 'Setup monitoring', background: '#f5f8fb' },
  { label: 'One platform', background: '#cbfa4d' },
]

export default function Component03ProblemAndSolution() {
  return (
    <div
      className="relative mx-auto flex w-full max-w-[1440px] flex-col items-center justify-center overflow-hidden px-[6.67%] py-[12%] sm:py-[10%] lg:py-[8.5%]"
      data-name="03 - Problem and solution"
    >
      <div className="pointer-events-none absolute inset-x-[12%] top-[18%] bottom-[12%]" aria-hidden="true">
        <div className="absolute left-0 top-[8%] h-[42%] w-[28%] rounded-full bg-[#d9fe75]/20 blur-3xl" />
        <div className="absolute bottom-0 right-0 h-[48%] w-[34%] rounded-full bg-[#d9fe75]/20 blur-3xl" />
      </div>

      <div className="relative flex w-full max-w-[800px] flex-col items-center gap-3 text-center">
        <h2 className="font-display text-[clamp(2rem,3vw,2.625rem)] font-medium leading-[1.1] tracking-[-0.04em] text-[#2b2e35]">
          Your strategy and your Shariah checks should not live in separate tools.
        </h2>
        <p className="max-w-[800px] font-sans text-[clamp(0.95rem,1.25vw,1.125rem)] leading-[1.5] text-[#19191b]">
          Hilal Markets brings strategy building, Shariah-screened assets, and continuous setup monitoring into one place.
        </p>
      </div>

      <div
        className="relative mt-[5%] flex w-full max-w-[760px] flex-col items-stretch justify-center gap-2.5 sm:flex-row sm:flex-wrap sm:items-center"
        data-name="Unified workflow"
      >
        {workflow.map((item, index) => (
          <div key={item.label} className="contents">
            {index > 0 && (
              <span className="hidden text-[17px] font-medium text-[#2b2e35] sm:block" aria-hidden="true">
                {index === workflow.length - 1 ? '->' : '+'}
              </span>
            )}
            <div
              className="flex min-h-10 items-center justify-center rounded-full px-5 py-2 text-center font-sans text-[12px] font-medium text-[#2b2e35]"
              style={{ backgroundColor: item.background }}
              data-name={`${item.label} pill`}
            >
              {item.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

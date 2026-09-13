# Role model preferences

These defaults define the current cost-first Hilal Markets OpenCode Go workflow.

The live local OpenCode Go catalog is always authoritative.

## Default roles

- Standard supervisor: `opencode-go/minimax-m3`
- Deep supervisor: `opencode-go/deepseek-v4.1-flash`

- Repository explorer: `opencode-go/deepseek-v4.1-flash`

- Fast worker: `opencode-go/qwen3.8-flash`
- Strong coder: `opencode-go/qwen3.8-flash`
- Test/debug engineer: `opencode-go/qwen3.8-flash`

- Logic reviewer: `opencode-go/minimax-m3`
- Adversarial reviewer: `opencode-go/deepseek-v4.1-flash`

- Direct read: `opencode-go/deepseek-v4.1-flash`
- Direct write: `opencode-go/qwen3.8-flash`

- Vision reviewer: `opencode-go/deepseek-v4-flash-vision-exp`

## Normal execution flow

Implementation:

`qwen3.8-flash`

Read-heavy exploration:

`deepseek-v4.1-flash`

Standard supervision and logic review:

`minimax-m3`

Deep supervision and adversarial review:

`deepseek-v4.1-flash`

Visual acceptance:

`deepseek-v4-flash-vision-exp`

## Cost escalation

Expensive models are not defaults.

Models such as:
- Kimi K2.7 Code;
- GLM-5.3 full;
- Qwen Max;
- other high-cost models

may only be selected after documented evidence that the normal cheap route is insufficient.

## Rules

- Do not use a stronger model simply because a task is important.
- Do not invoke the explorer unless repository ownership is materially unclear.
- Do not invoke a test specialist if the normal worker can author and verify the required tests efficiently.
- Do not invoke the adversarial reviewer for ordinary low-risk work.
- Do not invoke a vision model unless visual verification is required.
- After two meaningful failures, change approach or model family.
- Preserve reviewer-family diversity for high-risk work.
- The live model catalog always wins.
- Never fabricate an unavailable model or reasoning variant.
# Role model preferences

These defaults define the current cost-first Hilal Markets OpenCode Go workflow.

The live local OpenCode Go catalog is always authoritative.

## Approved model pool (owner decision 2026-09-16)

Only models with at least $60 monthly OpenCode Go quota are allowed. That is exactly four:

- `opencode-go/muse-spark-1.3-contributor` (Muse Spark 1.3 Contributor)
- `opencode-go/glm-5.3-flash` (GLM-5.3-Flash)
- `opencode-go/mimo-v2.5` (MiMo V2.5)
- `opencode-go/qwen3.7-plus` (Qwen3.7 Plus)

Never route to any other model. Not allowed: GPT-5.6 Luna, DeepSeek V4 Flash, Qwen3.7 Max,
Qwen3.8 Flash, Kimi K3, MiMo V2.5 Pro, GLM-5.3 (full), or anything else.

## Default roles and fallbacks

A fallback is used only when the primary fails technically (model not found, unsupported
endpoint, repeated timeout, unsupported feature, or repeated schema failure). Record what
failed. Never fall back outside the pool.

| Agent | Primary | Fallback |
|---|---|---|
| `hm-supervisor` | `muse-spark-1.3-contributor` | `glm-5.3-flash` |
| `hm-supervisor-deep` | `glm-5.3-flash` | `qwen3.7-plus` |
| `hm-worker-fast` | `muse-spark-1.3-contributor` | `glm-5.3-flash` |
| `hm-worker-strong` | `muse-spark-1.3-contributor` | `glm-5.3-flash` |
| `hm-direct-write` | `muse-spark-1.3-contributor` | `glm-5.3-flash` |
| `hm-test-debugger` | `muse-spark-1.3-contributor` | `glm-5.3-flash` |
| `hm-explorer` | `mimo-v2.5` | `muse-spark-1.3-contributor` |
| `hm-direct-read` | `mimo-v2.5` | `muse-spark-1.3-contributor` |
| `hm-reviewer-logic` | `glm-5.3-flash` | `qwen3.7-plus` |
| `hm-reviewer-adversarial` | `qwen3.7-plus` | `glm-5.3-flash` |
| `hm-reviewer-visual` | `glm-5.3-flash` | none tested |

`hm-reviewer-visual`: GLM-5.3-Flash read a probe image correctly through
`opencode run --file` on 2026-09-16.

## Muse always runs on the `high` variant (owner decision 2026-09-16)

Every agent whose model is `muse-spark-1.3-contributor` carries `variant: high` in its
frontmatter (`hm-supervisor`, `hm-worker-fast`, `hm-worker-strong`, `hm-direct-write`,
`hm-test-debugger`). `run-model.ps1` adds `--variant high` whenever it routes to Muse and the
caller named no variant, so a Muse fallback on a MiMo agent is also `high`.

Evidence: `high` is listed in Muse's live variants (`LIVE_MODELS_VERBOSE.txt`), and
`opencode debug agent hm-supervisor` resolves `"variant": "high"`. The same tiny prompt used
29 reasoning tokens with `--variant minimal`, and 90 and 239 on `high`.

High reasoning takes longer and uses more output tokens per turn. Size time limits for it.
The other three models keep their default variant.

## Normal execution flow

Implementation, tests and standard supervision: `muse-spark-1.3-contributor`

Read-heavy exploration and direct reading: `mimo-v2.5`

Deep supervision and logic review: `glm-5.3-flash`

Adversarial review: `qwen3.7-plus` (a different family from the Muse worker)

Visual acceptance: `glm-5.3-flash`

## Known limits (tested 2026-09-16)

- The file tools skip hidden folders such as `.hm-orchestrator/`. Give an agent the exact
  path and tell it to use the Read tool, or it may report the file as missing.
- On the raw OpenCode Go HTTP endpoint, only Muse accepts the OpenAI Responses format
  (`/responses`). GLM, MiMo and Qwen answer only on `/chat/completions`.

## Rules

- Do not use a stronger model simply because a task is important.
- Do not invoke the explorer unless repository ownership is materially unclear.
- Do not invoke a test specialist if the normal worker can author and verify the required tests efficiently.
- Do not invoke the adversarial reviewer for ordinary low-risk work.
- Do not invoke a vision model unless visual verification is required.
- After two meaningful failures, change approach or switch to the listed fallback.
- Preserve reviewer-family diversity for high-risk work.
- The live model catalog always wins.
- Never fabricate an unavailable model or reasoning variant.

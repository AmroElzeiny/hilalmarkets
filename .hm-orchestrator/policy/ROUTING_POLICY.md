# OpenCode Go routing policy

## Principle

Optimize completed-task cost, not cheapest single request.
A cheap model that causes repeated retries is more expensive than a stronger worker that completes cleanly.

Availability authority:
1. `.hm-orchestrator/models/LIVE_MODELS.md`
2. local `opencode models opencode-go --refresh --verbose`
3. OpenCode Go endpoint snapshot
4. static catalog only as fallback knowledge

Never invent a model ID or variant.

## Default role routing

### Supervisor — Standard
Preferred: `opencode-go/minimax-m3`
Reason: strong cost/allowance balance; good default orchestration target.
Supervisor plans microtasks, assigns workers, checks checkpoints, and owns report quality.
It should not be the main code writer.

### Supervisor — Deep
Preferred: `opencode-go/glm-5.3-flash`
Use when normal supervisor has failed, task has broad architecture/state coupling, or high-risk reasoning is needed.
Do not automatically use full `glm-5.3`; its Go allowance is much smaller.

### Explorer / repository search
Default: `opencode-go/deepseek-v4-flash`
Alternates: `opencode-go/mimo-v2.5`, `opencode-go/qwen3.8-flash`
Use for read-only discovery, call graphs, test discovery, and log triage.

### Fast worker
Default: `opencode-go/qwen3.8-flash`
Alternates: `opencode-go/deepseek-v4-flash`, `opencode-go/qwen3.7-plus`
Use for narrow, well-specified implementation and mechanical refactors.

### Strong coder
Default: `opencode-go/kimi-k2.7-code`
Alternates: `opencode-go/deepseek-v4-pro`, `opencode-go/minimax-m3`
Use for multi-file implementation, debugging, serialization, state flows, non-trivial refactors.

### Deep cheap worker before Claude escalation
Default: `opencode-go/glm-5.3-flash`
Alternates: `opencode-go/gpt-5.6-luna` only when retention/privacy policy allows and the expected gain justifies it.
Full `glm-5.3`, `kimi-k3`, `qwen3.8-max`, and `grok-4.6` are reserve models because their included Go allowance is materially smaller.

### Test/debug worker
Default: `opencode-go/qwen3.7-plus`
Alternates: `opencode-go/deepseek-v4-flash`, `opencode-go/minimax-m2.7`

### Independent logic reviewer
Default: `opencode-go/deepseek-v4-pro`
Do not use the same model family as the primary coder when another adequate family is available.

### Second/adversarial reviewer
Default: `opencode-go/glm-5.3-flash`
Cost-saving alternate: `opencode-go/qwen3.8-flash`

### Visual reviewer
Default: `opencode-go/deepseek-v4-flash-vision-exp`
Fallback: any live Go model whose current metadata explicitly confirms image input.
The screenshot must be passed as an image attachment. A text-only inspection of CSS is not a visual review.

## Privacy routing

Do not auto-route proprietary Hilal Markets source to models documented as training on prompts/completions.
As of the 2026-09-08 OpenCode Go documentation:
- Muse Spark 1.3 Contributor: training enabled, not ZDR.
- Muse Spark 1.2 Contributor: training enabled, not ZDR.
These are disabled for proprietary-code work unless the user explicitly opts in.

Grok 4.6 and GPT 5.6 Luna were documented with up to 30-day retention and not used for training.
Prefer 0-day-retention models for routine proprietary-code work when capability is adequate.

## Variants / reasoning effort

Variants are provider/model-specific.
Never assume a variant name.

Known upstream facts are not sufficient proof that the exact OpenCode Go route exposes the control.

Examples:
- GLM-5.3 upstream supports `low`, `high`, `max` and defaults to `max`, but OpenCode variant exposure has changed across providers/versions.
- Kimi K3 upstream supports effort levels in Kimi's OpenCode integration, but the OpenCode Go route must still be verified live.
- GPT 5.6 family commonly exposes reasoning-effort variants in OpenCode catalogs, but use only the variants shown for the current Go provider.

Default policy:
- use the base model when live variant exposure is uncertain;
- request `--variant` only after live metadata confirms it;
- record the variant in the supervisor report.

## Escalation ladder

Per work package:
1. first appropriate cheap worker;
2. one repair attempt with the same worker if the failure is local and understood;
3. switch model family or use stronger cheap worker;
4. supervisor reassesses root cause;
5. `ESCALATE_TO_CLAUDE` if architecture/product decision or repeated evidence conflict remains.

Do not run endless repair loops.

## Parallelism

Parallelize read-only exploration and independent review.
Do not let two writing agents modify overlapping files concurrently.
One writer owns a work package at a time unless file scopes are disjoint and explicit.

# OpenCode Go model catalog — Hilal orchestration

Snapshot date: 2026-09-09.

IMPORTANT: This file is routing knowledge, not availability authority. Run `tools/hm-orchestrator/refresh-models.ps1` and prefer `LIVE_MODELS.md`.

The role-fit notes below are system heuristics for economical routing, not vendor guarantees.

| Model ref | Source status | Best use in this system | Privacy note | Auto-route |
|---|---|---|---|---|
| `opencode-go/grok-4.6` | documented | Reserve flagship reasoning/review; expensive Go allowance | 30d, no training | reserve |
| `opencode-go/grok-4.5` | endpoint-extra | Older/extra endpoint model; use only if live catalog confirms and there is a reason | unknown from current Go table | manual |
| `opencode-go/gpt-5.6-luna` | documented | Strong general coding/reasoning; vision-capable in broader OpenCode metadata; deep worker/reviewer reserve | 30d, no training | reserve |
| `opencode-go/glm-5.3-flash` | documented | Strong reasoning/coding at lower cost than full GLM; deep supervisor/worker/reviewer | 0d, no training | preferred-deep |
| `opencode-go/glm-5.3` | documented | High-capability repo-scale/deep reasoning; expensive allowance | 0d, no training | reserve |
| `opencode-go/glm-5.2` | documented | Older strong reasoning/coding fallback | 0d, no training | fallback |
| `opencode-go/glm-5.1` | documented | Older strong fallback | 0d, no training | fallback |
| `opencode-go/glm-5` | endpoint-extra | Legacy/extra endpoint entry; avoid auto-routing | unknown current table | manual |
| `opencode-go/kimi-k3` | documented | Flagship long-context/deep agent; very expensive Go allowance | 0d, no training | reserve |
| `opencode-go/kimi-k2.7-code` | documented | Mature coding-focused model; strong multi-file coder | 0d, no training | preferred-coder |
| `opencode-go/kimi-k2.6` | documented | Strong coding/general fallback | 0d, no training | fallback |
| `opencode-go/kimi-k2.5` | endpoint-extra | Older/extra endpoint entry; avoid auto-routing | unknown current table | manual |
| `opencode-go/longcat-2.0` | documented | Cheap long-context/general worker; repo scan and low-risk implementation after verification | 0d, no training | cheap |
| `opencode-go/mimo-v2.5` | documented | Ultra-cheap mechanical/search/test-triage worker; not architecture authority | 0d, no training | cheap |
| `opencode-go/mimo-v2.5-pro` | documented | Cost-efficient stronger general worker/reviewer | 0d, no training | cheap-strong |
| `opencode-go/mimo-v2-pro` | endpoint-extra | Extra endpoint entry; no automatic routing until live metadata confirms suitability | unknown current table | manual |
| `opencode-go/mimo-v2-omni` | endpoint-extra | Extra omni endpoint entry; use only with live capability evidence | unknown current table | manual |
| `opencode-go/minimax-m3` | documented | Cost-efficient strong agent; default standard supervisor | 0d, no training | preferred-supervisor |
| `opencode-go/minimax-m2.7` | documented | Cheap general/test-debug fallback | 0d, no training | cheap |
| `opencode-go/minimax-m2.5` | endpoint-extra | Older/extra endpoint entry | current table lists pricing but not headline current list | manual |
| `opencode-go/muse-spark-1.3-contributor` | documented | Very cheap contributor model; DO NOT use proprietary Hilal code by default | training enabled; not ZDR; region limited | disabled |
| `opencode-go/muse-spark-1.2-contributor` | documented | Very cheap contributor model; DO NOT use proprietary Hilal code by default | training enabled; not ZDR; region limited | disabled |
| `opencode-go/qwen3.8-max` | documented | High-capability expensive reviewer/deep fallback | 0d, no training | reserve |
| `opencode-go/qwen3.8-flash` | documented | Cost-efficient fast coding/exploration/review | 0d, no training | preferred-fast |
| `opencode-go/qwen3.7-max` | documented | Older premium fallback | 0d, no training | fallback |
| `opencode-go/qwen3.7-plus` | documented | Cost-efficient coding/test/debug worker | 0d, no training | preferred-test |
| `opencode-go/qwen3.6-plus` | documented | Older cost-efficient worker fallback | 0d, no training | fallback |
| `opencode-go/qwen3.5-plus` | endpoint-extra | Older/extra endpoint entry | unknown current table | manual |
| `opencode-go/deepseek-v4-pro` | documented | Strong coding/reasoning; independent logic reviewer or strong worker | 0d* per OpenCode docs, no training | preferred-review |
| `opencode-go/deepseek-v4-flash` | documented | Cheap fast explorer/coder/test triage | 0d* per OpenCode docs, no training | preferred-explore |
| `opencode-go/deepseek-v4-flash-vision-exp` | documented | Vision-capable experimental reviewer for screenshots/visual tasks | 0d* per OpenCode docs, no training | preferred-vision |
| `opencode-go/hy4-preview` | documented | Preview model; use as alternate only after live verification | 0d, no training | experimental |
| `opencode-go/hy3` | documented | Cheap general alternate | 0d, no training | cheap |
| `opencode-go/hy3-preview` | endpoint-extra | Preview/extra endpoint entry | unknown current table | manual |
| `opencode-go/omen-alpha` | documented | Very cheap experimental/alpha worker; low-risk tasks only | 0d, no training | experimental |

## Reasoning / variant controls

- OpenCode variants are model/provider-specific. Never append a variant that is not confirmed in the live catalog.
- GLM-5.3 upstream supports `low`, `high`, `max` and defaults to `max`; provider exposure can differ.
- Kimi K3 supports effort controls in Kimi's own OpenCode integration; confirm the Go route before using one.
- GPT 5.6 Luna supports reasoning controls in OpenCode-family metadata; confirm the Go route and exact names live.
- MiniMax M3 supports thinking upstream, but OpenCode V2 variant exposure has changed; do not hardcode a variant.
- For all other models, base model is the safe default unless `LIVE_MODELS_VERBOSE.txt` proves a selectable variant.

## OpenCode Go allowance reference from 2026-09-08 docs

| Model | approx requests/5h | routing implication |
|---|---:|---|
| Grok 4.6 | 169 | reserve |
| GPT 5.6 Luna | 2,050 | capable reserve |
| GLM-5.3-Flash | 1,580 | deep economical |
| GLM-5.3 | 220 | reserve |
| Kimi K3 | 110 | rare reserve |
| Kimi K2.7 Code | 1,350 | strong coder |
| LongCat-2.0 | 11,400 | cheap |
| MiMo-V2.5 | 30,100 | ultra-cheap |
| MiMo-V2.5-Pro | 3,250 | cheap-strong |
| MiniMax M3 | 3,200 | standard supervisor |
| Qwen3.8 Max | 160 | reserve |
| Qwen3.8 Flash | 5,400 | fast worker |
| Qwen3.7 Plus | 4,300 | test/debug |
| DeepSeek V4 Pro | 1,050 | strong reviewer |
| DeepSeek V4 Flash | 7,600 | explorer/fast |
| DeepSeek V4 Flash Vision Exp | 3,800 | visual reviewer |
| Hy4 preview | 1,350 | alternate |
| Hy3 | 4,300 | cheap alternate |
| Omen Alpha | 11,600 | experimental low-risk |

Sources used to build this snapshot:
- https://dev.opencode.ai/docs/go/
- https://opencode.ai/zen/go/v1/models
- https://opencode.ai/v2/docs/models
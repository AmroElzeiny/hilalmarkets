# R13 — Claude's harness repair, confirmed in this run

Run: 20260912T193031Z-4125cfbe. Supervisor model: `opencode-go/deepseek-v4.1-flash`.

The stopped run already confirmed the repair in `R13_HARNESS_REPAIR.md`. This run
re-confirms it from the files and from the permissions actually in force here.

## 1. Every agent and command file uses only schema-valid frontmatter keys

Search and parse over `.opencode/agents/*.md` and `.opencode/commands/*.md`:

| Check | Result |
|---|---|
| `permissions:` (plural, the wrong key) | **0 files** |
| `permission:` (singular, the real key) | **11 of 11 agent files** |
| `subagent:` in commands | **0 files** |
| `subtask:` in commands | **2 of 2 command files** |
| YAML parses | all files parsed |

`hm-reviewer-visual` is `mode: all` with model `opencode-go/deepseek-v4-flash-vision-exp`,
so it can be called directly with a `--file` attachment and really receive a picture.

## 2. The permission rules are in force in this run

- The supervisor could not edit product code. Every money-path fix in this run went
  through `hm-worker-strong` / `hm-worker-fast`; the supervisor wrote only under
  `.hm-orchestrator/runs/*`. That is the `hm-supervisor-deep.md` rule working.
- The `read` rules deny `*.env` / `*.env.*`. No `.env` value appears anywhere in this
  run's evidence; the report names key names only.
- The visual reviewer was called **directly** from the shell with real image attachments
  (`--agent hm-reviewer-visual --file …`), and its answers name pixels it measured. This
  is the `mode: all` repair working: as `mode: subagent` it could never have received a
  picture.

## 3. Plain words for the report

**These guard rails were not active before 2026-09-12. They are active now.** Before the
repair, the files said "never read `.env`", "never `git push`", "never edit `CLAUDE.md`",
but the tool did not know the key that held those rules, so every agent actually ran on
OpenCode's defaults. This is the codebase's classic failure: two vocabularies for one
concept, and the one in force was not the one written down.

**Verdict: PASS.** No further change to `.opencode/*` was needed, so nothing was
escalated.

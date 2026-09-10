# Worker task — read-only fact verification (smoke test)

You are a cheap read-only sub-agent in an orchestration smoke test. Do not edit files.
Do not run network commands. Do not read .env or .env.* files.

Your only job is to read two specific files in this repository and answer two specific
questions with file evidence.

## Question A — one-owner table in CLAUDE.md

Open `CLAUDE.md`. Find the section titled "Fix the defect class, not the reported
instance". Inside it, there is a markdown table with three columns: `Concept`,
`Owner`, `Never re-implement`. The `Owner` column lists source files under
`engine/`.

List every owner module path you see in that table. Show the exact `Concept` text
on the same row as your evidence.

Do not paraphrase. Quote the row.

## Question B — safe_local commands

Open `.agents/commands.json`. The file is a JSON array under `commands`. Each entry
has a `safety` string.

Report the count of entries where `safety` is exactly `"safe_local"`. Show at least
two example `id` strings from those entries to prove you read the file rather than
guessing.

## Evidence required in your final answer

For each question, give:
- the file path;
- the exact text or count you found;
- the command or read method you used (file open, grep, etc.).

If you cannot read either file, say so and stop. Do not invent an answer.

Return your answer as a compact plain-text block. No markdown tables.
# Escalation contract

When the cheap supervisor needs Claude, create `<run_dir>/ESCALATION.json`.

Required shape:

```json
{
  "status": "ESCALATE_TO_CLAUDE",
  "run_id": "...",
  "needs_claude_implementation": false,
  "category": "architecture|product-decision|repeated-failure|evidence-conflict|security|governance|visual|other",
  "summary": "short precise statement",
  "decision_needed": "the exact question Claude must answer",
  "evidence": [
    "file:line / test / diff / command result"
  ],
  "attempts": [
    {"model":"...", "action":"...", "result":"..."}
  ],
  "allowed_files": [
    "src/path/file.py",
    "tests/path/test_file.py"
  ],
  "recommended_next_action": "replan|redelegate-deep|claude-implement"
}
```

Set `needs_claude_implementation=true` only when model escalation/replanning is not enough and Claude actually needs to edit code.
Keep `allowed_files` as narrow as possible.

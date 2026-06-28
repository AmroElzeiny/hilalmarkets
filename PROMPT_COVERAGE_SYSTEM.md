# Prompt Coverage System

`engine/prompt_audit.py` classifies every prompt fragment into one bucket:

- `executable_condition`
- `optional_condition`
- `assumption`
- `ambiguity`
- `unsupported`
- `ignored_filler`
- `unclassified`

If a meaningful fragment becomes `unclassified`, the interpretation is treated as needing
review and the rule-based interpreter adds a blocking `prompt_fragment_unclassified` issue.

## Report Fields

The generated `PromptCoverageReport` includes:

- original and normalized prompt
- prompt fragments
- extracted intent categories
- executable and optional condition fragments
- unsupported, ignored, and ambiguous fragments
- assumptions
- confidence score
- activation blocked flag
- coverage score
- critical missing fields
- warnings
- mapping table

## Condition Provenance

Each generated condition now carries:

- `source_fragment`
- `confidence`
- `ai_interpreted`
- `provider_required`
- `availability`

These fields are preserved in strategy schema JSON and exposed in the dashboard
interpretation response.

## Dashboard Behavior

The Strategy Builder preview now shows:

- prompt coverage percentage
- confidence percentage
- rules created
- source fragment per rule
- assumptions
- clarification issues
- prompt coverage map

The user can inspect what was understood before opening the visual strategy map.

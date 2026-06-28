# Strategy Board Spec

The Strategy Board is the canonical visual representation of a monitor draft.

Board areas:

- Start
- Monitor Overview
- Universe
- Entry Logic
- Condition nodes
- Filters
- Alert Rules
- Risk Context
- Proof & Review

Rules:

- Board positions are UI metadata only.
- Strategy logic remains in `StrategyDefinition`.
- Each condition card opens the condition drawer.
- Source metadata stays attached to conditions.
- Visual links help comprehension but must not secretly change strategy logic unless the user explicitly edits/deletes a rule.

The right trust panel contains:

- Summary
- Coverage
- Validation
- Preview
- AI Help

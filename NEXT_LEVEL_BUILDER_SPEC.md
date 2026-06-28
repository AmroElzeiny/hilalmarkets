# Next-Level Builder Spec

TraceEdge Strategy Builder is organized as a Strategy Guidebook plus a Strategy Board.

The first screen offers three paths:

- Describe my strategy
- Build visually
- Start from template

All paths end in the same editable Strategy Board. Nothing activates directly from AI text or a template.

Core requirements now implemented:

- Structured prompt fields instead of one large text box.
- Prompt Understanding Preview before the board opens.
- Prompt coverage, confidence, assumptions, ambiguities, ignored filler, and unsupported/provider-required items.
- Per-condition source traces, confidence, AI-interpreted metadata, provider-required metadata, and availability.
- Condition drawer editing rather than crowded inline forms.
- Searchable/categorized condition Guidebook.
- Board view with monitor, universe, entry logic, filters, risk, alerts, proof/review, and condition nodes.
- Validation-gated Start Monitoring action.
- Interpretation feedback buttons recorded as audit events.

Deferred:

- Admin prompt-test harness UI.
- Provider-backed market-context execution for currently provider-required items.
- Rich drag/drop connector creation beyond the current controlled board connections.

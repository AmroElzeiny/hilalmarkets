# Supported Capabilities

The capability registry is the source of truth for what the product can parse and evaluate.

Capability classes:

- Executable: deterministic evaluator support exists.
- Recognized not executable: the phrase is understood, but the scanner cannot evaluate it yet.
- Template-backed: available through guided templates.
- Dashboard-only: complex configuration opens the dashboard.

User-facing behavior:

- Executable mandatory rules can scan.
- Recognized but unsupported mandatory rules block activation or scan execution.
- Optional unsupported rules appear as warnings and proof rows.
- Every unsupported item should explain what the user needs to clarify or simplify.

Implementation rule:

Do not let an LLM invent indicator values, proof results, market health, or scan outcomes.

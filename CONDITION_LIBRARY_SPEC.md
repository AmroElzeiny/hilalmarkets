# Condition Library Spec

The condition library is powered by `condition_registry_payload()`.

Important payload sections:

- `items`
- `categories`
- `guidebook_categories`
- `logic_operators`
- `builtin_templates`
- `condition_template`
- `availability`
- `implementation_status`
- `compatibility_notes`

Search behavior:

- Empty search shows Popular conditions by default.
- Typed search scans the full catalogue.
- Category clicks filter by canonical guidebook category.

Add behavior:

- Available/implemented conditions can be added.
- Provider-required, unsupported, planned, or experimental items are disabled with a clear badge.
- Required parameters open the condition drawer.

Condition source metadata:

- `source_fragment`
- `confidence`
- `ai_interpreted`
- `provider_required`
- `availability`

# How Claude and the supervisor interpret live model metadata

The goal is to know every model that is actually selectable without pretending static documentation is complete.

Before an important mission, `refresh-models.ps1` saves:

- `LIVE_MODEL_IDS.txt` — exact locally selectable `opencode-go/...` IDs.
- `LIVE_MODELS_VERBOSE.txt` — OpenCode's current verbose metadata.
- `LIVE_ENDPOINT.json` — current OpenCode Go endpoint inventory when reachable.
- `LIVE_MODELS.md` — compact local/endpoint snapshot and drift.

## Selection fields

When choosing a model, inspect these dimensions when the live metadata exposes them:

1. exact provider/model ID;
2. tool support;
3. text/image input support;
4. reasoning/thinking support;
5. selectable variants and exact variant names;
6. context/output limits;
7. effective token/cost metadata;
8. whether the model is current, preview, alpha, contributor, or endpoint-extra;
9. privacy/retention policy from the static Go privacy table;
10. empirical role history in previous supervisor reports.

## Variant rule

A model may support reasoning upstream while the exact OpenCode Go provider does not expose a switch.
Therefore:
- upstream capability is informative;
- live Go variant metadata is authoritative for configuration;
- if no Go variant is proven, use the base model;
- never send a guessed `--variant`.

## Empirical learning

After each run, the supervisor report records model, role, attempts, and result.
Claude should prefer a model that has already completed the same class of Hilal task cleanly, even when a theoretically cheaper model exists.

Do not promote one lucky run into a universal rule. Prefer repeated evidence.

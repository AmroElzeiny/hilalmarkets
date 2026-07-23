# Cost implementation notes

- Online dynamic turns use the Responses API because each next trader message depends on the target response.
- `service_tier=flex` is configurable for eligible models.
- Deferred judge requests can be submitted through the Batch API.
- Stable instructions use `prompt_cache_key`; SQLite reuses identical evaluator outputs.
- Prices are environment inputs, never hard-coded, because model prices change.
- Batch stores request data until deleted and is not Zero Data Retention eligible; choose online mode when retention policy requires it.

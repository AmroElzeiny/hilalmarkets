# Retired Discord Compatibility

Discord is not an active HilalMarkets integration. The API routes, callbacks, adapters, worker
tasks, settings, customer controls, plan features, and current documentation were removed for the
private beta.

The database models and enum value remain read-only because historical alert, delivery, and audit
rows may reference them. New strategy validation rejects the channel with
`delivery_channel_retired`, current preferences filter it out, and release invariants scan active
API, templates, static assets, Telegram code, and worker entry points for accidental reintroduction.

Physical table or enum removal requires a separately reviewed data migration after production data
has been inventoried and exported. Historical implementation reports are retained as dated audit
records; they do not describe the current product surface.

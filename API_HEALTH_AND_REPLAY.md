# Health And Replay API

All routes require the normal authenticated dashboard session.

Base path: `/api/v1/dashboard/cockpit`

## Monitor Health

- `GET /strategies/{strategy_id}/health`
- `GET /strategies/{strategy_id}/bottlenecks?limit=500`
- `POST /strategies/{strategy_id}/frequency-forecast`
- `GET /strategies/{strategy_id}/decay`

Health response example:

```json
{
  "score": 72,
  "grade": "B",
  "status": "usable",
  "main_issue": "Volume confirmation is the strongest blocker.",
  "components": [],
  "non_advisory_notice": "Edge Health describes monitor behavior and evidence quality, not profitability."
}
```

## Validation And Improvement

- `POST /strategies/validate`
- `POST /strategies/{strategy_id}/suggestions`
- `POST /suggestions/{suggestion_id}/apply`

Applying a suggestion creates a draft `StrategyVersion`. It does not activate it.

## Universe

- `POST /strategies/{strategy_id}/universe-preview`

The response separates included symbols, static exclusion reasons, and filters deferred to
live provider metadata.

## Feedback

- `POST /alerts/{alert_id}/feedback`
- `GET /alerts/{alert_id}/proof`

Supported feedback includes good alert, too early, too late, false alert, missed move, too many
alerts, too strict, not relevant, bad market context, and good idea with weak proof.

## Missed Move

- `POST /missed-moves`
- `GET /missed-moves/{analysis_id}`

The result includes passed, failed, pending, and unavailable conditions, timeline evidence,
universe inclusion, persisted setup state, chart data, and a non-predictive notice.

## Lifecycle Timeline

- `GET /setups/{setup_id}/timeline`

Timeline entries include lifecycle transitions and condition state changes.

## Experiments

- `POST /strategies/{strategy_id}/experiments`
- `POST /experiments/{experiment_id}/promote`

Experiments compare stored evaluations, confirmed/forming counts, corrective feedback,
strictness, and schema differences.

## Preferences

- `GET /preferences`
- `PUT /preferences`
- `DELETE /preferences`

Delete implements “Forget my preferences.”

## Inbox

- `GET /inbox`
- `POST /inbox/{item_id}`
- `POST /inbox-actions/bulk`

Filters include item type, monitor, symbol, timeframe, state, review state, minimum health,
date range, and archive state.

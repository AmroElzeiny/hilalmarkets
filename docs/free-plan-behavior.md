# Free Plan Behavior

The free plan exists to let users understand the product without excessive infrastructure cost.

Expected limits:

- One saved strategy.
- One active monitor.
- Limited symbol universe.
- Telegram delivery only unless trial or paid plan allows more.
- Basic proof receipt.
- Limited historical preview and Light Scan.

Trial behavior:

- Trial claiming requires a linked dashboard account.
- A user should see historical preview, sample alert, sample Near-Miss result, and proof examples.
- Trial reminders must be useful and sparse.

Enforcement:

- Enforce limits at API and worker layers.
- Do not hide restricted actions only in the UI.
- Downgrades pause excess monitors but never delete strategies or proof history.

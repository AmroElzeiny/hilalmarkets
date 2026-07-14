# TradingView Charting Library Placeholder

HilalMarkets' lifecycle chart dialog is wired for the licensed TradingView Charting Library, not the public iframe widget.

Place the official TradingView Charting Library distribution in this folder so this file exists:

```text
src/ai_market_monitor/static/charting_library/charting_library.js
```

Keep the rest of the library package next to it, including its bundles, assets, and `datafeeds` files if supplied by TradingView.

The dashboard datafeed in `static/dashboard.js` provides deterministic lifecycle candles and condition marks from:

```text
/api/v1/dashboard/lifecycles/{setup_id}/chart
```

Do not commit proprietary TradingView library files unless your license permits it.

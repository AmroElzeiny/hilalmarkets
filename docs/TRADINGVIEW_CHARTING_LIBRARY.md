# TradingView Charting Library Setup

TraceEdge supports the licensed TradingView Advanced Charts / Charting Library in the Lifecycle chart dialog.

## Current Local Status

The repository contains only this placeholder:

```text
src/ai_market_monitor/static/charting_library/README.md
```

The actual licensed file is currently missing:

```text
src/ai_market_monitor/static/charting_library/charting_library.js
```

When this file is missing, TraceEdge falls back to TradingView Lightweight Charts or the native chart. Those fallback charts can show candles and lifecycle marks, but they do not include the full TradingView vertical drawing toolbar or full header controls.

## Why It Is Missing

TradingView distributes Advanced Charts / Charting Library through an official private GitHub repository after access approval. The package is not redistributable and should not be committed to a public repository.

## Install After Access Is Granted

1. Request access from TradingView and accept the GitHub invitation.
2. Download or clone the official package.
3. Run:

```powershell
.venv\Scripts\python.exe scripts\install_tradingview_charting_library.py --source "C:\path\to\charting_library"
```

You can also set:

```env
TRADINGVIEW_CHARTING_LIBRARY_SOURCE_DIR=C:\path\to\charting_library
```

Then run:

```powershell
.venv\Scripts\python.exe scripts\install_tradingview_charting_library.py
```

4. Restart the API or Docker service.
5. Reopen Dashboard -> Lifecycles -> Chart.

## Expected Files

At minimum this must exist:

```text
src/ai_market_monitor/static/charting_library/charting_library.js
```

The rest of the package, especially `bundles/`, must remain beside it.

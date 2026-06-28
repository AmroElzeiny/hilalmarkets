# Provider Env Placeholders

These placeholders are present in `.env.example`. Do not commit real keys.

## Market Data Mode

```text
TRACEDGE_MARKET_DATA_MODE=ccxt
TRACEDGE_FIXTURE_MARKET_DATA_ENABLED=false
MARKET_DATA_PROVIDER=ccxt
MARKET_DATA_EXCHANGE=binance
```

- `ccxt`: normal local/live public exchange mode.
- `fixture`: deterministic local/test candles only.
- Staging/production reject fixture mode.

## Binance

```text
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_REST_BASE_URL=https://api.binance.com
BINANCE_WS_BASE_URL=wss://stream.binance.com:9443
BINANCE_SPOT_API_BASE=https://api.binance.com
BINANCE_FUTURES_API_BASE=https://fapi.binance.com
BINANCE_MARKET_DATA_ENABLED=true
BINANCE_ORDER_BOOK_ENABLED=true
BINANCE_DERIVATIVES_ENABLED=false
```

- Spot public market data is the preferred private-beta source.
- Do not request trading or withdrawal permissions for v1.
- Derivatives context is disabled by default.

## Bybit

```text
BYBIT_API_KEY=
BYBIT_API_SECRET=
BYBIT_REST_BASE_URL=https://api.bybit.com
BYBIT_WS_BASE_URL=wss://stream.bybit.com/v5/public/spot
```

- Placeholder for public spot-market support.
- Do not enable private keys unless a read-only data adapter explicitly needs them.

## CoinGecko

```text
COINGECKO_API_BASE=https://api.coingecko.com/api/v3
COINGECKO_API_KEY=
COINGECKO_PLAN=none
COINGECKO_ENABLED=false
```

- Candidate for market cap, metadata, categories, rankings, and global crypto context.
- Disabled until adapter/proof/rate-limit tests exist.

## Alternative.me

```text
ALTERNATIVE_ME_API_BASE=https://api.alternative.me
ALTERNATIVE_ME_ENABLED=false
```

- Candidate for Fear and Greed sentiment context.
- Disabled until attribution and proof support are implemented.

## FRED

```text
FRED_API_BASE=https://api.stlouisfed.org/fred
FRED_API_KEY=
FRED_ENABLED=false
```

- Candidate for macro context.
- Disabled until macro-series mapping and release-window logic are tested.

## Generic Context Providers

```text
MARKET_METADATA_API_URL=
MARKET_METADATA_API_KEY=
CRYPTO_INDEX_API_URL=
CRYPTO_INDEX_API_KEY=
MACRO_MARKET_API_URL=
MACRO_MARKET_API_KEY=
EVENT_FEED_API_URL=
EVENT_FEED_API_KEY=
TOKEN_CATEGORY_API_URL=
TOKEN_CATEGORY_API_KEY=
DERIVATIVES_CONTEXT_API_URL=
DERIVATIVES_CONTEXT_API_KEY=
CONTEXT_PROVIDER_TIMEOUT_SECONDS=15
CONTEXT_FETCH_CONCURRENCY=8
```

These are strict condition-context providers. They must return deterministic scalar values and `as_of` timestamps. Missing or late provider data becomes unavailable proof, never a guessed pass.

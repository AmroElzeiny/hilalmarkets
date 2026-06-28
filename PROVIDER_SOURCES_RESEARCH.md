# Provider Sources Research

Date: 2026-06-27

This file records candidate data sources for hidden provider-required concepts. A concept must remain hidden until a real adapter, rate-limit handling, proof evidence, and tests exist.

## Official Sources Reviewed

### CoinGecko

- Documentation: https://docs.coingecko.com/reference/endpoint-overview
- Coins markets endpoint: https://docs.coingecko.com/reference/coins-markets
- Candidate support:
  - market cap
  - volume/ranking
  - token lists and metadata
  - categories and global market context, depending on plan and endpoint
- Classification: `FREE_WITH_LIMITS`
- Decision: keep hidden. Add placeholders, adapter contract, cache/backoff, proof fields, and commercial-terms review before enabling.

### Binance Spot Public API

- Documentation: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints
- Candidate support:
  - OHLCV/klines
  - order book depth
  - recent and aggregate trades
  - ticker/24h statistics
- Classification: `FREE_WITH_LIMITS`
- Decision: keep currently tested OHLCV paths available through CCXT. Keep advanced order-book/trade-flow concepts hidden until proof receipts include source timestamp, depth, latency, and exchange-specific rate-limit behavior.

### Binance USD-M Futures Public API

- Funding history: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
- Open interest: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest
- Candidate support:
  - funding rate
  - open interest
  - futures market context
- Classification: `FREE_WITH_LIMITS`
- Decision: hidden for normal spot-monitor UI in private beta. Futures-derived context can be added later as optional context only, not as a spot execution signal.

### Alternative.me

- Fear and Greed Index: https://alternative.me/crypto/fear-and-greed-index/
- Candidate support:
  - broad crypto sentiment context
- Classification: `FREE_WITH_LIMITS`
- Decision: hidden until attribution, cache, latency, and proof requirements are implemented.

### FRED

- API documentation: https://fred.stlouisfed.org/docs/api/fred/
- Series observations: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
- Candidate support:
  - macro series observations
  - rates and broad economic context
- Classification: `FREE_WITH_LIMITS`
- Decision: hidden until API key, macro-series mapping, economic-release calendars, and proof freshness are implemented.

## Human-Check Required

Before enabling any source:

- Verify commercial terms for SaaS use.
- Verify attribution requirements.
- Verify rate limits for private-beta scan volume.
- Verify caching permissions.
- Verify data freshness and latency.
- Verify outage behavior.
- Add provider-specific tests and proof receipts.

## Paid/Unclear Sources

News/event feeds, liquidation feeds, detailed order-flow feeds, and some token unlock/category datasets may require paid vendors or licensing review. They remain hidden and disabled until a human validates legal/commercial terms.

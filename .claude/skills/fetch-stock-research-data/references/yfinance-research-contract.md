# Yfinance research contract

The "first data pull" for the stock decision-support system
(`docs/plans/goals/stock_decision_support_system.md`). One JSON payload per
ticker, fetched from a verified Yahoo `provider_symbol`. Every value is
normalized to JSON-safe types; per-group and per-subfield failures are recorded
in `errors`, never raised.

## Request

Each request is `{"ticker", "provider_symbol", "groups"?, "history_days"?}`.
`groups` defaults to all groups below; `history_days` defaults to 400. Symbols
must come from `ticker_provider_mappings` (`provider='yahoo'`,
`verification_status='verified'`) — this pull does no fuzzy resolution of its
own. Batch size is capped at 25 tickers.

## Groups → yfinance source (maps to thesis-template sections)

| Group | yfinance source | Thesis section |
|---|---|---|
| `overview` | `get_info()`: longName, quoteType, longBusinessSummary, sector, industry, fullExchangeName, country, currency, financialCurrency, website, fullTimeEmployees | Company Overview |
| `valuation` | `get_info()`: currentPrice, marketCap, enterpriseValue, trailingPE, forwardPE, pegRatio, priceToBook, priceToSalesTrailing12Months, enterpriseToEbitda, freeCashflow, margins, ROE/ROA, dividendYield, targetMeanPrice | Valuation Analysis |
| `financials` | `income_stmt`/`quarterly_income_stmt`, `balance_sheet`/`quarterly_balance_sheet`, `cashflow`/`quarterly_cashflow` | Financial Analysis |
| `earnings` | `calendar`, `earnings_dates` | Earnings and Catalysts |
| `analyst` | `recommendations`, `recommendations_summary`, `analyst_price_targets`, `upgrades_downgrades` | Market Sentiment |
| `options` | `options` (expirations) + `option_chain(date)` for the nearest 3 expiries (calls/puts: volume, open interest, implied volatility) | Options Activity |
| `news` | `news` (raw headlines/links) | Market Sentiment |
| `insider` | `insider_transactions`, `insider_purchases`, `insider_roster_holders` | Insider Activity |
| `institutional` | `major_holders`, `institutional_holders`, `mutualfund_holders` | Market Sentiment |
| `dividends` | `dividends` (history) + `get_info()` dividendRate/Yield, payoutRatio, exDividendDate, fiveYearAvgDividendYield | Dividend Analysis |
| `history` | `fetch_security_history` (pipeline OHLCV) — raw only | Technical Analysis (indicators computed by a later agent) |

## Out of scope (needs another source or a later interpretation agent)

- Technical indicators (RSI, MACD, moving averages, support/resistance) — computed by a future Technical Analysis Agent from the raw `history` group.
- News/social sentiment scoring — `news` is raw headlines only.
- Unusual options activity / sweep detection — raw chains only.
- Segment/geographic revenue detail, competitive position, guidance and management commentary — not in yfinance (filings/transcripts/news).
- Peer valuation comparison and historical valuation ranges — require multi-ticker/time aggregation, not a single-ticker pull.
- Insider "routine vs. meaningful" judgment — raw transactions only.

## Output

`[{"ticker", "provider_symbol", "groups", "data": {group: payload}, "errors": {"group" | "group.subfield": message}}]`

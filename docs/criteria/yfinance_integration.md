# YFinance Integration Success Criteria

Use this checklist to decide whether the yfinance integration is ready to replace `extracted_yfinance_method/`.

## Scope

- Runtime yfinance code lives in `src/yfinance_extractor.py`.
- The future pipeline trigger is deferred.
- The future pipeline will pass a list of tickers and the required date range into the yfinance functions.
- Database lookup and upload behavior are out of scope for this integration.
- CSV export is out of scope unless a future caller explicitly requests it.

## Required Outcomes

- [x] The new extractor can fetch stock metadata fields that existed in the old method.
- [x] The new extractor can fetch ETF metadata fields that existed in the old method.
- [x] The new extractor can fetch historical OHLCV data for supplied tickers.
- [x] Metadata output is returned as dataframes, not dictionaries.
- [x] Historical output is returned as a dataframe.
- [x] Empty ticker input returns empty dataframes with stable columns.
- [x] Unknown or unsupported security types are logged and skipped.
- [x] One failed metadata ticker does not prevent other tickers from being processed.
- [x] The `.TO` retry behavior from the old method is retained for unresolved tickers.
- [x] The extractor does not depend on legacy database helper functions.
- [x] The extractor does not write `yFinance_Data.csv`.
- [x] The extractor can be run directly from the terminal for manual live testing.
- [x] The legacy `extracted_yfinance_method/` folder is removed after migration.

## Output Contracts

- [x] Stock metadata columns are `ticker`, `company_name`, `asset`, `exchange`, `currency`, `sector`, and `industry`.
- [x] ETF metadata columns are `ticker`, `company_name`, `exchange`, `currency`, `fund_family`, `asset`, `yield`, `expense_ratio`, `aum`, `nav`, `top_holdings`, and `sector_weights`.
- [x] Historical data columns are `Date`, `Ticker`, `Open`, `High`, `Low`, `Close`, `Adj Close`, and `Volume`.

## Test Expectations

- [x] Focused tests cover stock metadata extraction.
- [x] Focused tests cover ETF metadata extraction.
- [x] Focused tests cover `.TO` fallback behavior.
- [x] Focused tests cover unknown security type handling.
- [x] Focused tests cover single-ticker historical dataframe normalization.
- [x] Focused tests cover multi-ticker historical dataframe normalization.
- [x] Focused tests cover empty ticker input.
- [x] Focused tests cover CLI argument validation and CLI dispatch behavior.

## Improvements Applied

- [x] Removed database coupling from the yfinance fetcher.
- [x] Removed the automatic CSV side effect.
- [x] Moved static yfinance settings and output schemas into `src/config.py`.
- [x] Used shared logging through `src/system_logger.py`.
- [x] Made yfinance calls mockable for unit tests.
- [x] Returned stable dataframe schemas for empty results.

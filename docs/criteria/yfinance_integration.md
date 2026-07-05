# YFinance Integration Success Criteria

Use this checklist to decide whether the yfinance integration is ready to replace `extracted_yfinance_method/`.

## Scope

- Runtime yfinance code lives in `src/yfinance_extractor.py`.
- Extraction remains database-independent. `src/market_data.py` now supplies the
  separate database synchronization layer and `yfinance-sync` CLI.
- The synchronizer derives ticker/date targets from portfolio activity and verified
  provider mappings.
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
- [x] Routine synchronization never updates existing `tickers` rows.
- [x] Routine synchronization preserves automatic and manual Yahoo mappings.
- [x] Returned metadata is bound to the requested ticker IDs; unexpected rows are skipped.
- [x] Stock and ETF detail metadata can refresh without changing ticker identity.

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
- [x] Database tests cover enrichment of a ticker referenced by email transactions.
- [x] Database tests verify ticker identity and provider mappings remain unchanged.

## Improvements Applied

- [x] Removed database coupling from the yfinance fetcher.
- [x] Removed the automatic CSV side effect.
- [x] Moved static yfinance settings and output schemas into `src/config.py`.
- [x] Used shared logging through `src/system_logger.py`.
- [x] Made yfinance calls mockable for unit tests.
- [x] Returned stable dataframe schemas for empty results.

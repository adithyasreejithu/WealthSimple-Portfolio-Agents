# Database Foundation

The active database implementation is `src/database.py` and uses DuckDB.

## Startup Contract

Call `initialize_database()` when the future end-to-end process starts.

- A database with the complete current schema and matching schema version is active, so initialization returns without changing data.
- An empty database is initialized with every table in one transaction.
- A partial, legacy, or version-mismatched database raises an error. Migration is deferred and tracked in `docs/TODO.md`.

The `schema_metadata` table records the active schema version. Startup also verifies that every required table exists, so metadata alone cannot mark a partial schema as active.

## Security Normalization Contract

`tickers` is the primary table for exchange-listed instruments. It owns the normalized symbol, exchange, currency, security name, and security type. Symbols, exchanges, and currencies are uppercase, and `(ticker_symbol, exchange)` is unique so the same symbol can represent different listings on different exchanges.

`stock_details` and `etf_details` contain only type-specific attributes. Transaction, activity, email, and historical tables reference `tickers.ticker_id` instead of duplicating ticker text.

`staged_records.contains_fx_rate` records whether the source row included an FX rate during parsing or staging. The current ingestion assumption is:

- FX rate present → treat the security as USD-listed and keep the ticker unchanged.
- FX rate absent → treat the security as CAD-listed and append `.TO` before metadata enrichment.

This is a temporary rule and should be replaced later by a security master or exchange-mapping source.

## Full Activity Export Imports

`activity_imports` tracks each source file, its hash, status, row counts, duplicate counts, and unresolved ticker count.

`raw_activity_exports` preserves every original Wealthsimple export field for audit and reconciliation. `activities` is the typed analytics table and stores `ticker_id` instead of symbol or security name.

Ticker-bearing rows must resolve unambiguously before an import is published. Rejected imports retain their raw rows and source file. Successful imports append new activity, update lineage for repeated history, and archive the source CSV.

Identical files are detected by hash. Repeated historical rows are matched by a normalized row fingerprint and duplicate ordinal, allowing legitimate identical rows to remain separate.

## Implementation Metadata

Database foundation implementation model: 5.5 Medium.

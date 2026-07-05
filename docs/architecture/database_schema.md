# Database Foundation

The active database implementation is `src/database.py` and uses DuckDB.

## Startup Contract

Call `initialize_database()` when the future end-to-end process starts.

- A database with the complete current schema and matching schema version is active, so initialization returns without changing data.
- An empty database is initialized with every table in one transaction.
- Schema versions 2 through 7 are upgraded transactionally. Partial or unknown schemas still raise an error.

The `schema_metadata` table records the active schema version. Startup also verifies that every required table exists, so metadata alone cannot mark a partial schema as active.

## Security Normalization Contract

`tickers` is the primary table for exchange-listed instruments. It owns the normalized symbol, exchange, currency, security name, and security type. Symbols, exchanges, and currencies are uppercase, and `(ticker_symbol, exchange)` is unique so the same symbol can represent different listings on different exchanges.

`stock_details` and `etf_details` contain only type-specific attributes. Transaction, activity, email, and historical tables reference `tickers.ticker_id` instead of duplicating ticker text.

Ticker identity is immutable during routine market synchronization. Yfinance may
refresh `stock_details` and `etf_details` for an existing `ticker_id`, but it does
not update the parent ticker row, its Yahoo provider mapping, or its symbol history.
Only explicit ticker onboarding may insert a missing identity and its initial mapping.

`staged_records.contains_fx_rate` records whether the source row included an FX rate during parsing or staging. The current ingestion assumption is:

- FX rate present → treat the security as USD-listed and keep the ticker unchanged.
- FX rate absent → treat the security as CAD-listed and append `.TO` before metadata enrichment.

This is a temporary rule and should be replaced later by a security master or exchange-mapping source.

Email rows use parsed `price_currency` instead of the statement FX heuristic. Unknown
symbols are persisted as pending and retain their raw source symbol without blocking
known trades.

## Live Email Ledger

`email_messages` stores message identity, received time, and a content hash. Its
`(source, source_message_id)` key makes repeated boundary-date fetches idempotent.

`email_transactions` stores source symbol and currency alongside optional `ticker_id`.
Resolved provisional trades contribute to live quantities. A uniquely matching
statement row links to and supersedes the email row so analytics never counts both.

`historical_records` receives idempotent Yahoo OHLCV upserts. Initial history begins at
first portfolio activity and incremental runs continue after the latest stored date.

## Full Activity Export Imports

`activity_imports` tracks each source file, its hash, status, row counts, duplicate counts, and unresolved ticker count.

`raw_activity_exports` preserves every original Wealthsimple export field for audit and reconciliation. `activities` is the typed analytics table and stores `ticker_id` instead of symbol or security name.

Estimated Wealthsimple FX fees are intentionally not stored as schema fields.
Analytics derives them from statement `transactions.transaction_type`, `debit`,
`credit`, and `fx_rate` using the configured fee rate. This avoids stale derived
values and does not duplicate unavailable fields into activity or email tables.

Ticker-bearing rows must resolve unambiguously before an import is published. Rejected imports retain their raw rows and source file. Successful imports append new activity, update lineage for repeated history, and archive the source CSV.

Identical files are detected by hash. Repeated historical rows are matched by a normalized row fingerprint and duplicate ordinal, allowing legitimate identical rows to remain separate.

## YFinance Market Data

After an email batch commits, the application synchronizes market data for verified,
owned tickers. This applies to `pipeline --source email` and to the email branch of a
full pipeline. Statement-only and export-only runs do not contact Yahoo.

The initial history window begins on each ticker's earliest date in
`transactions`, `email_transactions`, or `activities`. Later runs begin on the
day after the latest `historical_records.record_date`. The fetch end is tomorrow
because yfinance treats its end date as exclusive.

Only tickers with a verified Yahoo provider mapping are eligible. The synchronizer
uses that mapping to bind returned metadata to an existing `ticker_id`; unexpected
provider rows are skipped. Detail metadata and history are written in one transaction,
and history is upserted by
`(ticker_id, record_date)`. A market-data failure does not roll back the already
completed Wealthsimple ingestion batch; it is returned as a failed pipeline
partial pipeline result for retry through `yfinance-sync`. One failed Yahoo symbol
does not prevent successful symbols from being committed.

## Implementation Metadata

Database foundation implementation model: 5.5 Medium.

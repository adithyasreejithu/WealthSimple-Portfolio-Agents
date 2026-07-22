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
Resolution is keyed purely on exact `ticker_symbol` text match; it never consults
`ticker_symbol_history`, so a provider renaming a symbol creates a second, separate
identity rather than being recognized as a continuation. `ticker-map merge`
(`src/ticker_mapping.py::merge_tickers`) is the one sanctioned, explicit exception to
the immutability rule above: it consolidates two already-populated ticker identities
into one after such a rename, distinct from anything routine sync ever does on its own.

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

## Earnings & Dividends Calendars

Two reference tables hold company-declared market data pulled from yfinance:
`earnings_events` (earnings calendar keyed by `(ticker_id, report_date)` with
`eps_estimate`, `eps_actual`, `surprise_pct`) and `dividend_events` (declared
dividend schedule keyed by `(ticker_id, ex_dividend_date)` with
`declared_amount`). `Surprise(%)` is stored exactly as yfinance returns it
(already in percentage points, verified against live held tickers). Columns
`earnings_events.period`/`revenue_estimate`/`revenue_actual` and
`dividend_events.pay_date`/`frequency` exist but stay `NULL` in v1 — yfinance's
`earnings_dates`/`dividends` calls do not carry them at the needed granularity.

This is company-declared reference data, **completely distinct** from
`cash_transactions`/`transactions`, which record the user's own received
dividend cash from brokerage statements. The two must never be joined,
implicitly merged, or conflated; `dividend_events` has no relationship to
position/holdings math.

Scope is the same owned + verified-Yahoo-mapping ticker set as the OHLCV sync
(`get_market_targets`). Unlike OHLCV history, there is no incremental date
window: every `earnings-dividends-sync` run re-fetches each ticker's full
available window and reconciles it against stored rows in one transaction.
Dividends upsert on `(ticker_id, ex_dividend_date)`. Earnings upsert on
`(ticker_id, report_date)`, and before each ticker's freshly fetched batch is
inserted, still-unreported future rows (`report_date >= CURRENT_DATE AND
eps_actual IS NULL`) are deleted so an estimate that gains an actual updates in
place and an abandoned/revised speculative date is retired; confirmed past rows
are never deleted. ETF/fund tickers legitimately return no earnings events and
are not treated as failures.

## Financial Snapshots

`financial_snapshots` holds per-quarter company financial statement data
pulled from yfinance's `quarterly_income_stmt`/`quarterly_balance_sheet`/
`quarterly_cashflow`, keyed by `(ticker_id, period_end_date)`. It is a hybrid
schema: named, typed columns (`revenue`, `net_income`, `eps`, `gross_margin`,
`operating_margin`, `debt_to_equity`, `current_ratio`, `free_cash_flow`) exist
only for the fields `Knowledge-Base/taxonomy/decision-rubric.yml`'s
`derived:*` evidence already scores against (plus revenue/EPS/margins as
first-class columns), and one `extra JSON` column carries every other line
item either statement returns (total assets, R&D, SG&A, industry-specific
lines like a bank's net interest income, etc.), so a new ratio never requires
its own migration. Figures are stored exactly as yfinance reports them in the
ticker's `financial_currency` — no FX conversion here (see
`portfolio_metrics.py`/`analytics.py` for that).

Named-column values come from an alias-list lookup (e.g. `Total Revenue`,
`TotalRevenue`, `Revenue` are all tried in order for `revenue`), since line
items vary by yfinance version and industry — confirmed live: a bank's
balance sheet (e.g. `JPM`) has no `Current Assets`/`Current Liabilities`
split at all, so `current_ratio` is legitimately `NULL` for banks while
`debt_to_equity` still computes; this is intentional, not a fetch failure.
`free_cash_flow` prefers yfinance's own direct `Free Cash Flow` row (present
for every equity checked live) and falls back to `operating_cash_flow +
capital_expenditure` when absent — confirmed live that `Capital Expenditure`
is stored negative-as-outflow, so this fallback is addition, not subtraction.

**Update semantics deliberately differ from `dividend_events`/
`earnings_events`.** A row here is a **plain upsert on `(ticker_id,
period_end_date)`** with no delete-before-insert step: quarterly figures can
legitimately restate after the fact (reclassifications,
discontinued-operations restatement, vendor data corrections), so
overwriting a stored row in place with newly fetched figures is the correct
and desired behavior, not a bug. Unlike `earnings_events`' speculative
future rows, a `period_end_date` can never be forward-looking (a snapshot
cannot exist before its period has ended and been reported), so there is
nothing to retire the way `upload_earnings_events` does.

Scope is the same owned + verified-Yahoo-mapping ticker set as the OHLCV and
earnings/dividends syncs (`get_market_targets`). Like earnings/dividends,
there is no incremental date window — yfinance's quarterly statement calls
return whatever trailing window is currently available (confirmed live:
roughly 5 quarters for the income statement, 6-7 for the balance
sheet/cash flow — not perfectly aligned across the three, so a period
present in only one or two statements still produces a row with the other
statement's named columns `NULL`), and every `financial-snapshots-sync` run
re-fetches and upserts that window. **This means the table only slowly
accumulates real year-over-year trend depth as repeated syncs run over
time** — a single sync never backfills years of history the way yfinance
itself never exposes it. ETF/fund tickers legitimately return empty
statements (confirmed live for `CDZ.TO`) and are not treated as failures.
This sync is not auto-triggered by the pipeline (same as
earnings/dividends); run it on demand.

A future rubric change (via the `author-decision-rubric` skill) may upgrade
`decision-rubric.yml`'s `derived:*` financial metrics to read trend-aware
figures from this table instead of a fresh per-run yfinance pull — out of
scope for the change that introduced this table.

Unlike the OHLCV sync, this is **not** part of the automatic post-email sync —
it runs on demand only via the `earnings-dividends-sync` CLI command (see
`docs/reference/cli.md`), before a knowledge-base pass or dashboard refresh.

## Implementation Metadata

Database foundation implementation model: 5.5 Medium.

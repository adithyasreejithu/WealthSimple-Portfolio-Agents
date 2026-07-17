# CLI Command Guide

`src/app.py` is the canonical entry point for user-facing commands. Run commands
from the repository root using `uv run`:

```powershell
uv run python src/app.py --help
```

**Note:** This project uses [`uv`](https://docs.astral.sh/uv/) for dependency and Python version management.
The first time you run a command, `uv` will sync dependencies. See "Setup" below.

Commands return exit code `0` on success and a nonzero code for invalid arguments
or operational failures. Use `uv run python src/app.py <command> --help` for the parser's
complete option details.

## Setup

If this is your first time running commands:

```powershell
uv sync
```

This installs all dependencies and locks them in `uv.lock`. On subsequent runs, `uv` reuses
the locked environment automatically when you run `uv run` commands.

## Pipeline

Run every source through the staged pipeline:

```powershell
uv run python src/app.py pipeline
uv run python src/app.py pipeline --source statements --data-folder Data --database Data/PRD_WealthSimple.duckdb
```

Options:

- `--source {all,export,statements,email}` selects a source; default: `all`.
- `--data-folder PATH` selects the input folder; default: repository `Data/`.
- `--database PATH` selects the DuckDB database; default: `DB_PATH` or the
  configured production database.

The legacy form `uv run python src/app.py --source all` remains supported.

A full (`--source all`, the default) run also classifies current holdings and
persists the result as its final step — the same work `portfolio-classify` +
`classification-sync` do (see "Portfolio Classification" below), run
automatically so a routine pipeline run always reflects current
classifications without a separate manual step. It prints as one more
`classification: succeeded/failed (N row(s))` line in the results. A
classification failure (e.g. a transient yfinance error) never rolls back
ingestion that already completed, matching how a market-data sync failure is
handled — it only affects the reported exit code, the same way a `partial`
email result already does. Partial-source runs (`--source
export/statements/email`) never trigger it.

## Analytics

```powershell
uv run python src/app.py analytics
uv run python src/app.py analytics --database Data/PRD_WealthSimple.duckdb --export
uv run python src/app.py analytics --date-from 2025-01-01 --date-to 2025-12-31 --dividend-source email
uv run python src/app.py analytics --benchmark VFV.TO
uv run python src/app.py analytics --no-benchmark --export --export-folder exports/analytics
```

Only current, positive-quantity holdings drive holdings, allocation, and
concentration figures; a ticker whose recorded sells exceed recorded buys is
excluded and instead surfaced in `data_quality`. See
[`docs/reference/analytics.md`](analytics.md) for the full calculation
reference, the exported JSON structure, and how unavailable metrics are
reported.

- `--database PATH` selects the database.
- `--date-from YYYY-MM-DD` and `--date-to YYYY-MM-DD` apply inclusive filters
  to cash-flow, income, fees, realized gains, and risk metrics.
- `--dividend-source {email,activities,statements}` defaults to `email`.
- `--cash-flow-source {activities,statements}` defaults to `activities`.
- `--fx-source {statements,exports,email}` defaults to `statements`; exports and
  email currently report unavailable because they lack complete FX inputs.
- `--benchmark SYMBOL` fetches that symbol's price history live from yfinance
  at report time (default `XEQT.TO`) to compute active return, tracking
  error, information ratio, beta, and alpha. Requires network access; any
  fetch failure degrades to an unavailable benchmark section rather than an
  error.
- `--no-benchmark` skips the benchmark fetch entirely (no network call).
- `--export` writes the full report as JSON to
  `exports/analytics/portfolio-analytics.json` (default) and prints the
  export path, instead of printing the formatted report. The file is
  overwritten on each run.
- `--export-folder PATH` selects the export destination folder; default:
  `exports/analytics`.

## Statement Extraction

```powershell
uv run python src/app.py statements --folder Data
uv run python src/app.py statements --include-glossary --export --export-folder exports
```

- `--folder PATH` selects the PDF folder; default: repository `Data/`.
- `--include-glossary` also extracts the statement-code glossary.
- `--export` writes this run's transactions to CSV.
- `--export-folder PATH` selects the CSV destination; default: `exports/`.

## Email Extraction

```powershell
uv run python src/app.py email
uv run python src/app.py email --date-from 2025-01-01 --export --export-folder exports
```

- `--date-from DATE` sets the earliest email date; default: configured `START_DATE`.
- `--export` writes this run's transactions to CSV.
- `--export-folder PATH` selects the CSV destination; default: `exports/`.

Email access still requires the credentials and mailbox configuration expected by
`src/email_extractor.py`.

`uv run python src/app.py email` is extractor-only. To persist new messages, update live
positions, reconcile statement trades, and refresh prices, run:

```powershell
uv run python src/app.py pipeline --source email
```

The database pipeline is incremental and deduplicates messages by message ID. Known
tickers publish immediately. Unknown tickers are retained as pending while unrelated
trades continue to publish; a pending run exits nonzero and prints the required action.

## YFinance

```powershell
uv run python src/app.py yfinance --tickers AAPL VFV.TO
uv run python src/app.py yfinance --tickers AAPL --include-history --start-date 2025-01-01
```

- `--tickers SYMBOL [SYMBOL ...]` is required.
- `--include-history` also fetches historical OHLCV data.
- `--start-date YYYY-MM-DD` is required with `--include-history`.
- `--end-date YYYY-MM-DD` defaults to today.
- `--skip-info` omits metadata and requires `--include-history`.
- `--cache-dir PATH` selects the yfinance cache.
- `--ignore-proxy` clears proxy environment variables for the run.

### Database synchronization

The default full pipeline automatically synchronizes owned ticker metadata and
history after all ingestion sources succeed. Retry or target that operation with:

```powershell
uv run python src/app.py yfinance-sync
uv run python src/app.py yfinance-sync --database Data/PRD_WealthSimple.duckdb --tickers AAPL VFV.TO
uv run python src/app.py yfinance-sync --full
```

- `--database PATH` selects the DuckDB database.
- `--tickers SYMBOL [SYMBOL ...]` optionally limits the run by canonical or
  Yahoo provider symbol.
- `--full` backfills again from each selected ticker's first portfolio activity.
- History starts at the earliest owned date on the first run and continues
  incrementally after the latest stored date on later runs.
- Synchronization refreshes stock/ETF detail records and historical prices only.
  It does not alter existing ticker identity fields, Yahoo mappings, or symbol history.
- Missing ticker identities must first be created through ingestion or the
  `ticker-map` workflow; unexpected metadata returned by Yahoo is skipped.
- The command exits nonzero when fetching or database publication fails.

## Ticker Mappings

```powershell
uv run python src/app.py ticker-map list
uv run python src/app.py ticker-map pending
uv run python src/app.py ticker-map resolve-pending
uv run python src/app.py ticker-map validate --source-symbol AAPL
uv run python src/app.py ticker-map import-csv mappings.csv
uv run python src/app.py ticker-map merge --old-symbol SPLG --new-symbol SPYM --currency USD --dry-run
```

Available actions are `add`, `update`, `list`, `pending`, `resolve-pending`,
`validate`, `retire`, `import-csv`, and `merge`. `pending` lists every source symbol still
blocking ingestion: symbols left `pending` on published email transactions, and
symbols that quarantined an activity export entirely (`staged_records.resolution_status
= 'unresolved'` on a `status = 'quarantined'` export file). Each row's `sources`
column shows whether it came from `email`, `export`, or both.
`resolve-pending` asks for the mapping interactively for every symbol `pending`
reports; scheduled pipelines never prompt. For each symbol it prompts for
currency, canonical symbol, and Yahoo (provider) symbol — the Yahoo symbol is
verified against Yahoo Finance before it can be saved: if it doesn't resolve to
a real, currency-matching equity or ETF, the prompt reprints with an error and
asks again, so a rejected suggestion (e.g. typing "no") can never be saved as a
literal provider symbol. On a successful match it prints `Matched: <company
name> (<symbol>, <exchange>)` before asking for confirmation. Type `SKIP` at
the Yahoo symbol prompt to abandon that symbol without saving a mapping.
Saving a mapping activates matching pending rows without refetching email, but
does not by itself re-publish a quarantined export — see `resolve-tickers` below
for the one-command version that also retries ingestion.
All actions accept `--database PATH` and `--output {text,json}`. With `--output
text` (the default), list-shaped results print as an aligned table and
single-record results print as `key : value` lines — not a raw Python dict dump.
Use action-level `--help` for mapping fields and effective-date options.

### Merging a renamed ticker

Use `merge` when a broker or data provider renames a symbol (e.g. `SPLG` became
`SPYM`) and the pipeline already created a **second, separate** `tickers` row
for the new symbol text, because ticker resolution is keyed purely on exact
symbol match and has no notion of renames. `merge` consolidates the two
identities into one:

```powershell
uv run python src/app.py ticker-map merge --old-symbol SPLG --new-symbol SPYM --currency USD --dry-run
uv run python src/app.py ticker-map merge --old-symbol SPLG --new-symbol SPYM --currency USD --yes
```

- The **older** (lower/first-created) `ticker_id` always survives, permanently —
  there is no heuristic or override flag. This keeps one stable anchor identity
  across any number of future renames of the same security, rather than the
  canonical id changing every time.
- The surviving ticker's `ticker_symbol` is always updated to `--new-symbol`,
  regardless of which side survived — so the visible symbol always ends up
  correct, only the internal id is chosen by age.
- Every referencing table (`transactions`, `email_transactions`, `activities`,
  `staged_records`, `historical_records`, `ticker_provider_mappings`,
  `stock_details`, `etf_details`, `portfolio_classifications`) is repointed onto
  the survivor in one transaction; a coincidental duplicate row in
  `transactions`/`email_transactions` aborts the whole merge rather than
  silently dropping data. Overlapping `historical_records` dates keep the
  survivor's own values.
- The losing ticker's row is **renamed and retained** (e.g. `SPYM_MERGED_26`),
  never deleted — `ticker_symbol_history` keeps its original rows pointing at
  it, so the merge is always backtrackable, plus a new open-ended history row
  records that the old symbol is now an alias of the survivor.
- Without `--yes`, a real run only previews (full per-table report) and exits
  nonzero; `--dry-run` does the same without requiring `--yes`. Nothing is
  written until you pass `--yes`.
- `--exchange` disambiguates if a symbol matches more than one exchange listing.
  `--effective-to` (default: today), `--reason`, and `--created-by` match
  `add`'s conventions.

## Resolve Pending Tickers

When a source is quarantined or published with pending tickers, the log names
the blocking symbol(s) and points here:

```powershell
uv run python src/app.py resolve-tickers
```

This is the one-shot recovery command: it lists every pending symbol (email and
export), prompts for each mapping interactively (same prompts as `ticker-map
resolve-pending`), and — if at least one mapping was saved — automatically
re-runs `uv run python src/app.py pipeline --source all` so any file quarantined only
because of that symbol is retried and published in the same run. Example
output:

```
MDA: mapped to MDA.TO (CAD) - 1 email row(s) updated
XNDU: mapped to XNDU.TO (CAD) - 1 email row(s) updated

Retrying ingestion for previously quarantined source(s)...
export [Data/activities-export-2026-07-07.csv]: succeeded (569 row(s))
```

If nothing is pending, it prints `No pending ticker mappings.` and exits `0`
without touching the pipeline. If every symbol is skipped interactively, it
prints the skip summary and exits `0` without retrying ingestion. It exits
nonzero if resolution or the retry fails.

- `--data-folder PATH` selects the input folder scanned during retry; default:
  repository `Data/`.
- `--database PATH` selects the DuckDB database.

## Portfolio Classification

```powershell
uv run python src/app.py portfolio-classify
uv run python src/app.py portfolio-classify --output exports/portfolio-classification/latest.json --pretty
uv run python src/app.py classification-sync
uv run python src/app.py classification-sync --input exports/portfolio-classification/latest.json
```

`portfolio-classify` runs the read-only, deterministic classification workflow
(read-only DuckDB connection, approved YAML rules, ephemeral allowlisted
yfinance enrichment) and writes the result as JSON.

- `--output PATH` selects the JSON destination inside
  `exports/portfolio-classification/`; a default path is used if omitted.
- `--pretty` indents and sorts the JSON output.

`classification-sync` is a separate, explicitly-invoked step that persists the
latest classification JSON into the `portfolio_classifications` table
(delete-then-reinsert, one transaction) — running `portfolio-classify` alone
never syncs to the database, keeping that workflow itself read-only. (A full
`pipeline` run does both automatically as its final step — see "Pipeline"
above — but the standalone `portfolio-classify`/`classification-sync`
commands remain independent for ad hoc use, e.g. reviewing the JSON before
deciding whether to persist it.)

- `--input PATH` selects the JSON file to sync; defaults to
  `exports/portfolio-classification/portfolio-classification.json`, the same
  path `portfolio-classify` writes to when `--output` is omitted.
- `--database PATH` selects the DuckDB database.

See `docs/agents/portfolio-classifier/architecture.md` for the full workflow
design and the `classify-portfolio` skill for the underlying scripts.

## Activity Import

Import the newest matching export from `Data/`:

```powershell
uv run python src/app.py import-activities
uv run python src/app.py import-activities --source-file Data/activities-export.csv --database Data/PRD_WealthSimple.duckdb
```

- `--source-file PATH` selects a CSV; otherwise the latest export is used.
- `--data-folder PATH` selects the search folder; default: repository `Data/`.
- `--database PATH` selects the database.
- `--processed-folder PATH` overrides the archive destination.
- `--enrich-tickers` enables provider metadata lookup and is the default.
- `--no-enrich-tickers` disables provider metadata lookup.

## Recompute Positions

```powershell
uv run python src/app.py recompute-positions
uv run python src/app.py recompute-positions --database Data/PRD_WealthSimple.duckdb
```

Rebuilds `position_ledger` and `position_snapshots` — the average-cost
holdings computed from the unified `v_trade_events` view (statements,
activities export, and email trades, deduplicated by reconciliation
precedence). Every holdings-reading command (`analytics`, `portfolio-classify`,
and a full `pipeline` run) already calls this automatically when the
underlying source tables have changed, so running it directly is normally
unnecessary. Use it after a manual database edit, a reconciliation-tolerance
change in `config.py`, or when investigating a holdings discrepancy.

- `--database PATH` selects the DuckDB database.

See `docs/architecture/ingestion_and_reconciliation.md` for how ownership is
reconstructed from the three source tables and how average cost is computed.

## Reconcile Holdings

```powershell
uv run python src/app.py reconcile-holdings --report ref/holdings-report-2026-07-07.csv
uv run python src/app.py reconcile-holdings --report holdings.csv --database Data/PRD_WealthSimple.duckdb
```

Compares computed holdings (`analytics.get_holdings`) against a Wealthsimple
holdings CSV export, treated as ground truth. Checks, per ticker: quantity
(exact to 1e-6), CAD book value (within `max($0.05, 0.1%)`), and
market-currency unrealized P/L (within `max($2, 1.5%)`, sized to absorb the
gap between the CSV's intraday price and the database's last stored close).
Also flags tickers present in only one side. Prints a per-ticker mismatch
report and exits 1 if anything mismatches, 0 otherwise — usable as a
regression gate after a database rebuild or a reconciliation-tolerance
change. This is the tool the holdings/P&L root-cause investigation
(`docs/holdings_reconciliation_2026-07-07.md`) was validated against.

- `--report PATH` selects the broker holdings CSV (required).
- `--database PATH` selects the DuckDB database.

## Compatibility Commands

Existing direct commands remain available for scripts and local workflows:

```powershell
uv run python src/statement_extractor.py --help
uv run python src/email_extractor.py --help
uv run python src/yfinance_extractor.py --help
uv run python src/data_sorter.py --help
uv run python src/ticker_mapping.py --help
```

New user documentation and automation should prefer `uv run python src/app.py ...`.

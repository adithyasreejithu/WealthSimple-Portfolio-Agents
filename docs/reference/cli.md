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
- `--full` backfills again from each selected ticker's history floor.
- Every ticker is backfilled to a **history floor** — the earlier of its first
  portfolio activity and `config.MINIMUM_PRICE_HISTORY_DAYS` (400 calendar days,
  ~275 trading bars) before today. Ownership alone is not deep enough: a name
  bought last month could otherwise never accumulate the history an SMA-200 or a
  365-day relative-strength window needs.
- A run therefore fetches up to two ranges per ticker: a head range when stored
  history starts later than the floor, plus the usual incremental tail after the
  latest stored date. The head range is self-limiting — once stored history
  reaches the floor, the floor keeps advancing daily while the stored minimum
  stays put, so it is fetched exactly once per ticker.
- A range covering only a weekend is never requested, since it cannot hold bars
  and yfinance reports an empty download as a misleading "possibly delisted"
  error. A ticker with no fetchable range is counted as skipped.
- A young security is backfilled only as far as the provider has data — its
  listing date, not the floor. That is a real gap for downstream calculations
  to report, not a sync failure.
- Each run also persists daily closes for the configured FX pair and the
  dashboard benchmark tickers (`config.BENCHMARK_TICKERS`, currently XEQT and
  VFV as the S&P 500 proxy), backfilled to the earliest transaction date so
  the dashboard's trend-chart overlays cover the full portfolio window. A
  benchmark fetch failure is isolated and never aborts the owned-ticker sync.
- Synchronization refreshes stock/ETF detail records and historical prices only.
  It does not alter existing ticker identity fields, Yahoo mappings, or symbol history.
- Missing ticker identities must first be created through ingestion or the
  `ticker-map` workflow; unexpected metadata returned by Yahoo is skipped.
- The command exits nonzero when fetching or database publication fails.
- **Owned tickers only, always.** `yfinance-sync` (like `earnings-dividends`
  and `financial-snapshots` below) never syncs a declared-wishlist research
  ticker — `market_data.get_market_targets`'s `include_research` widening is
  used only by the `investment-analyst-resources` skill's own on-demand
  refresh, never by this CLI command or the routine `pipeline` run. To pull
  and persist real DB data for a ticker you don't own, declare it first with
  `database status --ticker TICKER --set wishlist` (see below), then run
  `investment-analyst-resources` for it — not this command.

## Earnings & Dividends

Fetch company-declared earnings calendars and dividend schedules from yfinance.
This is reference market data (the company's declared events), distinct from
`cash_transactions`/`transactions`, which record the user's own received
dividend cash — the two are never joined or conflated.

```powershell
uv run python src/app.py earnings-dividends --tickers AAPL VFV.TO
uv run python src/app.py earnings-dividends --tickers CDZ.TO --skip-earnings
```

- `--tickers SYMBOL [SYMBOL ...]` is required; pass Yahoo provider symbols.
- `--skip-earnings` omits the earnings calendar fetch.
- `--skip-dividends` omits the dividend schedule fetch (skipping both is rejected).
- `--cache-dir PATH` selects the yfinance cache.
- `--ignore-proxy` clears proxy environment variables for the run.
- This is an ad-hoc fetch-and-print command; it does not write to the database.
- ETF/fund tickers legitimately return no earnings events (yfinance prints a
  misleading "No earnings dates found" message but raises no error); that is a
  normal outcome, not a failure.

### Database synchronization

```powershell
uv run python src/app.py earnings-dividends-sync
uv run python src/app.py earnings-dividends-sync --database Data/PRD_WealthSimple.duckdb --tickers AAPL VFV.TO
uv run python src/app.py earnings-dividends-sync --skip-dividends
```

- `--database PATH` selects the DuckDB database.
- `--tickers SYMBOL [SYMBOL ...]` optionally limits the run by canonical or
  Yahoo provider symbol. Scope is otherwise the same owned + verified-Yahoo-mapping
  ticker set as `yfinance-sync`.
- `--skip-earnings` / `--skip-dividends` limit the sync to one data type.
- Unlike OHLCV history, this is **not** date-windowed: every run re-fetches each
  ticker's full available earnings/dividend window and reconciles it against
  stored data. Dividends upsert on `(ticker_id, ex_dividend_date)`. Earnings
  upsert on `(ticker_id, report_date)`, and before each ticker's batch is
  inserted, still-unreported future rows (`report_date >= CURRENT_DATE AND
  eps_actual IS NULL`) are deleted so an abandoned/revised speculative date is
  retired; confirmed past rows are never deleted. `Surprise(%)` is stored as
  returned (already in percentage points).
- This sync is **not** auto-triggered by the pipeline (unlike `yfinance-sync`);
  run it on demand before a knowledge-base pass or dashboard refresh.
- The command exits nonzero when fetching or database publication fails.

## Financial Snapshots

Fetch per-quarter company financial statement data (revenue, net income,
EPS, margins, leverage, liquidity, free cash flow) from yfinance's quarterly
income statement, balance sheet, and cash flow. This is reference market
data, distinct from the pipeline's own portfolio holdings math.

```powershell
uv run python src/app.py financial-snapshots --tickers AAPL VFV.TO
uv run python src/app.py financial-snapshots --tickers NVDA MCD T
```

- `--tickers SYMBOL [SYMBOL ...]` is required; pass Yahoo provider symbols.
- `--cache-dir PATH` selects the yfinance cache.
- `--ignore-proxy` clears proxy environment variables for the run.
- This is an ad-hoc fetch-and-print command; it does not write to the database.
- ETF/fund tickers legitimately return no quarterly financial statements
  (confirmed live for `CDZ.TO`); that is a normal outcome, not a failure.
- yfinance's quarterly statement calls only ever return a shallow trailing
  window (roughly 5 quarters for the income statement, 6-7 for the balance
  sheet/cash flow, confirmed live) — this command shows only what yfinance
  currently exposes, not deep multi-year history.

### Database synchronization

```powershell
uv run python src/app.py financial-snapshots-sync
uv run python src/app.py financial-snapshots-sync --database Data/PRD_WealthSimple.duckdb --tickers AAPL VFV.TO
```

- `--database PATH` selects the DuckDB database.
- `--tickers SYMBOL [SYMBOL ...]` optionally limits the run by canonical or
  Yahoo provider symbol. Scope is otherwise the same owned + verified-Yahoo-mapping
  ticker set as `yfinance-sync`/`earnings-dividends-sync`.
- Unlike OHLCV history, this is **not** date-windowed: every run re-fetches
  each ticker's currently-available quarterly window and upserts it against
  stored data. Unlike `earnings-dividends-sync`, there is **no**
  delete-before-insert step — quarterly figures can legitimately restate
  after the fact (reclassifications, vendor data corrections), so a plain
  `INSERT ... ON CONFLICT (ticker_id, period_end_date) DO UPDATE` overwrites
  a stored row in place with the latest-known figures. This is intentional:
  a stored snapshot should reflect the best-known figures, not the
  first-ever-seen ones.
- **This table only accumulates real year-over-year trend depth over
  repeated syncs run over time** — yfinance's own quarterly endpoints expose
  a shallow rolling window, so a single sync never backfills years of
  history.
- Named columns are nullable by design for industry variance — e.g. a
  bank's balance sheet has no conventional current-ratio split, so
  `current_ratio` is legitimately `NULL` for those tickers, not a fetch
  failure. Everything yfinance returns that isn't one of the named columns
  is preserved in the row's `extra` JSON field.
- This sync is **not** auto-triggered by the pipeline (same as
  `earnings-dividends-sync`); run it on demand.
- The command exits nonzero when fetching or database publication fails.

### Annual Financial Context

```powershell
uv run python src/app.py annual-financial-context --ticker AAPL
uv run python src/app.py annual-financial-context --ticker SHOP.TO --ignore-proxy
```

Fetch **one** ticker's **annual** financial statements (`Ticker.financials`,
`Ticker.balance_sheet`, `Ticker.cashflow`) as ephemeral, non-persisted context
for the knowledge-base prep agent on a ticker's first-ever analysis run. Unlike
`financial-snapshots`/`financial-snapshots-sync`, this is a **KB-agent tool**,
not a pipeline or dashboard data source: it writes nothing to DuckDB, has no
`-sync` counterpart, and does not feed the decision rubric's scored dimensions —
it exists only to give the analyst a couple of years of annual context for the
initial Company Overview / Original Thesis narrative.

- `--ticker SYMBOL` is **required and singular** — this is the one command in
  the CLI that only ever operates on exactly one ticker (the singular flag
  makes that constraint visible at the call site).
- `--cache-dir PATH` selects the yfinance cache.
- `--ignore-proxy` clears proxy environment variables for the run.
- Output is a single `annual-financial-context.v1` JSON document printed to
  stdout (the calling agent captures it directly — no intermediate file). Its
  `periods` array is sorted ascending by `period_end_date` (oldest first) and
  uses the same named fields, units, and computation rules (gross/operating
  margin, debt-to-equity, current ratio, capex-sign-aware FCF fallback,
  `extra` blob for unmapped line items) as `financial-snapshots`.
- Never raises: an ETF/fund — or a fetch failure — yields an envelope with
  `"periods": []`, `"period_count": 0`, and a human-readable `note`, so the
  caller can always parse the result.

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
uv run python src/app.py classify
uv run python src/app.py classify --database Data/PRD_WealthSimple.duckdb
```

`classify` is the standalone command that classifies current holdings **and**
every ticker declared `wishlist` (`database status --set wishlist`) and
persists the result into the `portfolio_classifications` table in one step —
the classification workflow's read-only pass (approved YAML rules, ephemeral
allowlisted yfinance enrichment) followed by the sync. Each row's
`fields.ownership_status` is `"owned"` or `"wishlist"`. Run it explicitly
whenever holdings change outside a full pipeline run, e.g. after resolving a
new ticker or after declaring/clearing a wishlist ticker.

- `--database PATH` selects the DuckDB database.

The two lower-level steps `classify` wraps remain available independently for
ad hoc use, e.g. reviewing the JSON before deciding whether to persist it:

```powershell
uv run python src/app.py portfolio-classify
uv run python src/app.py portfolio-classify --output exports/portfolio-classification/latest.json --pretty
uv run python src/app.py classification-sync
uv run python src/app.py classification-sync --input exports/portfolio-classification/latest.json
```

`portfolio-classify` runs the read-only workflow alone and writes the result
as JSON (`--output PATH` selects the destination inside
`exports/portfolio-classification/`, `--pretty` indents and sorts it).
`classification-sync` persists an already-written classification JSON into
the database (delete-then-reinsert, one transaction; matches each holding by
`ticker_symbol` alone and raises — rolling back the whole sync — if a ticker
resolves to zero or more than one `tickers` row, rather than silently
dropping that holding's classification). It also refuses to sync a **stale**
export: one covering a strict subset of the tickers already classified in the
database *and* generated before the most recent wishlist declaration — the
signature of syncing an export from before a wishlist ticker was declared (or
before this widening existed at all), which would otherwise silently erase
that ticker's classification. Run `classify` (or `portfolio-classify`) again
to regenerate a current export. `--input PATH` selects the JSON file to sync,
defaulting to the same path `portfolio-classify` writes to when `--output` is
omitted; `--database PATH` selects the DuckDB database. (A full `pipeline`
run also classifies current holdings automatically as its final step — see
"Pipeline" above — but these standalone commands remain independent for ad
hoc use.)

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

## Run Workspace

```powershell
uv run python src/app.py run create --request tests/fixtures/synthetic_request.yaml
uv run python src/app.py run list
uv run python src/app.py run show --run-id <run-id>
uv run python src/app.py run register-evidence --run-id <run-id> --file workspace/runs/<run-id>/evidence/bundle.json --type market_data_bundle --source yfinance
uv run python src/app.py run register-evidence --run-id <run-id> --missing --type price_quote --source yfinance --status missing
uv run python src/app.py run build-manifest --run-id <run-id> --target-stage investment_analyst
uv run python src/app.py run build-worksheet --run-id <run-id> --ticker PLTR --mode initial_research --horizon Long-term
uv run python src/app.py run check-thesis --run-id <run-id> --path tmp/PLTR-thesis-draft.json
uv run python src/app.py run save-thesis --run-id <run-id> --path tmp/PLTR-thesis-draft.json --ticker PLTR
uv run python src/app.py run build-policy-context --run-id <run-id> --ticker PLTR
uv run python src/app.py run check-decision --run-id <run-id> --path tmp/PLTR-decision-draft.json
uv run python src/app.py run save-decision --run-id <run-id> --path tmp/PLTR-decision-draft.json --ticker PLTR
uv run python src/app.py run validate --run-id <run-id>
uv run python src/app.py run set-status --run-id <run-id> --status in_progress
uv run python src/app.py run archive --run-id <run-id>
uv run python src/app.py run gc --older-than-days 7 --dry-run
```

Creates and manages **run workspaces** — one directory per investment-research
request, holding that request's inputs, evidence, calculations, agent outputs,
and an append-only audit log, so an answer is reproducible and auditable as a
unit. Full design in `docs/architecture/run_workspace.md`.

Every subcommand prints JSON to stdout. This command group only creates,
reads, and validates files: it never invokes an agent, and it can never place
a trade.

### `run create`

Creates one run atomically under `workspace/runs/<run-id>/`. Assembles the
directory under a hidden temporary name and moves it into place only once
every required file exists, so a failure leaves no partial run. An existing
run directory is **never** overwritten.

This is the explicit path, for a run that answers a written question. A
data-collection skill (`investment-analyst-resources`,
`market-analyst-resources`) opens a run implicitly and by default, with no
flag required — see "Two ways a run begins" in
`docs/architecture/run_workspace.md`.

- `--request PATH` — request YAML or JSON (required). Validated against the
  request schema; a request that tries to enable `execute_trades` or disable
  `human_review_required` is rejected.
- `--trigger TEXT` — what initiated this run (default `cli`).

### `run list` / `run show`

`list` summarizes every active run (ID, mode, status, creation time, evidence
count), newest first. `show --run-id <id>` prints that run's full metadata and
evidence registry.

### `run register-evidence`

Appends one row to the run's `evidence/sources.jsonl`. Supply **either**
`--file` (an artifact already inside the run directory) **or** `--missing`.

Registering a gap is a first-class operation: a downstream stage must be able
to tell "we looked and it wasn't there" from "nobody looked." Never invent a
value for missing evidence.

- `--run-id ID` (required), `--type TEXT` (required), `--source TEXT` (required)
- `--file PATH` — artifact inside the run; its sha256 is computed and stored
- `--missing` — register an explicit gap instead of a file
- `--status {pending,available,partial,missing,stale,invalid}` (default `available`)
- `--url`, `--method`, `--note` (repeatable)

### `run build-manifest`

Regenerates `context_manifest.yaml` from what the run currently holds — the
contract naming what a receiving stage may use and what is absent. Always
regenerated, never hand-edited.

- `--target-stage TEXT` — which stage will consume it.
- `--prior-thesis PATH` — run-relative path to a prior thesis, if any.

### `run build-worksheet`

Builds this run's `investment-worksheet.v1` JSON and compact
`analyst-context.md` from an already-registered `investment-analyst-resources`
bundle (and, if present, a `security-technicals` artifact) — see
`docs/architecture/investment_thesis_schema.md` and
`docs/architecture/analysis_scope_schema.md`. Deterministic; no network or
database access; safe to re-run. Prints the worksheet/context paths, the
worksheet's sha256 hash, and `evidence_health` (including `blocking` — a
caller must check this before drafting a thesis; see the
`investment-analyst` agent's halt-on-blocking rule).

- `--run-id ID` (required), `--ticker TICKER` (required)
- `--mode {initial_research,scheduled_review,earnings_update,material_event,price_move_review,thesis_monitor,portfolio_decision}` (required)
- `--horizon {Short-term,Medium-term,Long-term}` (required)
- `--asset-track {equity,etf}` (default `equity`)
- `--trigger-type {schedule,earnings,material_event,price_move,user_request,monitor_trigger}` (default `user_request`)
- `--trigger-detail TEXT`, `--trigger-occurred-at TEXT`

### `run check-thesis`

Dry-run validates a draft `investment-thesis.v1` file against schema, forbidden
fields, evidence citations, the `worksheet_ref` hash, section/scope coverage,
and the TRACE-driven confidence cap — **writes nothing**. Meant to be called
repeatedly while iterating on a draft. Prints `{ok, status, errors, warnings}`
and exits 1 when not ok.

- `--run-id ID` (required)
- `--path PATH` (required) — run-relative path to the draft JSON, e.g.
  `tmp/PLTR-thesis-draft.json`.

### `run save-thesis`

Re-validates the draft independently (never trusts a prior `check-thesis`
call saw the same bytes). Only if valid or valid-with-warnings: overwrites
`policy_version`, `artifact_id`, and `validation{}` authoritatively, writes it
to `agent_outputs/<ticker>-<stamp>-thesis.json`, registers it as
`investment_thesis` evidence, and appends `analyst_drafted` + `validated`
audit events. On failure, nothing is written and only a `validated` (failure)
event is recorded. Prints the same shape as `check-thesis` plus
`artifact_path`/`evidence_id`/`artifact_id` on success; exits 1 on failure.

- `--run-id ID` (required), `--path PATH` (required), `--ticker TICKER` (required)

### `run build-policy-context`

Builds this run's `portfolio-policy-worksheet.v1` JSON for one ticker — the
Portfolio Manager's deterministic "Python calculates" layer, mirroring
`build-worksheet`'s role for the Investment Analyst. Reads the live pipeline
database fresh (portfolio state is not run-scoped) and evaluates the two
checks `Knowledge-Base/ref/policy_v1_1.yaml` actually defines: the
single-name cap (`constraints.single_name_max_percent`, with the same
Core-classified-broad-market-ETF exemption `analytics.portfolio_report`'s
concentration check applies) and the security's classifier group's
allocation-target band (`allocation_targets.<group>.max_percent`). Sector,
look-through-sector, and currency exposure
(`get_sector_allocation`/`get_look_through_sector_exposure`/`get_currency_exposure`)
are included as informational `portfolio_context` only — `policy_v1_1.yaml`
defines no cap for either, so none is invented. Prints the worksheet path and
sha256 hash, `policy_checks` (`{name, result: pass|fail|unavailable, detail}`),
`current_weight_pct`, and `subject` (resolved ticker_id/group/held status).

- `--run-id ID` (required), `--ticker TICKER` (required)
- `--db-path PATH` — override the configured pipeline database (test/debug only)

### `run check-decision`

Dry-run validates a draft `DecisionProposal` file against schema (the locked
`Buy|Hold|Trim|Sell|Add` action enum — `Watchlist`/`Avoid` are rejected, that
vocabulary belongs to the not-held research path), the `thesis_ref` and
`policy_worksheet_ref` hashes, and the policy-consistency gate: if the cited
policy worksheet recorded any `fail` check for this security, `proposed_action`
must be `Hold`/`Trim`/`Sell` — `Buy`/`Add` against a failing check is a
validation error, not left to judgment. **Writes nothing**. Prints
`{ok, status, errors, warnings}` and exits 1 when not ok.

- `--run-id ID` (required)
- `--path PATH` (required) — run-relative path to the draft JSON, e.g.
  `tmp/PLTR-decision-draft.json`.

### `run save-decision`

Re-validates the draft independently. Only if valid or valid-with-warnings:
overwrites `policy_version` authoritatively, writes it to
`final/<ticker>-<stamp>-decision.json` (a proposal, never an order —
`trade_executed`/`human_approval_required` are pinned by the schema), registers
it as `decision_proposal` evidence, and appends `pm_drafted` + `validated`
audit events. On failure, nothing is written and only a `validated` (failure)
event is recorded. Prints the same shape as `check-decision` plus
`artifact_path`/`evidence_id`/`proposal_id` on success; exits 1 on failure.

- `--run-id ID` (required), `--path PATH` (required), `--ticker TICKER` (required)

### `run validate`

Checks files, schemas, and cross-references: evidence citations resolve to
registered IDs, referenced paths stay inside the run, artifact hashes still
match, no trade-execution fields appear anywhere, neither append-only log has
malformed lines, any `investment-thesis.v1` artifact under `agent_outputs/`
independently re-passes `thesis_validation.validate_thesis` (schema, citations,
`worksheet_ref` hash, section/scope, confidence cap), and any `DecisionProposal`
under `final/` independently re-passes `decision_validation.validate_decision`
(schema, `thesis_ref`/`policy_worksheet_ref` hashes, the policy-consistency
gate). Prints `{ok, errors, warnings, counts}` and **exits 1** when not ok, so
it works as a gate.

### `run set-status`

Moves a run through `created → in_progress → awaiting_input |
awaiting_human_review → completed | failed | insufficient_evidence →
archived`. `insufficient_evidence` is the outcome of a `build-worksheet` call
whose `evidence_health.blocking` came back `true` (a `stop`-policy run with a
required evidence domain that failed TRACE) — distinct from `failed` (a bug),
it means the pipeline worked correctly and found a real gap a human must fill
before the run can continue. Transitions outside this model are refused with
an error naming the legal alternatives.

- `--status STATUS` (required), `--note TEXT` — recorded as a warning, or as
  an error when failing the run or marking it `insufficient_evidence`.

Entering `completed`, `failed`, or `insufficient_evidence` also purges the
run's `cache/` directory (bulk payloads a data-pull skill fetched for this
run's lifetime — see `docs/architecture/run_workspace.md`), recording each
deleted file's provenance in `cache/cache_manifest.json`.
`awaiting_human_review` is deliberately **not** covered: the investment
analyst's cache must survive for the portfolio-manager stage that follows in
the same run. A run shared by a ticker fan-out has one status for the whole
run — do not set a terminal status until every ticker has finished, or the
first to finish purges cache the others still need.

### `run archive`

Moves the run into `workspace/archive/<YYYY-MM>/<run-id>/`, structure intact.
**Runs are never deleted**; only `tmp/` and `cache/` contents are discarded
(the latter into a retained manifest, same as `set-status`), and an occupied
archive slot is refused. Validates first by default.

- `--no-validate` — archive without validating, for retaining an abandoned or
  failed run.

### `run gc`

Sweeps every run under `workspace/runs/` and purges `cache/` for any run
whose metadata is older than `--older-than-days` and still has un-purged
payloads — reclaiming a run that crashed before ever reaching a terminal
`set-status` call (the only other place a purge happens). Idempotent: a run
already purged, or one whose skills never wrote to `cache/`, is skipped from
the report entirely.

- `--older-than-days INT` (default `7`)
- `--dry-run` — report what would be purged without deleting anything; safe
  to run against the real `workspace/runs/` first.

## Database

```powershell
uv run python src/app.py database status --ticker OUST --set wishlist --rationale "LIDAR play, watching for entry"
uv run python src/app.py database status --ticker OUST --set clear
```

Manages database-level declarations that fall outside pipeline ingestion.
Currently one subcommand, `status`, which declares or clears a ticker's
non-ownership status in the `security_status` table (schema v15) — a
wishlist candidate, a name to avoid, or a retired idea. **CLI/human-only by
design:** no agent has write access to this table (see the read-only
`.claude/skills/security-status/` skill, shared by the `investment-analyst`
and `investment-portfolio-manager` agents).

Ownership itself is never declared here — it stays derived from
`position_snapshots.quantity > 0` and always overrides any declaration, so a
wishlist ticker that gets bought becomes `owned` automatically with no write
to this table. See `src/database.py`'s `_create_security_status_table`
docstring and `src/analytics.py`'s `resolve_security_status`.

### `database status`

- `--ticker SYMBOL` (required)
- `--set {wishlist,avoid,retired,clear}` (required) — `clear` removes any
  existing declaration.
- `--rationale TEXT` — optional free-text reason, stored alongside the
  declaration.
- `--database PATH` — override the configured pipeline database.

A symbol with no existing `tickers` row is resolved and created the same way
every other ingestion path does (via `ensure_tickers`, yfinance-enriched),
so a never-traded wishlist name still gets a `ticker_id` and a clean foreign
key like every other row in the schema. A symbol matching more than one
exchange must already be uniquely identified elsewhere in the database —
this command does not disambiguate by exchange.

**This is the registration door for a research ticker.** A `wishlist`
declaration is the prerequisite for the `investment-analyst`/
`investment-portfolio-manager` track on a name you do not own: once
declared, `market_data.get_market_targets`'s `include_research` widening
admits it, and `investment-analyst-resources` (see
`.claude/skills/investment-analyst-resources/`) persists real prices,
earnings, dividends, financials, and stock/ETF details for it — the same
DB-backed depth an owned ticker gets, on demand, without touching the
routine `pipeline` run. That skill's `--register-wishlist` flag runs this
exact declaration for you, agent-side, when the caller opts in.

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

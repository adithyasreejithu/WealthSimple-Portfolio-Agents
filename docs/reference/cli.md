# CLI Command Guide

`src/app.py` is the canonical entry point for user-facing commands. Run commands
from the repository root with the active virtual environment.

```powershell
python src/app.py --help
```

Commands return exit code `0` on success and a nonzero code for invalid arguments
or operational failures. Use `python src/app.py <command> --help` for the parser's
complete option details.

## Pipeline

Run every source through the staged pipeline:

```powershell
python src/app.py pipeline
python src/app.py pipeline --source statements --data-folder Data --database Data/PRD_WealthSimple.duckdb
```

Options:

- `--source {all,export,statements,email}` selects a source; default: `all`.
- `--data-folder PATH` selects the input folder; default: repository `Data/`.
- `--database PATH` selects the DuckDB database; default: `DB_PATH` or the
  configured production database.

The legacy form `python src/app.py --source all` remains supported.

## Analytics

```powershell
python src/app.py analytics
python src/app.py analytics --database Data/PRD_WealthSimple.duckdb --export
```

- `--database PATH` selects the database.
- `--export` prints JSON instead of the formatted report.

## Statement Extraction

```powershell
python src/app.py statements --folder Data
python src/app.py statements --include-glossary --export --export-folder exports
```

- `--folder PATH` selects the PDF folder; default: repository `Data/`.
- `--include-glossary` also extracts the statement-code glossary.
- `--export` writes this run's transactions to CSV.
- `--export-folder PATH` selects the CSV destination; default: `exports/`.

## Email Extraction

```powershell
python src/app.py email
python src/app.py email --date-from 2025-01-01 --export --export-folder exports
```

- `--date-from DATE` sets the earliest email date; default: configured `START_DATE`.
- `--export` writes this run's transactions to CSV.
- `--export-folder PATH` selects the CSV destination; default: `exports/`.

Email access still requires the credentials and mailbox configuration expected by
`src/email_extractor.py`.

## YFinance

```powershell
python src/app.py yfinance --tickers AAPL VFV.TO
python src/app.py yfinance --tickers AAPL --include-history --start-date 2025-01-01
```

- `--tickers SYMBOL [SYMBOL ...]` is required.
- `--include-history` also fetches historical OHLCV data.
- `--start-date YYYY-MM-DD` is required with `--include-history`.
- `--end-date YYYY-MM-DD` defaults to today.
- `--skip-info` omits metadata and requires `--include-history`.
- `--cache-dir PATH` selects the yfinance cache.
- `--ignore-proxy` clears proxy environment variables for the run.

## Ticker Mappings

```powershell
python src/app.py ticker-map list
python src/app.py ticker-map validate --source-symbol AAPL
python src/app.py ticker-map import-csv mappings.csv
```

Available actions are `add`, `update`, `list`, `validate`, `retire`, and
`import-csv`. All actions accept `--database PATH` and `--output {text,json}`.
Use action-level `--help` for mapping fields and effective-date options.

## Activity Import

Import the newest matching export from `Data/`:

```powershell
python src/app.py import-activities
python src/app.py import-activities --source-file Data/activities-export.csv --database Data/PRD_WealthSimple.duckdb
```

- `--source-file PATH` selects a CSV; otherwise the latest export is used.
- `--data-folder PATH` selects the search folder; default: repository `Data/`.
- `--database PATH` selects the database.
- `--processed-folder PATH` overrides the archive destination.
- `--enrich-tickers` enables provider metadata lookup and is the default.
- `--no-enrich-tickers` disables provider metadata lookup.

## Compatibility Commands

Existing direct commands remain available for scripts and local workflows:

```powershell
python src/statement_extractor.py --help
python src/email_extractor.py --help
python src/yfinance_extractor.py --help
python src/data_sorter.py --help
python src/ticker_mapping.py --help
```

New user documentation and automation should prefer `python src/app.py ...`.

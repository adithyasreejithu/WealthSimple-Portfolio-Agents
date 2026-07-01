# Handover

Use this document when signing off, clearing context, or resuming work after a break.

## Current Focus

- WealthSimple portfolio tooling.
- Main active modules: `src/data_sorter.py`, `src/statement_extractor.py`, `src/email_extractor.py`, and `src/yfinance_extractor.py`.
- Supporting runtime helper: `src/system_logger.py`.
- Repo-wide working rules: `instructions.md`.

## What We Were Doing

- Consolidated the Wealthsimple PDF statement extraction refactor into one flat runtime file.
- Removed the Camelot package folder and separate run script from `src/`.
- Removed the old extracted Camelot reference folder.
- Kept glossary extraction available, but made it opt-in because it is expected to be run once later for database setup.
- Added a flat email extraction runtime under `src/` for Wealthsimple and Interac emails.
- Added opt-in CSV export for both statement extraction and email extraction.
- Tightened email date handling so parsed email dates take priority and IMAP received dates are the fallback.
- Expanded stage-level usage and error logging across the runtime files in `src/`.
- Kept logging centralized through `src/system_logger.py`.
- Migrated the old yfinance bundle into a flat runtime module under `src/`.
- Removed the old `extracted_yfinance_method/` folder after preserving its stock, ETF, and historical-data behavior.

## Current State

- The sorter reads `Data/activities-export-*.csv`.
- Trailing footer rows are trimmed before processing.
- Every source field is stored in `raw_activity_exports`.
- Typed analytics rows are published to `activities` after symbols resolve to `ticker_id`.
- Ambiguous or missing ticker mappings retain raw rows and the source CSV but reject analytics publication.
- Successful imports deduplicate overlapping export history and move the source file into `Data/processed_data/` with the same filename.
- Unknown activity rows are retained with the normalized code `UNKNOWN`.
- The statement extractor reads Wealthsimple statement PDFs from `Data/*.pdf`.
- Transaction extraction runs by default with `python src/statement_extractor.py`.
- Glossary extraction runs only with `python src/statement_extractor.py --include-glossary`.
- Statement export runs with `python src/statement_extractor.py --export`.
- `src/statement_extractor.py` exposes `extract_statement_pdf`, `extract_statement_glossary_pdf`, and `extract_folder`.
- Transaction output keeps `date`, `transaction`, `ticker_id`, `quantity`, `execDate`, `fx_rate`, `debit`, `credit`, `balance`, `statement_code`, and `description`.
- Glossary output keeps `code` and `description`.
- The email extractor reads matching Wealthsimple and Interac emails from Gmail.
- Email extraction runs with `python src/email_extractor.py`.
- Email export runs with `python src/email_extractor.py --export`.
- Email output keeps `account`, `transaction`, `ticker_id`, `ticker`, `quantity`, `avg_price`, `total_cost`, `debit`, and `date`.
- Email `date` is resolved from the email body first and falls back to the IMAP received date when needed.
- `START_DATE` is a temporary environment-based start-date fallback until a database checkpoint exists.
- The yfinance extractor fetches stock metadata, ETF metadata, and historical OHLCV data for caller-supplied tickers.
- Yfinance metadata output is returned as stock and ETF dataframes with stable columns.
- Yfinance historical output is returned as a dataframe with `Date`, `Ticker`, `Open`, `High`, `Low`, `Close`, `Adj Close`, and `Volume`.
- The future pipeline trigger is not implemented yet; it should pass required tickers and date ranges into `src/yfinance_extractor.py`.
- All active runtime files under `src/` now use the shared logger and include stage-level usage/error logging.
- `src/` is intended to stay flat; there should be no Camelot subfolder or separate extractor runner file.
- `instructions.md` is the repo-wide implementation guide.

## Next Session: Activity Export Pipeline

- `activities` is an analytics-ready table inside the main DuckDB database, not a separate database.
- Running `src/data_sorter.py` currently writes original CSV rows to `raw_activity_exports`, resolves source symbols against `tickers`, and writes normalized rows containing `ticker_id` to `activities`.
- `src/data_sorter.py` does not populate `tickers`. The required ticker records must currently exist before an activity export can be normalized.
- A ticker-bearing import is rejected when its symbol cannot resolve unambiguously, so raw rows remain stored but no normalized `activities` rows are published.
- This database orchestration is currently embedded in `src/data_sorter.py`. The next implementation should create a dedicated activity-export pipeline and return `data_sorter.py` to extraction and normalization responsibilities.
- The future pipeline must define how `tickers` is populated before activity resolution, then own ticker resolution, raw and normalized database writes, deduplication, import status, and successful source-file movement.

## Important Files

- `src/data_sorter.py`
- `src/statement_extractor.py`
- `src/email_extractor.py`
- `src/yfinance_extractor.py`
- `src/system_logger.py`
- `tests/test_statement_extractor.py`
- `tests/test_email_extractor.py`
- `tests/test_yfinance_extractor.py`
- `docs/SuccessCritera.md`
- `docs/camelot_extraction_refactor_plan.md`
- `docs/email_extraction_success_criteria.md`
- `docs/email_extraction_plan.md`
- `docs/yfinance_success_criteria.md`
- `docs/TODO.md`
- `instructions.md`

## Known Constraints

- Do not create extra tracked files unless the task explicitly requires them.
- Do not rename the source file during the processed-file move.
- Keep future changes aligned with `instructions.md`.
- Keep logging behavior predictable and repository-relative.
- New runtime code added under `src/` should include logger setup by default unless a task explicitly says otherwise.
- Keep statement extraction consolidated in `src/statement_extractor.py` until a future `src/main.py` is introduced.
- Keep email extraction consolidated in `src/email_extractor.py` until a future shared entrypoint exists.
- Do not recreate `src/wealthsimple_camelot/`, `src/run_camelot_extract.py`, or `extracted_camelot_method/`.
- Do not recreate `email_export/` after the migration is complete.
- Do not recreate `extracted_yfinance_method/` after the yfinance migration is complete.
- Keep yfinance runtime code independent from database helper functions until the pipeline/database layer is introduced.
- Do not run glossary extraction by default; use the `--include-glossary` flag only when explicitly needed.

## Last Completed Work

- Added `src/email_extractor.py` as the flat runtime for combined Wealthsimple and Interac email extraction.
- Added opt-in CSV export to the email extractor and kept statement export in `src/statement_extractor.py`.
- Tightened email date parsing for non-dividend order emails and kept received-date fallback when no in-email date exists.
- Added focused email tests in `tests/test_email_extractor.py`.
- Added email docs in `docs/email_extraction_plan.md`, `docs/email_extraction_success_criteria.md`, and `docs/TODO.md`.
- Removed the legacy `email_export/` folder.
- Expanded balanced stage-level logging across `src/data_sorter.py`, `src/statement_extractor.py`, and `src/email_extractor.py`.
- Updated `instructions.md` so new runtime code under `src/` should include logger setup by default.
- Added `src/yfinance_extractor.py` for dataframe-returning stock metadata, ETF metadata, and historical OHLCV fetches.
- Added mocked yfinance tests in `tests/test_yfinance_extractor.py`.
- Added `docs/yfinance_success_criteria.md`.
- Removed the legacy `extracted_yfinance_method/` folder.

## Open Checks Before Resuming

- Confirm `src/__pycache__/` is not left behind.
- Confirm `tests/__pycache__/` is not left behind.
- Confirm `src/data_sorter.py` still preserves raw imports and only publishes fully resolved analytics rows.
- Confirm `src/statement_extractor.py` remains the only statement extraction runtime file.
- Confirm `src/email_extractor.py` still reflects the latest email filter, export, and date-fallback behavior.
- Confirm no Camelot-named folders or `run_camelot_extract.py` were recreated.
- Confirm `email_export/` was removed after the email extractor migration.
- Confirm `extracted_yfinance_method/` was removed after the yfinance migration.
- Confirm `src/yfinance_extractor.py` still returns stable dataframe schemas and does not write `yFinance_Data.csv`.
- Confirm no new generated files were introduced accidentally.
- Confirm the repo is in the expected git state before continuing.
- Current expected modified/untracked work includes `instructions.md`, `requirements.txt`, the runtime files under `src/`, `tests/`, and the docs added or updated during this session.

## Last Verification Commands

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_email_extractor.py'
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py --help
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py --include-glossary
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\email_extractor.py --help
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\email_extractor.py
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\email_extractor.py --export
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_yfinance_extractor.py'
```

## Sign-Off Checklist

- Write down the exact file or behavior being worked on.
- Note any user constraints that changed the direction of the task.
- Record the last verification command that passed.
- Record any blockers or environment issues.
- Record the next concrete step, not just the broad goal.

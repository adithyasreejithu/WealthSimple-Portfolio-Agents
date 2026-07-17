# Handover

Use this document when signing off, clearing context, or resuming work after a break.

## Current Focus

- WealthSimple portfolio tooling.
- Planning a constrained portfolio-classification agent with one supported prompt:
  `Classify my portfolio`.
- Main active modules: `src/data_sorter.py`, `src/statement_extractor.py`, `src/email_extractor.py`, and `src/yfinance_extractor.py`.
- Supporting runtime helper: `src/system_logger.py`.
- Repo-wide working rules: `instructions.md`.

## What We Were Doing

- Added the decision-complete design in
  `docs/plans/constrained_portfolio_classification_agent.md` for a script-driven,
  read-only classification agent and reusable skills.
- Implemented the `portfolio-classifier` agent as a Claude Code subagent at
  `.claude/agents/portfolio-classifier.md` (model `haiku`, `tools: ["Bash"]`), and
  made its skill dependencies explicit in the system prompt.
- Added a human-readable companion doc at
  `docs/agents/portfolio-classifier/architecture.md` that explains how the
  classifier agent relates to its supporting skills, alongside the design
  rationale in `docs/agents/portfolio-classifier/plan.md`.
- Defined a database-first enrichment workflow: retain populated database values,
  use yfinance only for missing allowlisted classification fields, keep enrichment
  ephemeral, apply the approved YAML rules, and emit JSON.
- Limited the initial agent surface to one prebuilt workflow. The agent must not
  receive arbitrary SQL, database paths, ticker lists, Python execution, yfinance
  fields, or yfinance modes.
- Added schema v8 live email ingestion with message-level idempotency, pending ticker
  retention, provisional holdings, deterministic statement reconciliation, and
  consolidated market synchronization through `src/market_data.py`.

- Consolidated every user-facing command under `src/app.py` while preserving the
  standalone module entry points.
- Added a CLI reference and durable documentation/comment-update rules.
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

- The constrained classification-agent design is implemented as a Claude Code
  subagent (`.claude/agents/portfolio-classifier.md`) plus three skills under
  `.claude/skills/`.
- Access restrictions are enforced in the skill scripts and in the agent's tool
  restrictions (`tools: ["Bash"]`), not in prose or presentation metadata.
- The classification workflow modules (`classification_workflow.py`,
  `portfolio_classifier.py`) were moved out of `src/` into
  `.claude/skills/classify-portfolio/scripts/` because they are used only by the
  classifier agent. `src/app.py`'s `portfolio-classify` subcommand now runs the
  live, database-backed workflow (it previously ran a hardcoded sample-data demo).
- The worktree currently contains an uncommitted classifier prototype in
  `src/portfolio_classifier.py` and focused tests in
  `tests/test_portfolio_classifier.py`. Review and align that prototype with the
  approved constrained workflow before including it in an implementation commit.
- The classifier agent uses model `haiku` and documents its dependency on
  the `classify-portfolio`, `read-portfolio-classification-data`, and
  `fetch-yfinance-classification-data` skills.
- The human-readable docs for the agent live in `docs/agents/portfolio-classifier/`.
- `src/app.py` is the canonical CLI and exposes `pipeline`, `analytics`,
  `statements`, `email`, `yfinance`, `yfinance-sync`, `ticker-map`, and
  `import-activities`.
- Legacy top-level pipeline flags and direct module commands remain compatible.
- `docs/reference/cli.md` is the command reference and must be updated with CLI changes.
- This handover is the context file and must be updated after repository-changing tasks.
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
- Email output keeps the transaction fields plus `price_currency`,
  `source_message_id`, and `received_at`.
- Email `date` is resolved from the email body first and falls back to the IMAP received date when needed.
- `pipeline --source email` stores messages incrementally. Unknown symbols are retained
  as pending and reported without blocking known trades.
- `ticker-map pending` lists unresolved symbols and `ticker-map resolve-pending` runs
  the explicitly interactive mapping workflow.
- Resolved email BUY/SELL rows contribute provisional quantities until matching
  statement transactions supersede them.
- The yfinance extractor fetches stock metadata, ETF metadata, and historical OHLCV data for caller-supplied tickers.
- Yfinance metadata output is returned as stock and ETF dataframes with stable columns.
- Yfinance historical output is returned as a dataframe with `Date`, `Ticker`, `Open`, `High`, `Low`, `Close`, `Adj Close`, and `Volume`.
- A committed email branch triggers `src/market_data.py`. It derives each owned
  ticker's initial or incremental date window and upserts metadata and history.
- Use `uv run python src/app.py yfinance-sync` for retries or targeted updates and add
  `--full` to restart from first portfolio activity.
- All active runtime files under `src/` now use the shared logger and include stage-level usage/error logging.
- `src/` is intended to stay flat; there should be no Camelot subfolder or separate extractor runner file.
- `instructions.md` is the repo-wide implementation guide.

## Next Session: Constrained Portfolio Classification Agent

- Use `docs/plans/constrained_portfolio_classification_agent.md` as the source of
  truth for the first implementation.
- The `portfolio-classifier` subagent (`.claude/agents/portfolio-classifier.md`)
  has one fixed job and one supported prompt: `Classify my portfolio`.
- Script-backed skills under `.claude/skills/` provide orchestration, read-only
  portfolio data access, and restricted yfinance classification enrichment.
- The public agent workflow may invoke only the classification orchestrator. The
  database and yfinance scripts are constrained implementation dependencies, not
  open-ended tools exposed to the agent.
- Open the configured DuckDB database with `read_only=True`. Do not accept SQL or a
  production database path from the prompt or command line.
- Use hard-coded, parameterized database queries and return only approved holdings,
  security metadata, transaction-derived context, and verified provider symbols.
- Select yfinance modes internally by security type. Allow only `identity`,
  `equity-classification`, and `etf-classification`; do not expose mode or field
  selection to the agent.
- Yfinance may fill only empty allowlisted fields. It must never replace populated
  database values or write enrichment results to DuckDB or a cache.
- Apply manual overrides and the YAML classification rules deterministically. Send
  incomplete or conflicting records to `Needs Review`.
- Emit schema-validated JSON with field-level provenance and per-ticker enrichment
  errors. One ticker failure must not abort the remaining portfolio.
- Before implementation is complete, test that the database is unchanged, arguments
  cannot escape the allowlists, yfinance is mocked in unit tests, and malformed
  policy or output data fails safely.

## Next Session: Activity Export Pipeline

- `activities` is an analytics-ready table inside the main DuckDB database, not a separate database.
- Running `src/data_sorter.py` currently writes original CSV rows to `raw_activity_exports`, resolves source symbols against `tickers`, and writes normalized rows containing `ticker_id` to `activities`.
- `src/data_sorter.py` does not populate `tickers`. The required ticker records must currently exist before an activity export can be normalized.
- A ticker-bearing import is rejected when its symbol cannot resolve unambiguously, so raw rows remain stored but no normalized `activities` rows are published.
- This database orchestration is currently embedded in `src/data_sorter.py`. The next implementation should create a dedicated activity-export pipeline and return `data_sorter.py` to extraction and normalization responsibilities.
- The future pipeline must define how `tickers` is populated before activity resolution, then own ticker resolution, raw and normalized database writes, deduplication, import status, and successful source-file movement.

## Important Files

- `docs/plans/constrained_portfolio_classification_agent.md`
- `Knowledge-Base/ref/required_fields_v1_1.yaml`
- `Knowledge-Base/ref/classification_rules_v1_1.yaml`
- `Knowledge-Base/ref/manual_overrides_v1_1.yaml`
- `src/app.py`
- `src/data_sorter.py`
- `src/statement_extractor.py`
- `src/email_extractor.py`
- `src/yfinance_extractor.py`
- `src/market_data.py`
- `src/system_logger.py`
- `tests/test_statement_extractor.py`
- `tests/test_email_extractor.py`
- `tests/test_yfinance_extractor.py`
- `docs/criteria/camelot_extraction_refactor.md`
- `docs/plans/camelot_extraction_refactor.md`
- `docs/criteria/email_extraction.md`
- `docs/plans/email_extraction.md`
- `docs/criteria/yfinance_integration.md`
- `docs/project/todo.md`
- `docs/reference/cli.md`
- `instructions.md`

## Known Constraints

- The initial classification agent supports only `Classify my portfolio` and JSON
  output.
- Classification database access must be view-only and must not expose arbitrary
  SQL, paths, or mutation methods.
- Classification yfinance access must be ephemeral, classification-only, and
  restricted to fixed script modes and field allowlists.
- Enforce access limits in executable wrappers and the agent's tool restrictions,
  not only in prompt wording.
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
- Keep yfinance extraction independent from database writes; `src/market_data.py`
  owns synchronization and `src/database_command.py` owns persistence.
- Do not run glossary extraction by default; use the `--include-glossary` flag only when explicitly needed.

## Last Completed Work

- Added and pushed the constrained portfolio-classification agent plan on branch
  `Agent-Development` in commit `9e72293`.
- Recorded the fixed prompt, script and skill boundaries, DB-first source priority,
  restricted yfinance modes, JSON contract, and acceptance criteria.
- Verification: `uv run python -m unittest discover -s tests` passes 109 tests.
- Consolidated yfinance persistence around `src/market_data.py` and `yfinance-sync`;
  removed the duplicate history-sync command design.
- Added partial publication for unknown email tickers, timestamp checkpoints,
  pending mapping activation, provisional analytics, and statement supersession.

- Added automatic yfinance metadata and historical-price synchronization after
  successful full-source ingestion, plus the standalone `yfinance-sync` retry
  command.
- Added owned-period and incremental date selection, verified Yahoo mapping use,
  atomic metadata/history upserts, and failure isolation from committed source
  ingestion.
- Added focused synchronization and orchestration tests. Market-data and
  extractor tests pass; app/database tests remain affected by the existing
  Windows temporary-directory access issue.

- Extended the analytics report with non-policy reference metrics, selectable
  dividend and cash-flow sources, commissions, weighted-average realized gains,
  XIRR, and on-demand Wealthsimple FX-fee estimates for statement BUY/SELL rows.
- Kept the schema unchanged because FX fees are derived from existing statement
  values; email/export FX requests explicitly report unavailable inputs.
- Centralized financial assumptions in `src/config.py` and documented formulas
  and source selection in `docs/reference/analytics.md`.

- Added canonical `app.py` subcommands for all user-facing runtime features.
- Kept existing direct extractor, sorter, yfinance, and ticker-mapping commands as wrappers.
- Added optional argument forwarding and consistent success exit codes to delegated CLIs.
- Added `docs/reference/cli.md` and linked it from the documentation index.
- Updated `instructions.md` with feature-comment, CLI-reference, and handover-update rules.
- Added focused tests for command dispatch, root help, pipeline forwarding, and failures.
- Added `src/email_extractor.py` as the flat runtime for combined Wealthsimple and Interac email extraction.
- Added opt-in CSV export to the email extractor and kept statement export in `src/statement_extractor.py`.
- Tightened email date parsing for non-dividend order emails and kept received-date fallback when no in-email date exists.
- Added focused email tests in `tests/test_email_extractor.py`.
- Added email docs in `docs/plans/email_extraction.md`, `docs/criteria/email_extraction.md`, and `docs/project/todo.md`.
- Removed the legacy `email_export/` folder.
- Expanded balanced stage-level logging across `src/data_sorter.py`, `src/statement_extractor.py`, and `src/email_extractor.py`.
- Updated `instructions.md` so new runtime code under `src/` should include logger setup by default.
- Added `src/yfinance_extractor.py` for dataframe-returning stock metadata, ETF metadata, and historical OHLCV fetches.
- Added mocked yfinance tests in `tests/test_yfinance_extractor.py`.
- Added `docs/criteria/yfinance_integration.md`.
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
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest tests.test_app
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\app.py --help
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\app.py <command> --help
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test*.py'
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_email_extractor.py'
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py --help
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\statement_extractor.py --include-glossary
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\email_extractor.py --help
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\email_extractor.py
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe src\email_extractor.py --export
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_yfinance_extractor.py'
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest tests.test_portfolio_metrics
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest tests.test_analytics tests.test_app
```

The new pure metric tests pass. The full suite now runs 90 tests with 85 passing
and 5 pre-existing failures: one vetted holdings calculation conflicts with its
SELL-aware test expectation, and four activity-import ticker/deduplication tests
remain outside this analytics change. All newly added analytics tests pass.

## Sign-Off Checklist

- Write down the exact file or behavior being worked on.
- Note any user constraints that changed the direction of the task.
- Record the last verification command that passed.
- Record any blockers or environment issues.
- Record the next concrete step, not just the broad goal.

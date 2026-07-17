# Project Overview

This is a plain-English tour of what the project does and how its pieces fit together. For exact commands see the [CLI reference](../reference/cli.md); for calculation detail see the [analytics reference](../reference/analytics.md); for deep technical detail see the [architecture docs](../README.md).

## What problem this solves

Wealthsimple doesn't give you a single, queryable source of truth for your portfolio's history. This project builds one, locally, from data you already have access to:

- **CSV activity exports** you download from Wealthsimple.
- **PDF account statements** Wealthsimple emails or generates monthly.
- **Order and dividend confirmation emails** sitting in your Gmail inbox.

Everything gets parsed, de-duplicated, and merged into one DuckDB database file. From there, two things become possible: rich **analytics** (returns, risk, allocation, fees, income) and **portfolio classification** (grouping each holding into an approved asset-allocation bucket like Core Equity or Bonds).

The project deliberately does not log into Wealthsimple or scrape it directly — that's flagged as a legal/ToS risk in [`issues.md`](issues.md). It only reads exports you provide and public Yahoo Finance data.

## The two halves of the project

### 1. The data pipeline (`src/`)

This is plain Python, run from the command line via `uv run python src/app.py <command>`. It has three jobs:

**Ingest** — three independent extractors turn raw source material into normalized transaction rows:
- `email_extractor.py` connects to Gmail over IMAP and parses Wealthsimple/Interac emails.
- `statement_extractor.py` parses PDF account statements (the "official record," used for reconciliation).
- `data_sorter.py` cleans and imports the CSV activity-export files.

**Resolve & store** — `ticker_mapping.py` maps whatever symbol appears in a source (which can vary) to a canonical ticker; `ticker_pipeline.py` and `market_data.py` handle currency-suffix correction and pull in Yahoo Finance metadata/price history via `yfinance_extractor.py`. `database.py` owns the DuckDB schema and connection; `database_command.py` provides the higher-level transactional operations (upload transactions, resolve tickers, record classification results) built on top of it. `staging.py` makes ingestion resumable — if something fails partway through, nothing is lost or silently duplicated.

**Report** — `analytics.py` is a read-only reporting engine that queries the finished database and produces a full portfolio report. `portfolio_metrics.py` holds the underlying financial math (returns, volatility, Sharpe/Sortino, drawdown, XIRR) as pure, independently testable functions. Nothing in the analytics path writes back to the database.

Supporting modules: `config.py` centralizes shared constants (paths, DB schema version, fee/risk assumptions); `system_logger.py` gives every module the same log formatting. `Database_Commands.py` (repo root) is an older, ad hoc set of query helpers that predates `database_command.py` — largely superseded. `sitecustomize.py` just makes `src/` importable without manual `PYTHONPATH` setup.

### 2. The Claude Code agent system (`.claude/`)

A narrowly scoped agent, **portfolio-classifier**, answers one request: "classify my portfolio." It's intentionally limited — it runs a small model, has access to nothing but the `Bash` tool, and can only execute one fixed script. It cannot edit files, run arbitrary code, or touch the database.

That script chains together three skills:

1. **`read-portfolio-classification-data`** — opens the database read-only and runs fixed queries to pull current holdings, ticker metadata, quantities, cost basis, and transaction history.
2. **`fetch-yfinance-classification-data`** — for whatever classification fields are still missing (never overwriting what the database already has), fetches identity/sector/fund metadata from Yahoo Finance. Nothing it fetches is cached or written back to the database.
3. **`classify-portfolio`** — merges everything, applies manual per-ticker overrides and a deterministic, priority-ordered set of YAML rules, flags anything incomplete or ambiguous as "Needs Review," and writes a schema-validated JSON report.

The output JSON (default under `exports/portfolio-classification/`) lists each holding's assigned group, confidence, evidence, and where each field's value came from (database, Yahoo Finance, override, or missing). The agent never modifies anything — a separate, explicitly-run pipeline command (`classification-sync`) is the only way that report gets persisted into the database's `portfolio_classifications` table.

The rules themselves live in `Knowledge-Base/` as version-controlled YAML: approved groups and allocation targets (`policy`), rule priority/logic (`classification_rules`), asset-class/sector reference data (`security_grouping_reference`), highest-priority per-ticker overrides (`manual_overrides`), and minimum required inputs (`required_fields`). This makes classification logic auditable and changeable without touching code.

## What you can actually do with it

Run `uv run python src/app.py --help` for the full list; the main things:

| Command | What it does |
|---|---|
| `pipeline` | End-to-end ingest from CSV, PDF statements, or email — extract, resolve tickers, store, and (for email) refresh market data |
| `analytics` | Generate the full portfolio report — console or JSON, with date filtering and optional benchmark comparison |
| `statements` / `email` | Run one extractor standalone |
| `yfinance` / `yfinance-sync` | Fetch or refresh Yahoo Finance metadata and price history |
| `ticker-map` / `resolve-tickers` | Manage and resolve ticker symbol mappings that are blocking ingestion |
| `import-activities` | Import a CSV activity export directly |

**Analytics** covers holdings and allocation (by ticker, sector, currency, geography — including "look-through" ETF exposure), concentration risk, rebalancing drift, time-weighted and money-weighted (XIRR) returns, volatility/Sharpe/Sortino/drawdown, benchmark comparison, dividend/income analysis, realized/unrealized gains, fees, and portfolio turnover. Anything it can't reliably compute is reported as "unavailable with a reason" rather than guessed.

**Classification** (via the Claude agent, not the CLI directly) answers "what type of asset is each of my holdings, and does my allocation match policy?"

## Testing

Every `src/` module has a matching `tests/test_*.py` file using Python's built-in `unittest`. Run the whole suite with:

```bash
uv run python -m unittest discover -s tests
```

External calls (Yahoo Finance, email, database) are mocked in tests, so the suite runs offline and deterministically.

## Current status

The classification agent and its three-skill workflow are fully implemented, as is the consolidated `app.py` CLI. Email ingestion, provisional holdings, and market-data sync work end-to-end. Open work items — tracked in [`todo.md`](todo.md) — include better handling of partial sync failures, FX-normalized multi-currency totals, and finishing the separation of activity-export logic out of `data_sorter.py`. See [`handover.md`](handover.md) for the latest session-to-session status.

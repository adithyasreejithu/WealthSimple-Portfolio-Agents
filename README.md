# WealthSimple-Portfolio-Agents

A local-first data pipeline and Claude Code agent system for consolidating Wealthsimple investment activity into a single database and turning it into portfolio analytics and classification reports.

## What this does

- **Ingests** your Wealthsimple activity from three sources you already have: CSV activity exports, PDF account statements, and order/dividend confirmation emails (via Gmail).
- **Normalizes** everything into one DuckDB database (`Data/PRD_WealthSimple.duckdb`), resolving ticker symbols and enriching them with Yahoo Finance market data.
- **Reports** on the result with a read-only analytics engine: holdings, allocation, performance/risk, income, fees, and rebalancing drift.
- **Classifies** your holdings into approved portfolio groups (e.g. Core Equity, Bonds, Cash) using a dedicated Claude Code agent that runs deterministic, rules-based logic — never live trading, never write access beyond a generated report.

The project deliberately avoids scraping Wealthsimple directly; it works only from exports you provide and Yahoo Finance's public data. See [`docs/project/issues.md`](docs/project/issues.md) for the reasoning.

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python src/app.py --help
```

All pipeline stages run through `python src/app.py <command>` — see the [CLI reference](docs/reference/cli.md) for every command (`pipeline`, `analytics`, `statements`, `email`, `yfinance`, `ticker-map`, `resolve-tickers`, `import-activities`, and more).

## Learn more

- **[Project overview](docs/project/overview.md)** — start here for a plain-English tour of what each part does and how the pieces fit together.
- **[Documentation index](docs/README.md)** — architecture references, agent docs, plans, and project status.

## Development

- Run tests: `python -m unittest discover -s tests`
- See [`CLAUDE.md`](CLAUDE.md) for repo conventions (structure, style, commit/PR guidelines).

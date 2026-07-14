---
name: fetch-stock-research-data
description: Fetch ephemeral, JSON-safe yfinance research data (company overview, valuation, financial statements, earnings, analyst ratings, options, news, insider and institutional activity, dividends, and raw OHLCV history) for verified provider symbols, as the first data pull feeding stock thesis pages. Use when gathering source data to write or update a Knowledge-Base/stocks/TICKER.md thesis.
---

# Fetch Stock Research Data

The "first data pull" for the stock decision-support system
(`docs/plans/goals/stock_decision_support_system.md`). It gathers everything
yfinance can supply for a ticker so a Thesis Writer / Wiki Update agent can fill
the sections of `Knowledge-Base/templates/stock-thesis-template.md`.

Run `python .claude/skills/fetch-stock-research-data/scripts/fetch_stock_research_data.py`:

- `--ticker AAPL` — resolve the verified Yahoo symbol from the database and pull all groups.
- `--ticker AAPL --provider-symbol AAPL` — supply the Yahoo symbol explicitly (aligned one-to-one).
- `--all-holdings` — pull every verified holding.
- `--groups overview valuation dividends` — limit which groups to fetch (default: all).
- `--history-days N`, `--pretty`, `--output PATH`.

Symbols come only from verified `ticker_provider_mappings` rows; the script does
no fuzzy resolution. It **fetches raw provider data only** — it does not compute
technical indicators, score sentiment, or flag unusual options activity; those
interpretation layers belong to later agents. Read
[yfinance-research-contract.md](references/yfinance-research-contract.md) for the
fixed groups, thesis-section mapping, and what is out of scope.

---
name: fetch-yfinance-classification-data
description: Fetch ephemeral, JSON-safe yfinance metadata for missing portfolio-classification fields using fixed identity, equity, or ETF allowlists. Use only as an internal dependency of classify-portfolio with database-originated tickers and verified provider symbols.
---

# Fetch Yfinance Classification Data

Use `scripts/fetch_classification_data.py` only through the classify-portfolio orchestrator. Never request prices, history, analysts, news, options, statements, arbitrary `.info` fields, or fields selected by a user. Read [yfinance-contract.md](references/yfinance-contract.md) for the fixed modes.

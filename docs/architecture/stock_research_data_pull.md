# Stock Research Data Pull

The `fetch-stock-research-data` skill is the **first data pull** for the stock
decision-support system (`docs/plans/goals/stock_decision_support_system.md`).
It gathers everything yfinance can supply for a ticker into one JSON-safe
payload so a downstream Thesis Writer / Wiki Update agent can fill the sections
of `Knowledge-Base/templates/stock-thesis-template.md`
(`Knowledge-Base/stocks/<TICKER>.md`).

It is the research analogue of `fetch-yfinance-classification-data`: same
ephemeral, allowlisted, JSON-safe discipline, but a much wider field set. The
classification skill is deliberately restricted to identity/classification
fields; this one covers the research surface (overview, valuation, financials,
earnings, analyst, options, news, insider, institutional, dividends, history).

## Files

- `.claude/skills/fetch-stock-research-data/SKILL.md` — invocation and guardrails.
- `.claude/skills/fetch-stock-research-data/references/yfinance-research-contract.md` — the fixed groups, thesis-section mapping, and out-of-scope list.
- `.claude/skills/fetch-stock-research-data/scripts/fetch_stock_research_data.py` — the fetcher and CLI.

## What it reuses

- `src/yfinance_extractor.py`: `fetch_security_history` (OHLCV), `configure_yfinance_cache`, and the impersonated `_build_session`/`_create_ticker` — history and session handling are not reimplemented.
- Verified `ticker_provider_mappings` (`provider='yahoo'`, `verification_status='verified'`) via a small read-only DuckDB query, the same trust boundary read-portfolio-classification-data uses. The pull does **no** fuzzy symbol resolution of its own.

## Design boundaries

- **Fetch only.** No technical-indicator computation, sentiment scoring, or unusual-options detection — those interpretation layers belong to later agents (Technical Analysis, Options & Sentiment, Insider Activity). Raw `history` is fetched precisely so the Technical Analysis Agent can compute RSI/MACD/moving averages from it. The first interpretation layer now exists: this pull is registered as the `yfinance` research **source** consumed by the `stock-analyst` scoring workflow (see [`decision_support_flow.md`](decision_support_flow.md)), which computes a small set of derived metrics (FCF yield, YoY growth, simple price returns, put/call OI) from these groups; the deeper indicator/sentiment agents remain deferred.
- **Failures are data.** Per-ticker, per-group, and per-subfield failures land in the payload's `errors` map (`"group"` or `"group.subfield"` keys); a blocked provider call never sinks the batch. Batch size is capped at 25 tickers.
- **Coverage gaps are documented, not faked.** Segment/geographic revenue detail, competitive position, guidance/management commentary, social sentiment, news-sentiment scoring, and peer/historical valuation comparison are outside yfinance and are listed as out-of-scope in the contract rather than approximated.

## Invocation

```
python .claude/skills/fetch-stock-research-data/scripts/fetch_stock_research_data.py \
    --ticker AAPL [--provider-symbol AAPL] [--groups overview valuation ...] \
    [--all-holdings] [--history-days N] [--pretty] [--output PATH]
```

With `--ticker` and no `--provider-symbol`, the symbol is resolved from the
verified DB mapping; `--all-holdings` pulls every verified holding. Output is a
JSON list of `{ticker, provider_symbol, groups, data, errors}` objects.

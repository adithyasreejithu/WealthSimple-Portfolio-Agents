# Stock Decision-Support System — Research Data Pull (fetch-stock-research-data)

## Summary

Implement the **first data pull** for `docs/plans/goals/stock_decision_support_system.md`:
a `fetch-stock-research-data` skill that gathers everything yfinance can supply for a
ticker into one JSON-safe payload, structured to feed the sections of
`Knowledge-Base/templates/stock-thesis-template.md`. This is the data-gathering layer the
future Thesis Writer / research agents consume; it does not itself write wiki pages.

Key decisions:

- **New standalone skill, not an extension** of `fetch-yfinance-classification-data`. That
  skill is deliberately locked to identity/classification fields (its SKILL.md forbids
  prices, history, analysts, news, options, statements). Research is a different consumer
  needing a much wider surface, so it gets its own skill mirroring the same ephemeral,
  allowlisted, JSON-safe discipline.
- **Fetch everything yfinance offers** that maps to the goal doc's research areas (broad
  single pull), organized into 11 groups: overview, valuation, financials, earnings,
  analyst, options, news, insider, institutional, dividends, history.
- **Raw OHLCV only, no computed indicators.** RSI/MACD/moving averages/support-resistance
  are intentionally deferred to a future Technical Analysis Agent; the `history` group
  exists so that agent has the raw series to compute from.
- **No fuzzy symbol resolution.** Symbols come only from verified `ticker_provider_mappings`
  (`provider='yahoo'`, `verification_status='verified'`), the same trust boundary
  read-portfolio-classification-data uses.
- **Failures are data.** Per-ticker, per-group, and per-subfield failures land in the
  payload's `errors` map rather than raising, so one blocked provider call never sinks the
  batch. Batch capped at 25 tickers.
- **Coverage gaps documented, not faked.** Segment/geographic revenue, competitive
  position, guidance/management commentary, social sentiment, news-sentiment scoring,
  unusual-options detection, and peer/historical valuation comparison are outside yfinance
  and are listed as out-of-scope in the contract, to be sourced by later agents/tools.
- **No new `src/app.py` CLI command** — the skill script is the entry point (same pattern
  as the other KB/classification skills), so `docs/reference/cli.md` is unchanged.

## Implementation Steps

1. **Skill script.** `.claude/skills/fetch-stock-research-data/scripts/fetch_stock_research_data.py`:
   - `fetch_stock_research_data(requests, *, ticker_factory, history_fetcher)` — the core,
     injectable for testing. Request shape `{ticker, provider_symbol, groups?, history_days?}`.
   - `RESEARCH_GROUPS` frozenset + per-group fetchers (`GROUP_FETCHERS`); `INFO_GROUPS`
     read `get_info()` once per ticker. `_safe_map` isolates sub-attribute failures.
   - `_json_value` normalization copied from the sibling skills (skills are self-contained).
   - Reuses `src/yfinance_extractor.py`: `fetch_security_history` (OHLCV),
     `configure_yfinance_cache`, and `_build_session`/`_create_ticker` (impersonated
     session) — history and session handling are not reimplemented.
   - `_resolve_provider_symbols` — small read-only DuckDB query for verified Yahoo symbols.
   - CLI: `--ticker`, `--provider-symbol`, `--all-holdings`, `--groups`, `--history-days`,
     `--db-path`, `--cache-dir`, `--output`, `--pretty`; stdin JSON-list fallback.
2. **Contract + SKILL.md.**
   `.claude/skills/fetch-stock-research-data/references/yfinance-research-contract.md`
   (groups → yfinance source → thesis section, request/output shape, out-of-scope list)
   and `SKILL.md` (invocation, guardrails, fetch-only boundary).
3. **Docs.** `docs/architecture/stock_research_data_pull.md` and this implementation record.

## Success Criteria

- `python -m unittest discover -s tests` passes (no regression; `test_app`/`test_analytics`
  loader errors are pre-existing on this branch, unrelated to the skill).
- A mocked-ticker run produces JSON-serializable output with all 11 groups and the
  `errors` map populated per subfield when an attribute is missing.
- A real pull (`--ticker AAPL --provider-symbol AAPL`) returns live overview/valuation/
  financials/earnings/history with no errors.
- `_resolve_provider_symbols` returns verified holdings from the real DB (e.g. `CDZ → CDZ.TO`).
- `fetch-yfinance-classification-data` and the classifier are untouched.

## Assumptions

- Downstream indicator computation, sentiment scoring, and unusual-activity detection are
  separate later agents; this skill's contract is fetch-only.
- Verified `ticker_provider_mappings` are maintained by the existing ticker-resolution
  pipeline; this pull trusts them and does no resolution of its own.
- yfinance field availability varies by security and over time; missing fields surface as
  absent keys or `errors` entries, never as fabricated values.

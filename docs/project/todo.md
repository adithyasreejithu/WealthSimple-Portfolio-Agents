# TODO

## Priority

- Cross-reference `classify-portfolio`'s holdings against the Knowledge-Base
  wiki: nothing today diffs "what the DB says I hold" against "what has a
  `stocks/TICKER.md` page." `classify-portfolio` only connects to DuckDB and
  never reads `Knowledge-Base/`; `kb-search`/`kb-discovery` only read the wiki
  and never touch the database. Add a step (likely owned by `kb-discovery` or
  a new skill) that reports held tickers with no thesis page, and/or thesis
  pages for tickers no longer held.

## Backlog

- Define the KB required-fields schema (what must be populated per stock
  page/ticker before it counts as "done", including validated
  `tickers:`/`tags:` accuracy per the KB population system's decision #8)
  once the agent rebuild in `docs/plans/kb-population-agent-rebuild.md` is
  far enough along to know what's actually needed in practice, rather than
  designing it up front. A prior version already exists archived at
  `.claude/skills/_archive/kb-update-thesis/references/thesis-contract.md`
  — restore and update that against decisions #7-#18 when this comes up,
  rather than starting from scratch.

- Add a dashboard "upcoming earnings" view once `earnings_events`/
  `dividend_events` tables exist in the pipeline database (see
  `docs/plans/` KB population system scope): API endpoint + calendar/chart
  surfacing upcoming report dates with consensus EPS/revenue estimates, and
  a yearly rollup estimate. Dashboard-facing only — the underlying pull and
  storage is pipeline work, not a dashboard concern.

- Restrict `earnings-dividends-sync` to equities only: currently pulls
  earnings and dividend data for both stocks and ETFs, but the decision
  rubric only scores equities (ETFs use a different set of fund-appropriate
  dimensions). Filter out fund tickers at sync time to avoid unnecessary
  yfinance calls and database rows for tickers that will never be scored or
  displayed in the analyst's worksheet.

- Add a dashboard "financials trend" view once a `financial_snapshots`
  table exists in the pipeline database (see
  `docs/plans/financial-snapshots-pipeline.md`): API endpoint + chart(s)
  surfacing revenue/margin/EPS trends over time per held ticker.
  Dashboard-facing only — the underlying pull and storage is pipeline work,
  not a dashboard concern.

- Revisit a persisted `insider_events`-style table once `earnings_events`/
  `dividend_events`/`financial_snapshots` have proven out in practice. The
  rubric's `insider_activity` dimension reads `derived:net_insider_shares`,
  recomputed fresh from yfinance every analysis run with no history kept —
  same root problem financials had before `financial_snapshots`, but
  deliberately not built alongside it to avoid standing up three new tables
  in one pass before the first two are validated in use.

- Implement technical analysis for the stock decision-support system per the
  proposed plan in `docs/plans/stock-decision-support-technical-analysis.md`
  (awaiting approval): new `analyze-stock-technicals` skill computing
  SMA/RSI/MACD/support-resistance/gaps/volume/relative-strength from the
  already-fetched OHLCV history, generic-source fixes in
  `scoring_worksheet.py`/`validate_recommendation.py`, and rubric v1.2 with a
  scored `technical` dimension (0.08, funded from `market_sentiment`).

- Add operational monitoring for partial email runs and failed Yahoo symbols.
- Add a review command for email/statement rows that cannot be reconciled by ticker,
  direction, execution date, and quantity.
- Replace the date-level IMAP boundary with a UID high-water mark only if avoiding
  same-day metadata refetches becomes necessary; message IDs prevent duplicate storage.
- Add FX-normalized cross-currency portfolio totals. Current market values retain each
  ticker's listing currency.

- Remove `src/database_test_main.py` after database creation has been manually verified and the permanent startup orchestration exists.
- Finish separating the activity-export database orchestration from `src/data_sorter.py`.
- Create a dedicated activity-export pipeline between `src/data_sorter.py` and `src/database.py`; remove direct database insertion, ticker resolution, deduplication, and file-movement orchestration from `data_sorter.py`.
- Add ticker resolution that creates or finds each ticker and replaces source ticker text with `ticker_id` before database insertion.
- Add normalized statement-code glossary storage and its pipeline consumer.
- Add migration and backfill tooling for legacy or incompatible database schemas; normal startup intentionally refuses partial schemas.
- Add repository backup and restore procedures for the DuckDB file.
- Add multiprocessing so statement files can be processed at the same time.
- Remove the files once read.

## Implementation Metadata

- Database foundation implementation model: 5.5 Medium.

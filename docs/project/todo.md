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

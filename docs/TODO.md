# TODO

- Invoke `initialize_database()` from the future end-to-end process startup orchestration.
- Remove `src/database_test_main.py` after database creation has been manually verified and the permanent startup orchestration exists.
- Add extractor-to-database pipelines for statement, email, and yfinance data.
- Create a dedicated activity-export pipeline between `src/data_sorter.py` and `src/database.py`; remove direct database insertion, ticker resolution, deduplication, and file-movement orchestration from `data_sorter.py`.
- Add ticker resolution that creates or finds each ticker and replaces source ticker text with `ticker_id` before database insertion.
- Add normalized statement-code glossary storage and its pipeline consumer.
- Add database-backed email start-date checkpoint retrieval from `email_checkpoints`.
- Update email checkpoints only after a successful email pipeline transaction.
- Replace the temporary `START_DATE` environment fallback once the database layer exists.
- Add migration and backfill tooling for legacy or incompatible database schemas; normal startup intentionally refuses partial schemas.
- Add repository backup and restore procedures for the DuckDB file.
- Add multiprocessing so statement files can be processed at the same time.
- Remove the files once read.
- Add the future workflow trigger that passes required tickers and date ranges into `src/yfinance_extractor.py`.

## Implementation Metadata

- Database foundation implementation model: 5.5 Medium.

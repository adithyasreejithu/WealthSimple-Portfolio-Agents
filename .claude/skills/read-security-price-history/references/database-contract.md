# Database contract

Open `config.DATABASE_PATH` with DuckDB `read_only=True`. Execute only the
hard-coded ticker-resolution and `historical_records` queries below. Expose no
SQL, no production database-path argument, and no caller-supplied date range.

## Boundary

| Rule | Why |
| --- | --- |
| `duckdb.connect(path, read_only=True)`, never `database.get_shared_connection` | The shared connection is read-write and takes the exclusive file lock. Several of these reads run in parallel; a read-only connection is what makes that safe. Same rule `investment-analyst-resources/scripts/db_resources.py` states in its module docstring. |
| Fixed, parameterized queries only | No caller can widen the read. `ticker_id` is bound as a parameter, never interpolated. |
| No `--db-path`-equivalent argument on the public functions | The configured database is the only production target. `db_path` exists as a keyword default for the test suite, matching `read_classification_data`. |
| Schema + version validated before any read | A mismatched schema fails loudly rather than returning shapes the caller silently misreads. |
| An unresolvable symbol returns `None`, never raises | A watchlist ticker with no `historical_records` rows, or a benchmark that has never been fetched, is a normal condition the caller reports as a gap — not an error. |

## Tables read

`tickers`, `historical_records`. Nothing else, and nothing is written.

## Functions

| Function | Returns |
| --- | --- |
| `connect_read_only(db_path=DATABASE_PATH)` | A read-only connection. Raises `DatabaseNotReady` with an actionable message when the file is missing or another process holds the write lock. |
| `validate_database(connection)` | `None`; raises `DatabaseNotReady` on a missing or version-mismatched schema. |
| `resolve_ticker(connection, symbol)` | `{ticker_id, ticker_symbol, security_name, currency, exchange}` or `None`. Case-insensitive; ties broken by lowest `ticker_id` for determinism. Accepts the canonical symbol (`XEQT`) or its Yahoo form (`XEQT.TO`) — the exact match is tried first, then the exchange suffix (`config.YFINANCE_CANADIAN_SUFFIXES`) is stripped, so a ticker genuinely stored as `FOO.TO` still wins over a different `FOO`. |
| `read_security_prices(connection, ticker_id)` | Ascending `list[dict]` of `record_date, open, high, low, close, adjusted_close, volume`. Empty list when the ticker has no rows. |
| `read_benchmark_prices(connection, symbol=DEFAULT_BENCHMARK_SYMBOL)` | `(symbol, rows)`. Resolves the benchmark symbol then delegates to `read_security_prices`. `rows` is empty when the benchmark is unknown or unfetched. The default is the Yahoo-form `XEQT.TO` while `tickers` stores `XEQT`, which is precisely why `resolve_ticker` falls back to the bare symbol. |

`read_benchmark_prices` exists because nothing else in the repository reads a
benchmark's stored price series: `src/analytics.py` does an ad-hoc inline query
inside `get_trend_overlays`, `analytics.get_benchmark_returns` fetches live from
yfinance and never persists, and `market_data.ensure_benchmark_history` only
writes. This is the canonical read.

## Row shape

`record_date` is a `date`, price columns are `Decimal`, `volume` is an integer.
Callers that need JSON pass rows through their own coercion — this module
returns database-native types so a numeric caller (the technicals math) is not
forced to parse strings back into numbers.

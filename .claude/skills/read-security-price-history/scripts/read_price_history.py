"""Read-only DuckDB access for the security-technicals workflow.

Opens its own `duckdb.connect(path, read_only=True)` connection -- never
`database.get_shared_connection`, which is read-write and would take the
exclusive file lock parallel invocations depend on nobody holding. Fixed
queries only: no arbitrary SQL, no caller-supplied database path beyond the
configured one, mirroring the boundary `read-portfolio-classification-data`
already establishes and `investment-analyst-resources/scripts/db_resources.py`
restates.

`read_benchmark_prices` is the reason this module exists as its own dependency
skill rather than living inside the technicals CLI: nothing else in the
repository reads a benchmark's *stored* price series. `analytics.py` runs an
ad-hoc inline query inside `get_trend_overlays`, `analytics.get_benchmark_returns`
fetches live from yfinance and never persists, and
`market_data.ensure_benchmark_history` only writes. Beta, alpha, and relative
strength all need that series, so it gets one canonical reader here.

The connect/validate helpers are deliberately near-duplicates of
`db_resources.py`'s -- the same duplication that file documents as blessed,
for the same reason: an independently-invocable read boundary should not
import another skill's private module to open a connection.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import duckdb

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from config import DATABASE_PATH, DATABASE_SCHEMA_VERSION, DEFAULT_BENCHMARK_SYMBOL  # noqa: E402
from database import REQUIRED_TABLES, SCHEMA_COMPONENT  # noqa: E402


class DatabaseNotReady(RuntimeError):
    """Raised when the configured database is missing, mismatched, or locked."""


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def to_json_safe(value: Any) -> Any:
    """Public alias -- callers serializing these rows should not reach for the
    private helper the way a copy-paste would encourage."""
    return _json_value(value)


def _rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def connect_read_only(db_path: str | Path = DATABASE_PATH) -> duckdb.DuckDBPyConnection:
    """Open a read-only connection, translating DuckDB's lock error into an
    actionable message rather than surfacing a raw IO exception."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise DatabaseNotReady(f"Configured database does not exist: {path}")
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise DatabaseNotReady(
            "Could not open the database read-only -- another process is likely "
            "holding the write lock (a pipeline run or refresh in progress). "
            f"Retry shortly. ({exc})"
        ) from exc


def validate_database(connection: duckdb.DuckDBPyConnection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
    }
    if not REQUIRED_TABLES.issubset(tables):
        raise DatabaseNotReady("Configured database does not contain the required schema")
    row = connection.execute(
        "SELECT schema_version FROM schema_metadata WHERE component = ?", [SCHEMA_COMPONENT]
    ).fetchone()
    if row is None or int(row[0]) != DATABASE_SCHEMA_VERSION:
        raise DatabaseNotReady("Configured database schema version is inactive or incompatible")


def resolve_ticker(connection: duckdb.DuckDBPyConnection, symbol: str) -> dict[str, Any] | None:
    """Resolve a symbol to its `tickers` identity, or `None` if unknown.

    Case-insensitive. `UNIQUE(ticker_symbol, exchange)` permits the same symbol
    on two exchanges, so the lowest `ticker_id` wins for determinism. Unlike
    `db_resources.resolve_ticker` this does not require a verified Yahoo
    mapping: every read here is from the local database, so a provider mapping
    is irrelevant, and requiring one would exclude a benchmark whose history is
    already stored.
    """
    row = connection.execute(
        """
        SELECT ticker_id, ticker_symbol, security_name, currency, exchange
        FROM tickers
        WHERE UPPER(ticker_symbol) = UPPER(?)
        ORDER BY ticker_id ASC
        LIMIT 1
        """,
        [symbol],
    ).fetchone()
    if row is None:
        return None
    return {
        "ticker_id": row[0],
        "ticker_symbol": row[1],
        "security_name": row[2],
        "currency": row[3],
        "exchange": row[4],
    }


def read_security_prices(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> list[dict[str, Any]]:
    """One security's full daily OHLCV history, oldest first.

    Returns database-native types (`date`, `Decimal`), not JSON strings: the
    only consumer is numeric math that would otherwise have to parse them back.
    An empty list means the ticker exists but has no stored history -- a normal
    condition for a newly-added holding, not an error.
    """
    cursor = connection.execute(
        """
        SELECT record_date, open, high, low, close, adjusted_close, volume
        FROM historical_records WHERE ticker_id = ? ORDER BY record_date
        """,
        [ticker_id],
    )
    return _rows_as_dicts(cursor)


def read_benchmark_prices(
    connection: duckdb.DuckDBPyConnection, symbol: str = DEFAULT_BENCHMARK_SYMBOL
) -> tuple[str, list[dict[str, Any]]]:
    """The configured benchmark's stored price series.

    Returns `(symbol, rows)`; `rows` is empty when the benchmark is unknown to
    `tickers` or has no `historical_records` yet -- both of which the caller
    reports as a gap rather than a failure, since beta/alpha/relative-strength
    are legitimately unavailable without it.
    """
    identity = resolve_ticker(connection, symbol)
    if identity is None:
        return symbol, []
    return symbol, read_security_prices(connection, identity["ticker_id"])


def read_prices_for(
    symbol: str, benchmark: str = DEFAULT_BENCHMARK_SYMBOL, db_path: str | Path = DATABASE_PATH
) -> dict[str, Any]:
    """One-call convenience for the common case: open, validate, read both
    series, close. The CLI uses the individual functions on a connection it
    owns; this exists for ad-hoc use and for the argument-free `__main__`.
    """
    connection = connect_read_only(db_path)
    try:
        validate_database(connection)
        identity = resolve_ticker(connection, symbol)
        rows = read_security_prices(connection, identity["ticker_id"]) if identity else []
        benchmark_symbol, benchmark_rows = read_benchmark_prices(connection, benchmark)
    finally:
        connection.close()
    return {
        "symbol": symbol,
        "resolved": identity is not None,
        "identity": identity,
        "rows": rows,
        "benchmark_symbol": benchmark_symbol,
        "benchmark_rows": benchmark_rows,
    }


if __name__ == "__main__":
    # Argument-free by design (see references/database-contract.md): reports
    # only whether the configured benchmark's series is readable, which is the
    # one thing worth checking without a ticker in hand.
    result = read_prices_for(DEFAULT_BENCHMARK_SYMBOL)
    json.dump(
        {
            "benchmark_symbol": result["benchmark_symbol"],
            "benchmark_rows": len(result["benchmark_rows"]),
        },
        sys.stdout,
    )
    sys.stdout.write("\n")

"""Schema, connections, and insert-on-change writes for the market skill's own database.

`market.duckdb` is a **separate file** from the pipeline's `PRD_WealthSimple.duckdb`
-- deliberately. See docs/plans/market-analyst-resources-skill.md Part 1: an
audit found that adding market-proxy rows to the pipeline's `tickers` table is
unsafe (it corrupts the portfolio valuation date axis and can shadow a real
holding's price chart), and a second audit found that even isolated new
tables in the pipeline database break `investment-analyst-resources` and
`classify-portfolio` on the schema-version bump. A dedicated file avoids both
problems and needs no changes to `src/`.

Revisions matter here in a way they don't for ticker prices: macro statistics
(GDP, payrolls) get revised after publication. A plain
`(series_id, obs_date)` primary key would silently overwrite the original
value, making a revision indistinguishable from real economic change. So
this store is insert-on-change and append-only: an unchanged refetch just
bumps `last_confirmed_at`; a changed value inserts a new row. Every reader
goes through `v_macro_current` (latest `first_seen_at` per series/date), so
downstream code is exactly as simple as an overwrite design would have been,
while the revision history is still recoverable.
"""

from __future__ import annotations

import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import duckdb

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from config import DATA_FOLDER  # noqa: E402

MARKET_DB_PATH = DATA_FOLDER / "market.duckdb"

# Exact equality, not a tolerance band: these are as-published source values,
# not computed floats, so two fetches of the same observation should produce
# bit-identical numbers. A tiny epsilon guards only against float round-trip
# noise (e.g. a source serializing 4.25 as 4.249999999999999).
_VALUE_EQUALITY_EPSILON = 1e-9


class MarketStoreNotReady(RuntimeError):
    """Raised when `market.duckdb` is missing, locked, or has no schema yet."""


def _values_equal(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= _VALUE_EQUALITY_EPSILON


def connect_read_only(db_path: str | Path = MARKET_DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open market.duckdb read-only, translating lock/missing-file errors into
    an actionable message -- mirrors
    investment-analyst-resources/scripts/db_resources.py's `connect_read_only`,
    which this is a deliberate sibling of, not a reimplementation from scratch."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise MarketStoreNotReady(
            f"{path} does not exist yet -- run --mode refresh once to create it "
            "and backfill the registry's indicators."
        )
    try:
        connection = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise MarketStoreNotReady(
            "Could not open market.duckdb read-only -- another process is likely "
            "holding the write lock (a --mode refresh in progress). Retry shortly, "
            f"or use --no-refresh to skip refreshing this run. ({exc})"
        ) from exc
    return connection


def connect_read_write(db_path: str | Path = MARKET_DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open market.duckdb read-write, creating the file and schema if needed.
    This is the sole writer path -- callers must run it once, sequentially,
    never fanned out (same DuckDB one-writer-or-many-readers rule as the main
    pipeline database)."""
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = duckdb.connect(str(path), read_only=False)
    except duckdb.Error as exc:
        raise MarketStoreNotReady(
            f"Could not open market.duckdb read-write -- is another --mode refresh "
            f"already running? ({exc})"
        ) from exc
    ensure_schema(connection)
    return connection


def ensure_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Idempotent schema creation. Only ever called from the read-write path."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_observations (
            series_id         VARCHAR   NOT NULL,
            obs_date          DATE      NOT NULL,
            value             DOUBLE,
            status            VARCHAR   NOT NULL DEFAULT 'ok',
            source_id         VARCHAR   NOT NULL,
            units             VARCHAR   NOT NULL,
            first_seen_at     TIMESTAMP NOT NULL,
            last_confirmed_at TIMESTAMP NOT NULL,
            PRIMARY KEY (series_id, obs_date, first_seen_at)
        )
        """
    )
    connection.execute(
        """
        CREATE VIEW IF NOT EXISTS v_macro_current AS
        SELECT o.*
        FROM macro_observations o
        INNER JOIN (
            SELECT series_id, obs_date, MAX(first_seen_at) AS max_first_seen_at
            FROM macro_observations
            GROUP BY series_id, obs_date
        ) latest
          ON o.series_id = latest.series_id
         AND o.obs_date = latest.obs_date
         AND o.first_seen_at = latest.max_first_seen_at
        """
    )
    connection.execute("CREATE SEQUENCE IF NOT EXISTS macro_run_id_sequence START 1")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_runs (
            run_id           BIGINT PRIMARY KEY DEFAULT nextval('macro_run_id_sequence'),
            run_date         DATE      NOT NULL,
            registry_version INTEGER   NOT NULL,
            status           VARCHAR   NOT NULL,
            bundle_path      VARCHAR,
            indicator_count  INTEGER,
            created_at       TIMESTAMP NOT NULL
        )
        """
    )


def upsert_observation(
    connection: duckdb.DuckDBPyConnection,
    *,
    series_id: str,
    obs_date: date,
    value: float | None,
    status: str,
    source_id: str,
    units: str,
    now: datetime,
) -> dict[str, Any]:
    """Insert-on-change write for one observation. Returns what happened:
    `{"action": "new" | "confirmed" | "revised", "prior_value": ... }`.

    Must run inside a transaction the caller controls (see `sources.py`'s
    per-indicator commit pattern) so a failure partway through a batch
    doesn't leave a half-written observation.
    """
    existing = connection.execute(
        """
        SELECT value, status, first_seen_at
        FROM macro_observations
        WHERE series_id = ? AND obs_date = ?
        ORDER BY first_seen_at DESC
        LIMIT 1
        """,
        [series_id, obs_date],
    ).fetchone()

    if existing is None:
        connection.execute(
            """
            INSERT INTO macro_observations
                (series_id, obs_date, value, status, source_id, units, first_seen_at, last_confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [series_id, obs_date, value, status, source_id, units, now, now],
        )
        return {"action": "new", "prior_value": None}

    existing_value, existing_status, existing_first_seen_at = existing
    if _values_equal(existing_value, value) and existing_status == status:
        connection.execute(
            """
            UPDATE macro_observations
            SET last_confirmed_at = ?
            WHERE series_id = ? AND obs_date = ? AND first_seen_at = ?
            """,
            [now, series_id, obs_date, existing_first_seen_at],
        )
        return {"action": "confirmed", "prior_value": existing_value}

    connection.execute(
        """
        INSERT INTO macro_observations
            (series_id, obs_date, value, status, source_id, units, first_seen_at, last_confirmed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [series_id, obs_date, value, status, source_id, units, now, now],
    )
    return {"action": "revised", "prior_value": existing_value}


def get_history(
    connection: duckdb.DuckDBPyConnection, series_id: str, *, start_date: date | None = None
) -> list[dict[str, Any]]:
    """Current (revision-resolved) history for one series, oldest first."""
    query = "SELECT obs_date, value, status, source_id, units FROM v_macro_current WHERE series_id = ?"
    params: list[Any] = [series_id]
    if start_date is not None:
        query += " AND obs_date >= ?"
        params.append(start_date)
    query += " ORDER BY obs_date ASC"
    rows = connection.execute(query, params).fetchall()
    return [
        {"obs_date": row[0], "value": row[1], "status": row[2], "source_id": row[3], "units": row[4]}
        for row in rows
    ]


def get_history_as_of(
    connection: duckdb.DuckDBPyConnection, series_id: str, as_of: datetime, *, start_date: date | None = None
) -> list[dict[str, Any]]:
    """History reconstructed as it would have read at a past moment --
    filters to rows seen by `as_of`, then takes each date's latest
    `first_seen_at` within that filter. This is what insert-on-change makes
    possible and overwrite would not: recomputing what "the value as of the
    last report" actually was, including pre-revision values, for
    `delta_vs_last_report`."""
    query = """
        WITH filtered AS (
            SELECT
                obs_date, value, status, source_id, units, first_seen_at,
                ROW_NUMBER() OVER (PARTITION BY obs_date ORDER BY first_seen_at DESC) AS rn
            FROM macro_observations
            WHERE series_id = ? AND first_seen_at <= ?
        )
        SELECT obs_date, value, status, source_id, units
        FROM filtered
        WHERE rn = 1
    """
    params: list[Any] = [series_id, as_of]
    if start_date is not None:
        query += " AND obs_date >= ?"
        params.append(start_date)
    query += " ORDER BY obs_date ASC"
    rows = connection.execute(query, params).fetchall()
    return [
        {"obs_date": row[0], "value": row[1], "status": row[2], "source_id": row[3], "units": row[4]}
        for row in rows
    ]


def get_new_observation_count_since(connection: duckdb.DuckDBPyConnection, series_id: str, since: datetime) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT obs_date, MIN(first_seen_at) AS first_seen
            FROM macro_observations WHERE series_id = ?
            GROUP BY obs_date
        ) t WHERE first_seen > ?
        """,
        [series_id, since],
    ).fetchone()
    return int(row[0]) if row else 0


def get_latest(connection: duckdb.DuckDBPyConnection, series_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT obs_date, value, status, source_id, units
        FROM v_macro_current
        WHERE series_id = ?
        ORDER BY obs_date DESC
        LIMIT 1
        """,
        [series_id],
    ).fetchone()
    if row is None:
        return None
    return {"obs_date": row[0], "value": row[1], "status": row[2], "source_id": row[3], "units": row[4]}


def get_coverage_start(connection: duckdb.DuckDBPyConnection, series_id: str) -> date | None:
    row = connection.execute(
        "SELECT MIN(obs_date) FROM macro_observations WHERE series_id = ?", [series_id]
    ).fetchone()
    return row[0] if row else None


def get_observed_cadence_days(
    connection: duckdb.DuckDBPyConnection, series_id: str, *, window: int = 6
) -> float | None:
    """Median gap between the last `window` observations, so a source that
    quietly stopped publishing can be told apart from "no change yet" (see
    the freshness gate's `cadence_mismatch` flag)."""
    rows = connection.execute(
        "SELECT obs_date FROM v_macro_current WHERE series_id = ? ORDER BY obs_date DESC LIMIT ?",
        [series_id, window + 1],
    ).fetchall()
    dates = [row[0] for row in rows]
    if len(dates) < 2:
        return None
    gaps = sorted((dates[i] - dates[i + 1]).days for i in range(len(dates) - 1))
    mid = len(gaps) // 2
    if len(gaps) % 2:
        return float(gaps[mid])
    return (gaps[mid - 1] + gaps[mid]) / 2.0


def get_revisions_since(connection: duckdb.DuckDBPyConnection, since: datetime) -> list[dict[str, Any]]:
    """Observations whose value changed (`first_seen_at` is not each row's
    only sighting) where the *change itself* happened after `since`. Uses a
    window-function self-comparison instead of `LATERAL` for portability."""
    rows = connection.execute(
        """
        WITH ranked AS (
            SELECT
                series_id, obs_date, value, first_seen_at,
                LAG(value) OVER (PARTITION BY series_id, obs_date ORDER BY first_seen_at) AS prior_value
            FROM macro_observations
        )
        SELECT series_id, obs_date, prior_value, value AS new_value, first_seen_at
        FROM ranked
        WHERE prior_value IS NOT NULL AND first_seen_at > ?
        ORDER BY first_seen_at ASC
        """,
        [since],
    ).fetchall()
    return [
        {"series_id": row[0], "obs_date": row[1], "prior_value": row[2], "new_value": row[3], "first_seen_at": row[4]}
        for row in rows
    ]


def get_last_successful_run(connection: duckdb.DuckDBPyConnection) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT run_id, run_date, registry_version, bundle_path, indicator_count, created_at
        FROM macro_runs
        WHERE status = 'ok'
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0], "run_date": row[1], "registry_version": row[2],
        "bundle_path": row[3], "indicator_count": row[4], "created_at": row[5],
    }


def to_json_safe(value: Any) -> Any:
    """Mirrors investment-analyst-resources/scripts/db_resources.py's
    `to_json_safe` -- dates/datetimes to ISO strings, NaN/Inf to None,
    recursing through mappings and lists."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    return value


def record_run(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_date: date,
    registry_version: int,
    status: str,
    bundle_path: str | None,
    indicator_count: int,
    created_at: datetime,
) -> int:
    """Records a run. Callers must only call this from a *successful* `read`
    that emitted a bundle -- never from `refresh`, and never when the caller
    overrode `--db-path` for debugging -- so a diagnostic run can't silently
    reset the diff baseline other callers rely on."""
    row = connection.execute(
        """
        INSERT INTO macro_runs
            (run_date, registry_version, status, bundle_path, indicator_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING run_id
        """,
        [run_date, registry_version, status, bundle_path, indicator_count, created_at],
    ).fetchone()
    return int(row[0])

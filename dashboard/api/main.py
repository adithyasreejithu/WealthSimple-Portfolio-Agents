"""Read-only dashboard API.

This is a thin FastAPI wrapper around the existing, already-tested
`src/analytics.py` module — every endpoint here just calls a function that
already exists in the pipeline and converts its return value to JSON. There
is no new database logic in this file on purpose: the pipeline's analytics
module is the single source of truth for how holdings/allocation/trend
numbers are computed, and this file must never diverge from it.

Run it from the repo root with:
    uv run uvicorn main:app --reload --port 8000 --app-dir dashboard/api

It reads the same DuckDB file the CLI uses (src/config.py's DATABASE_PATH,
overridable with the DB_PATH environment variable) in read-only fashion —
this process never writes to the database. If that file does not exist the
process refuses to start: `database.get_shared_connection` would otherwise
silently create an empty database, which a read-only API must never do.

Configuration:
    DB_PATH                  DuckDB file to serve (default Data/PRD_WealthSimple.duckdb)
    DASHBOARD_CORS_ORIGINS   comma-separated allowed origins
                             (default http://localhost:3000)

`/api/portfolio/report` recomputes the full `analytics.portfolio_report`
on demand, cached against the database file's modification time — it is
rebuilt at most once per pipeline run, so the dashboard is never stale
relative to the pipeline and no manual `analytics --export` step is needed.
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import analytics  # noqa: E402
import config  # noqa: E402


def cors_origins(raw: str | None) -> list[str]:
    """Parse DASHBOARD_CORS_ORIGINS (comma-separated) with a localhost default."""
    if raw is None or not raw.strip():
        return ["http://localhost:3000"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not config.DATABASE_PATH.exists():
        raise RuntimeError(
            f"Database not found at {config.DATABASE_PATH}. "
            "Run the ingestion pipeline first, or point DB_PATH at an existing "
            "DuckDB file. Refusing to start so an empty database is not "
            "silently created."
        )
    yield


app = FastAPI(title="Portfolio Dashboard API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(os.getenv("DASHBOARD_CORS_ORIGINS")),
    allow_methods=["GET"],
    allow_headers=["*"],
)


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses/Decimal/date objects into plain JSON types."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def db_path() -> str:
    return str(config.DATABASE_PATH)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "database": db_path(),
        "database_exists": config.DATABASE_PATH.exists(),
    }


@app.get("/api/portfolio/summary")
def portfolio_summary() -> dict[str, Any]:
    summary = analytics.get_portfolio_summary(db_path())
    return {
        "portfolio_value": to_jsonable(summary.portfolio_value),
        "cash_balance": to_jsonable(summary.cash.balance),
        "cash_source": summary.cash.source,
        "holdings_count": len(summary.holdings),
    }


@app.get("/api/portfolio/holdings")
def portfolio_holdings() -> list[dict[str, Any]]:
    holdings = analytics.get_holdings(db_path())
    total_value = sum((h.market_value for h in holdings), start=Decimal("0"))
    rows: list[dict[str, Any]] = []
    for holding in holdings:
        row = to_jsonable(holding)
        row["weight"] = float(holding.market_value / total_value) if total_value > 0 else 0.0
        rows.append(row)
    rows.sort(key=lambda r: r["market_value"], reverse=True)
    return rows


@app.get("/api/portfolio/allocation")
def portfolio_allocation() -> dict[str, Any]:
    holdings = analytics.get_holdings(db_path())
    return to_jsonable(analytics.get_group_allocation(db_path(), holdings))


@app.get("/api/portfolio/trend")
def portfolio_trend() -> list[dict[str, Any]]:
    return to_jsonable(analytics.get_historical_portfolio_values(db_path()))


_report_cache: dict[str, Any] = {}
_report_lock = threading.Lock()


@app.get("/api/portfolio/report")
def portfolio_report() -> dict[str, Any]:
    database_path = config.DATABASE_PATH
    if not database_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Database not found at {database_path}. "
            "Run the ingestion pipeline, or point DB_PATH at an existing DuckDB file.",
        )
    mtime = database_path.stat().st_mtime
    with _report_lock:
        if _report_cache.get("mtime") != mtime:
            _report_cache["payload"] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "database_mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "report": to_jsonable(analytics.portfolio_report(db_path())),
            }
            _report_cache["mtime"] = mtime
        return _report_cache["payload"]


@app.get("/api/portfolio/classifications")
def portfolio_classifications() -> dict[str, Any]:
    """Per-holding classification detail (group, confidence, review flag, enriched fields)."""
    return to_jsonable(analytics.get_classification_details(db_path()))


# Range keyword -> lookback window for the per-ticker price series. `max` (no
# lower bound) is represented by None so the query returns the full history.
_HISTORY_RANGES: dict[str, int | None] = {
    "1m": 30,
    "3m": 91,
    "6m": 182,
    "1y": 365,
    "3y": 1095,
    "max": None,
}


@app.get("/api/stocks/{symbol}/history")
def stock_history(
    symbol: str,
    range: str = Query("1y", description="One of: 1m, 3m, 6m, 1y, 3y, max"),
) -> dict[str, Any]:
    """Daily close series for one ticker, used by the price and benchmark-compare charts."""
    if range not in _HISTORY_RANGES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid range '{range}'. Expected one of: {', '.join(_HISTORY_RANGES)}.",
        )
    lookback_days = _HISTORY_RANGES[range]
    date_from = date.today() - timedelta(days=lookback_days) if lookback_days is not None else None
    history = analytics.get_price_history(symbol, db_path(), date_from=date_from)
    if history is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker symbol '{symbol}'.")
    return to_jsonable(history)

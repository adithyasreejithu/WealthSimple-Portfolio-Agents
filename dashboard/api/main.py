"""Dashboard API: a read-only GET surface plus a small guarded action surface.

The GET endpoints are a thin FastAPI wrapper around the existing,
already-tested `src/analytics.py` module — every one of them just calls a
function that already exists in the pipeline and converts its return value to
JSON. There is no new database logic behind them on purpose: the pipeline's
analytics module is the single source of truth for how holdings/allocation/
trend numbers are computed, and this file must never diverge from it.

Run it from the repo root with:
    uv run uvicorn main:app --reload --port 8000 --app-dir dashboard/api

It reads the same DuckDB file the CLI uses (src/config.py's DATABASE_PATH,
overridable with the DB_PATH environment variable). If that file does not
exist the process refuses to start: `database.get_shared_connection` would
otherwise silently create an empty database, which this API must never do.

`POST /api/actions/*` is the one place that changes state, so it is fenced in:

* Every action requires the auth gate in `require_action_auth` — a bearer
  token when DASHBOARD_ACTION_TOKEN is set, otherwise loopback callers only.
* Actions run as background jobs (see jobs.py); a request never blocks on a
  pipeline or classification run. Only one job runs at a time.
* Writers run as subprocesses, and this process closes its DuckDB connection
  first, because DuckDB permits a single read-write process at a time.
* The GET surface stays read-only and never writes the database itself.

Because job state lives in memory, run a single uvicorn worker.

Configuration:
    DB_PATH                  DuckDB file to serve (default Data/PRD_WealthSimple.duckdb)
    DASHBOARD_CORS_ORIGINS   comma-separated allowed origins
                             (default http://localhost:3000)
    DASHBOARD_ACTION_TOKEN   bearer token required for POST /api/actions/*
                             (unset = loopback-only actions)

`/api/portfolio/report` recomputes the full `analytics.portfolio_report`
on demand, cached against the database file's modification time — it is
rebuilt at most once per pipeline run, so the dashboard is never stale
relative to the pipeline and no manual `analytics --export` step is needed.
An action that rewrites the database therefore invalidates the cache for free.
"""

from __future__ import annotations

import os
import secrets
import subprocess
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

from fastapi import Depends, FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import analytics  # noqa: E402
import app as pipeline_app  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import manual_overrides  # noqa: E402
import ticker_mapping  # noqa: E402
from jobs import Job, JobBusyError, JobRunner  # noqa: E402


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
    database.close_connection()


app = FastAPI(title="Portfolio Dashboard API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(os.getenv("DASHBOARD_CORS_ORIGINS")),
    # POST is allowed only for the guarded /api/actions/* surface; every
    # data endpoint remains a read-only GET.
    allow_methods=["GET", "POST"],
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
    if isinstance(value, Path):
        return str(value)
    return value


def db_path() -> str:
    return str(config.DATABASE_PATH)


# Serializes database use across uvicorn's threadpool. DuckDB's shared
# connection is not safe for concurrent cursors, and an action's subprocess
# needs the API to let go of the file entirely while it writes.
_db_lock = threading.RLock()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "database": db_path(),
        "database_exists": config.DATABASE_PATH.exists(),
    }


@app.get("/api/portfolio/summary")
def portfolio_summary() -> dict[str, Any]:
    with _db_lock:
        summary = analytics.get_portfolio_summary(db_path())
    return {
        "portfolio_value": to_jsonable(summary.portfolio_value),
        "cash_balance": to_jsonable(summary.cash.balance),
        "cash_source": summary.cash.source,
        "holdings_count": len(summary.holdings),
    }


@app.get("/api/portfolio/holdings")
def portfolio_holdings() -> list[dict[str, Any]]:
    with _db_lock:
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
    with _db_lock:
        holdings = analytics.get_holdings(db_path())
        return to_jsonable(analytics.get_group_allocation(db_path(), holdings))


@app.get("/api/portfolio/trend")
def portfolio_trend() -> list[dict[str, Any]]:
    with _db_lock:
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
            with _db_lock:
                report = to_jsonable(analytics.portfolio_report(db_path()))
            _report_cache["payload"] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "database_mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "report": report,
            }
            _report_cache["mtime"] = mtime
        return _report_cache["payload"]


@app.get("/api/portfolio/classifications")
def portfolio_classifications() -> dict[str, Any]:
    """Per-holding classification detail (group, confidence, review flag, enriched fields)."""
    with _db_lock:
        return to_jsonable(analytics.get_classification_details(db_path()))


@app.get("/api/etfs/overlap")
def etf_overlap() -> dict[str, Any]:
    """How much of the ETF sleeve sits in underlying names more than one fund holds."""
    with _db_lock:
        return to_jsonable(analytics.get_etf_overlap(db_path()))


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
    with _db_lock:
        history = analytics.get_price_history(symbol, db_path(), date_from=date_from)
    if history is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker symbol '{symbol}'.")
    return to_jsonable(history)


# --------------------------------------------------------------------------
# Actions: the only endpoints that change state.
# --------------------------------------------------------------------------

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

runner = JobRunner()


def require_action_auth(request: Request) -> None:
    """Gate every action endpoint.

    With DASHBOARD_ACTION_TOKEN set, a matching bearer token is required —
    that is the mode to use if the API is ever bound to a non-loopback
    interface. With it unset, only loopback callers are accepted, which keeps
    the default single-user local setup usable without configuration while
    still refusing anything arriving from the network.
    """
    token = os.getenv("DASHBOARD_ACTION_TOKEN", "").strip()
    if token:
        header = request.headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="A valid action token is required.")
        return
    client_host = request.client.host if request.client else None
    if client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=(
                "Actions are limited to local requests. Set DASHBOARD_ACTION_TOKEN "
                "to allow authenticated remote use."
            ),
        )


ActionAuth = Depends(require_action_auth)


class OverrideRequest(BaseModel):
    ticker: str = Field(min_length=1)
    primary_group: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    secondary_tags: list[str] = Field(default_factory=list)


class ResolveTickerRequest(BaseModel):
    source_symbol: str = Field(min_length=1)
    canonical_symbol: str = Field(min_length=1)
    yahoo_symbol: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    exchange: str = ""


class RetryTickerRequest(BaseModel):
    source_symbol: str = Field(min_length=1)


def _submit(kind: str, work: Any) -> dict[str, Any]:
    """Queue an action, mapping the single-flight rule onto HTTP 409."""
    try:
        return runner.submit(kind, work).to_dict()
    except JobBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"{exc} Wait for it to finish before starting another.",
        ) from exc


def _run_cli(*args: str) -> dict[str, Any]:
    """Run a pipeline CLI command as a subprocess that owns the database.

    DuckDB allows one read-write process at a time, so this releases the API's
    own connection first and lets the subprocess take the write lock. The next
    request reopens it lazily, and the report cache rebuilds on its own because
    the file's mtime changed.
    """
    with _db_lock:
        database.close_connection()
        completed = subprocess.run(
            [sys.executable, str(SRC_DIR / "app.py"), *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=1800,
        )
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or output or "no output"
        raise RuntimeError(f"`app.py {' '.join(args)}` exited {completed.returncode}: {detail}")
    return {"command": " ".join(args), "output": output[-2000:]}


def _classify_and_sync() -> dict[str, Any]:
    """Re-run classification, then persist it — the two CLI steps, in order."""
    return {
        "steps": [
            _run_cli("portfolio-classify"),
            _run_cli("classification-sync"),
        ]
    }


@app.post("/api/actions/classify", status_code=202, dependencies=[ActionAuth])
def action_classify() -> dict[str, Any]:
    """Re-run the classification workflow and sync the result into DuckDB."""
    return _submit("classify", _classify_and_sync)


@app.post("/api/actions/refresh", status_code=202, dependencies=[ActionAuth])
def action_refresh() -> dict[str, Any]:
    """Run the full ingestion pipeline."""
    return _submit("refresh", lambda: _run_cli("pipeline"))


@app.post("/api/actions/overrides", status_code=202, dependencies=[ActionAuth])
def action_override(payload: OverrideRequest) -> dict[str, Any]:
    """Pin a holding to a group, then re-classify so the change takes effect.

    The YAML edit is quick and its validation errors are worth reporting
    synchronously; only the reclassification that follows becomes a job.
    """
    try:
        stored = manual_overrides.upsert_override(
            payload.ticker,
            payload.primary_group,
            rationale=payload.rationale,
            secondary_tags=payload.secondary_tags,
        )
    except manual_overrides.OverrideError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"override": stored, "job": _submit("classify", _classify_and_sync)}


@app.post("/api/actions/resolve-ticker", status_code=202, dependencies=[ActionAuth])
def action_resolve_ticker(payload: ResolveTickerRequest) -> dict[str, Any]:
    """Map a pending source symbol to a verified canonical/provider symbol.

    Also retries any export data already quarantined on this symbol in the
    same click, using the mapping just saved -- so the common case (the
    source file is still on disk) clears the pending list immediately
    instead of leaving the user to press a second button.
    """

    def work() -> dict[str, Any]:
        with _db_lock:
            resolved = to_jsonable(
                ticker_mapping.resolve_pending_symbol(
                    payload.source_symbol,
                    payload.canonical_symbol,
                    payload.yahoo_symbol,
                    payload.currency,
                    payload.exchange,
                    db_path=db_path(),
                )
            )
            resolved["export_retry"] = to_jsonable(
                pipeline_app.retry_quarantined_exports_for_symbol(
                    payload.source_symbol, db_path=db_path()
                )
            )
            return resolved

    return _submit("resolve-ticker", work)


@app.post("/api/actions/retry-ticker", status_code=202, dependencies=[ActionAuth])
def action_retry_ticker(payload: RetryTickerRequest) -> dict[str, Any]:
    """Retry publishing quarantined export data using an already-saved mapping.

    No new mapping is created here -- this is the "already resolved, just
    retry" button, for a symbol `list_pending` reports as `already_mapped`.
    """

    def work() -> dict[str, Any]:
        with _db_lock:
            results = pipeline_app.retry_quarantined_exports_for_symbol(
                payload.source_symbol, db_path=db_path()
            )
            return {"results": to_jsonable(results)}

    return _submit("retry-ticker", work)


@app.get("/api/tickers/pending")
def pending_tickers() -> list[dict[str, Any]]:
    """Source symbols still blocking ingestion, for the resolve form."""
    with _db_lock:
        return to_jsonable(ticker_mapping.list_pending(db_path()))


@app.get("/api/actions")
def list_actions() -> dict[str, Any]:
    """Recent jobs plus whichever one is in flight, if any."""
    active = runner.active()
    return {
        "active": active.to_dict() if active else None,
        "jobs": [job.to_dict() for job in runner.recent()],
    }


@app.get("/api/actions/{job_id}")
def get_action(job_id: str) -> dict[str, Any]:
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_id}'.")
    return job.to_dict()

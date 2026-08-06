"""Per-domain freshness gate + scoped refresh dispatch for one ticker.

Two halves, deliberately split by which DuckDB lock they need (see
docs/plans/investment-analyst-resources-skill.md's concurrency model):

- `compute_freshness` is read-only. It takes an already-open read-only
  connection (never opens its own, never touches `get_shared_connection`) so
  it is safe to call from `--mode gate` alongside any number of parallel
  callers.
- `refresh_domains` is the sole write path. It calls `market_data.py`'s sync
  functions, which internally use `database.get_shared_connection` (the
  read-write, process-wide connection) -- so it must run once, sequentially,
  never fanned out. `--mode refresh` is the only caller.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import market_data  # noqa: E402
from config import DATABASE_PATH  # noqa: E402
from database import close_connection  # noqa: E402
from position_engine import FINGERPRINT_COMPONENT, compute_fingerprint  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db_resources  # noqa: E402

EARNINGS_WINDOW_DAYS = 2
EARNINGS_CADENCE_DAYS = 7
DIVIDENDS_CADENCE_DAYS = 30
FINANCIALS_CADENCE_DAYS = 30
CLASSIFICATION_CADENCE_DAYS = 7

REFRESHABLE_DOMAINS = ("prices", "earnings", "dividends", "financials")
REPORT_ONLY_DOMAINS = ("classification", "positions")


def _last_business_day(today: date) -> date:
    # Weekday-only approximation (no market-holiday calendar) -- close enough
    # for "is the price series stale", where the real failure mode this
    # guards against is a multi-day-old cache, not a single missed holiday.
    offset = {0: 3, 6: 2}.get(today.weekday(), 1)
    return today - timedelta(days=offset)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _age_days(last: datetime | date | None, now: datetime) -> float | None:
    ts = _as_datetime(last)
    if ts is None:
        return None
    return (now - ts).total_seconds() / 86400


def _prices_domain(connection: duckdb.DuckDBPyConnection, ticker_id: int, today: date) -> dict[str, Any]:
    row = connection.execute(
        "SELECT MAX(record_date) FROM historical_records WHERE ticker_id = ?", [ticker_id]
    ).fetchone()
    last = row[0] if row else None
    boundary = _last_business_day(today)
    stale = last is None or last < boundary
    return {"last": last, "boundary": boundary, "stale": stale, "refreshable": True}


def _fetched_at_domain(
    connection: duckdb.DuckDBPyConnection, table: str, ticker_id: int, cadence_days: int, now: datetime
) -> dict[str, Any]:
    row = connection.execute(
        f"SELECT MAX(fetched_at) FROM {table} WHERE ticker_id = ?", [ticker_id]
    ).fetchone()
    last = row[0] if row else None
    age = _age_days(last, now)
    stale = age is None or age >= cadence_days
    return {"last": last, "age_days": age, "cadence_days": cadence_days, "stale": stale, "refreshable": True}


def _classification_domain(
    connection: duckdb.DuckDBPyConnection, ticker_id: int, now: datetime
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT generated_at FROM portfolio_classifications WHERE ticker_id = ?", [ticker_id]
    ).fetchone()
    last = row[0] if row else None
    age = _age_days(last, now)
    stale = age is None or age >= CLASSIFICATION_CADENCE_DAYS
    return {
        "last": last, "age_days": age, "cadence_days": CLASSIFICATION_CADENCE_DAYS,
        "stale": stale, "refreshable": False,
        "note": "portfolio-wide -- run classify-portfolio, not this skill" if stale else None,
    }


def _positions_domain(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT ledger_fingerprint FROM position_engine_meta WHERE component = ?",
        [FINGERPRINT_COMPONENT],
    ).fetchone()
    stored = row[0] if row else None
    current = compute_fingerprint(connection)
    stale = stored != current
    return {
        "stale": stale, "refreshable": False,
        "note": "portfolio-wide -- run `python src/app.py recompute-positions`, not this skill" if stale else None,
    }


def _earnings_window(connection: duckdb.DuckDBPyConnection, ticker_id: int, today: date) -> bool:
    rows = connection.execute(
        "SELECT report_date FROM earnings_events WHERE ticker_id = ?", [ticker_id]
    ).fetchall()
    return any(
        row[0] is not None and abs((row[0] - today).days) <= EARNINGS_WINDOW_DAYS for row in rows
    )


def compute_freshness(
    connection: duckdb.DuckDBPyConnection,
    ticker: str,
    *,
    force: bool = False,
    run_date: date | None = None,
) -> dict[str, Any]:
    """Read-only per-domain freshness verdict for one ticker.

    `connection` must already be open (read-only) -- this function never
    opens or closes one, so callers can share a single connection across
    several tickers in one `--mode gate` invocation.
    """
    today = run_date or date.today()
    now = datetime.combine(today, datetime.min.time())

    identity = db_resources.resolve_ticker(connection, ticker)
    if identity is None:
        return {
            "ticker": ticker.upper(), "resolved": False, "owned": False,
            "gaps": [f"'{ticker}' not found in tickers table"],
        }

    ticker_id = identity["ticker_id"]
    provider_symbol = identity["provider_symbol"]
    first_owned = db_resources.resolve_ownership(connection, ticker_id)
    owned = first_owned is not None

    gaps: list[str] = []
    if provider_symbol is None:
        gaps.append("no verified Yahoo provider_symbol -- live top-up and DB refresh unavailable")
    if not owned:
        gaps.append("ticker is not owned (no transactions) -- market_data sync is portfolio-scoped to owned tickers, so DB refresh is unavailable; falling back to a live-only bundle")

    domains: dict[str, Any] = {}
    can_refresh = owned and provider_symbol is not None
    if can_refresh:
        domains["prices"] = _prices_domain(connection, ticker_id, today)
        domains["earnings"] = _fetched_at_domain(connection, "earnings_events", ticker_id, EARNINGS_CADENCE_DAYS, now)
        domains["dividends"] = _fetched_at_domain(connection, "dividend_events", ticker_id, DIVIDENDS_CADENCE_DAYS, now)
        domains["financials"] = _fetched_at_domain(connection, "financial_snapshots", ticker_id, FINANCIALS_CADENCE_DAYS, now)
    domains["classification"] = _classification_domain(connection, ticker_id, now)
    domains["positions"] = _positions_domain(connection)

    earnings_window = can_refresh and _earnings_window(connection, ticker_id, today)
    if earnings_window:
        for domain in ("prices", "earnings", "financials"):
            domains[domain]["stale"] = True
            domains[domain]["forced_by"] = "earnings_window"

    due_domains = [
        name for name in REFRESHABLE_DOMAINS
        if name in domains and (force or domains[name]["stale"])
    ]

    return {
        "ticker": identity["ticker_symbol"],
        "resolved": True,
        "ticker_id": ticker_id,
        "provider_symbol": provider_symbol,
        "asset_class": "etf" if identity["security_type"] == "etf" else "stock",
        "owned": owned,
        "first_owned_date": first_owned,
        "earnings_window": earnings_window,
        "domains": domains,
        "due_domains": due_domains,
        "can_refresh": can_refresh,
        "gaps": gaps,
    }


def refresh_domains(
    due_by_domain: dict[str, list[str]],
    db_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    """The sole write path: batch-refresh each due domain for the symbols that need it.

    `due_by_domain` maps domain name -> list of verified Yahoo provider
    symbols needing that domain refreshed. Each domain is refreshed in one
    batched call (market_data's sync functions accept multiple symbols), so
    an N-ticker `--mode refresh` invocation still does exactly one sync call
    per due domain, not N.
    """
    results: dict[str, Any] = {}
    try:
        prices_symbols = due_by_domain.get("prices") or []
        if prices_symbols:
            result = market_data.sync_market_data(db_path, symbols=prices_symbols)
            results["prices"] = {"tickers": result.tickers, "rows": result.rows, "error": result.error}

        earnings_symbols = due_by_domain.get("earnings") or []
        if earnings_symbols:
            result = market_data.sync_earnings_dividends(
                db_path, symbols=earnings_symbols, skip_dividends=True
            )
            results["earnings"] = {"tickers": result.tickers, "rows": result.earnings_rows, "error": result.error}

        dividend_symbols = due_by_domain.get("dividends") or []
        if dividend_symbols:
            result = market_data.sync_earnings_dividends(
                db_path, symbols=dividend_symbols, skip_earnings=True
            )
            results["dividends"] = {"tickers": result.tickers, "rows": result.dividend_rows, "error": result.error}

        financial_symbols = due_by_domain.get("financials") or []
        if financial_symbols:
            result = market_data.sync_financial_snapshots(db_path, symbols=financial_symbols)
            results["financials"] = {"tickers": result.tickers, "rows": result.snapshot_rows, "error": result.error}
    finally:
        close_connection()
    return results

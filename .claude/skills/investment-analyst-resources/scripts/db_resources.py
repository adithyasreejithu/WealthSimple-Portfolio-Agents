"""Read-only DuckDB access for the investment-analyst-resources skill.

Opens its own `duckdb.connect(path, read_only=True)` connection -- never
`database.get_shared_connection`, which is read-write and would take the
exclusive file lock every parallel `--mode read` invocation depends on not
holding (see docs/plans/investment-analyst-resources-skill.md's concurrency
model). Fixed queries only: no arbitrary SQL, no caller-supplied database
path beyond the configured one, mirroring the boundary
`read-portfolio-classification-data` already establishes.

Position market value and portfolio weight are computed live, read-only,
via `position_engine.read_live_position_values` + `position_engine.
latest_fx_rate` -- the same query and FX helper `analytics.py`'s write path
uses (after its own `ensure_positions_fresh` self-heal), so there is one
implementation of "quantity x latest price x FX -> value" shared by both
paths, not two. Only `role`/`account_type`/`confidence`-type fields, which
don't need daily freshness, still come from the already-generated
`portfolio-classification.json` export. Sector/group allocation,
look-through exposure, and ETF overlap remain out of scope -- those stay
inside `analytics.py`, unused here.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import duckdb

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from config import DATABASE_PATH  # noqa: E402
from database import REQUIRED_TABLES, SCHEMA_COMPONENT  # noqa: E402
from config import DATABASE_SCHEMA_VERSION  # noqa: E402
from position_engine import latest_fx_rate, read_live_position_values  # noqa: E402

DEFAULT_CLASSIFICATION_JSON = ROOT / "exports" / "portfolio-classification" / "portfolio-classification.json"


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


def _rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def connect_read_only(db_path: str | Path = DATABASE_PATH) -> duckdb.DuckDBPyConnection:
    """Open a read-only connection, translating DuckDB's lock error into an
    actionable message (this is the concurrency model's whole point -- a
    `--mode refresh` holding the write lock must not surface as a raw
    DuckDB IO exception to a parallel `--mode read` caller)."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise DatabaseNotReady(f"Configured database does not exist: {path}")
    try:
        connection = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise DatabaseNotReady(
            "Could not open the database read-only -- another process is likely "
            "holding the write lock (a --mode refresh in progress). Retry shortly, "
            f"or use --no-refresh to skip refreshing this run. ({exc})"
        ) from exc
    return connection


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


def resolve_ticker(connection: duckdb.DuckDBPyConnection, ticker: str) -> dict[str, Any] | None:
    """Resolve a pipeline ticker symbol to its identity + verified Yahoo symbol.

    No fuzzy resolution -- only a verified `ticker_provider_mappings` row is
    trusted, matching `fetch-stock-research-data`'s rule. When a symbol
    exists on more than one exchange (not expected in this personal
    portfolio's schema, but the `UNIQUE(ticker_symbol, exchange)` constraint
    permits it), the owned row wins, then lowest ticker_id, for determinism.
    """
    row = connection.execute(
        """
        SELECT t.ticker_id, t.ticker_symbol, t.exchange, t.currency, t.financial_currency,
               t.security_name, t.security_type, m.provider_symbol,
               (s.ticker_id IS NOT NULL AND s.quantity <> 0) AS currently_held
        FROM tickers t
        LEFT JOIN ticker_provider_mappings m
            ON m.ticker_id = t.ticker_id AND m.provider = 'yahoo' AND m.verification_status = 'verified'
        LEFT JOIN position_snapshots s ON s.ticker_id = t.ticker_id
        WHERE UPPER(t.ticker_symbol) = UPPER(?)
        ORDER BY currently_held DESC, t.ticker_id ASC
        LIMIT 1
        """,
        [ticker],
    ).fetchone()
    if row is None:
        return None
    columns = ["ticker_id", "ticker_symbol", "exchange", "currency", "financial_currency",
               "security_name", "security_type", "provider_symbol", "currently_held"]
    return dict(zip(columns, row))


def resolve_ownership(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> date | None:
    """Return the first-owned date for a ticker, or None if never owned.

    Mirrors `market_data.get_market_targets`'s `owned_dates` CTE, but against
    a read-only connection -- `get_market_targets` itself opens the
    read-write shared connection and must not be called from the read path.
    """
    row = connection.execute(
        """
        SELECT MIN(d) FROM (
            SELECT transaction_date AS d FROM transactions WHERE ticker_id = ?
            UNION ALL
            SELECT transaction_date FROM email_transactions WHERE ticker_id = ?
            UNION ALL
            SELECT transaction_date FROM activities WHERE ticker_id = ?
        )
        """,
        [ticker_id, ticker_id, ticker_id],
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def read_position(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT quantity, book_value_cad, book_value_mkt, realized_gain_cad,
               provisional_quantity, data_quality_flags, computed_at
        FROM position_snapshots WHERE ticker_id = ?
        """,
        [ticker_id],
    ).fetchone()
    if row is None:
        return None
    flags_json = row[5]
    return {
        "quantity": row[0],
        "book_value_cad": row[1],
        "book_value_mkt": row[2],
        "realized_gain_cad": row[3],
        "provisional_quantity": row[4],
        "data_quality_flags": json.loads(flags_json) if flags_json else [],
        "computed_at": row[6],
        "has_provisional_activity": bool(row[4]),
    }


def read_ledger_summary(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT MIN(CASE WHEN event_type = 'BUY' THEN event_date END),
               MAX(CASE WHEN event_type = 'BUY' THEN event_date END),
               COUNT(*) FILTER (WHERE event_type = 'BUY'),
               COUNT(*) FILTER (WHERE event_type = 'SELL')
        FROM position_ledger WHERE ticker_id = ?
        """,
        [ticker_id],
    ).fetchone()
    return {
        "first_purchase_date": row[0],
        "latest_purchase_date": row[1],
        "number_of_buys": row[2] or 0,
        "number_of_sells": row[3] or 0,
    }


def read_ledger_events(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> list[dict[str, Any]]:
    """Full per-trade ledger, for the on-disk bundle only -- never surfaced in the digest."""
    cursor = connection.execute(
        """
        SELECT event_date, event_type, source, quantity_delta, cost_cad, proceeds_cad,
               fx_rate, running_quantity, running_book_cad, realized_gain_cad
        FROM position_ledger WHERE ticker_id = ? ORDER BY event_date, ledger_id
        """,
        [ticker_id],
    )
    return _rows_as_dicts(cursor)


def read_price_history(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> dict[str, Any]:
    cursor = connection.execute(
        """
        SELECT record_date, open, high, low, close, adjusted_close, volume
        FROM historical_records WHERE ticker_id = ? ORDER BY record_date
        """,
        [ticker_id],
    )
    rows = _rows_as_dicts(cursor)
    if not rows:
        return {"rows": [], "count": 0, "start": None, "end": None, "latest_close": None,
                "period_return_pct": None, "week52_low": None, "week52_high": None}

    closes = [float(r["close"]) for r in rows]
    latest_close = closes[-1]
    first_close = closes[0]
    cutoff = rows[-1]["record_date"] - timedelta(days=365)
    trailing = [float(r["close"]) for r in rows if r["record_date"] >= cutoff]
    return {
        "rows": rows,
        "count": len(rows),
        "start": rows[0]["record_date"],
        "end": rows[-1]["record_date"],
        "latest_close": latest_close,
        "period_return_pct": ((latest_close - first_close) / first_close * 100) if first_close else None,
        "week52_low": min(trailing) if trailing else None,
        "week52_high": max(trailing) if trailing else None,
    }


def read_financial_snapshots(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT period_end_date, revenue, net_income, eps, gross_margin, operating_margin,
               debt_to_equity, current_ratio, free_cash_flow, extra, fetched_at
        FROM financial_snapshots WHERE ticker_id = ? ORDER BY period_end_date
        """,
        [ticker_id],
    )
    rows = _rows_as_dicts(cursor)
    for row in rows:
        raw_extra = row.get("extra")
        row["extra"] = json.loads(raw_extra) if raw_extra else {}
    return rows


def read_earnings_events(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT report_date, period, eps_estimate, eps_actual, revenue_estimate,
               revenue_actual, surprise_pct, fetched_at
        FROM earnings_events WHERE ticker_id = ? ORDER BY report_date
        """,
        [ticker_id],
    )
    return _rows_as_dicts(cursor)


def read_dividend_events(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT ex_dividend_date, pay_date, declared_amount, frequency, fetched_at
        FROM dividend_events WHERE ticker_id = ? ORDER BY ex_dividend_date
        """,
        [ticker_id],
    )
    return _rows_as_dicts(cursor)


def read_dividends_received(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COALESCE(SUM(credit), 0), COUNT(*), MAX(transaction_date)
        FROM transactions WHERE ticker_id = ? AND UPPER(transaction_type) = 'DIV'
        """,
        [ticker_id],
    ).fetchone()
    return {"total_received_cad": row[0], "payment_count": row[1], "last_payment_date": row[2]}


def read_stock_details(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT sector, industry FROM stock_details WHERE ticker_id = ?", [ticker_id]
    ).fetchone()
    if row is None:
        return None
    return {"sector": row[0], "industry": row[1]}


def read_etf_details(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT fund_family, yield, expense_ratio, aum, nav, top_holdings, sector_weights
        FROM etf_details WHERE ticker_id = ?
        """,
        [ticker_id],
    ).fetchone()
    if row is None:
        return None
    top_holdings = json.loads(row[5]) if row[5] else None
    sector_weights = json.loads(row[6]) if row[6] else None
    return {
        "fund_family": row[0], "yield": row[1], "expense_ratio": row[2], "aum": row[3],
        "nav": row[4], "top_holdings": top_holdings, "sector_weights": sector_weights,
    }


def read_classification(connection: duckdb.DuckDBPyConnection, ticker_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT primary_group, secondary_tags, confidence, reasoning, evidence_used,
               missing_data, review_needed, generated_at
        FROM portfolio_classifications WHERE ticker_id = ?
        """,
        [ticker_id],
    ).fetchone()
    if row is None:
        return None
    return {
        "primary_group": row[0],
        "secondary_tags": json.loads(row[1]) if row[1] else [],
        "confidence": row[2],
        "reasoning": row[3],
        "evidence_used": json.loads(row[4]) if row[4] else [],
        "missing_data": json.loads(row[5]) if row[5] else [],
        "review_needed": row[6],
        "generated_at": row[7],
    }


def read_live_market_value(
    connection: duckdb.DuckDBPyConnection, ticker_id: int, *, live_quote: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Current market value for one currently-held position, computed live
    and read-only (quantity x latest price x FX), plus book cost and the
    unrealized gain/loss between the two. Mirrors
    `analytics.py::_get_net_positions`'s math via the shared
    `position_engine` helpers -- see module docstring. Returns None when the
    ticker isn't currently held (no position row, or quantity <= 0).

    `live_quote` (from `investment_analyst_resources._fetch_latest_quote`),
    when given, replaces `historical_records`'s last stored close as the
    price for *this* ticker -- the DB close can be a full trading day stale.
    `price_source` on the returned dict names which one was actually used.

    Book cost (`cost_basis_cad`) is `position_snapshots.book_value_cad`,
    already returned by `read_live_position_values` -- market value and cost
    basis were previously computed/reported in different call sites even
    though the query underneath already carried both; this just stops
    discarding the cost-basis half.
    """
    rows = read_live_position_values(connection, ticker_ids=[ticker_id])
    if not rows:
        return None
    row = rows[0]
    quantity = Decimal(str(row["quantity"]))
    if quantity <= 0:
        return None
    if live_quote is not None:
        last_price = Decimal(str(live_quote["price"]))
        last_price_date = live_quote["as_of"]
        price_source = "live_quote"
    else:
        last_price = Decimal(str(row["last_price"])) if row["last_price"] is not None else None
        last_price_date = row["last_price_date"]
        price_source = "db_close"
    fx_rate, fx_flag = latest_fx_rate(connection, row["currency"])
    market_value_mkt = quantity * last_price if last_price is not None else Decimal("0")
    market_value_cad = market_value_mkt * fx_rate

    cost_basis_cad = Decimal(str(row["book_value_cad"])) if row["book_value_cad"] is not None else None
    unrealized_gain_cad = (market_value_cad - cost_basis_cad) if cost_basis_cad is not None else None
    unrealized_gain_pct = (
        float(unrealized_gain_cad / cost_basis_cad * 100)
        if unrealized_gain_cad is not None and cost_basis_cad
        else None
    )

    return {
        "market_value_cad": float(market_value_cad),
        "market_value_mkt": float(market_value_mkt),
        "cost_basis_cad": float(cost_basis_cad) if cost_basis_cad is not None else None,
        "unrealized_gain_cad": float(unrealized_gain_cad) if unrealized_gain_cad is not None else None,
        "unrealized_gain_pct": unrealized_gain_pct,
        "last_price": float(last_price) if last_price is not None else None,
        "last_price_date": last_price_date,
        "fx_rate": float(fx_rate),
        "fx_flag": fx_flag,
        "price_source": price_source,
    }


def read_live_portfolio_weight(
    connection: duckdb.DuckDBPyConnection, ticker_id: int, *, live_quote: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Portfolio weight for one position, computed live from every
    currently-held position's market value. Matches the dashboard's own
    formula (`dashboard/api/main.py`: `holding.market_value / total_value`,
    no cash in the denominator) so the two surfaces agree. Returns None when
    the ticker isn't currently held.

    `live_quote`, when given, overrides the price used for `ticker_id`'s own
    row only -- every other position in the `total` denominator keeps its
    own DB close (fetching a live quote for the whole portfolio is out of
    scope for a single-ticker run).
    """
    fx_cache: dict[str, Decimal] = {}
    total = Decimal("0")
    this_value: Decimal | None = None
    for row in read_live_position_values(connection):
        quantity = Decimal(str(row["quantity"]))
        if quantity <= 0:
            continue
        is_target = row["ticker_id"] == ticker_id
        if is_target and live_quote is not None:
            last_price = Decimal(str(live_quote["price"]))
        else:
            last_price = Decimal(str(row["last_price"])) if row["last_price"] is not None else None
        currency = row["currency"]
        if currency not in fx_cache:
            fx_cache[currency], _flag = latest_fx_rate(connection, currency)
        market_value_mkt = quantity * last_price if last_price is not None else Decimal("0")
        market_value_cad = market_value_mkt * fx_cache[currency]
        total += market_value_cad
        if is_target:
            this_value = market_value_cad
    if this_value is None:
        return None
    weight_pct = float(this_value / total * 100) if total > 0 else 0.0
    return {
        "weight_pct": weight_pct,
        "position_market_value": float(this_value),
        "total_portfolio_value": float(total),
        "price_source": "live_quote" if live_quote is not None else "db_close",
    }


def read_portfolio_context(
    connection: duckdb.DuckDBPyConnection,
    ticker_id: int,
    ticker: str,
    classification_json: str | Path = DEFAULT_CLASSIFICATION_JSON,
    *,
    live_quote: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Role/account-type from the classification export (stable, no daily
    freshness need) merged with market value/weight computed live and
    read-only (see module docstring). Returns None (a gap, not an error)
    only when the ticker is neither currently held nor present in the
    classification export."""
    role = None
    account_type = None
    classification_generated_at = None
    path = Path(classification_json)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if payload is not None:
            classification_generated_at = payload.get("generated_at")
            for holding in payload.get("holdings") or []:
                if str(holding.get("ticker", "")).upper() == ticker.upper():
                    fields = holding.get("fields") or {}
                    role = holding.get("primary_group")
                    account_type = fields.get("account_type")
                    break

    weight_info = read_live_portfolio_weight(connection, ticker_id, live_quote=live_quote)
    value_info = read_live_market_value(connection, ticker_id, live_quote=live_quote)

    if role is None and weight_info is None and value_info is None:
        return None

    price_source = None
    if value_info:
        price_source = value_info["price_source"]
    elif weight_info:
        price_source = weight_info["price_source"]

    return {
        "role": role,
        "weight_pct": weight_info["weight_pct"] if weight_info else None,
        "position_market_value": value_info["market_value_cad"] if value_info else None,
        "cost_basis_cad": value_info["cost_basis_cad"] if value_info else None,
        "unrealized_gain_cad": value_info["unrealized_gain_cad"] if value_info else None,
        "unrealized_gain_pct": value_info["unrealized_gain_pct"] if value_info else None,
        "account_type": account_type,
        "classification_generated_at": classification_generated_at,
        "price_source": price_source,
    }


def read_ticker_bundle(
    connection: duckdb.DuckDBPyConnection,
    ticker_id: int,
    ticker: str,
    *,
    classification_json: str | Path = DEFAULT_CLASSIFICATION_JSON,
    live_quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble every DB-sourced section for one ticker via one read-only connection."""
    return {
        "position": read_position(connection, ticker_id),
        "ledger_summary": read_ledger_summary(connection, ticker_id),
        "ledger": read_ledger_events(connection, ticker_id),
        "prices": read_price_history(connection, ticker_id),
        "financials": read_financial_snapshots(connection, ticker_id),
        "earnings": read_earnings_events(connection, ticker_id),
        "dividends": {
            "declared": read_dividend_events(connection, ticker_id),
            "received": read_dividends_received(connection, ticker_id),
        },
        "stock_details": read_stock_details(connection, ticker_id),
        "etf_details": read_etf_details(connection, ticker_id),
        "classification": read_classification(connection, ticker_id),
        "portfolio_context": read_portfolio_context(
            connection, ticker_id, ticker, classification_json, live_quote=live_quote
        ),
    }


def to_json_safe(value: Any) -> Any:
    return _json_value(value)

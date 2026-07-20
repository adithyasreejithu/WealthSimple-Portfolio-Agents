"""Read-only portfolio analytics helpers built on the normalized DuckDB schema."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yaml

from config import (
    DATABASE_PATH,
    DEFAULT_BENCHMARK_SYMBOL,
    FX_PAIR_SYMBOL,
    POLICY_FILE,
    SINGLE_NAME_MAX_WEIGHT,
    STALE_PRICE_MAX_AGE_DAYS,
    SUSPICIOUS_UNREALIZED_GAIN_THRESHOLD,
    TREND_BENCHMARKS,
    WEALTHSIMPLE_FX_FEE_RATE,
)
from database import get_shared_connection
from position_engine import _build_fx_series, _resolve_fx, ensure_positions_fresh, latest_fx_rate
from portfolio_metrics import (
    calculate_adjusted_daily_returns,
    calculate_adjusted_sharpe_ratio,
    calculate_adjusted_volatility,
    calculate_benchmark_stats,
    calculate_concentration,
    calculate_drawdown_details,
    calculate_position_weights,
    calculate_rebalance_drift,
    calculate_sortino_ratio,
    calculate_twr_total_return,
    calculate_unrealized_gain_percent,
    calculate_weighted_mer,
    build_wealth_index,
    estimate_wealthsimple_fx_fee_cad,
)
from system_logger import get_logger


logger = get_logger(__name__)


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))


@dataclass(frozen=True)
class Holding:
    """A current position, valued in CAD unless a `_mkt` field says otherwise.

    `cost_basis` and `market_value` are always CAD (average-cost book value
    and quantity * price * fx respectively), so allocation weights,
    concentration, and portfolio totals can sum across USD- and CAD-listed
    holdings without a currency-mixing bug. `market_value_mkt` and
    `unrealized_mkt` carry the same figures in the ticker's listing currency,
    matching the units a broker holdings export reports unrealized P/L in.
    """

    ticker_id: int
    ticker_symbol: str
    exchange: str
    security_name: str
    security_type: str
    quantity: Decimal
    cost_basis: Decimal
    market_value: Decimal
    currency: str = ""
    last_price: Decimal | None = None
    last_price_date: date | None = None
    provisional_quantity: Decimal = Decimal("0")
    has_provisional_activity: bool = False
    market_value_mkt: Decimal = Decimal("0")
    cost_basis_mkt: Decimal = Decimal("0")
    unrealized_mkt: Decimal = Decimal("0")
    realized_gain_cad: Decimal = Decimal("0")
    data_quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CashSummary:
    balance: Decimal
    source: str


@dataclass(frozen=True)
class PortfolioSummary:
    holdings: list[Holding]
    cash: CashSummary
    portfolio_value: Decimal


def _get_net_positions(db_path: str = DATABASE_PATH) -> list[Holding]:
    """Reconstruct every position needing attention, positive or flagged, from
    `position_snapshots` -- the average-cost engine's output (see
    `position_engine.py` and `docs/architecture/ingestion_and_reconciliation.md`).

    Includes zero-quantity positions the engine flagged (e.g. an oversell
    clamped to zero rather than left negative -- see `position_engine.py`'s
    SELL handling) so a "sold more than bought" data error stays visible to
    `get_excluded_positions`/`build_data_quality` instead of silently
    disappearing once quantity reaches exactly zero. `cost_basis`/`market_value`
    are CAD; `market_value_mkt`/`unrealized_mkt` are in the ticker's own
    listing currency.
    """
    connection = get_shared_connection(db_path)
    ensure_positions_fresh(connection)

    rows = connection.execute(
        """
        WITH latest_prices AS (
            SELECT DISTINCT ON (ticker_id)
                ticker_id,
                record_date,
                close
            FROM historical_records
            ORDER BY ticker_id, record_date DESC
        )
        SELECT
            t.ticker_id,
            t.ticker_symbol,
            t.exchange,
            t.security_name,
            t.security_type,
            t.currency,
            s.quantity,
            s.book_value_cad,
            s.book_value_mkt,
            s.provisional_quantity,
            s.realized_gain_cad,
            s.data_quality_flags,
            lp.record_date,
            lp.close
        FROM position_snapshots s
        JOIN tickers t ON t.ticker_id = s.ticker_id
        LEFT JOIN latest_prices lp ON lp.ticker_id = s.ticker_id
        WHERE s.quantity <> 0 OR s.data_quality_flags IS NOT NULL
        ORDER BY t.ticker_symbol, t.exchange
        """
    ).fetchall()

    fx_cache: dict[str, Decimal] = {}
    holdings: list[Holding] = []
    for row in rows:
        (
            ticker_id, symbol, exchange, name, security_type, currency,
            quantity, book_cad, book_mkt, provisional, realized_cad, flags_json,
            price_date, close,
        ) = row
        quantity_value = _decimal(quantity)
        last_price = _decimal(close) if close is not None else None
        if currency not in fx_cache:
            fx_cache[currency], _flag = latest_fx_rate(connection, currency)
        fx_rate = fx_cache[currency]

        market_value_mkt = quantity_value * last_price if last_price is not None else Decimal("0")
        market_value_cad = market_value_mkt * fx_rate
        cost_basis_cad = _decimal(book_cad)
        cost_basis_mkt = _decimal(book_mkt)
        flags = tuple(json.loads(flags_json)) if flags_json else ()

        holdings.append(
            Holding(
                ticker_id=int(ticker_id),
                ticker_symbol=symbol,
                exchange=exchange,
                security_name=name,
                security_type=security_type,
                currency=currency,
                quantity=quantity_value,
                cost_basis=cost_basis_cad,
                market_value=market_value_cad,
                last_price=last_price,
                last_price_date=_date(price_date) if price_date is not None else None,
                provisional_quantity=_decimal(provisional),
                has_provisional_activity=_decimal(provisional) != 0,
                market_value_mkt=market_value_mkt,
                cost_basis_mkt=cost_basis_mkt,
                unrealized_mkt=market_value_mkt - cost_basis_mkt,
                realized_gain_cad=_decimal(realized_cad),
                data_quality_flags=flags,
            )
        )
    return holdings


def get_holdings(db_path: str = DATABASE_PATH) -> list[Holding]:
    """Return only currently-held positions (positive net quantity)."""
    return [holding for holding in _get_net_positions(db_path) if holding.quantity > 0]


def get_excluded_positions(db_path: str = DATABASE_PATH) -> list[Holding]:
    """Return positions excluded from current-holdings analytics as a data error.

    A negative quantity means recorded sells exceed recorded buys for that
    ticker; an `oversell_clamped` flag means the position engine clamped an
    oversell at zero rather than let it go negative (see `position_engine.py`).
    Either points at a reconciliation or data-entry issue rather than a real
    short position, so these are surfaced only in data-quality reporting.
    """
    return [
        holding
        for holding in _get_net_positions(db_path)
        if holding.quantity < 0 or "oversell_clamped" in holding.data_quality_flags
    ]


def get_cash_summary(db_path: str = DATABASE_PATH) -> CashSummary:
    """Return the current cash balance, anchored to the latest statement and
    rolled forward with any CSV-sourced activity since then.

    `statement_balances` captures every statement line's trailing balance
    regardless of whether that line resolved to a ticker (unlike
    `cash_transactions.balance`, which only ever sees cash-only lines and so
    understates the balance whenever a BUY/SELL/DIV row is the statement's
    last line -- see `docs/architecture/ingestion_and_reconciliation.md`).
    Statement PDFs typically lag the activities CSV export by weeks, so the
    latest statement balance alone is stale between statements; `activities`
    (the deduplicated, typed CSV table -- see `data_sorter.py`) already
    reports every cash-affecting row (trades, dividends, interest, tax
    withholding) in CAD regardless of the security's own listing currency,
    so summing `net_cash_amount` for rows strictly after the anchor date and
    adding it to the anchor balance gives a currently-accurate figure
    without waiting for the next statement.
    """
    connection = get_shared_connection(db_path)
    row = connection.execute(
        """
        SELECT transaction_date, balance
        FROM statement_balances
        ORDER BY transaction_date DESC, statement_balance_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is not None:
        anchor_date, anchor_balance = row
        rollforward = _decimal(
            connection.execute(
                "SELECT COALESCE(SUM(net_cash_amount), 0) FROM activities WHERE transaction_date > ?",
                [anchor_date],
            ).fetchone()[0]
        )
        source = "explicit_balance" if rollforward == 0 else "explicit_balance_rolled_forward"
        return CashSummary(balance=_decimal(anchor_balance) + rollforward, source=source)

    # Fall back to net cash flow when the source set does not store a direct balance.
    row = connection.execute(
        """
        SELECT COALESCE(SUM(COALESCE(credit, 0) - COALESCE(debit, 0)), 0)
        FROM cash_transactions
        """
    ).fetchone()
    return CashSummary(balance=_decimal(row[0]), source="net_cash_flow")


def get_portfolio_summary(db_path: str = DATABASE_PATH) -> PortfolioSummary:
    holdings = get_holdings(db_path)
    cash = get_cash_summary(db_path)
    # Portfolio value is the cash balance plus the current market value of all holdings.
    portfolio_value = cash.balance + sum((holding.market_value for holding in holdings), start=Decimal("0"))
    return PortfolioSummary(holdings=holdings, cash=cash, portfolio_value=portfolio_value)


def get_position(ticker_id: int, db_path: str = DATABASE_PATH) -> Holding:
    # Return a zero-valued placeholder so callers do not need special-case missing positions.
    for holding in get_holdings(db_path):
        if holding.ticker_id == ticker_id:
            return holding
    return Holding(
        ticker_id=ticker_id,
        ticker_symbol="",
        exchange="",
        security_name="",
        security_type="",
        quantity=Decimal("0"),
        cost_basis=Decimal("0"),
        market_value=Decimal("0"),
        last_price=None,
        last_price_date=None,
    )


def _load_json_column(value: Any, default: Any) -> Any:
    """Parse a DuckDB JSON column (returned as a string) into a Python value.

    Classification columns are stored as JSON text; NULL/empty columns fall
    back to the supplied default so callers always see a concrete structure.
    """
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# Nested keys inside the `fields` JSON that the classifier stores as
# JSON-encoded strings (double-encoded), so a single json.loads on the column
# leaves them as text. Parse them so callers get real structures.
_CLASSIFICATION_NESTED_JSON_FIELDS = ("sector_weights", "top_holdings")


def _normalize_classification_fields(fields: Any) -> dict[str, Any]:
    parsed = _load_json_column(fields, {})
    if not isinstance(parsed, dict):
        return {}
    for key in _CLASSIFICATION_NESTED_JSON_FIELDS:
        if isinstance(parsed.get(key), str):
            parsed[key] = _load_json_column(parsed[key], None)
    return parsed


def get_classification_details(db_path: str = DATABASE_PATH) -> dict[str, Any]:
    """Return per-holding portfolio classification records joined with ticker identity.

    Reads the `portfolio_classifications` table the classifier writes (one row
    per ticker) and joins `tickers` for symbol/name/type. JSON columns are
    parsed into real structures. This is the only read path that exposes
    per-holding classification detail (group, confidence, reasoning, evidence,
    review flag, enriched fields) -- allocation reporting elsewhere only uses
    the aggregate `primary_group`/geography.
    """
    connection = get_shared_connection(db_path)
    rows = connection.execute(
        """
        SELECT
            c.ticker_id,
            t.ticker_symbol,
            t.security_name,
            t.security_type,
            c.primary_group,
            c.secondary_tags,
            c.confidence,
            c.reasoning,
            c.evidence_used,
            c.missing_data,
            c.review_needed,
            c.fields,
            c.field_provenance,
            c.generated_at
        FROM portfolio_classifications c
        JOIN tickers t ON t.ticker_id = c.ticker_id
        ORDER BY t.ticker_symbol
        """
    ).fetchall()

    classifications: list[dict[str, Any]] = []
    review_count = 0
    latest_generated: datetime | None = None
    for row in rows:
        (
            ticker_id, symbol, name, security_type, primary_group,
            secondary_tags, confidence, reasoning, evidence_used, missing_data,
            review_needed, fields, field_provenance, generated_at,
        ) = row
        review_flag = bool(review_needed)
        if review_flag:
            review_count += 1
        if isinstance(generated_at, datetime) and (latest_generated is None or generated_at > latest_generated):
            latest_generated = generated_at
        classifications.append(
            {
                "ticker_id": int(ticker_id),
                "ticker_symbol": symbol,
                "security_name": name,
                "security_type": security_type,
                "primary_group": primary_group,
                "secondary_tags": _load_json_column(secondary_tags, []),
                "confidence": confidence,
                "reasoning": reasoning,
                "evidence_used": _load_json_column(evidence_used, []),
                "missing_data": _load_json_column(missing_data, []),
                "review_needed": review_flag,
                "fields": _normalize_classification_fields(fields),
                "field_provenance": _load_json_column(field_provenance, {}),
                "generated_at": generated_at,
            }
        )

    return {
        "generated_at": latest_generated,
        "count": len(classifications),
        "review_count": review_count,
        "classifications": classifications,
    }


def get_price_history(
    symbol: str,
    db_path: str = DATABASE_PATH,
    *,
    date_from: date | None = None,
) -> dict[str, Any] | None:
    """Return a single ticker's daily close series from `historical_records`.

    Symbol resolution is case-insensitive against `tickers.ticker_symbol`;
    an unknown symbol returns ``None`` so callers can surface a 404. Points
    are ordered oldest-first for direct charting. Open/high/low are omitted --
    the dashboard's line and compare views only need close/adjusted_close.
    """
    connection = get_shared_connection(db_path)
    ticker_row = connection.execute(
        """
        SELECT ticker_id, ticker_symbol, security_name, currency
        FROM tickers
        WHERE UPPER(ticker_symbol) = UPPER(?)
        ORDER BY ticker_id
        LIMIT 1
        """,
        [symbol],
    ).fetchone()
    if ticker_row is None:
        return None
    ticker_id, ticker_symbol, security_name, currency = ticker_row

    params: list[Any] = [ticker_id]
    clause = ""
    if date_from is not None:
        clause = "AND record_date >= ?"
        params.append(date_from)
    price_rows = connection.execute(
        f"""
        SELECT record_date, close, adjusted_close, volume
        FROM historical_records
        WHERE ticker_id = ? {clause}
        ORDER BY record_date
        """,
        params,
    ).fetchall()

    points = [
        {
            "date": _date(record_date).isoformat(),
            "close": float(close),
            "adjusted_close": float(adjusted_close),
            "volume": int(volume),
        }
        for record_date, close, adjusted_close, volume in price_rows
    ]
    return {
        "ticker_id": int(ticker_id),
        "ticker_symbol": ticker_symbol,
        "security_name": security_name,
        "currency": currency,
        "count": len(points),
        "points": points,
    }


def get_historical_portfolio_values(db_path: str = DATABASE_PATH) -> list[dict[str, Any]]:
    """Build a chronological valuation series (securities + cash) as one set-based query.

    Quantities come from `position_ledger`'s running_quantity (the average-cost
    engine's deduplicated, split-adjusted event stream), taking each ticker's
    last event of the day and forward-filling across the date grid; a
    data-error negative position is clamped at zero, matching ``get_holdings``
    excluding negative net positions from current value. Prices are carried
    forward from the latest known close on or before each date and converted
    to CAD using the configured FX pair's close (forward-filled the same way),
    so the series stays in one currency throughout. Cash uses the latest
    explicit statement balance on or before each date, rolled forward with
    CSV-sourced activity since that statement, falling back to cumulative
    net cash flow before any statement balance is known -- mirroring
    `get_cash_summary`.
    """
    connection = get_shared_connection(db_path)
    ensure_positions_fresh(connection)
    rows = connection.execute(
        """
        WITH day_end_state AS (
            SELECT ticker_id, event_date AS d, running_quantity AS qty
            FROM (
                SELECT ticker_id, event_date, running_quantity,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker_id, event_date ORDER BY ledger_id DESC
                       ) AS rn
                FROM position_ledger
            )
            WHERE rn = 1
        ),
        traded_tickers AS (SELECT DISTINCT ticker_id FROM day_end_state),
        fx_history AS (
            SELECT h.record_date AS d, h.close AS rate
            FROM historical_records h
            JOIN tickers t ON t.ticker_id = h.ticker_id
            WHERE t.ticker_symbol = ?
        ),
        value_dates AS (
            SELECT DISTINCT value_date
            FROM (
                SELECT d AS value_date FROM day_end_state
                UNION
                SELECT record_date AS value_date FROM historical_records
                UNION
                SELECT transaction_date AS value_date FROM cash_transactions
                UNION
                SELECT transaction_date AS value_date FROM statement_balances
                UNION
                SELECT transaction_date AS value_date FROM activities
            )
            WHERE value_date IS NOT NULL
        ),
        grid AS (
            SELECT vd.value_date, tt.ticker_id
            FROM value_dates vd CROSS JOIN traded_tickers tt
        ),
        qty_series AS (
            SELECT g.value_date, g.ticker_id,
                   GREATEST(
                       COALESCE(
                           LAST_VALUE(des.qty IGNORE NULLS) OVER (
                               PARTITION BY g.ticker_id ORDER BY g.value_date
                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                           ),
                           0
                       ),
                       0
                   ) AS qty
            FROM grid g
            LEFT JOIN day_end_state des ON des.ticker_id = g.ticker_id AND des.d = g.value_date
        ),
        price_series AS (
            SELECT g.value_date, g.ticker_id,
                   LAST_VALUE(hr.close IGNORE NULLS) OVER (
                       PARTITION BY g.ticker_id ORDER BY g.value_date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS close
            FROM grid g
            LEFT JOIN historical_records hr ON hr.ticker_id = g.ticker_id AND hr.record_date = g.value_date
        ),
        fx_series AS (
            SELECT vd.value_date,
                   LAST_VALUE(fx.rate IGNORE NULLS) OVER (
                       ORDER BY vd.value_date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS rate
            FROM value_dates vd
            LEFT JOIN fx_history fx ON fx.d = vd.value_date
        ),
        securities_series AS (
            SELECT q.value_date,
                   SUM(
                       q.qty * COALESCE(p.close, 0) *
                       CASE WHEN t.currency = 'CAD' THEN 1 ELSE COALESCE(fx.rate, 1) END
                   ) AS securities_value
            FROM qty_series q
            JOIN price_series p ON p.ticker_id = q.ticker_id AND p.value_date = q.value_date
            JOIN tickers t ON t.ticker_id = q.ticker_id
            LEFT JOIN fx_series fx ON fx.value_date = q.value_date
            GROUP BY q.value_date
        ),
        -- Explicit balances come from `statement_balances` (every statement
        -- line, regardless of whether it resolved to a ticker -- see
        -- `database._create_statement_balances_table`), but a statement
        -- typically lags the activities CSV export by weeks. `activities`
        -- (the deduplicated, typed CSV table) reports every cash-affecting
        -- row in CAD, so it rolls each statement anchor forward to stay
        -- current: `adjusted_baseline` folds the anchor's own date out of
        -- the all-time activities running total once
        -- (`balance_on_date - cumulative_activities_flow(anchor_date)`), so
        -- adding the (also all-time) `cumulative_activities_flow(date)` back
        -- at every later date yields exactly
        -- `balance_on_date + sum(activities after anchor_date, through date)`
        -- without re-summing per anchor. `cash_transactions` net flow is
        -- unchanged and is only ever used before the first explicit balance
        -- is known, via the `cumulative_flow` fallback below.
        activities_flow_by_date AS (
            SELECT transaction_date AS d, SUM(COALESCE(net_cash_amount, 0)) AS activities_flow_on_date
            FROM activities
            GROUP BY transaction_date
        ),
        activities_cumulative AS (
            SELECT vd.value_date,
                   SUM(COALESCE(af.activities_flow_on_date, 0)) OVER (
                       ORDER BY vd.value_date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumulative_activities_flow
            FROM value_dates vd
            LEFT JOIN activities_flow_by_date af ON af.d = vd.value_date
        ),
        statement_balance_by_date AS (
            SELECT sb.d,
                   sb.balance_on_date,
                   sb.balance_on_date - ac.cumulative_activities_flow AS adjusted_baseline
            FROM (
                SELECT transaction_date AS d, MAX(balance) AS balance_on_date
                FROM statement_balances
                GROUP BY transaction_date
            ) sb
            JOIN activities_cumulative ac ON ac.value_date = sb.d
        ),
        cash_flow_by_date AS (
            SELECT transaction_date AS d,
                   SUM(COALESCE(credit, 0) - COALESCE(debit, 0)) AS net_flow_on_date
            FROM cash_transactions
            GROUP BY transaction_date
        ),
        cash_by_date AS (
            SELECT
                COALESCE(sb.d, cf.d) AS d,
                sb.adjusted_baseline,
                cf.net_flow_on_date
            FROM statement_balance_by_date sb
            FULL JOIN cash_flow_by_date cf ON cf.d = sb.d
        ),
        cash_series AS (
            SELECT vd.value_date,
                   LAST_VALUE(cb.adjusted_baseline IGNORE NULLS) OVER (
                       ORDER BY vd.value_date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS adjusted_baseline,
                   SUM(COALESCE(cb.net_flow_on_date, 0)) OVER (
                       ORDER BY vd.value_date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumulative_flow,
                   ac.cumulative_activities_flow
            FROM value_dates vd
            LEFT JOIN cash_by_date cb ON cb.d = vd.value_date
            LEFT JOIN activities_cumulative ac ON ac.value_date = vd.value_date
        )
        SELECT vd.value_date,
               COALESCE(sv.securities_value, 0) AS securities_value,
               CASE
                   WHEN cs.adjusted_baseline IS NOT NULL
                       THEN cs.adjusted_baseline + COALESCE(cs.cumulative_activities_flow, 0)
                   ELSE COALESCE(cs.cumulative_flow, 0)
               END AS cash_balance
        FROM value_dates vd
        LEFT JOIN securities_series sv ON sv.value_date = vd.value_date
        LEFT JOIN cash_series cs ON cs.value_date = vd.value_date
        ORDER BY vd.value_date
        """,
        [FX_PAIR_SYMBOL],
    ).fetchall()
    results: list[dict[str, Any]] = []
    for value_date, securities_value, cash_balance in rows:
        securities = _decimal(securities_value)
        cash = _decimal(cash_balance)
        results.append(
            {
                "date": _date(value_date),
                "securities_value": securities,
                "cash_balance": cash,
                "portfolio_value": securities + cash,
            }
        )
    return results


def get_trend_overlay_series(
    db_path: str = DATABASE_PATH,
    historical_values: list[dict[str, Any]] | None = None,
    *,
    flow_source: str = "activities",
) -> dict[str, Any]:
    """Overlay series for the dashboard trend chart, aligned to the valuation date grid.

    Returns cumulative net deposits (contributions minus withdrawals on or
    before each date) plus raw CAD closes for each configured benchmark in
    ``config.TREND_BENCHMARKS`` (forward-filled; ``None`` before the first
    stored close). Benchmark closes are intentionally NOT normalized to the
    portfolio's start -- anchoring to the first visible point is presentation
    math that must re-run when the dashboard's visible range changes, so it
    lives in the frontend.
    """
    if historical_values is None:
        historical_values = get_historical_portfolio_values(db_path)
    if not historical_values:
        return {
            "available": False,
            "reason": "No historical valuation series.",
            "benchmarks": {},
            "points": [],
        }
    connection = get_shared_connection(db_path)
    dates = [_date(row["date"]) for row in historical_values]

    flows = get_external_flow_series(db_path, source=flow_source)
    flow_index = 0
    cumulative = Decimal("0")

    benchmark_closes: dict[str, list[tuple[date, Decimal]]] = {}
    benchmark_meta: dict[str, dict[str, Any]] = {}
    for key, symbol in TREND_BENCHMARKS.items():
        rows = connection.execute(
            """
            SELECT h.record_date, h.close
            FROM historical_records h
            JOIN tickers t ON t.ticker_id = h.ticker_id
            WHERE UPPER(t.ticker_symbol) = UPPER(?)
            ORDER BY h.record_date
            """,
            [symbol],
        ).fetchall()
        benchmark_closes[key] = [(_date(row[0]), _decimal(row[1])) for row in rows]
        benchmark_meta[key] = {
            "symbol": symbol,
            "available": bool(rows),
            **({} if rows else {"reason": f"No stored price history for {symbol}."}),
        }

    close_index: dict[str, int] = {key: 0 for key in TREND_BENCHMARKS}
    last_close: dict[str, Decimal | None] = {key: None for key in TREND_BENCHMARKS}
    points: list[dict[str, Any]] = []
    for value_date in dates:
        while flow_index < len(flows) and _date(flows[flow_index]["date"]) <= value_date:
            cumulative += _decimal(flows[flow_index]["amount"])
            flow_index += 1
        bench_point: dict[str, Decimal | None] = {}
        for key in TREND_BENCHMARKS:
            closes = benchmark_closes[key]
            while close_index[key] < len(closes) and closes[close_index[key]][0] <= value_date:
                last_close[key] = closes[close_index[key]][1]
                close_index[key] += 1
            bench_point[key] = last_close[key]
        points.append(
            {"date": value_date, "net_deposits_cum": cumulative, "benchmarks": bench_point}
        )
    return {"available": True, "benchmarks": benchmark_meta, "points": points}


def _date_filters(
    date_from: date | None, date_to: date | None, column: str
) -> tuple[str, list[date]]:
    """Build a parameterized inclusive date clause for known internal columns."""
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from must be on or before date_to")
    clauses: list[str] = []
    values: list[date] = []
    if date_from:
        clauses.append(f"{column} >= ?")
        values.append(date_from)
    if date_to:
        clauses.append(f"{column} <= ?")
        values.append(date_to)
    return (" AND " + " AND ".join(clauses)) if clauses else "", values


def get_fx_fee_summary(
    db_path: str = DATABASE_PATH,
    *,
    source: str = "statements",
    date_from: date | None = None,
    date_to: date | None = None,
    include_transactions: bool = False,
) -> dict[str, Any]:
    """Estimate the configured Wealthsimple FX fee from statement CAD cash values.

    Statement debits are gross amounts for BUY rows and credits are net proceeds
    for SELL rows, so the fee must be extracted rather than multiplying the
    already fee-inclusive amount directly.
    """
    if source not in {"statements", "exports", "email"}:
        raise ValueError("fx source must be statements, exports, or email")
    if source != "statements":
        _date_filters(date_from, date_to, "transaction_date")
        return {
            "source": source,
            "available": False,
            "reason": "Source does not store both an applied FX rate and a confirmed CAD amount.",
            "fee_rate": WEALTHSIMPLE_FX_FEE_RATE,
            "transaction_count": 0,
            "cad_exposure": Decimal("0"),
            "estimated_fx_fee_cad": Decimal("0"),
        }

    clause, params = _date_filters(date_from, date_to, "transaction_date")
    rows = get_shared_connection(db_path).execute(
        f"""
        SELECT transaction_id, transaction_date, transaction_type, debit, credit, fx_rate
        FROM transactions
        WHERE UPPER(transaction_type) IN ('BUY', 'SELL')
          AND COALESCE(fx_rate, 0) > 0
          AND ((UPPER(transaction_type) = 'BUY' AND COALESCE(debit, 0) > 0)
            OR (UPPER(transaction_type) = 'SELL' AND COALESCE(credit, 0) > 0))
          {clause}
        ORDER BY transaction_date, transaction_id
        """,
        params,
    ).fetchall()
    penny = Decimal("0.0001")
    details: list[dict[str, Any]] = []
    buy_fees = Decimal("0")
    sell_fees = Decimal("0")
    exposure = Decimal("0")
    buy_count = 0
    sell_count = 0
    for transaction_id, transaction_date, kind, debit, credit, fx_rate in rows:
        kind = str(kind).upper()
        cad_amount = _decimal(debit if kind == "BUY" else credit)
        if kind == "BUY":
            fee = estimate_wealthsimple_fx_fee_cad(kind, cad_amount)
            buy_fees += fee
            buy_count += 1
        else:
            fee = estimate_wealthsimple_fx_fee_cad(kind, cad_amount)
            sell_fees += fee
            sell_count += 1
        exposure += cad_amount
        details.append(
            {
                "transaction_id": int(transaction_id),
                "date": _date(transaction_date),
                "transaction_type": kind,
                "cad_amount": cad_amount,
                "fx_rate": _decimal(fx_rate),
                "estimated_fx_fee_cad": fee.quantize(penny, rounding=ROUND_HALF_UP),
            }
        )
    total_fee = (buy_fees + sell_fees).quantize(penny, rounding=ROUND_HALF_UP)
    result: dict[str, Any] = {
        "source": source,
        "available": True,
        "fee_rate": WEALTHSIMPLE_FX_FEE_RATE,
        "transaction_count": len(rows),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "cad_exposure": exposure.quantize(penny, rounding=ROUND_HALF_UP),
        "estimated_buy_fee_cad": buy_fees.quantize(penny, rounding=ROUND_HALF_UP),
        "estimated_sell_fee_cad": sell_fees.quantize(penny, rounding=ROUND_HALF_UP),
        "estimated_fx_fee_cad": total_fee,
        "fee_percent_of_exposure": (total_fee / exposure) if exposure else Decimal("0"),
    }
    if include_transactions:
        result["transactions"] = details
    logger.info(
        "FX fee analytics | source=%s | transactions=%d | cad_exposure=%s | estimated_fee=%s",
        source, len(rows), exposure, total_fee,
    )
    return result


def get_dividend_summary(
    db_path: str = DATABASE_PATH,
    *,
    source: str = "email",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Return dividend totals from one source to avoid overlapping-source duplicates."""
    connection = get_shared_connection(db_path)
    if source == "email":
        clause, params = _date_filters(date_from, date_to, "et.transaction_date")
        query = f"""
            SELECT COALESCE(t.currency, 'UNKNOWN'), COALESCE(SUM(et.debit), 0), COUNT(*)
            FROM email_transactions et LEFT JOIN tickers t ON t.ticker_id = et.ticker_id
            WHERE LOWER(et.transaction_type) = 'dividend' {clause}
            GROUP BY COALESCE(t.currency, 'UNKNOWN')
        """
    elif source == "activities":
        clause, params = _date_filters(date_from, date_to, "transaction_date")
        query = f"""
            SELECT COALESCE(transaction_currency, 'UNKNOWN'),
                   COALESCE(SUM(CASE WHEN net_cash_amount > 0 THEN net_cash_amount ELSE 0 END), 0),
                   COUNT(*)
            FROM activities WHERE activity_code = 'DIV' {clause}
            GROUP BY COALESCE(transaction_currency, 'UNKNOWN')
        """
    elif source == "statements":
        clause, params = _date_filters(date_from, date_to, "transaction_date")
        query = f"""
            SELECT 'CAD', COALESCE(SUM(credit), 0), COUNT(*)
            FROM transactions WHERE UPPER(transaction_type) = 'DIV' {clause}
        """
    else:
        raise ValueError("dividend source must be email, activities, or statements")
    rows = connection.execute(query, params).fetchall()
    totals = {str(currency): _decimal(amount) for currency, amount, count in rows if int(count) > 0}
    return {
        "source": source,
        "transaction_count": sum(int(row[2]) for row in rows),
        "totals_by_currency": totals,
    }


def get_commission_summary(
    db_path: str = DATABASE_PATH,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Return explicit commissions from the activity export source."""
    clause, params = _date_filters(date_from, date_to, "transaction_date")
    rows = get_shared_connection(db_path).execute(
        f"""
        SELECT COALESCE(transaction_currency, 'UNKNOWN'),
               COALESCE(SUM(ABS(commission_amount)), 0),
               COUNT(*) FILTER (WHERE COALESCE(commission_amount, 0) <> 0)
        FROM activities WHERE 1 = 1 {clause}
        GROUP BY COALESCE(transaction_currency, 'UNKNOWN')
        """,
        params,
    ).fetchall()
    return {
        "source": "activities",
        "transaction_count": sum(int(row[2]) for row in rows),
        "totals_by_currency": {str(row[0]): _decimal(row[1]) for row in rows},
    }


def _cash_flow_rows(
    db_path: str, source: str, date_from: date | None, date_to: date | None
) -> list[tuple[date, Decimal]]:
    connection = get_shared_connection(db_path)
    if source == "activities":
        clause, params = _date_filters(date_from, date_to, "transaction_date")
        rows = connection.execute(
            f"""SELECT transaction_date, net_cash_amount FROM activities
                WHERE activity_code = 'CONT' AND net_cash_amount IS NOT NULL
                  AND COALESCE(transaction_currency, 'CAD') = 'CAD' {clause}
                ORDER BY transaction_date""", params,
        ).fetchall()
    elif source == "statements":
        clause, params = _date_filters(date_from, date_to, "transaction_date")
        rows = connection.execute(
            f"""SELECT transaction_date, COALESCE(credit, 0) - COALESCE(debit, 0)
                FROM cash_transactions
                WHERE UPPER(transaction_type) IN
                    ('CONT', 'CONTRIBUTION', 'DEPOSIT', 'WITH', 'WITHDRAWAL') {clause}
                ORDER BY transaction_date""", params,
        ).fetchall()
    else:
        raise ValueError("cash-flow source must be activities or statements")
    return [(_date(row[0]), _decimal(row[1])) for row in rows]


def get_external_flow_series(
    db_path: str = DATABASE_PATH,
    *,
    source: str = "activities",
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Return external cash flows (contributions positive, withdrawals negative) per date.

    Used to strip contribution/withdrawal noise out of daily returns; the
    money-weighted (XIRR) calculation in ``get_cash_flow_summary`` is
    unaffected by this helper.
    """
    rows = _cash_flow_rows(db_path, source, date_from, date_to)
    totals: dict[date, Decimal] = {}
    for flow_date, amount in rows:
        totals[flow_date] = totals.get(flow_date, Decimal("0")) + amount
    return [{"date": flow_date, "amount": amount} for flow_date, amount in sorted(totals.items())]


def _xirr(flows: list[tuple[date, Decimal]]) -> float | None:
    """Solve annualized irregular cash-flow return by bounded bisection."""
    if len(flows) < 2 or not any(v < 0 for _, v in flows) or not any(v > 0 for _, v in flows):
        return None
    origin = min(day for day, _ in flows)

    def npv(rate: float) -> float:
        return sum(float(value) / ((1 + rate) ** ((day - origin).days / 365.0)) for day, value in flows)

    low, high = -0.9999, 10.0
    low_value, high_value = npv(low), npv(high)
    while low_value * high_value > 0 and high < 1_000_000:
        high *= 10
        high_value = npv(high)
    if low_value * high_value > 0:
        return None
    for _ in range(200):
        midpoint = (low + high) / 2
        value = npv(midpoint)
        if abs(value) < 1e-8:
            return midpoint
        if low_value * value <= 0:
            high = midpoint
        else:
            low, low_value = midpoint, value
    return (low + high) / 2


def get_realized_gain_summary(
    db_path: str = DATABASE_PATH,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Calculate realized gains using a running weighted-average CAD cost.

    Reads the deduplicated, split-adjusted ``v_trade_events`` view (see
    ``database.py``) rather than raw ``transactions``, so a trade recorded in
    more than one source is counted once and a split adjusts the running
    share count without disturbing cost basis.
    """
    connection = get_shared_connection(db_path)
    # Prior BUY rows are required to establish cost basis even when the report
    # begins later, so only the upper boundary belongs in the SQL ledger query.
    clause, params = _date_filters(None, date_to, "v.event_date")
    rows = connection.execute(
        f"""
        SELECT v.event_date, v.event_type, v.ticker_id, v.quantity, v.amount_cad,
               v.amount_currency, v.fx_rate, t.ticker_symbol
        FROM v_trade_events v JOIN tickers t ON t.ticker_id = v.ticker_id
        WHERE v.event_type IN ('BUY', 'SELL', 'SPLIT') {clause}
        ORDER BY v.event_date, v.source_priority, v.source_id
        """, params,
    ).fetchall()
    fx_series = _build_fx_series(connection)
    latest_txn_fx_row = connection.execute(
        "SELECT fx_rate FROM transactions WHERE fx_rate IS NOT NULL AND fx_rate > 0 "
        "ORDER BY transaction_date DESC LIMIT 1"
    ).fetchone()
    latest_txn_fx = _decimal(latest_txn_fx_row[0]) if latest_txn_fx_row else None
    state: dict[int, tuple[Decimal, Decimal]] = {}
    gains: dict[str, Decimal] = {}
    for event_date, kind, ticker_id, quantity, amount_cad, amount_currency, fx_rate, symbol in rows:
        held, cost = state.get(int(ticker_id), (Decimal("0"), Decimal("0")))
        qty = _decimal(quantity)
        # The view's amount column is only truly CAD when amount_currency says
        # so; activities rows carry the raw transaction-currency cash amount.
        amount = _decimal(amount_cad)
        if amount_cad is not None and amount_currency and str(amount_currency) != "CAD":
            rate, _flag = _resolve_fx(
                str(amount_currency),
                _decimal(fx_rate) if fx_rate is not None else None,
                _date(event_date),
                fx_series,
                latest_txn_fx,
            )
            amount *= rate
        if kind == "BUY":
            state[int(ticker_id)] = (held + qty, cost + amount)
            continue
        if kind == "SPLIT":
            state[int(ticker_id)] = (held + qty, cost)
            continue
        sold = min(qty, held)
        allocated_cost = (cost / held) * sold if held > 0 else Decimal("0")
        if date_from is None or _date(event_date) >= date_from:
            gains[str(symbol)] = gains.get(str(symbol), Decimal("0")) + amount - allocated_cost
        state[int(ticker_id)] = (held - sold, cost - allocated_cost)
    return {
        "source": "v_trade_events",
        "by_ticker": gains,
        "total_realized_gain": sum(gains.values(), Decimal("0")),
    }


def get_cash_flow_summary(
    db_path: str = DATABASE_PATH,
    *,
    source: str = "activities",
    date_from: date | None = None,
    date_to: date | None = None,
    terminal_value: Decimal = Decimal("0"),
) -> dict[str, Any]:
    """Summarize external flows and compute XIRR with the current terminal value."""
    rows = _cash_flow_rows(db_path, source, date_from, date_to)
    contributions = sum((amount for _, amount in rows if amount > 0), Decimal("0"))
    withdrawals = -sum((amount for _, amount in rows if amount < 0), Decimal("0"))
    investor_flows = [(day, -amount) for day, amount in rows]
    if terminal_value > 0:
        investor_flows.append((date_to or date.today(), terminal_value))
    # A filtered period requires an accurate opening valuation and historical
    # cash balance, which the current schema cannot reconstruct safely.
    filtered_period = date_from is not None or date_to is not None
    rate = None if filtered_period else _xirr(investor_flows)
    unavailable_reason = (
        "Period XIRR requires opening portfolio and historical cash valuations."
        if filtered_period
        else "At least one dated contribution and a positive ending flow are required."
    )
    return {
        "source": source,
        "contributions": contributions,
        "withdrawals": withdrawals,
        "net_contributions": contributions - withdrawals,
        "net_investment_profit": terminal_value + withdrawals - contributions,
        "money_weighted_return": {
            "available": rate is not None,
            "xirr": rate,
            "reason": None if rate is not None else unavailable_reason,
        },
    }


GEOGRAPHY_TAGS = ("Canada", "US", "Global", "India")


def _weights_from_totals(totals: dict[str, Decimal]) -> dict[str, dict[str, Any]]:
    total_value = sum(totals.values(), Decimal("0"))
    if total_value <= 0:
        return {}
    return {
        label: {"market_value": value, "weight": float(value / total_value)}
        for label, value in totals.items()
    }


def _get_classifications(db_path: str, ticker_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ticker_ids:
        return {}
    connection = get_shared_connection(db_path)
    placeholders = ",".join("?" for _ in ticker_ids)
    rows = connection.execute(
        f"""
        SELECT ticker_id, primary_group, secondary_tags, review_needed
        FROM portfolio_classifications
        WHERE ticker_id IN ({placeholders})
        """,
        ticker_ids,
    ).fetchall()
    classifications: dict[int, dict[str, Any]] = {}
    for ticker_id, primary_group, secondary_tags, review_needed in rows:
        tags = json.loads(secondary_tags) if secondary_tags else []
        classifications[int(ticker_id)] = {
            "primary_group": primary_group,
            "secondary_tags": tags if isinstance(tags, list) else [],
            "review_needed": bool(review_needed),
        }
    return classifications


def get_group_allocation(db_path: str, holdings: list[Holding]) -> dict[str, Any]:
    """Allocate current holdings by classifier group, and by geography tag when tagged.

    Holdings without a ``portfolio_classifications`` row are bucketed as
    ``Unclassified`` rather than dropped, so allocation weights still sum to
    the full current-holdings market value.
    """
    classifications = _get_classifications(db_path, [h.ticker_id for h in holdings])
    group_totals: dict[str, Decimal] = {}
    geography_totals: dict[str, Decimal] = {}
    tagged_value = Decimal("0")
    for holding in holdings:
        info = classifications.get(holding.ticker_id)
        group = info["primary_group"] if info else "Unclassified"
        group_totals[group] = group_totals.get(group, Decimal("0")) + holding.market_value
        if info:
            geography = next((tag for tag in info["secondary_tags"] if tag in GEOGRAPHY_TAGS), None)
            if geography:
                geography_totals[geography] = geography_totals.get(geography, Decimal("0")) + holding.market_value
                tagged_value += holding.market_value
    result: dict[str, Any] = {"by_group": _weights_from_totals(group_totals), "classifications": classifications}
    if not classifications:
        result["group_unavailable_reason"] = (
            "No rows in portfolio_classifications; run the classify-portfolio workflow."
        )
    if tagged_value > 0:
        result["by_geography"] = _weights_from_totals(geography_totals)
        result["geography_unavailable_reason"] = None
    else:
        result["by_geography"] = None
        result["geography_unavailable_reason"] = "No current holdings carry a geography tag."
    return result


def _get_sectors(db_path: str, ticker_ids: list[int]) -> dict[int, str]:
    if not ticker_ids:
        return {}
    connection = get_shared_connection(db_path)
    placeholders = ",".join("?" for _ in ticker_ids)
    rows = connection.execute(
        f"SELECT ticker_id, sector FROM stock_details WHERE ticker_id IN ({placeholders})",
        ticker_ids,
    ).fetchall()
    return {int(ticker_id): sector for ticker_id, sector in rows if sector}


def get_sector_allocation(db_path: str, holdings: list[Holding]) -> dict[str, Any]:
    """Allocate current holdings by sector; ETFs bucket as ``ETF`` (no per-ticker sector)."""
    sectors = _get_sectors(db_path, [h.ticker_id for h in holdings])
    totals: dict[str, Decimal] = {}
    missing_sector_tickers: list[str] = []
    for holding in holdings:
        if holding.security_type == "etf":
            label = "ETF"
        else:
            label = sectors.get(holding.ticker_id) or "Unknown"
            if holding.ticker_id not in sectors:
                missing_sector_tickers.append(holding.ticker_symbol)
        totals[label] = totals.get(label, Decimal("0")) + holding.market_value
    return {"by_sector": _weights_from_totals(totals), "missing_sector_tickers": missing_sector_tickers}


# yfinance funds_data uses snake_case sector keys while stock_details stores the
# Title Case names; both must land on one canonical label or look-through
# exposure double-counts a sector under two spellings.
_ETF_SECTOR_LABELS = {
    "realestate": "Real Estate",
    "real_estate": "Real Estate",
    "basic_materials": "Basic Materials",
    "consumer_cyclical": "Consumer Cyclical",
    "consumer_defensive": "Consumer Defensive",
    "financial_services": "Financial Services",
    "communication_services": "Communication Services",
    "technology": "Technology",
    "healthcare": "Healthcare",
    "utilities": "Utilities",
    "industrials": "Industrials",
    "energy": "Energy",
}


def _canonical_sector(key: str) -> str:
    return _ETF_SECTOR_LABELS.get(key, key.replace("_", " ").title())


def _get_etf_sector_weights(db_path: str, ticker_ids: list[int]) -> dict[int, dict[str, float]]:
    if not ticker_ids:
        return {}
    connection = get_shared_connection(db_path)
    placeholders = ",".join("?" for _ in ticker_ids)
    rows = connection.execute(
        f"SELECT ticker_id, sector_weights FROM etf_details WHERE ticker_id IN ({placeholders})",
        ticker_ids,
    ).fetchall()
    result: dict[int, dict[str, float]] = {}
    for ticker_id, sector_weights in rows:
        if not sector_weights:
            continue
        try:
            parsed = json.loads(sector_weights)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed:
            canonical: dict[str, float] = {}
            for key, value in parsed.items():
                label = _canonical_sector(str(key))
                canonical[label] = canonical.get(label, 0.0) + float(value)
            result[int(ticker_id)] = canonical
    return result


def get_look_through_sector_exposure(db_path: str, holdings: list[Holding]) -> dict[str, Any]:
    """Blend ETF ``sector_weights`` with stock sectors into one look-through view.

    ``coverage_percent`` is the share of current-holdings market value backed
    by known sector data (a stock's own sector, or an ETF's stored
    sector_weights); the rest is simply excluded from the sector split rather
    than guessed.
    """
    total_value = sum((h.market_value for h in holdings), Decimal("0"))
    if total_value <= 0:
        return {
            "available": False,
            "reason": "No current holdings to assess.",
            "weights": {},
            "coverage_percent": 0.0,
        }
    ticker_ids = [h.ticker_id for h in holdings]
    sectors = _get_sectors(db_path, ticker_ids)
    etf_weights = _get_etf_sector_weights(db_path, ticker_ids)
    exposure: dict[str, float] = {}
    covered_value = Decimal("0")
    for holding in holdings:
        if holding.security_type == "etf":
            weights = etf_weights.get(holding.ticker_id)
            if not weights:
                continue
            covered_value += holding.market_value
            for sector, weight in weights.items():
                exposure[sector] = exposure.get(sector, 0.0) + float(holding.market_value) * weight
        else:
            sector = sectors.get(holding.ticker_id)
            if not sector:
                continue
            covered_value += holding.market_value
            label = _canonical_sector(sector)
            exposure[label] = exposure.get(label, 0.0) + float(holding.market_value)
    coverage_percent = float(covered_value / total_value)
    if coverage_percent <= 0:
        return {
            "available": False,
            "reason": "No holdings have sector or ETF sector-weight data.",
            "weights": {},
            "coverage_percent": 0.0,
        }
    total_exposure = sum(exposure.values())
    weights = {sector: value / total_exposure for sector, value in exposure.items()} if total_exposure else {}
    return {"available": True, "weights": weights, "coverage_percent": coverage_percent}


# Legal-form tokens stripped when matching an underlying holding across two
# funds. The stored `top_holdings` records carry a display name and a weight
# but no ticker symbol, so the name is the only identity available and it has
# to survive "Broadcom Inc" vs "Broadcom". Class letters are deliberately not
# stripped -- "Berkshire Hathaway Class B" must not collapse into Class A.
_HOLDING_NAME_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "plc", "nv", "sa", "ag", "adr", "ordinary", "shares",
}


def _normalize_holding_name(name: str) -> str:
    """Reduce a fund's holding label to a comparable key across providers."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(name).strip().lower())
    tokens = cleaned.split()
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    while tokens and tokens[-1] in _HOLDING_NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _parse_top_holdings(raw: Any) -> dict[str, float]:
    """Return ``{normalized_name: weight}`` from a stored top_holdings blob.

    Accepts both shapes the pipeline has produced: a list of
    ``{"Name": ..., "Holding Percent": ...}`` records (what the classifier
    stores today) and a mapping keyed by row index. Malformed rows are skipped
    rather than failing the whole fund.
    """
    parsed = _load_json_column(raw, None)
    if isinstance(parsed, dict):
        records: list[Any] = list(parsed.values())
    elif isinstance(parsed, list):
        records = parsed
    else:
        return {}
    holdings: dict[str, float] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get("Name") or record.get("name") or record.get("Symbol")
        weight = record.get("Holding Percent", record.get("holding_percent"))
        if not name or weight is None:
            continue
        try:
            value = float(weight)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        key = _normalize_holding_name(name)
        if not key:
            continue
        holdings[key] = holdings.get(key, 0.0) + value
    return holdings


def get_etf_overlap(db_path: str = DATABASE_PATH) -> dict[str, Any]:
    """Measure how much the portfolio's ETFs hold the same underlying names.

    Each fund's reported top holdings are scaled by that fund's share of the
    ETF sleeve, so the returned shares are portfolio-level: `overlapping` is
    the slice of ETF money sitting in a name at least two funds report holding,
    `unique` is reported holdings only one fund carries, and `unreported` is
    the remainder of each fund that its top-holdings list does not cover.

    The provider only publishes each fund's largest positions (typically ten)
    and publishes them without ticker symbols, so this is a floor on true
    overlap computed by name -- two funds can share names further down their
    books than either reports. `basis` and `caveat` carry that caveat to the UI.
    """
    etfs = [h for h in get_holdings(db_path) if h.security_type == "etf" and h.market_value > 0]
    sleeve_value = sum((h.market_value for h in etfs), Decimal("0"))
    unavailable = {
        "available": False,
        "basis": "top_holdings",
        "etf_count": len(etfs),
        "etfs": [],
        "share": {"overlapping_weight": 0.0, "unique_weight": 0.0, "unreported_weight": 0.0},
        "pairs": [],
        "top_shared_holdings": [],
    }
    if not etfs or sleeve_value <= 0:
        return {**unavailable, "reason": "No ETF holdings with market value."}

    connection = get_shared_connection(db_path)
    ticker_ids = [h.ticker_id for h in etfs]
    placeholders = ",".join("?" for _ in ticker_ids)
    rows = connection.execute(
        f"SELECT ticker_id, fields FROM portfolio_classifications WHERE ticker_id IN ({placeholders})",
        ticker_ids,
    ).fetchall()
    raw_by_ticker = {
        int(ticker_id): _normalize_classification_fields(fields).get("top_holdings")
        for ticker_id, fields in rows
    }

    # Keep display names alongside the match keys so the UI can label a shared
    # position with the wording the funds actually use.
    holdings_by_etf: dict[str, dict[str, float]] = {}
    display_names: dict[str, str] = {}
    sleeve_weights: dict[str, float] = {}
    etf_rows: list[dict[str, Any]] = []
    for holding in etfs:
        parsed = _parse_top_holdings(raw_by_ticker.get(holding.ticker_id))
        raw = _load_json_column(raw_by_ticker.get(holding.ticker_id), None)
        for record in (raw.values() if isinstance(raw, dict) else raw or []):
            if isinstance(record, dict) and record.get("Name"):
                display_names.setdefault(_normalize_holding_name(record["Name"]), str(record["Name"]).strip())
        sleeve_weight = float(holding.market_value / sleeve_value)
        sleeve_weights[holding.ticker_symbol] = sleeve_weight
        reported = sum(parsed.values())
        etf_rows.append(
            {
                "ticker_symbol": holding.ticker_symbol,
                "security_name": holding.security_name,
                "market_value": holding.market_value,
                "sleeve_weight": sleeve_weight,
                "reported_weight": reported,
                "holdings_count": len(parsed),
            }
        )
        if parsed:
            holdings_by_etf[holding.ticker_symbol] = parsed

    etf_rows.sort(key=lambda row: row["market_value"], reverse=True)
    if len(holdings_by_etf) < 2:
        return {
            **unavailable,
            "reason": "Fewer than two ETFs have reported holdings to compare.",
            "etfs": etf_rows,
        }

    funds_by_name: dict[str, list[str]] = {}
    for symbol, parsed in holdings_by_etf.items():
        for name in parsed:
            funds_by_name.setdefault(name, []).append(symbol)

    overlapping = unique = 0.0
    shared_rows: list[dict[str, Any]] = []
    for name, symbols in funds_by_name.items():
        contribution = sum(sleeve_weights[s] * holdings_by_etf[s][name] for s in symbols)
        if len(symbols) > 1:
            overlapping += contribution
            shared_rows.append(
                {
                    "name": display_names.get(name, name),
                    "etfs": sorted(symbols),
                    "combined_weight": contribution,
                }
            )
        else:
            unique += contribution
    unreported = sum(
        sleeve_weights[row["ticker_symbol"]] * max(0.0, 1.0 - row["reported_weight"])
        for row in etf_rows
        if row["ticker_symbol"] in holdings_by_etf
    )
    # Funds with no reported holdings at all are entirely unreported exposure.
    unreported += sum(
        row["sleeve_weight"] for row in etf_rows if row["ticker_symbol"] not in holdings_by_etf
    )
    shared_rows.sort(key=lambda row: row["combined_weight"], reverse=True)

    pairs: list[dict[str, Any]] = []
    symbols = sorted(holdings_by_etf)
    for index, first in enumerate(symbols):
        for second in symbols[index + 1 :]:
            shared_names = set(holdings_by_etf[first]) & set(holdings_by_etf[second])
            if not shared_names:
                continue
            # Overlap coefficient: the weight both funds commit to the same
            # names, taking the smaller position on each side.
            overlap_pct = sum(
                min(holdings_by_etf[first][name], holdings_by_etf[second][name]) for name in shared_names
            )
            pairs.append(
                {
                    "a": first,
                    "b": second,
                    "overlap_pct": overlap_pct,
                    "shared": sorted(
                        (
                            {
                                "name": display_names.get(name, name),
                                "a_pct": holdings_by_etf[first][name],
                                "b_pct": holdings_by_etf[second][name],
                            }
                            for name in shared_names
                        ),
                        key=lambda row: min(row["a_pct"], row["b_pct"]),
                        reverse=True,
                    ),
                }
            )
    pairs.sort(key=lambda row: row["overlap_pct"], reverse=True)

    return {
        "available": True,
        "basis": "top_holdings",
        "caveat": (
            "Based on each fund's reported top holdings matched by name, weighted by "
            "the fund's share of the ETF sleeve. Providers publish only the largest "
            "positions, so true overlap is at least this much."
        ),
        "etf_count": len(etfs),
        "compared_count": len(holdings_by_etf),
        "etfs": etf_rows,
        "share": {
            "overlapping_weight": overlapping,
            "unique_weight": unique,
            "unreported_weight": unreported,
        },
        "pairs": pairs,
        "top_shared_holdings": shared_rows,
    }


def get_currency_exposure(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    """Allocate current holdings by listing currency (values are not FX-converted)."""
    totals: dict[str, Decimal] = {}
    for holding in holdings:
        label = holding.currency or "UNKNOWN"
        totals[label] = totals.get(label, Decimal("0")) + holding.market_value
    return _weights_from_totals(totals)


def get_expense_ratios(db_path: str, ticker_ids: list[int]) -> dict[int, Decimal]:
    """Return known ETF expense ratios (MER) for the given tickers."""
    if not ticker_ids:
        return {}
    connection = get_shared_connection(db_path)
    placeholders = ",".join("?" for _ in ticker_ids)
    rows = connection.execute(
        f"""
        SELECT ticker_id, expense_ratio FROM etf_details
        WHERE ticker_id IN ({placeholders}) AND expense_ratio IS NOT NULL
        """,
        ticker_ids,
    ).fetchall()
    return {int(ticker_id): _decimal(expense_ratio) for ticker_id, expense_ratio in rows}


def load_allocation_targets(policy_path: str | Path = POLICY_FILE) -> dict[str, dict[str, Any]] | None:
    """Load group allocation targets from the classifier policy YAML, if configured."""
    path = Path(policy_path)
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        logger.exception("Failed to parse allocation targets policy file | path=%s", path)
        return None
    if not isinstance(data, dict):
        return None
    targets = data.get("allocation_targets")
    return targets if isinstance(targets, dict) else None


def get_turnover_and_holding_period(
    db_path: str = DATABASE_PATH,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    historical_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Estimate portfolio turnover and quantity-weighted average holding period.

    Turnover is ``min(buy dollars, sell dollars) / average portfolio value``
    over the period, from statement transactions. Holding period is computed
    from a chronological per-ticker weighted-average-acquisition-date ledger
    walk (the same technique ``get_realized_gain_summary`` uses for cost);
    open positions measure to today, closed lots measure to their sale date,
    and a ticker whose net quantity ends negative contributes nothing to the
    open-holding-period figure.
    """
    connection = get_shared_connection(db_path)
    clause, params = _date_filters(date_from, date_to, "transaction_date")
    turnover_row = connection.execute(
        f"""
        SELECT
            SUM(CASE WHEN UPPER(transaction_type) = 'BUY' THEN COALESCE(debit, 0) ELSE 0 END),
            SUM(CASE WHEN UPPER(transaction_type) = 'SELL' THEN COALESCE(credit, 0) ELSE 0 END)
        FROM transactions
        WHERE UPPER(transaction_type) IN ('BUY', 'SELL') {clause}
        """,
        params,
    ).fetchone()
    total_buys = _decimal(turnover_row[0])
    total_sells = _decimal(turnover_row[1])

    values = historical_values if historical_values is not None else get_historical_portfolio_values(db_path)
    period_values = [
        row["portfolio_value"]
        for row in values
        if (date_from is None or row["date"] >= date_from) and (date_to is None or row["date"] <= date_to)
    ]
    positive_values = [value for value in period_values if value > 0]
    average_value = (
        sum(positive_values, Decimal("0")) / len(positive_values) if positive_values else Decimal("0")
    )
    if average_value > 0:
        turnover = {
            "available": True,
            "turnover_ratio": float(min(total_buys, total_sells) / average_value),
            "method": "min(buy_dollars, sell_dollars) / average portfolio value over the period",
        }
    else:
        turnover = {
            "available": False,
            "reason": "No positive portfolio valuation available over the period.",
            "turnover_ratio": None,
        }

    ledger_rows = connection.execute(
        """
        SELECT transaction_date, transaction_type, ticker_id, quantity
        FROM transactions
        WHERE UPPER(transaction_type) IN ('BUY', 'SELL')
        ORDER BY transaction_date, transaction_id
        """
    ).fetchall()
    epoch = date(1970, 1, 1)
    state: dict[int, tuple[Decimal, Decimal]] = {}
    closed_days_total = Decimal("0")
    closed_qty_total = Decimal("0")
    for transaction_date, kind, ticker_id, quantity in ledger_rows:
        held_qty, held_weighted = state.get(int(ticker_id), (Decimal("0"), Decimal("0")))
        qty = abs(_decimal(quantity))
        current_epoch_day = Decimal((_date(transaction_date) - epoch).days)
        if str(kind).upper() == "BUY":
            new_qty = held_qty + qty
            held_weighted = (
                (held_weighted * held_qty + current_epoch_day * qty) / new_qty if new_qty > 0 else Decimal("0")
            )
            state[int(ticker_id)] = (new_qty, held_weighted)
            continue
        sold_qty = min(qty, held_qty)
        if sold_qty > 0:
            closed_days_total += sold_qty * (current_epoch_day - held_weighted)
            closed_qty_total += sold_qty
        state[int(ticker_id)] = (held_qty - sold_qty, held_weighted)

    today_epoch_day = Decimal((date.today() - epoch).days)
    open_days_total = Decimal("0")
    open_qty_total = Decimal("0")
    for held_qty, held_weighted in state.values():
        if held_qty > 0:
            open_days_total += held_qty * (today_epoch_day - held_weighted)
            open_qty_total += held_qty

    average_holding_period = {
        "open": (
            {"available": True, "days": float(open_days_total / open_qty_total)}
            if open_qty_total > 0
            else {"available": False, "reason": "No open positions.", "days": None}
        ),
        "closed": (
            {"available": True, "days": float(closed_days_total / closed_qty_total)}
            if closed_qty_total > 0
            else {"available": False, "reason": "No closed lots (no sell transactions).", "days": None}
        ),
    }
    return {"turnover": turnover, "average_holding_period": average_holding_period}


def _is_full_calendar_year(year: int) -> bool:
    return year < date.today().year


def get_dividend_history(
    db_path: str = DATABASE_PATH,
    *,
    source: str = "email",
    date_from: date | None = None,
    date_to: date | None = None,
    portfolio_value: Decimal = Decimal("0"),
    book_cost: Decimal = Decimal("0"),
) -> dict[str, Any]:
    """Return dividend income by month/currency plus trailing yield and growth figures."""
    connection = get_shared_connection(db_path)
    if source == "email":
        clause, params = _date_filters(date_from, date_to, "et.transaction_date")
        query = f"""
            SELECT et.transaction_date, COALESCE(t.currency, 'UNKNOWN'), et.debit
            FROM email_transactions et LEFT JOIN tickers t ON t.ticker_id = et.ticker_id
            WHERE LOWER(et.transaction_type) = 'dividend' {clause}
        """
    elif source == "activities":
        clause, params = _date_filters(date_from, date_to, "transaction_date")
        query = f"""
            SELECT transaction_date, COALESCE(transaction_currency, 'UNKNOWN'), net_cash_amount
            FROM activities WHERE activity_code = 'DIV' AND COALESCE(net_cash_amount, 0) > 0 {clause}
        """
    elif source == "statements":
        clause, params = _date_filters(date_from, date_to, "transaction_date")
        query = f"""
            SELECT transaction_date, 'CAD', credit
            FROM transactions WHERE UPPER(transaction_type) = 'DIV' {clause}
        """
    else:
        raise ValueError("dividend source must be email, activities, or statements")
    rows = connection.execute(query, params).fetchall()

    by_month: dict[str, Decimal] = {}
    by_currency: dict[str, Decimal] = {}
    by_year: dict[int, Decimal] = {}
    t12m_by_currency: dict[str, Decimal] = {}
    twelve_months_ago = date.today() - timedelta(days=365)
    for transaction_date, currency, amount in rows:
        day = _date(transaction_date)
        value = _decimal(amount)
        month_key = f"{day.year:04d}-{day.month:02d}"
        by_month[month_key] = by_month.get(month_key, Decimal("0")) + value
        by_currency[currency] = by_currency.get(currency, Decimal("0")) + value
        by_year[day.year] = by_year.get(day.year, Decimal("0")) + value
        if day >= twelve_months_ago:
            t12m_by_currency[currency] = t12m_by_currency.get(currency, Decimal("0")) + value

    cad_t12m = t12m_by_currency.get("CAD", Decimal("0"))
    yield_on_cost = (
        {"available": True, "value": float(cad_t12m / book_cost)}
        if book_cost > 0
        else {"available": False, "reason": "No CAD book cost to compute yield on cost.", "value": None}
    )
    trailing_yield = (
        {"available": True, "value": float(cad_t12m / portfolio_value)}
        if portfolio_value > 0
        else {"available": False, "reason": "No portfolio value to compute trailing yield.", "value": None}
    )

    full_years = sorted(year for year in by_year if _is_full_calendar_year(year))
    if len(full_years) >= 2:
        previous_year, latest_year = full_years[-2], full_years[-1]
        previous_total = by_year[previous_year]
        growth = (
            {
                "available": True,
                "value": float((by_year[latest_year] - previous_total) / previous_total),
                "from_year": previous_year,
                "to_year": latest_year,
            }
            if previous_total > 0
            else {"available": False, "reason": "Prior full year had zero dividends.", "value": None}
        )
    else:
        growth = {
            "available": False,
            "reason": "Fewer than two full calendar years of dividend history.",
            "value": None,
        }

    return {
        "source": source,
        "transaction_count": len(rows),
        "by_month": {month: by_month[month] for month in sorted(by_month)},
        "totals_by_currency": by_currency,
        "trailing_12_month": {
            "totals_by_currency": t12m_by_currency,
            "yield_on_portfolio_value": trailing_yield,
        },
        "yield_on_cost": yield_on_cost,
        "dividend_growth": growth,
    }


def build_data_quality(
    holdings: list[Holding],
    excluded_positions: list[Holding],
    classifications: dict[int, dict[str, Any]],
    *,
    missing_sector_tickers: list[str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Flag positions that need attention instead of silently including or excluding them."""
    today = today or date.today()
    missing_sector = set(missing_sector_tickers or [])
    flags: list[dict[str, Any]] = []

    for holding in excluded_positions:
        flags.append(
            {
                "code": "negative_quantity",
                "ticker_symbol": holding.ticker_symbol,
                "detail": f"Net quantity is {holding.quantity}; recorded sells exceed recorded buys.",
                "severity": "warning",
            }
        )

    for holding in holdings:
        if holding.has_provisional_activity:
            flags.append(
                {
                    "code": "provisional_activity",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": "Includes unreconciled provisional email activity.",
                    "severity": "info",
                }
            )
        for flag_code in holding.data_quality_flags:
            flags.append(
                {
                    "code": f"position_engine_{flag_code}",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": (
                        "Position engine could not fully resolve this ticker's book value; "
                        f"see `docs/architecture/ingestion_and_reconciliation.md` for `{flag_code}`."
                    ),
                    "severity": "warning",
                }
            )
        if holding.last_price_date is None:
            flags.append(
                {
                    "code": "stale_price",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": "No stored price history for this ticker.",
                    "severity": "warning",
                }
            )
        elif (today - holding.last_price_date).days > STALE_PRICE_MAX_AGE_DAYS:
            flags.append(
                {
                    "code": "stale_price",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": (
                        f"Last price is from {holding.last_price_date}, "
                        f"older than {STALE_PRICE_MAX_AGE_DAYS} days."
                    ),
                    "severity": "warning",
                }
            )
        if holding.cost_basis <= 0:
            flags.append(
                {
                    "code": "missing_cost_basis",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": "Cost basis is zero or negative for a currently held position.",
                    "severity": "warning",
                }
            )
        elif holding.market_value > 0:
            gain_percent = (holding.market_value - holding.cost_basis) / holding.cost_basis
            if abs(gain_percent) > Decimal(str(SUSPICIOUS_UNREALIZED_GAIN_THRESHOLD)):
                flags.append(
                    {
                        "code": "suspicious_gain",
                        "ticker_symbol": holding.ticker_symbol,
                        "detail": (
                            f"Unrealized gain is {gain_percent:.1%}, beyond the "
                            f"{SUSPICIOUS_UNREALIZED_GAIN_THRESHOLD:.0%} sanity threshold."
                        ),
                        "severity": "warning",
                    }
                )
        if holding.ticker_symbol in missing_sector:
            flags.append(
                {
                    "code": "missing_sector",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": "No stock_details.sector recorded for this ticker.",
                    "severity": "info",
                }
            )
        info = classifications.get(holding.ticker_id)
        if info is None:
            flags.append(
                {
                    "code": "missing_classification",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": "No portfolio_classifications row for this ticker.",
                    "severity": "info",
                }
            )
        elif info.get("review_needed"):
            flags.append(
                {
                    "code": "missing_classification",
                    "ticker_symbol": holding.ticker_symbol,
                    "detail": "Classifier flagged this ticker for manual review.",
                    "severity": "info",
                }
            )

    counts_by_code: dict[str, int] = {}
    for flag in flags:
        counts_by_code[flag["code"]] = counts_by_code.get(flag["code"], 0) + 1

    return {
        "flags": flags,
        "excluded_negative_positions": [
            {"ticker_symbol": h.ticker_symbol, "quantity": h.quantity, "cost_basis": h.cost_basis}
            for h in excluded_positions
        ],
        "counts_by_code": counts_by_code,
    }


def _normalize_benchmark_history(history: Any) -> list[dict[str, Any]]:
    """Normalize a yfinance-shaped history frame or list into sorted {date, close} rows."""
    if isinstance(history, pd.DataFrame):
        if history.empty:
            return []
        records: list[dict[str, Any]] = []
        for _, row in history.iterrows():
            close = row.get("Close")
            if close is None or pd.isna(close):
                continue
            raw_date = row.get("Date")
            normalized_date = raw_date.date() if hasattr(raw_date, "date") else raw_date
            records.append({"date": normalized_date, "close": float(close)})
        records.sort(key=lambda record: record["date"])
        return records
    # Test fixtures: a plain iterable of {"date", "close"} dicts.
    records = [
        {"date": row["date"], "close": float(row["close"])}
        for row in history
        if row.get("close") is not None
    ]
    records.sort(key=lambda record: record["date"])
    return records


def get_benchmark_returns(
    symbol: str,
    start: date,
    end: date,
    *,
    fetch_history: Callable[[list[str], date, date], Any] | None = None,
) -> list[dict[str, Any]] | None:
    """Fetch a benchmark's daily returns from yfinance, or ``None`` on any failure.

    Never persisted to the database; used only to compute in-memory benchmark
    comparison statistics for a single report run. ``fetch_history`` is
    injectable so tests never need a live network call.
    """
    if fetch_history is None:
        try:
            from yfinance_extractor import fetch_security_history as fetch_history
        except ImportError:
            return None
    try:
        history = fetch_history([symbol], start, end)
    except Exception:
        logger.exception("Benchmark history fetch failed | symbol=%s", symbol)
        return None
    try:
        records = _normalize_benchmark_history(history)
    except Exception:
        logger.exception("Benchmark history normalization failed | symbol=%s", symbol)
        return None
    if len(records) < 2:
        return None
    returns: list[dict[str, Any]] = []
    previous_close: float | None = None
    for record in records:
        if previous_close is not None and previous_close > 0:
            returns.append(
                {"date": record["date"], "return": (record["close"] - previous_close) / previous_close}
            )
        previous_close = record["close"]
    return returns or None


def portfolio_report(
    db_path: str = DATABASE_PATH,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    dividend_source: str = "email",
    cash_flow_source: str = "activities",
    fx_source: str = "statements",
    benchmark_symbol: str | None = DEFAULT_BENCHMARK_SYMBOL,
    benchmark_fetcher: Callable[[list[str], date, date], Any] | None = None,
) -> dict[str, Any]:
    """Compose the full read-only portfolio analytics report.

    Only currently-held positive-quantity positions drive holdings, allocation,
    and concentration figures; excluded negative net positions are reported
    only through ``data_quality``. Every metric that cannot be computed
    reliably from available data is recorded in ``unavailable_metrics`` with a
    reason instead of being guessed or omitted silently.
    """
    unavailable: list[dict[str, str]] = []

    def _mark_unavailable(metric: str, reason: str) -> None:
        unavailable.append({"metric": metric, "reason": reason})

    summary_obj = get_portfolio_summary(db_path)
    holding_objs = summary_obj.holdings
    excluded_objs = get_excluded_positions(db_path)
    holdings = [asdict(holding) for holding in holding_objs]

    historical_values = get_historical_portfolio_values(db_path)

    # --- Allocation ---
    group_allocation = get_group_allocation(db_path, holding_objs)
    sector_allocation = get_sector_allocation(db_path, holding_objs)
    look_through = get_look_through_sector_exposure(db_path, holding_objs)
    currency_exposure = get_currency_exposure(holding_objs)
    weights_by_ticker = calculate_position_weights(holdings)
    for row in holdings:
        row["weight"] = weights_by_ticker.get(row["ticker_symbol"], {}).get("weight", 0.0)
    concentration = calculate_concentration(weights_by_ticker)

    if group_allocation.get("group_unavailable_reason"):
        _mark_unavailable("allocation.by_group", group_allocation["group_unavailable_reason"])
    if group_allocation.get("geography_unavailable_reason"):
        _mark_unavailable("allocation.by_geography", group_allocation["geography_unavailable_reason"])
    if sector_allocation["missing_sector_tickers"]:
        _mark_unavailable(
            "allocation.by_sector",
            "Missing sector metadata for: " + ", ".join(sector_allocation["missing_sector_tickers"]),
        )
    if not look_through["available"]:
        _mark_unavailable("allocation.look_through_sector", look_through["reason"])
    if not concentration["available"]:
        _mark_unavailable("allocation.concentration", concentration["reason"])

    # The single-name cap targets idiosyncratic single-company risk, so
    # diversified broad-market funds (ETFs classified as Core) are exempt:
    # XEQT at 26% is the market, not a concentrated bet.
    classifications_by_id = group_allocation.get("classifications", {})
    exempt_tickers = sorted(
        h.ticker_symbol
        for h in holding_objs
        if h.security_type == "etf"
        and (classifications_by_id.get(h.ticker_id) or {}).get("primary_group") == "Core"
    )
    exempt_set = set(exempt_tickers)
    single_name_breach = None
    max_flagged_weight: float | None = None
    max_flagged_ticker: str | None = None
    if concentration["available"]:
        flagged = {s: info for s, info in weights_by_ticker.items() if s not in exempt_set}
        if flagged:
            max_flagged_ticker, flagged_row = max(
                flagged.items(), key=lambda item: item[1]["weight"]
            )
            max_flagged_weight = float(flagged_row["weight"])
            single_name_breach = max_flagged_weight > float(SINGLE_NAME_MAX_WEIGHT)
        else:
            single_name_breach = False
    concentration_with_limit = {
        **concentration,
        "single_name_limit": float(SINGLE_NAME_MAX_WEIGHT),
        "single_name_limit_breached": single_name_breach,
        "max_flagged_name_weight": max_flagged_weight,
        "max_flagged_name_ticker": max_flagged_ticker,
        "exempt_tickers": exempt_tickers,
    }
    allocation = {
        "by_ticker": weights_by_ticker,
        "by_group": group_allocation["by_group"],
        "by_sector": sector_allocation["by_sector"],
        "by_currency": currency_exposure,
        "by_geography": group_allocation["by_geography"],
        "look_through_sector": look_through,
        "concentration": concentration_with_limit,
    }

    # --- Targets / rebalance drift ---
    allocation_targets = load_allocation_targets()
    actual_group_weights = {group: info["weight"] for group, info in group_allocation["by_group"].items()}
    drift = calculate_rebalance_drift(actual_group_weights, allocation_targets)
    if not drift["available"]:
        _mark_unavailable("targets", drift["reason"])
    targets = {"available": drift["available"], "source_file": str(POLICY_FILE), "groups": drift["groups"]}

    # --- Performance ---
    external_flows = get_external_flow_series(
        db_path,
        source=cash_flow_source if cash_flow_source in ("activities", "statements") else "activities",
        date_from=date_from,
        date_to=date_to,
    )
    adjusted_returns = calculate_adjusted_daily_returns(historical_values, external_flows)
    wealth_index = build_wealth_index(adjusted_returns)
    total_return = calculate_twr_total_return(adjusted_returns)
    volatility = calculate_adjusted_volatility(adjusted_returns)
    sharpe_ratio = calculate_adjusted_sharpe_ratio(adjusted_returns)
    sortino_ratio = calculate_sortino_ratio(adjusted_returns)
    drawdown_details = calculate_drawdown_details(wealth_index)
    for metric_name, block in (
        ("performance.total_return", total_return),
        ("performance.volatility", volatility),
        ("performance.sharpe_ratio", sharpe_ratio),
        ("performance.sortino_ratio", sortino_ratio),
        ("performance.drawdown", drawdown_details),
    ):
        if not block.get("available"):
            _mark_unavailable(metric_name, block.get("reason", "Not enough adjusted return history."))

    cash_flow = get_cash_flow_summary(
        db_path,
        source=cash_flow_source,
        date_from=date_from,
        date_to=date_to,
        terminal_value=summary_obj.portfolio_value,
    )
    if not cash_flow["money_weighted_return"]["available"]:
        _mark_unavailable("performance.money_weighted_return", cash_flow["money_weighted_return"]["reason"])

    benchmark_stats: dict[str, Any] | None = None
    if benchmark_symbol and historical_values:
        start = historical_values[0]["date"]
        end = historical_values[-1]["date"]
        benchmark_returns = get_benchmark_returns(benchmark_symbol, start, end, fetch_history=benchmark_fetcher)
        if benchmark_returns:
            benchmark_stats = calculate_benchmark_stats(adjusted_returns, benchmark_returns)
        if not benchmark_stats:
            _mark_unavailable(
                "performance.benchmark",
                f"Could not fetch or align benchmark data for {benchmark_symbol}.",
            )
    else:
        _mark_unavailable(
            "performance.benchmark",
            "Benchmark comparison disabled." if not benchmark_symbol else "No historical valuation series to compare.",
        )

    trend_overlays = get_trend_overlay_series(
        db_path, historical_values, flow_source=cash_flow_source
    )
    if not trend_overlays["available"]:
        _mark_unavailable("performance.trend_overlays", trend_overlays["reason"])
    for overlay_key, meta in trend_overlays["benchmarks"].items():
        if not meta["available"]:
            _mark_unavailable(f"performance.trend_overlays.{overlay_key}", meta["reason"])

    performance = {
        "historical_values": historical_values,
        "trend_overlays": trend_overlays,
        "adjusted_returns": {
            "total_return": total_return,
            "volatility": volatility,
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "drawdown": drawdown_details,
        },
        "money_weighted": cash_flow,
        "benchmark": {
            "available": benchmark_stats is not None,
            "symbol": benchmark_symbol,
            **(benchmark_stats or {}),
        },
    }

    # --- Income ---
    cost_basis = sum((holding.cost_basis for holding in holding_objs), Decimal("0"))
    income = get_dividend_history(
        db_path,
        source=dividend_source,
        date_from=date_from,
        date_to=date_to,
        portfolio_value=summary_obj.portfolio_value,
        book_cost=cost_basis,
    )
    if not income["yield_on_cost"]["available"]:
        _mark_unavailable("income.yield_on_cost", income["yield_on_cost"]["reason"])
    if not income["dividend_growth"]["available"]:
        _mark_unavailable("income.dividend_growth", income["dividend_growth"]["reason"])

    # --- Fees ---
    commissions = get_commission_summary(db_path, date_from=date_from, date_to=date_to)
    fx_fees = get_fx_fee_summary(db_path, source=fx_source, date_from=date_from, date_to=date_to)
    expense_ratios = get_expense_ratios(db_path, [h.ticker_id for h in holding_objs])
    weighted_mer = calculate_weighted_mer(
        [
            {"market_value": h.market_value, "expense_ratio": expense_ratios.get(h.ticker_id)}
            for h in holding_objs
        ]
    )
    if not weighted_mer["available"]:
        _mark_unavailable("fees.weighted_mer", weighted_mer["reason"])
    fx_fee_percent_of_value = (
        {"available": True, "value": float(fx_fees["estimated_fx_fee_cad"] / summary_obj.portfolio_value)}
        if fx_fees["available"] and summary_obj.portfolio_value > 0
        else {"available": False, "reason": "No positive portfolio value to compare fees against.", "value": None}
    )
    unrealized_total = sum((h.market_value - h.cost_basis for h in holding_objs), Decimal("0"))
    fx_fee_percent_of_gains = (
        {"available": True, "value": float(fx_fees["estimated_fx_fee_cad"] / unrealized_total)}
        if fx_fees["available"] and unrealized_total > 0
        else {"available": False, "reason": "No positive unrealized gain to compare fees against.", "value": None}
    )
    if not fx_fee_percent_of_value["available"]:
        _mark_unavailable("fees.fx_fee_percent_of_value", fx_fee_percent_of_value["reason"])
    if not fx_fee_percent_of_gains["available"]:
        _mark_unavailable("fees.fx_fee_percent_of_gains", fx_fee_percent_of_gains["reason"])
    fees = {
        "commissions": commissions,
        "fx": fx_fees,
        "fee_drag": {
            "fx_fee_percent_of_value": fx_fee_percent_of_value,
            "fx_fee_percent_of_gains": fx_fee_percent_of_gains,
            "weighted_mer": weighted_mer,
        },
    }

    # --- Activity ---
    activity = get_turnover_and_holding_period(
        db_path, date_from=date_from, date_to=date_to, historical_values=historical_values
    )
    if not activity["turnover"]["available"]:
        _mark_unavailable("activity.turnover", activity["turnover"]["reason"])
    for horizon in ("open", "closed"):
        block = activity["average_holding_period"][horizon]
        if not block["available"]:
            _mark_unavailable(f"activity.average_holding_period.{horizon}", block["reason"])

    # --- Data quality ---
    data_quality = build_data_quality(
        holding_objs,
        excluded_objs,
        group_allocation["classifications"],
        missing_sector_tickers=sector_allocation["missing_sector_tickers"],
    )

    # --- Summary ---
    realized_gains = get_realized_gain_summary(db_path, date_from=date_from, date_to=date_to)
    securities_value = sum((h.market_value for h in holding_objs), Decimal("0"))
    summary = {
        "portfolio_value": summary_obj.portfolio_value,
        "cash": asdict(summary_obj.cash),
        "securities_value": securities_value,
        "book_cost": cost_basis,
        "holdings_count": len(holding_objs),
        "unrealized_gain": {
            "amount": unrealized_total,
            "percent": float(unrealized_total / cost_basis) if cost_basis > 0 else None,
        },
        "unrealized_gains_by_ticker": {
            row["ticker_symbol"]: calculate_unrealized_gain_percent(row) for row in holdings
        },
        "realized_gain_total": realized_gains["total_realized_gain"],
    }
    if cost_basis <= 0 and holdings:
        _mark_unavailable("summary.unrealized_gain.percent", "No positive book cost to compute a percentage.")

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "parameters": {
            "date_from": date_from,
            "date_to": date_to,
            "dividend_source": dividend_source,
            "cash_flow_source": cash_flow_source,
            "fx_source": fx_source,
            "benchmark_symbol": benchmark_symbol,
        },
        "summary": summary,
        "holdings": holdings,
        "allocation": allocation,
        "targets": targets,
        "performance": performance,
        "income": income,
        "fees": fees,
        "activity": activity,
        "realized_gains": realized_gains,
        "data_quality": data_quality,
        "unavailable_metrics": unavailable,
    }

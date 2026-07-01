"""Read-only portfolio analytics helpers built on the normalized DuckDB schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from config import DATABASE_PATH
from database import get_shared_connection
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
    ticker_id: int
    ticker_symbol: str
    exchange: str
    security_name: str
    security_type: str
    quantity: Decimal
    cost_basis: Decimal
    market_value: Decimal
    last_price: Decimal | None = None
    last_price_date: date | None = None


@dataclass(frozen=True)
class CashSummary:
    balance: Decimal
    source: str


@dataclass(frozen=True)
class PortfolioSummary:
    holdings: list[Holding]
    cash: CashSummary
    portfolio_value: Decimal


def get_holdings(db_path: str = DATABASE_PATH) -> list[Holding]:
    connection = get_shared_connection(db_path)
    # Reconstruct the current position state from cumulative transaction history.
    rows = connection.execute(
        """
        WITH net_transactions AS (
            SELECT
                ticker_id,
                SUM(quantity) AS total_amount,
                SUM(COALESCE(debit, 0)) AS total_debit,
                SUM(COALESCE(credit, 0)) AS total_credit
            FROM transactions
            WHERE transaction_type = 'BUY'
            GROUP BY ticker_id
        ),
        latest_prices AS (
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
            nt.total_amount,
            nt.total_debit,
            nt.total_credit,
            lp.record_date,
            lp.close
        FROM net_transactions nt
        JOIN tickers t ON t.ticker_id = nt.ticker_id
        LEFT JOIN latest_prices lp ON lp.ticker_id = nt.ticker_id
        WHERE COALESCE(nt.total_amount, 0) <> 0
        ORDER BY t.ticker_symbol, t.exchange
        """
    ).fetchall()
    holdings: list[Holding] = []
    for row in rows:
        ticker_id, symbol, exchange, name, security_type, total_amount, debit, credit, price_date, close = row
        quantity_value = _decimal(total_amount)
        cost_basis = _decimal(debit) - _decimal(credit)
        last_price = _decimal(close) if close is not None else None
        # If no usable price exists yet, keep the holding value neutral instead of guessing.
        market_value = quantity_value * last_price if last_price is not None else Decimal("0")
        holdings.append(
            Holding(
                ticker_id=int(ticker_id),
                ticker_symbol=symbol,
                exchange=exchange,
                security_name=name,
                security_type=security_type,
                quantity=quantity_value,
                cost_basis=cost_basis,
                market_value=market_value,
                last_price=last_price,
                last_price_date=_date(price_date) if price_date is not None else None,
            )
        )
    return holdings


def get_cash_summary(db_path: str = DATABASE_PATH) -> CashSummary:
    connection = get_shared_connection(db_path)
    # Prefer the most recent explicit cash balance when the source data provides one.
    row = connection.execute(
        """
        SELECT balance
        FROM cash_transactions
        WHERE balance IS NOT NULL
        ORDER BY transaction_date DESC, cash_transaction_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is not None:
        return CashSummary(balance=_decimal(row[0]), source="explicit_balance")

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


def get_historical_portfolio_values(db_path: str = DATABASE_PATH) -> list[dict[str, Any]]:
    connection = get_shared_connection(db_path)
    # Build a chronological valuation series from every relevant date in the stored tables.
    dates = [
        _date(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT value_date
            FROM (
                SELECT transaction_date AS value_date FROM transactions
                UNION
                SELECT transaction_date AS value_date FROM cash_transactions
                UNION
                SELECT record_date AS value_date FROM historical_records
            )
            WHERE value_date IS NOT NULL
            ORDER BY value_date
            """
        ).fetchall()
    ]
    ticker_ids = [
        int(row[0]) for row in connection.execute("SELECT ticker_id FROM tickers ORDER BY ticker_id").fetchall()
    ]
    results: list[dict[str, Any]] = []
    for value_date in dates:
        total = Decimal("0")
        for ticker_id in ticker_ids:
            # Use the latest known close on or before each valuation date.
            quantity_row = connection.execute(
                """
                SELECT SUM(quantity) AS total_amount
                FROM transactions
                WHERE ticker_id = ?
                  AND transaction_type = 'BUY'
                  AND transaction_date <= ?
                """,
                [ticker_id, value_date],
            ).fetchone()
            price_row = connection.execute(
                """
                SELECT close
                FROM historical_records
                WHERE ticker_id = ?
                  AND record_date <= ?
                ORDER BY record_date DESC
                LIMIT 1
                """,
                [ticker_id, value_date],
            ).fetchone()
            if quantity_row and price_row and price_row[0] is not None:
                total += _decimal(quantity_row[0]) * _decimal(price_row[0])
        results.append({"date": value_date, "portfolio_value": total})
    return results


def portfolio_report(db_path: str = DATABASE_PATH) -> dict[str, Any]:
    # Keep the dashboard-facing payload plain and easy to serialize.
    summary = get_portfolio_summary(db_path)
    return {
        "holdings": [asdict(holding) for holding in summary.holdings],
        "cash": asdict(summary.cash),
        "portfolio_value": summary.portfolio_value,
        "historical_values": get_historical_portfolio_values(db_path),
    }

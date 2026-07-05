"""Read-only portfolio analytics helpers built on the normalized DuckDB schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from config import DATABASE_PATH, WEALTHSIMPLE_FX_FEE_RATE
from database import get_shared_connection
from portfolio_metrics import estimate_wealthsimple_fx_fee_cad, financial_metrics_summary
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
    provisional_quantity: Decimal = Decimal("0")
    has_provisional_activity: bool = False


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
        WITH statement_positions AS (
            SELECT
                ticker_id,
                SUM(CASE WHEN UPPER(transaction_type) = 'SELL'
                         THEN -ABS(quantity) ELSE ABS(quantity) END) AS total_amount,
                SUM(COALESCE(debit, 0)) AS total_debit,
                SUM(COALESCE(credit, 0)) AS total_credit
            FROM transactions
            WHERE UPPER(transaction_type) IN ('BUY', 'SELL')
            GROUP BY ticker_id
        ),
        email_positions AS (
            SELECT ticker_id,
                   SUM(CASE WHEN UPPER(transaction_type) LIKE '%SELL%'
                            THEN -ABS(quantity) ELSE ABS(quantity) END) AS provisional_amount
            FROM email_transactions
            WHERE ticker_id IS NOT NULL
              AND ticker_resolution_status = 'resolved'
              AND reconciliation_status = 'provisional'
              AND (UPPER(transaction_type) LIKE '%BUY%'
                   OR UPPER(transaction_type) LIKE '%SELL%')
            GROUP BY ticker_id
        ),
        net_transactions AS (
            SELECT COALESCE(s.ticker_id, e.ticker_id) AS ticker_id,
                   COALESCE(s.total_amount, 0) + COALESCE(e.provisional_amount, 0) AS total_amount,
                   COALESCE(s.total_debit, 0) AS total_debit,
                   COALESCE(s.total_credit, 0) AS total_credit,
                   COALESCE(e.provisional_amount, 0) AS provisional_amount
            FROM statement_positions s FULL OUTER JOIN email_positions e USING (ticker_id)
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
            lp.close,
            nt.provisional_amount
        FROM net_transactions nt
        JOIN tickers t ON t.ticker_id = nt.ticker_id
        LEFT JOIN latest_prices lp ON lp.ticker_id = nt.ticker_id
        WHERE COALESCE(nt.total_amount, 0) <> 0
        ORDER BY t.ticker_symbol, t.exchange
        """
    ).fetchall()
    holdings: list[Holding] = []
    for row in rows:
        ticker_id, symbol, exchange, name, security_type, total_amount, debit, credit, price_date, close, provisional = row
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
                provisional_quantity=_decimal(provisional),
                has_provisional_activity=_decimal(provisional) != 0,
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
    """Calculate realized gains using a running weighted-average CAD cost."""
    # Prior BUY rows are required to establish cost basis even when the report
    # begins later, so only the upper boundary belongs in the SQL ledger query.
    clause, params = _date_filters(None, date_to, "tr.transaction_date")
    rows = get_shared_connection(db_path).execute(
        f"""
        SELECT tr.transaction_date, tr.transaction_type, tr.ticker_id,
               tr.quantity, tr.debit, tr.credit, t.ticker_symbol
        FROM transactions tr JOIN tickers t ON t.ticker_id = tr.ticker_id
        WHERE UPPER(tr.transaction_type) IN ('BUY', 'SELL') {clause}
        ORDER BY tr.transaction_date, tr.transaction_id
        """, params,
    ).fetchall()
    state: dict[int, tuple[Decimal, Decimal]] = {}
    gains: dict[str, Decimal] = {}
    for transaction_date, kind, ticker_id, quantity, debit, credit, symbol in rows:
        held, cost = state.get(int(ticker_id), (Decimal("0"), Decimal("0")))
        qty = abs(_decimal(quantity))
        if str(kind).upper() == "BUY":
            state[int(ticker_id)] = (held + qty, cost + _decimal(debit))
            continue
        sold = min(qty, held)
        allocated_cost = (cost / held) * sold if held > 0 else Decimal("0")
        if date_from is None or _date(transaction_date) >= date_from:
            gains[str(symbol)] = gains.get(str(symbol), Decimal("0")) + _decimal(credit) - allocated_cost
        state[int(ticker_id)] = (held - sold, cost - allocated_cost)
    return {"source": "statements", "by_ticker": gains, "total_realized_gain": sum(gains.values(), Decimal("0"))}


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


def portfolio_report(
    db_path: str = DATABASE_PATH,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    dividend_source: str = "email",
    cash_flow_source: str = "activities",
    fx_source: str = "statements",
) -> dict[str, Any]:
    # Keep the dashboard-facing payload plain and easy to serialize.
    summary = get_portfolio_summary(db_path)
    holdings = [asdict(holding) for holding in summary.holdings]
    historical_values = get_historical_portfolio_values(db_path)
    filtered_historical_values = [
        row for row in historical_values
        if (date_from is None or row["date"] >= date_from)
        and (date_to is None or row["date"] <= date_to)
    ]
    dividends = get_dividend_summary(
        db_path, source=dividend_source, date_from=date_from, date_to=date_to
    )
    commissions = get_commission_summary(db_path, date_from=date_from, date_to=date_to)
    fx_fees = get_fx_fee_summary(
        db_path, source=fx_source, date_from=date_from, date_to=date_to
    )
    cash_flow = get_cash_flow_summary(
        db_path,
        source=cash_flow_source,
        date_from=date_from,
        date_to=date_to,
        terminal_value=summary.portfolio_value,
    )
    cost_basis = sum((holding.cost_basis for holding in summary.holdings), Decimal("0"))
    cad_dividends = dividends["totals_by_currency"].get("CAD", Decimal("0"))
    return {
        "holdings": holdings,
        "cash": asdict(summary.cash),
        "portfolio_value": summary.portfolio_value,
        "historical_values": historical_values,
        "financial_metrics": financial_metrics_summary(holdings, filtered_historical_values),
        "cash_flow": cash_flow,
        "realized_gains": get_realized_gain_summary(db_path, date_from=date_from, date_to=date_to),
        "dividends": {
            **dividends,
            "yield_on_current_cost_basis": cad_dividends / cost_basis if cost_basis > 0 else Decimal("0"),
        },
        "fees": {"commissions": commissions, "fx": fx_fees},
    }

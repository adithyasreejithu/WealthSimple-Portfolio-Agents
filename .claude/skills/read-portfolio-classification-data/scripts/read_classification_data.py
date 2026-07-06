"""Read-only DuckDB access for the constrained portfolio classification workflow."""

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

from config import DATABASE_PATH, DATABASE_SCHEMA_VERSION
from database import REQUIRED_TABLES, SCHEMA_COMPONENT


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


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _validate_database(connection: duckdb.DuckDBPyConnection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()
    }
    if not REQUIRED_TABLES.issubset(tables):
        raise RuntimeError("Configured database does not contain the required schema")
    row = connection.execute(
        "SELECT schema_version FROM schema_metadata WHERE component = ?", [SCHEMA_COMPONENT]
    ).fetchone()
    if row is None or int(row[0]) != DATABASE_SCHEMA_VERSION:
        raise RuntimeError("Configured database schema version is inactive or incompatible")


def read_classification_data(db_path: str | Path = DATABASE_PATH) -> list[dict[str, Any]]:
    """Read current holdings using a hard-coded query and a read-only connection."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configured database does not exist: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_database(connection)
        rows = connection.execute(
            """
            WITH statement_activity AS (
                SELECT ticker_id,
                       SUM(CASE WHEN UPPER(transaction_type) = 'SELL' THEN -ABS(quantity) ELSE ABS(quantity) END) quantity,
                       SUM(COALESCE(debit, 0)) - SUM(COALESCE(credit, 0)) cost_basis,
                       MIN(CASE WHEN UPPER(transaction_type) = 'BUY' THEN transaction_date END) first_purchase_date,
                       MAX(CASE WHEN UPPER(transaction_type) = 'BUY' THEN transaction_date END) latest_purchase_date,
                       COUNT(*) FILTER (WHERE UPPER(transaction_type) = 'BUY') number_of_buys,
                       COUNT(*) FILTER (WHERE UPPER(transaction_type) = 'SELL') number_of_sells,
                       SUM(CASE WHEN UPPER(transaction_type) = 'DIV' THEN COALESCE(credit, 0) ELSE 0 END) dividends_received
                FROM transactions
                WHERE UPPER(transaction_type) IN ('BUY', 'SELL', 'DIV')
                GROUP BY ticker_id
            ), provisional AS (
                SELECT ticker_id,
                       SUM(CASE WHEN UPPER(transaction_type) LIKE '%SELL%' THEN -ABS(quantity) ELSE ABS(quantity) END) quantity,
                       MIN(CASE WHEN UPPER(transaction_type) LIKE '%BUY%' THEN transaction_date END) first_purchase_date,
                       MAX(CASE WHEN UPPER(transaction_type) LIKE '%BUY%' THEN transaction_date END) latest_purchase_date,
                       COUNT(*) FILTER (WHERE UPPER(transaction_type) LIKE '%BUY%') number_of_buys,
                       COUNT(*) FILTER (WHERE UPPER(transaction_type) LIKE '%SELL%') number_of_sells,
                       STRING_AGG(DISTINCT account, ', ' ORDER BY account) account_type
                FROM email_transactions
                WHERE ticker_id IS NOT NULL AND ticker_resolution_status = 'resolved'
                  AND reconciliation_status = 'provisional'
                  AND (UPPER(transaction_type) LIKE '%BUY%' OR UPPER(transaction_type) LIKE '%SELL%')
                GROUP BY ticker_id
            ), positions AS (
                SELECT COALESCE(s.ticker_id, p.ticker_id) ticker_id,
                       COALESCE(s.quantity, 0) + COALESCE(p.quantity, 0) quantity,
                       COALESCE(s.cost_basis, 0) cost_basis,
                       LEAST(s.first_purchase_date, p.first_purchase_date) first_purchase_date,
                       GREATEST(s.latest_purchase_date, p.latest_purchase_date) latest_purchase_date,
                       COALESCE(s.number_of_buys, 0) + COALESCE(p.number_of_buys, 0) number_of_buys,
                       COALESCE(s.number_of_sells, 0) + COALESCE(p.number_of_sells, 0) number_of_sells,
                       COALESCE(s.dividends_received, 0) dividends_received,
                       p.account_type
                FROM statement_activity s FULL OUTER JOIN provisional p USING (ticker_id)
            ), latest_prices AS (
                SELECT ticker_id, close,
                       ROW_NUMBER() OVER (PARTITION BY ticker_id ORDER BY record_date DESC) rn
                FROM historical_records
            ), base AS (
                SELECT t.ticker_id, t.ticker_symbol ticker, t.security_name company_name,
                       t.security_type asset_class, t.currency, t.financial_currency, t.exchange,
                       sd.sector, sd.industry, ed.fund_family, ed.yield dividend_yield,
                       ed.expense_ratio, ed.aum, ed.nav, ed.top_holdings, ed.sector_weights,
                       m.provider_symbol, p.quantity, p.cost_basis,
                       p.first_purchase_date, p.latest_purchase_date, p.number_of_buys,
                       p.number_of_sells, p.dividends_received, p.account_type,
                       CASE WHEN lp.close IS NULL THEN NULL ELSE p.quantity * lp.close END position_market_value
                FROM positions p JOIN tickers t USING (ticker_id)
                LEFT JOIN stock_details sd USING (ticker_id)
                LEFT JOIN etf_details ed USING (ticker_id)
                LEFT JOIN ticker_provider_mappings m ON m.ticker_id = t.ticker_id
                    AND m.provider = 'yahoo' AND m.verification_status = 'verified'
                LEFT JOIN latest_prices lp ON lp.ticker_id = t.ticker_id AND lp.rn = 1
                WHERE COALESCE(p.quantity, 0) <> 0
            )
            SELECT *, CASE WHEN SUM(position_market_value) OVER () > 0
                           THEN 100 * position_market_value / SUM(position_market_value) OVER () END current_weight_percent
            FROM base ORDER BY ticker, exchange
            """
        )
        columns = [item[0] for item in rows.description]
        records = [dict(zip(columns, row)) for row in rows.fetchall()]
    finally:
        connection.close()

    for record in records:
        record["etf_category"] = None
        record["market_cap"] = None
        record["user_thesis"] = None
        record["target_weight_percent"] = None
        record["unrealized_gain_loss_percent"] = None
        record["field_provenance"] = {
            key: ("derived" if key in {"position_market_value", "current_weight_percent"} else "database")
            for key, value in record.items()
            if key != "field_provenance" and not _is_missing(value)
        }
        for key, value in record.items():
            if key != "field_provenance" and _is_missing(value):
                record["field_provenance"][key] = "missing"
    return [_json_value(record) for record in records]


if __name__ == "__main__":
    json.dump(read_classification_data(), sys.stdout)
    sys.stdout.write("\n")

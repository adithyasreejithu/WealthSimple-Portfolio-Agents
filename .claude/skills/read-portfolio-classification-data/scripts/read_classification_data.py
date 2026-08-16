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
from market_data import RESEARCH_STATUSES
from position_engine import FINGERPRINT_COMPONENT, compute_fingerprint


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


def _validate_positions_fresh(connection: duckdb.DuckDBPyConnection) -> None:
    """Ensure `position_snapshots` reflects the current source tables.

    This script only ever opens a read-only connection, so it cannot itself
    recompute positions (see `position_engine.py`). Instead of silently
    reading stale holdings, it raises an actionable error naming the CLI
    command that fixes it.
    """
    row = connection.execute(
        "SELECT ledger_fingerprint FROM position_engine_meta WHERE component = ?",
        [FINGERPRINT_COMPONENT],
    ).fetchone()
    stored_fingerprint = row[0] if row else None
    if stored_fingerprint != compute_fingerprint(connection):
        raise RuntimeError(
            "Position data is stale (transactions/activities/email changed since the "
            "last recompute). Run `python src/app.py recompute-positions` and retry."
        )


def read_classification_data(db_path: str | Path = DATABASE_PATH) -> list[dict[str, Any]]:
    """Read current holdings from `position_snapshots`, the average-cost engine's
    output, using a read-only connection.

    See `docs/architecture/ingestion_and_reconciliation.md` for how
    `position_snapshots`/`position_ledger` are built (statement + activities
    export + email trades, deduplicated by source precedence, splits applied,
    average-cost book value in CAD).
    """
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configured database does not exist: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_database(connection)
        _validate_positions_fresh(connection)
        rows = connection.execute(
            """
            WITH ledger_stats AS (
                SELECT ticker_id,
                       MIN(CASE WHEN event_type = 'BUY' THEN event_date END) first_purchase_date,
                       MAX(CASE WHEN event_type = 'BUY' THEN event_date END) latest_purchase_date,
                       COUNT(*) FILTER (WHERE event_type = 'BUY') number_of_buys,
                       COUNT(*) FILTER (WHERE event_type = 'SELL') number_of_sells
                FROM position_ledger
                GROUP BY ticker_id
            ), dividends AS (
                SELECT ticker_id, SUM(COALESCE(credit, 0)) dividends_received
                FROM transactions
                WHERE UPPER(transaction_type) = 'DIV'
                GROUP BY ticker_id
            ), accounts AS (
                SELECT ticker_id, STRING_AGG(DISTINCT account_type, ', ' ORDER BY account_type) account_type
                FROM activities
                WHERE ticker_id IS NOT NULL
                GROUP BY ticker_id
            ), latest_prices AS (
                SELECT ticker_id, close,
                       ROW_NUMBER() OVER (PARTITION BY ticker_id ORDER BY record_date DESC) rn
                FROM historical_records
            ), base AS (
                SELECT t.ticker_id, t.ticker_symbol ticker, t.security_name company_name,
                       t.security_type asset_class, t.currency, t.financial_currency, t.exchange,
                       sd.sector, sd.industry, ed.fund_family, ed.yield dividend_yield,
                       ed.expense_ratio, ed.aum, ed.nav, ed.top_holdings, ed.sector_weights,
                       m.provider_symbol, s.quantity, s.book_value_cad cost_basis,
                       s.provisional_quantity, s.data_quality_flags,
                       ls.first_purchase_date, ls.latest_purchase_date,
                       COALESCE(ls.number_of_buys, 0) number_of_buys,
                       COALESCE(ls.number_of_sells, 0) number_of_sells,
                       COALESCE(d.dividends_received, 0) dividends_received, a.account_type,
                       CASE WHEN lp.close IS NULL THEN NULL ELSE s.quantity * lp.close END position_market_value
                FROM position_snapshots s JOIN tickers t USING (ticker_id)
                LEFT JOIN ledger_stats ls USING (ticker_id)
                LEFT JOIN dividends d USING (ticker_id)
                LEFT JOIN accounts a USING (ticker_id)
                LEFT JOIN stock_details sd USING (ticker_id)
                LEFT JOIN etf_details ed USING (ticker_id)
                LEFT JOIN ticker_provider_mappings m ON m.ticker_id = t.ticker_id
                    AND m.provider = 'yahoo' AND m.verification_status = 'verified'
                LEFT JOIN latest_prices lp ON lp.ticker_id = t.ticker_id AND lp.rn = 1
                WHERE s.quantity <> 0
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
        record["ownership_status"] = "owned"
        market_value = record.get("position_market_value")
        cost_basis = record.get("cost_basis")
        record["unrealized_gain_loss_percent"] = (
            (float(market_value) - float(cost_basis)) / float(cost_basis) * 100
            if market_value is not None and cost_basis not in (None, 0)
            else None
        )
        # data_quality_flags is a JSON column; DuckDB returns it as JSON text
        # (see analytics.py's identical json.loads(flags_json) handling), so
        # unwrap it into a real list here rather than leaving raw JSON text
        # for downstream consumers to parse themselves.
        raw_flags = record.get("data_quality_flags")
        record["data_quality_flags"] = json.loads(raw_flags) if raw_flags else []
        # A non-zero provisional_quantity means part of this holding's quantity
        # is still sourced from an unmatched email trade, not yet confirmed by
        # a statement or activities export (see ingestion_and_reconciliation.md).
        record["has_provisional_activity"] = bool(record.get("provisional_quantity"))
        record["field_provenance"] = _build_field_provenance(record)
    return [_json_value(record) for record in records]


def _build_field_provenance(record: dict[str, Any]) -> dict[str, str]:
    """Shared by both `read_classification_data` and
    `read_wishlist_classification_data`: `"derived"` for the small set of
    Python-computed fields, `"database"` for everything else populated,
    `"missing"` for anything absent."""
    provenance = {
        key: (
            "derived"
            if key in {
                "position_market_value", "current_weight_percent",
                "has_provisional_activity", "unrealized_gain_loss_percent",
            }
            else "database"
        )
        for key, value in record.items()
        if key != "field_provenance" and not _is_missing(value)
    }
    for key, value in record.items():
        if key != "field_provenance" and _is_missing(value):
            provenance[key] = "missing"
    return provenance


def read_wishlist_classification_data(db_path: str | Path = DATABASE_PATH) -> list[dict[str, Any]]:
    """Read declared-wishlist tickers (`security_status.declared_status` in
    `RESEARCH_STATUSES`) using the same read-only connection discipline as
    `read_classification_data`, so `classify_portfolio()` can classify them
    alongside owned holdings instead of requiring a one-off script.

    Excludes any ticker currently owned (`position_snapshots.quantity <> 0`)
    even if it still carries a stale `wishlist` declaration -- ownership
    always wins over any declaration (see `database._create_security_status_table`'s
    docstring and `analytics.resolve_security_status`), so such a ticker is
    already covered by `read_classification_data` and must not be classified
    twice. Every ownership-derived field (`quantity`, `cost_basis`,
    `position_market_value`, ledger/dividend/account summaries, ...) is a
    fixed "not owned" default -- a wishlist ticker has no position to compute
    them from, by construction, not because data is missing.

    Does not call `_validate_positions_fresh`: this function reads
    `position_snapshots` only as a live exclusion filter, never for cached
    position pricing, so a stale ledger fingerprint (which
    `read_classification_data` must guard against for its owned-holding
    pricing) has no bearing on correctness here.
    """
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configured database does not exist: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        _validate_database(connection)
        placeholders = ", ".join("?" for _ in RESEARCH_STATUSES)
        rows = connection.execute(
            f"""
            SELECT t.ticker_id, t.ticker_symbol ticker, t.security_name company_name,
                   t.security_type asset_class, t.currency, t.financial_currency, t.exchange,
                   sd.sector, sd.industry, ed.fund_family, ed.yield dividend_yield,
                   ed.expense_ratio, ed.aum, ed.nav, ed.top_holdings, ed.sector_weights,
                   m.provider_symbol
            FROM tickers t
            JOIN security_status ss USING (ticker_id)
            LEFT JOIN stock_details sd USING (ticker_id)
            LEFT JOIN etf_details ed USING (ticker_id)
            LEFT JOIN ticker_provider_mappings m ON m.ticker_id = t.ticker_id
                AND m.provider = 'yahoo' AND m.verification_status = 'verified'
            WHERE ss.declared_status IN ({placeholders})
              AND t.ticker_id NOT IN (
                  SELECT ticker_id FROM position_snapshots WHERE quantity <> 0
              )
            ORDER BY ticker, exchange
            """,
            list(RESEARCH_STATUSES),
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
        record["ownership_status"] = "wishlist"
        record["quantity"] = 0
        record["cost_basis"] = None
        record["provisional_quantity"] = 0
        record["position_market_value"] = None
        record["current_weight_percent"] = None
        record["unrealized_gain_loss_percent"] = None
        record["has_provisional_activity"] = False
        record["data_quality_flags"] = []
        record["first_purchase_date"] = None
        record["latest_purchase_date"] = None
        record["number_of_buys"] = 0
        record["number_of_sells"] = 0
        record["dividends_received"] = 0
        record["account_type"] = None
        record["field_provenance"] = _build_field_provenance(record)
    return [_json_value(record) for record in records]


if __name__ == "__main__":
    json.dump(read_classification_data(), sys.stdout)
    sys.stdout.write("\n")

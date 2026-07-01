"""Shared ticker resolution, upload, and checkpoint database commands."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from config import DATABASE_PATH
from database import get_shared_connection
from system_logger import get_logger
from yfinance_extractor import TickerHint, configure_yfinance_cache, fetch_security_info


logger = get_logger(__name__)
SecurityFetcher = Callable[[list[Any]], tuple[pd.DataFrame, pd.DataFrame]]
EMAIL_CHECKPOINT_SOURCE = "wealthsimple_email"


def _text(value: Any) -> str:
    if value is None or value is pd.NA:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    normalized = _text(value).replace("$", "").replace(",", "")
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid numeric value: {value}") from exc


def _optional_decimal(value: Any) -> Decimal | None:
    """Convert optional provider numbers to Decimal, treating non-finite values as missing."""
    result = _decimal(value)
    return None if result is None or not result.is_finite() else result


def _optional_date(value: Any) -> str | None:
    """Convert optional date-like values to ISO dates and drop pandas NaN/NaT values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        value = value.to_dict(orient="records")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return json.dumps(value, default=str)


def _ticker_rows(db_path: Path | str = DATABASE_PATH) -> dict[str, list[dict[str, Any]]]:
    rows = get_shared_connection(db_path).execute(
        """
        SELECT ticker_id, ticker_symbol, exchange, currency, security_name
        FROM tickers
        """
    ).fetchall()
    tickers: dict[str, list[dict[str, Any]]] = {}
    for ticker_id, symbol, exchange, currency, name in rows:
        tickers.setdefault(symbol, []).append(
            {
                "ticker_id": int(ticker_id),
                "exchange": exchange,
                "currency": currency,
                "security_name": name,
            }
        )
    return tickers


def _insert_metadata_frame(
    data: pd.DataFrame,
    security_type: str,
    db_path: Path | str,
) -> None:
    connection = get_shared_connection(db_path)
    for row in data.to_dict(orient="records"):
        symbol = _text(row.get("ticker")).upper()
        exchange = _text(row.get("exchange")).upper()
        currency = _text(row.get("currency")).upper()
        financial_currency = _text(row.get("financial_currency")).upper() or None
        name = _text(row.get("company_name")) or symbol
        if not symbol or not exchange or not currency:
            logger.warning(
                "Skipping incomplete yfinance metadata | ticker=%s | exchange=%s | currency=%s",
                symbol,
                exchange,
                currency,
            )
            continue
        ticker_id = int(
            connection.execute(
                """
                INSERT INTO tickers (
                    ticker_symbol, exchange, currency, financial_currency,
                    security_name, security_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker_symbol, exchange) DO UPDATE SET
                    currency = excluded.currency,
                    financial_currency = excluded.financial_currency,
                    security_name = excluded.security_name,
                    security_type = excluded.security_type
                RETURNING ticker_id
                """,
                [symbol, exchange, currency, financial_currency, name, security_type],
            ).fetchone()[0]
        )
        if security_type == "stock":
            connection.execute(
                """
                INSERT INTO stock_details (ticker_id, sector, industry)
                VALUES (?, ?, ?)
                ON CONFLICT (ticker_id) DO UPDATE SET
                    sector = excluded.sector,
                    industry = excluded.industry
                """,
                [ticker_id, _text(row.get("sector")) or None, _text(row.get("industry")) or None],
            )
        else:
            numeric_fields = {
                field: _optional_decimal(row.get(field))
                for field in ("yield", "expense_ratio", "aum", "nav")
            }
            missing_fields = [field for field, value in numeric_fields.items() if value is None]
            if missing_fields:
                logger.warning(
                    "Storing missing ETF metadata as NULL | ticker=%s | fields=%s",
                    symbol,
                    ",".join(missing_fields),
                )
            connection.execute(
                """
                INSERT INTO etf_details (
                    ticker_id, fund_family, yield, expense_ratio, aum, nav,
                    top_holdings, sector_weights
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker_id) DO UPDATE SET
                    fund_family = excluded.fund_family,
                    yield = excluded.yield,
                    expense_ratio = excluded.expense_ratio,
                    aum = excluded.aum,
                    nav = excluded.nav,
                    top_holdings = excluded.top_holdings,
                    sector_weights = excluded.sector_weights
                """,
                [
                    ticker_id,
                    _text(row.get("fund_family")) or None,
                    numeric_fields["yield"],
                    numeric_fields["expense_ratio"],
                    numeric_fields["aum"],
                    numeric_fields["nav"],
                    _json(row.get("top_holdings")),
                    _json(row.get("sector_weights")),
                ],
            )
        provider_symbol = _text(row.get("provider_symbol")) or symbol
        connection.execute(
            """
            INSERT INTO ticker_provider_mappings (
                ticker_id, provider, provider_symbol, verification_status
            ) VALUES (?, 'yahoo', ?, 'verified')
            ON CONFLICT (ticker_id, provider) DO UPDATE SET
                provider_symbol = excluded.provider_symbol,
                verification_status = excluded.verification_status,
                verified_at = now()
            """,
            [ticker_id, provider_symbol.upper()],
        )
        connection.execute(
            """
            INSERT INTO ticker_symbol_history (
                ticker_id, source_symbol, provider_symbol, currency, exchange,
                reason, mapping_source, created_by
            ) VALUES (?, ?, ?, ?, ?, 'validated provider resolution', 'automatic', 'pipeline')
            ON CONFLICT DO NOTHING
            """,
            [ticker_id, symbol, provider_symbol.upper(), currency, exchange],
        )
        logger.info(
            "Ticker metadata stored | ticker=%s | provider_symbol=%s | "
            "trading_currency=%s | financial_currency=%s | exchange=%s",
            symbol, provider_symbol.upper(), currency, financial_currency or "", exchange,
        )


def ensure_tickers(
    symbols: list[str | TickerHint | dict[str, Any]],
    db_path: Path | str = DATABASE_PATH,
    fetcher: SecurityFetcher = fetch_security_info,
    require_all: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Create metadata for first-seen symbols and return all ticker candidates."""
    hints: list[TickerHint] = []
    seen_hints: set[tuple[str, str]] = set()
    for value in symbols:
        if isinstance(value, TickerHint):
            hint = value
        elif isinstance(value, dict):
            hint = TickerHint(
                _text(value.get("symbol") or value.get("ticker")).upper(),
                _text(value.get("currency")).upper(),
                _text(value.get("name") or value.get("company_name")),
            )
        else:
            hint = TickerHint(_text(value).upper())
        key = (hint.symbol, hint.currency)
        if hint.symbol and key not in seen_hints:
            hints.append(hint)
            seen_hints.add(key)
    normalized = sorted({hint.symbol for hint in hints})
    candidates = _ticker_rows(db_path)
    reused_count = 0
    missing: list[TickerHint] = []
    for hint in hints:
        matches = candidates.get(hint.symbol, [])
        currency = hint.currency.upper()
        if not matches or (currency and not any(_text(match["currency"]).upper() == currency for match in matches)):
            missing.append(hint)
        else:
            reused_count += 1
    fully_enriched = 0
    partially_enriched = 0
    if missing:
        logger.info("Fetching metadata for %d first-seen ticker(s)", len(missing))
        if fetcher is fetch_security_info:
            configure_yfinance_cache(Path(__import__("tempfile").gettempdir()) / "wealthsimple-yfinance-cache")
        requested = missing
        fetch_values: list[Any] = (
            requested
            if any(hint.currency or hint.name for hint in requested)
            else [hint.symbol for hint in missing]
        )
        stocks, etfs = fetcher(fetch_values)
        fully_enriched += len(stocks)
        for row in etfs.to_dict(orient="records"):
            numeric = [_optional_decimal(row.get(field)) for field in ("yield", "expense_ratio", "aum", "nav")]
            if all(value is not None for value in numeric):
                fully_enriched += 1
            else:
                partially_enriched += 1
        _insert_metadata_frame(stocks, "stock", db_path)
        _insert_metadata_frame(etfs, "etf", db_path)
        candidates = _ticker_rows(db_path)
    unresolved = []
    for hint in hints:
        matches = candidates.get(hint.symbol, [])
        if not matches or (
            hint.currency
            and not any(_text(match["currency"]).upper() == hint.currency.upper() for match in matches)
        ):
            unresolved.append(f"{hint.symbol}/{hint.currency}" if hint.currency else hint.symbol)
    if unresolved and require_all:
        raise ValueError(f"Ticker metadata could not be resolved: {', '.join(unresolved)}")
    if unresolved:
        logger.warning("Ticker metadata unresolved: %s", ", ".join(unresolved))
    logger.info(
        "Ticker enrichment summary | requested=%d | reused=%d | full=%d | partial=%d | unresolved=%d",
        len(hints), reused_count, fully_enriched, partially_enriched, len(unresolved),
    )
    return candidates


def normalize_ticker_dataframe(
    data: pd.DataFrame,
    symbol_column: str,
    db_path: Path | str = DATABASE_PATH,
    fetcher: SecurityFetcher = fetch_security_info,
) -> pd.DataFrame:
    """Replace source ticker text with an unambiguous canonical ticker_id."""
    normalized = data.copy()
    if normalized.empty:
        normalized["ticker_id"] = pd.Series(dtype="Int64")
        return normalized
    symbols = normalized[symbol_column].map(_text)
    candidates = ensure_tickers(symbols.tolist(), db_path, fetcher)
    resolved: dict[str, int] = {}
    for symbol in sorted({value.upper() for value in symbols if value}):
        matches = candidates.get(symbol, [])
        if len(matches) != 1:
            exchanges = ", ".join(sorted(match["exchange"] for match in matches))
            raise ValueError(f"Ticker {symbol} is ambiguous across exchanges: {exchanges}")
        resolved[symbol] = matches[0]["ticker_id"]
    normalized["ticker_id"] = symbols.map(
        lambda symbol: resolved.get(symbol.upper()) if symbol else None
    ).astype("Int64")
    if symbol_column != "ticker_id":
        normalized = normalized.drop(columns=[symbol_column])
    return normalized


def upload_statement_transactions(
    data: pd.DataFrame,
    db_path: Path | str = DATABASE_PATH,
) -> int:
    connection = get_shared_connection(db_path)
    written = 0
    for row in data.to_dict(orient="records"):
        ticker_id = row.get("ticker_id")
        transaction_date = _optional_date(row.get("date"))
        transaction_type = _text(row.get("transaction")) or "UNKNOWN"
        execution_date = _optional_date(row.get("execDate"))
        debit = _decimal(row.get("debit"))
        credit = _decimal(row.get("credit"))
        fx_rate = _decimal(row.get("fx_rate"))
        if ticker_id is None or pd.isna(ticker_id):
            balance = _decimal(row.get("balance"))
            cash_values = [
                transaction_date,
                transaction_type,
                execution_date,
                debit or Decimal(0),
                credit or Decimal(0),
                fx_rate or Decimal(0),
                balance,
            ]
            duplicate = connection.execute(
                """
                SELECT 1 FROM cash_transactions
                WHERE transaction_date = ?
                  AND transaction_type = ?
                  AND execution_date IS NOT DISTINCT FROM ?
                  AND debit = ?
                  AND credit = ?
                  AND fx_rate = ?
                  AND balance IS NOT DISTINCT FROM ?
                """,
                cash_values,
            ).fetchone()
            if duplicate:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO cash_transactions (
                    transaction_date, transaction_type, execution_date,
                    debit, credit, fx_rate, balance
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                cash_values,
            )
        else:
            transaction_values = [
                transaction_date,
                transaction_type,
                int(ticker_id),
                _decimal(row.get("quantity")),
                execution_date,
                debit,
                credit,
                fx_rate,
            ]
            duplicate = connection.execute(
                """
                SELECT 1 FROM transactions
                WHERE transaction_date = ?
                  AND transaction_type = ?
                  AND ticker_id = ?
                  AND quantity IS NOT DISTINCT FROM ?
                  AND execution_date IS NOT DISTINCT FROM ?
                  AND debit IS NOT DISTINCT FROM ?
                  AND credit IS NOT DISTINCT FROM ?
                  AND fx_rate IS NOT DISTINCT FROM ?
                """,
                transaction_values,
            ).fetchone()
            if duplicate:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO transactions (
                    transaction_date, transaction_type, ticker_id, quantity,
                    execution_date, debit, credit, fx_rate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                transaction_values,
            )
        written += 1
    logger.info("Statement upload complete | inserted=%d | input=%d", written, len(data))
    return written


def upload_email_transactions(
    data: pd.DataFrame,
    db_path: Path | str = DATABASE_PATH,
) -> int:
    connection = get_shared_connection(db_path)
    written = 0
    for row in data.to_dict(orient="records"):
        ticker_id = row.get("ticker_id")
        values = [
            _text(row.get("account")) or None,
            _text(row.get("transaction")) or "UNKNOWN",
            None if ticker_id is None or pd.isna(ticker_id) else int(ticker_id),
            _decimal(row.get("quantity")),
            _decimal(row.get("avg_price")),
            _decimal(row.get("total_cost")),
            _decimal(row.get("debit")),
            _optional_date(row.get("date")),
        ]
        duplicate = connection.execute(
            """
            SELECT 1 FROM email_transactions
            WHERE account IS NOT DISTINCT FROM ?
              AND transaction_type = ?
              AND ticker_id IS NOT DISTINCT FROM ?
              AND quantity IS NOT DISTINCT FROM ?
              AND average_price IS NOT DISTINCT FROM ?
              AND total_cost IS NOT DISTINCT FROM ?
              AND debit IS NOT DISTINCT FROM ?
              AND transaction_date = ?
            """,
            values,
        ).fetchone()
        if duplicate:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO email_transactions (
                account, transaction_type, ticker_id, quantity, average_price,
                total_cost, debit, transaction_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        written += 1
    logger.info("Email upload complete | inserted=%d | input=%d", written, len(data))
    return written


def get_email_checkpoint(
    db_path: Path | str = DATABASE_PATH,
    source: str = EMAIL_CHECKPOINT_SOURCE,
) -> date | None:
    row = get_shared_connection(db_path).execute(
        "SELECT checked_through_date FROM email_checkpoints WHERE source = ?",
        [source],
    ).fetchone()
    return row[0] if row else None


def update_email_checkpoint(
    checked_through_date: date,
    email_count: int,
    db_path: Path | str = DATABASE_PATH,
    source: str = EMAIL_CHECKPOINT_SOURCE,
) -> None:
    get_shared_connection(db_path).execute(
        """
        INSERT INTO email_checkpoints (source, checked_through_date, email_count)
        VALUES (?, ?, ?)
        ON CONFLICT (source) DO UPDATE SET
            checked_through_date = excluded.checked_through_date,
            email_count = email_checkpoints.email_count + excluded.email_count,
            updated_at = now()
        """,
        [source, checked_through_date, email_count],
    )

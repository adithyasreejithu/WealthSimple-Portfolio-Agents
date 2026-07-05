"""Shared ticker resolution, upload, and checkpoint database commands."""

from __future__ import annotations

import json
import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from config import DATABASE_PATH
from database import get_shared_connection
from portfolio_metrics import estimate_wealthsimple_fx_fee_cad
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
    ticker_ids: dict[str, int] | None = None,
) -> None:
    """Persist provider enrichment without mutating an existing ticker identity."""
    connection = get_shared_connection(db_path)
    for row in data.to_dict(orient="records"):
        symbol = _text(row.get("ticker")).upper()
        provider_symbol = _text(row.get("provider_symbol")).upper() or symbol
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

        if ticker_ids is not None:
            ticker_id = ticker_ids.get(provider_symbol)
            if ticker_id is None:
                logger.warning(
                    "Skipping unexpected yfinance metadata | ticker=%s | provider_symbol=%s",
                    symbol,
                    provider_symbol,
                )
                continue
        else:
            existing = connection.execute(
                """
                SELECT ticker_id, currency, financial_currency, security_name, security_type
                FROM tickers
                WHERE ticker_symbol = ? AND exchange = ?
                """,
                [symbol, exchange],
            ).fetchone()
            if existing:
                ticker_id = int(existing[0])
            else:
                ticker_id = int(connection.execute(
                    """
                    INSERT INTO tickers (
                        ticker_symbol, exchange, currency, financial_currency,
                        security_name, security_type
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING ticker_id
                    """,
                    [symbol, exchange, currency, financial_currency, name, security_type],
                ).fetchone()[0])

        stored = connection.execute(
            """
            SELECT ticker_symbol, exchange, currency, financial_currency,
                   security_name, security_type
            FROM tickers WHERE ticker_id = ?
            """,
            [ticker_id],
        ).fetchone()
        if stored is None:
            logger.warning(
                "Skipping yfinance metadata for missing ticker identity | ticker_id=%s",
                ticker_id,
            )
            continue
        provider_identity = (symbol, exchange, currency, financial_currency, name, security_type)
        if tuple(stored) != provider_identity:
            logger.info(
                "Preserving ticker identity despite yfinance metadata difference | "
                "ticker_id=%s | stored=%s | provider=%s",
                ticker_id,
                tuple(stored),
                provider_identity,
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
        if ticker_ids is None:
            connection.execute(
                """
                INSERT INTO ticker_provider_mappings (
                    ticker_id, provider, provider_symbol, verification_status
                ) VALUES (?, 'yahoo', ?, 'verified')
                ON CONFLICT DO NOTHING
                """,
                [ticker_id, provider_symbol],
            )
            connection.execute(
                """
                INSERT INTO ticker_symbol_history (
                    ticker_id, source_symbol, provider_symbol, currency, exchange,
                    reason, mapping_source, created_by
                ) VALUES (?, ?, ?, ?, ?, 'validated provider resolution', 'automatic', 'pipeline')
                ON CONFLICT DO NOTHING
                """,
                [ticker_id, symbol, provider_symbol, currency, exchange],
            )
        logger.info(
            "Ticker metadata stored | ticker=%s | provider_symbol=%s | "
            "trading_currency=%s | financial_currency=%s | exchange=%s",
            symbol, provider_symbol, currency, financial_currency or "", exchange,
        )


def upload_security_metadata(
    stocks: pd.DataFrame,
    etfs: pd.DataFrame,
    db_path: Path | str = DATABASE_PATH,
    *,
    ticker_ids: dict[str, int] | None = None,
) -> int:
    """Store enrichment, inserting ticker identities only during onboarding."""
    normalized_ids = (
        {symbol.upper(): ticker_id for symbol, ticker_id in ticker_ids.items()}
        if ticker_ids is not None else None
    )
    _insert_metadata_frame(stocks, "stock", db_path, normalized_ids)
    _insert_metadata_frame(etfs, "etf", db_path, normalized_ids)
    return len(stocks) + len(etfs)


def upload_security_history(
    data: pd.DataFrame,
    ticker_ids: dict[str, int],
    db_path: Path | str = DATABASE_PATH,
) -> int:
    """Upsert normalized yfinance history by provider symbol and market date."""
    connection = get_shared_connection(db_path)
    written = 0
    for row in data.to_dict(orient="records"):
        provider_symbol = _text(row.get("Ticker")).upper()
        ticker_id = ticker_ids.get(provider_symbol)
        record_date = _optional_date(row.get("Date"))
        close = _optional_decimal(row.get("Close"))
        adjusted_close = _optional_decimal(row.get("Adj Close")) or close
        values = [
            _optional_decimal(row.get(field))
            for field in ("Open", "High", "Low")
        ]
        volume = _optional_decimal(row.get("Volume"))
        if ticker_id is None:
            raise ValueError(f"No ticker_id mapping for yfinance symbol {provider_symbol}")
        if not record_date or close is None or adjusted_close is None or any(
            value is None for value in values
        ) or volume is None:
            raise ValueError(
                f"Incomplete yfinance history row for {provider_symbol or 'unknown'}"
            )
        if volume < 0 or volume != volume.to_integral_value():
            raise ValueError(f"Invalid yfinance volume for {provider_symbol}")
        connection.execute(
            """
            INSERT INTO historical_records (
                ticker_id, record_date, open, high, low, close,
                adjusted_close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ticker_id, record_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                adjusted_close = excluded.adjusted_close,
                volume = excluded.volume
            """,
            [
                ticker_id,
                record_date,
                values[0],
                values[1],
                values[2],
                close,
                adjusted_close,
                int(volume),
            ],
        )
        written += 1
    return written


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
    fx_count = 0
    fx_exposure = Decimal("0")
    estimated_fx_fees = Decimal("0")
    for row in data.to_dict(orient="records"):
        ticker_id = row.get("ticker_id")
        transaction_date = _optional_date(row.get("date"))
        transaction_type = _text(row.get("transaction")) or "UNKNOWN"
        execution_date = _optional_date(row.get("execDate"))
        debit = _decimal(row.get("debit"))
        credit = _decimal(row.get("credit"))
        fx_rate = _decimal(row.get("fx_rate"))
        kind = transaction_type.upper()
        cad_amount = debit if kind == "BUY" else credit if kind == "SELL" else None
        if fx_rate is not None and fx_rate > 0 and cad_amount is not None and cad_amount > 0:
            fx_count += 1
            fx_exposure += cad_amount
            estimated_fx_fees += estimate_wealthsimple_fx_fee_cad(kind, cad_amount)
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
    logger.info(
        "Statement FX summary | transactions=%d | cad_exposure=%s | estimated_fee=%s",
        fx_count,
        fx_exposure,
        estimated_fx_fees,
    )
    return written


def upload_email_transactions(
    data: pd.DataFrame,
    db_path: Path | str = DATABASE_PATH,
) -> int:
    connection = get_shared_connection(db_path)
    written = 0
    for row in data.to_dict(orient="records"):
        source = "interac" if _text(row.get("ticker")).upper() == "EMAIL" else "wealthsimple"
        source_message_id = _text(row.get("source_message_id"))
        if not source_message_id:
            source_message_id = "legacy:" + hashlib.sha256(
                json.dumps(row, default=str, sort_keys=True).encode("utf-8")
            ).hexdigest()
        existing_message = connection.execute(
            "SELECT email_message_id FROM email_messages WHERE source = ? AND source_message_id = ?",
            [source, source_message_id],
        ).fetchone()
        if existing_message:
            continue
        content_hash = hashlib.sha256(
            json.dumps(row, default=str, sort_keys=True).encode("utf-8")
        ).hexdigest()
        email_message_id = int(connection.execute(
            """
            INSERT INTO email_messages (
                source, source_message_id, received_at, content_hash
            ) VALUES (?, ?, ?, ?) RETURNING email_message_id
            """,
            [source, source_message_id, row.get("received_at") or None, content_hash],
        ).fetchone()[0])
        ticker_id = row.get("ticker_id")
        resolved = ticker_id is not None and not pd.isna(ticker_id)
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
                total_cost, debit, transaction_date, email_message_id,
                source_symbol, price_currency, ticker_resolution_status,
                reconciliation_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values + [
                email_message_id,
                _text(row.get("ticker")) or None,
                _text(row.get("price_currency")) or None,
                "resolved" if resolved else "pending",
                "not_applicable" if source == "interac" else "provisional",
            ],
        )
        written += 1
    logger.info("Email upload complete | inserted=%d | input=%d", written, len(data))
    logger.info(
        "Email FX summary unavailable | transactions=0 | reason=missing applied FX rate and confirmed CAD amount"
    )
    return written


def reconcile_email_transactions(db_path: Path | str = DATABASE_PATH) -> int:
    """Supersede provisional email trades when one statement row matches exactly."""
    connection = get_shared_connection(db_path)
    rows = connection.execute(
        """
        SELECT email_transaction_id, ticker_id, transaction_type,
               ABS(quantity), transaction_date
        FROM email_transactions
        WHERE ticker_id IS NOT NULL
          AND ticker_resolution_status = 'resolved'
          AND reconciliation_status = 'provisional'
          AND (UPPER(transaction_type) LIKE '%BUY%'
               OR UPPER(transaction_type) LIKE '%SELL%')
        ORDER BY email_transaction_id
        """
    ).fetchall()
    reconciled = 0
    for email_id, ticker_id, transaction_type, quantity, transaction_date in rows:
        direction = "SELL" if "SELL" in str(transaction_type).upper() else "BUY"
        candidates = connection.execute(
            """
            SELECT transaction_id
            FROM transactions tr
            WHERE tr.ticker_id = ?
              AND UPPER(tr.transaction_type) = ?
              AND ABS(tr.quantity) = ?
              AND COALESCE(tr.execution_date, tr.transaction_date) = ?
              AND NOT EXISTS (
                  SELECT 1 FROM email_transactions et
                  WHERE et.matched_transaction_id = tr.transaction_id
              )
            ORDER BY transaction_id
            """,
            [ticker_id, direction, quantity, transaction_date],
        ).fetchall()
        if not candidates:
            nearby = connection.execute(
                """
                SELECT transaction_id
                FROM transactions tr
                WHERE tr.ticker_id = ?
                  AND UPPER(tr.transaction_type) = ?
                  AND ABS(tr.quantity) = ?
                  AND ABS(date_diff('day', COALESCE(tr.execution_date, tr.transaction_date), ?)) <= 3
                  AND NOT EXISTS (
                      SELECT 1 FROM email_transactions et
                      WHERE et.matched_transaction_id = tr.transaction_id
                  )
                ORDER BY transaction_id
                """,
                [ticker_id, direction, quantity, transaction_date],
            ).fetchall()
            if len(nearby) == 1:
                candidates = nearby
            elif len(nearby) > 1:
                connection.execute(
                    """
                    UPDATE email_transactions
                    SET reconciliation_status = 'review_required'
                    WHERE email_transaction_id = ?
                    """,
                    [email_id],
                )
                logger.warning(
                    "Email reconciliation requires review | email_transaction_id=%d | candidates=%d",
                    email_id, len(nearby),
                )
                continue
            else:
                continue
        if len(candidates) > 1:
            logger.info(
                "Email reconciliation pairing repeated trade deterministically | "
                "email_transaction_id=%d | candidates=%d",
                email_id, len(candidates),
            )
        connection.execute(
            """
            UPDATE email_transactions
            SET reconciliation_status = 'superseded', matched_transaction_id = ?
            WHERE email_transaction_id = ?
            """,
            [candidates[0][0], email_id],
        )
        reconciled += 1
    logger.info("Email reconciliation complete | reconciled=%d | candidates=%d", reconciled, len(rows))
    return reconciled


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
    checked_through_date: date | datetime,
    email_count: int,
    db_path: Path | str = DATABASE_PATH,
    source: str = EMAIL_CHECKPOINT_SOURCE,
) -> None:
    get_shared_connection(db_path).execute(
        """
        INSERT INTO email_checkpoints (
            source, checked_through_date, checked_through_at, email_count
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT (source) DO UPDATE SET
            checked_through_date = excluded.checked_through_date,
            checked_through_at = excluded.checked_through_at,
            email_count = email_checkpoints.email_count + excluded.email_count,
            updated_at = now()
        """,
        [source, checked_through_date, checked_through_date, email_count],
    )

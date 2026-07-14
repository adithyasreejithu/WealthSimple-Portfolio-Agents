"""Shared ticker resolution, upload, and checkpoint database commands."""

from __future__ import annotations

import json
import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from config import (
    BASE_DIR,
    DATABASE_PATH,
    RECON_DATE_WINDOW_DAYS_EMAIL,
    RECON_DATE_WINDOW_DAYS_STMT,
    RECON_QTY_ABS_TOL,
    RECON_QTY_REL_TOL_EMAIL,
    RECON_QTY_REL_TOL_STMT,
)
from database import get_shared_connection
from portfolio_metrics import estimate_wealthsimple_fx_fee_cad
from system_logger import get_logger
from yfinance_extractor import TickerHint, configure_yfinance_cache, fetch_security_info


logger = get_logger(__name__)
SecurityFetcher = Callable[[list[Any]], tuple[pd.DataFrame, pd.DataFrame]]
EMAIL_CHECKPOINT_SOURCE = "wealthsimple_email"
DEFAULT_CLASSIFICATION_JSON_PATH = (
    BASE_DIR / "exports" / "portfolio-classification" / "portfolio-classification.json"
)


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


def upload_portfolio_classifications(
    json_path: Path | str | None = None,
    db_path: Path | str = DATABASE_PATH,
) -> int:
    """Fully replace portfolio_classifications from the classify-portfolio JSON output."""
    path = Path(json_path) if json_path is not None else DEFAULT_CLASSIFICATION_JSON_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    generated_at = payload["generated_at"]
    holdings = payload["holdings"]
    connection = get_shared_connection(db_path)
    written = 0
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute("DELETE FROM portfolio_classifications")
        for holding in holdings:
            fields = holding.get("fields") or {}
            row = connection.execute(
                "SELECT ticker_id FROM tickers WHERE ticker_symbol = ? AND exchange = ?",
                [holding["ticker"], fields.get("exchange")],
            ).fetchone()
            if row is None:
                logger.warning(
                    "Skipping classification upload for unresolved ticker | ticker=%s",
                    holding["ticker"],
                )
                continue
            connection.execute(
                """
                INSERT INTO portfolio_classifications (
                    ticker_id, primary_group, secondary_tags, confidence, reasoning,
                    evidence_used, missing_data, review_needed, fields, field_provenance,
                    enrichment, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row[0],
                    holding["primary_group"],
                    _json(holding.get("secondary_tags")),
                    holding.get("confidence"),
                    holding.get("reasoning"),
                    _json(holding.get("evidence_used")),
                    _json(holding.get("missing_data")),
                    bool(holding.get("review_needed")),
                    _json(fields),
                    _json(holding.get("field_provenance")),
                    _json(holding.get("enrichment")),
                    generated_at,
                ],
            )
            written += 1
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
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
    null_money_buy_sell_count = 0
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
        if kind in ("BUY", "SELL") and debit is None and credit is None:
            # A statement-extraction gap (see statement_extractor.py's money
            # parsing): the position engine's activities-precedence rule
            # (database_command.reconcile_statement_activities) self-heals
            # this automatically when the same trade is also present in an
            # activities export, so this is a heads-up, not a hard failure.
            null_money_buy_sell_count += 1
            logger.warning(
                "Statement %s row has no debit/credit; book value depends on an "
                "activities export covering the same trade | date=%s | ticker_id=%s",
                kind, transaction_date, ticker_id,
            )
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
    if null_money_buy_sell_count:
        logger.warning(
            "Statement upload | %d BUY/SELL row(s) missing debit/credit",
            null_money_buy_sell_count,
        )
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
        # Interac deposits carry a placeholder ticker_id (0) and the "EMAIL"
        # source symbol; neither is a real security, so they must never be
        # inserted with a ticker_id or sit as 'pending' ticker resolution.
        ticker_id = None if source == "interac" else row.get("ticker_id")
        resolved = ticker_id is not None and not pd.isna(ticker_id)
        ticker_resolution_status = (
            "not_applicable" if source == "interac" else ("resolved" if resolved else "pending")
        )
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
                ticker_resolution_status,
                "not_applicable" if source == "interac" else "provisional",
            ],
        )
        written += 1
    logger.info("Email upload complete | inserted=%d | input=%d", written, len(data))
    logger.info(
        "Email FX summary unavailable | transactions=0 | reason=missing applied FX rate and confirmed CAD amount"
    )
    return written


def reconcile_statement_activities(
    db_path: Path | str = DATABASE_PATH, connection: Any | None = None
) -> int:
    """Link statement BUY/SELL rows to the activities Trade row that reports them.

    Precedence is activities > statements (see `v_trade_events` in
    `database.py`): once a statement row is linked here, `v_trade_events`
    stops emitting it and the activities row is used instead. This is what
    heals statement rows with missing debit/credit (a statement-extraction
    gap) without any manual backfill -- the covering activities row simply
    takes over.

    This pass is clear-and-rebuild: every `superseded_by_activity_id` link is
    reset before rematching, so it is idempotent and safe to rerun after every
    import or from `recompute-positions`. Matching is one-to-one and
    deterministic (exact quantity first, then closest quantity, then closest
    date, then lowest id) so repeated identical trades on the same day pair
    off consistently.
    """
    connection = connection if connection is not None else get_shared_connection(db_path)
    connection.execute("UPDATE transactions SET superseded_by_activity_id = NULL")

    statement_rows = connection.execute(
        """
        SELECT transaction_id, ticker_id, UPPER(transaction_type) AS direction,
               ABS(quantity) AS quantity, COALESCE(execution_date, transaction_date) AS event_date
        FROM transactions
        WHERE UPPER(transaction_type) IN ('BUY', 'SELL')
          AND ticker_id IS NOT NULL
        ORDER BY transaction_id
        """
    ).fetchall()
    activity_rows = connection.execute(
        """
        SELECT activity_id, ticker_id,
               CASE
                   WHEN quantity < 0 OR UPPER(COALESCE(activity_subtype, '')) = 'SELL' THEN 'SELL'
                   ELSE 'BUY'
               END AS direction,
               ABS(quantity) AS quantity, transaction_date AS event_date
        FROM activities
        WHERE activity_type = 'Trade' AND ticker_id IS NOT NULL
        ORDER BY activity_id
        """
    ).fetchall()

    activities_by_key: dict[tuple[int, str], list[tuple[int, Decimal, date]]] = {}
    for activity_id, ticker_id, direction, quantity, event_date in activity_rows:
        activities_by_key.setdefault((ticker_id, direction), []).append(
            (activity_id, quantity, event_date)
        )

    # (is_tolerant_match, qty_diff, date_diff, transaction_id, activity_id)
    candidates: list[tuple[int, Decimal, int, int, int]] = []
    for transaction_id, ticker_id, direction, quantity, event_date in statement_rows:
        for activity_id, act_quantity, act_date in activities_by_key.get((ticker_id, direction), []):
            qty_diff = abs(quantity - act_quantity)
            tolerance = max(RECON_QTY_ABS_TOL, RECON_QTY_REL_TOL_STMT * act_quantity)
            if qty_diff > tolerance:
                continue
            date_diff = abs((event_date - act_date).days)
            if date_diff > RECON_DATE_WINDOW_DAYS_STMT:
                continue
            is_tolerant = 0 if qty_diff == 0 else 1
            candidates.append((is_tolerant, qty_diff, date_diff, transaction_id, activity_id))

    candidates.sort()
    matched_transactions: set[int] = set()
    matched_activities: set[int] = set()
    reconciled = 0
    for _is_tolerant, _qty_diff, _date_diff, transaction_id, activity_id in candidates:
        if transaction_id in matched_transactions or activity_id in matched_activities:
            continue
        connection.execute(
            "UPDATE transactions SET superseded_by_activity_id = ? WHERE transaction_id = ?",
            [activity_id, transaction_id],
        )
        matched_transactions.add(transaction_id)
        matched_activities.add(activity_id)
        reconciled += 1

    logger.info(
        "Statement/activities reconciliation complete | superseded=%d | "
        "statement_candidates=%d | activity_candidates=%d",
        reconciled, len(statement_rows), len(activity_rows),
    )
    return reconciled


def _email_match_candidates(
    connection: Any,
    *,
    table: str,
    id_column: str,
    date_expr: str,
    extra_where: str,
    already_matched_column: str,
    ticker_id: int,
    direction: str,
    quantity: Decimal,
    event_date: date,
) -> list[tuple[int, Decimal, int]]:
    """Return (id, qty_diff, date_diff) rows within tolerance, closest first."""
    tolerance = max(RECON_QTY_ABS_TOL, RECON_QTY_REL_TOL_EMAIL * quantity)
    rows = connection.execute(
        f"""
        SELECT {id_column}, ABS(quantity) AS qty, {date_expr} AS event_date
        FROM {table}
        WHERE ticker_id = ?
          AND {extra_where}
          AND NOT EXISTS (
              SELECT 1 FROM email_transactions et
              WHERE et.{already_matched_column} = {table}.{id_column}
          )
        """,
        [ticker_id],
    ).fetchall()
    candidates = []
    for row_id, row_qty, row_date in rows:
        qty_diff = abs(quantity - row_qty)
        if qty_diff > tolerance:
            continue
        date_diff = abs((event_date - row_date).days)
        if date_diff > RECON_DATE_WINDOW_DAYS_EMAIL:
            continue
        candidates.append((row_id, qty_diff, date_diff))
    candidates.sort(key=lambda item: (item[1], item[2], item[0]))
    return candidates


def _resolve_email_match(
    candidates: list[tuple[int, Decimal, int]],
) -> tuple[int | None, bool]:
    """Pick a single match id from candidates, or flag ambiguity.

    Exact matches (same quantity, same date) are preferred and resolved
    deterministically by id even when more than one exists (repeated
    identical trades on the same day). Once no exact match exists, a single
    tolerant candidate is accepted; two or more tolerant candidates cannot be
    told apart with confidence, so the row is left for human review instead
    of guessing based on which happens to be numerically closest.
    """
    exact = [c for c in candidates if c[1] == 0 and c[2] == 0]
    if exact:
        return exact[0][0], False
    if len(candidates) == 1:
        return candidates[0][0], False
    if len(candidates) > 1:
        return None, True
    return None, False


def reconcile_email_transactions(
    db_path: Path | str = DATABASE_PATH, connection: Any | None = None
) -> int:
    """Match provisional email BUY/SELL trades to activities or statement rows.

    Activities rows are tried before statement rows, matching the precedence
    used by `v_trade_events`, so a DRIP buy that shows up in both an
    activities export and a statement is only ever linked once. Quantity
    tolerance is wider than the statement/activities pass
    (`RECON_QTY_REL_TOL_EMAIL`) because DRIP fractional-share quantities can
    drift a little between the email confirmation and the broker's settled
    record.

    Rows this pass previously marked 'superseded' are reset to 'provisional'
    and rematched when their link target no longer exists (e.g. a statement
    row that has since been superseded by an activities row), which keeps the
    pass idempotent across reruns. Rows a human marked 'review_required', or
    rows that are 'not_applicable' (Interac transfers), are left untouched.
    """
    connection = connection if connection is not None else get_shared_connection(db_path)

    connection.execute(
        """
        UPDATE email_transactions
        SET reconciliation_status = 'provisional', matched_transaction_id = NULL, matched_activity_id = NULL
        WHERE reconciliation_status = 'superseded'
          AND (
                (matched_activity_id IS NOT NULL
                 AND matched_activity_id NOT IN (SELECT activity_id FROM activities))
             OR (matched_transaction_id IS NOT NULL
                 AND matched_transaction_id NOT IN (
                     SELECT transaction_id FROM transactions WHERE superseded_by_activity_id IS NULL
                 ))
          )
        """
    )

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

        activity_candidates = _email_match_candidates(
            connection,
            table="activities",
            id_column="activity_id",
            date_expr="transaction_date",
            extra_where=(
                "activity_type = 'Trade' AND "
                "(CASE WHEN quantity < 0 OR UPPER(COALESCE(activity_subtype, '')) = 'SELL' "
                "THEN 'SELL' ELSE 'BUY' END) = '" + direction + "'"
            ),
            already_matched_column="matched_activity_id",
            ticker_id=ticker_id,
            direction=direction,
            quantity=quantity,
            event_date=transaction_date,
        )
        if activity_candidates:
            match_id, ambiguous = _resolve_email_match(activity_candidates)
            if ambiguous:
                connection.execute(
                    "UPDATE email_transactions SET reconciliation_status = 'review_required' "
                    "WHERE email_transaction_id = ?",
                    [email_id],
                )
                logger.warning(
                    "Email reconciliation requires review (activities) | "
                    "email_transaction_id=%d | candidates=%d",
                    email_id, len(activity_candidates),
                )
                continue
            connection.execute(
                """
                UPDATE email_transactions
                SET reconciliation_status = 'superseded', matched_activity_id = ?, matched_transaction_id = NULL
                WHERE email_transaction_id = ?
                """,
                [match_id, email_id],
            )
            reconciled += 1
            continue

        statement_candidates = _email_match_candidates(
            connection,
            table="transactions",
            id_column="transaction_id",
            date_expr="COALESCE(execution_date, transaction_date)",
            extra_where=f"UPPER(transaction_type) = '{direction}' AND superseded_by_activity_id IS NULL",
            already_matched_column="matched_transaction_id",
            ticker_id=ticker_id,
            direction=direction,
            quantity=quantity,
            event_date=transaction_date,
        )
        if not statement_candidates:
            continue
        match_id, ambiguous = _resolve_email_match(statement_candidates)
        if ambiguous:
            connection.execute(
                "UPDATE email_transactions SET reconciliation_status = 'review_required' "
                "WHERE email_transaction_id = ?",
                [email_id],
            )
            logger.warning(
                "Email reconciliation requires review (statements) | "
                "email_transaction_id=%d | candidates=%d",
                email_id, len(statement_candidates),
            )
            continue
        connection.execute(
            """
            UPDATE email_transactions
            SET reconciliation_status = 'superseded', matched_transaction_id = ?, matched_activity_id = NULL
            WHERE email_transaction_id = ?
            """,
            [match_id, email_id],
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

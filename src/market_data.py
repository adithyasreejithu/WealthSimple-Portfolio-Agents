"""Incremental yfinance metadata and history synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from config import DATABASE_PATH
from database import get_shared_connection, initialize_database
from database_command import upload_security_history, upload_security_metadata
from system_logger import get_logger
from yfinance_extractor import fetch_security_history, fetch_security_info


logger = get_logger(__name__)

MetadataFetcher = Callable[[Iterable[dict[str, str]]], tuple[pd.DataFrame, pd.DataFrame]]
HistoryFetcher = Callable[[Iterable[str], date, date], pd.DataFrame]


def _fetch_security_history_strict(
    tickers: Iterable[str], start_date: date, end_date: date
) -> pd.DataFrame:
    return fetch_security_history(
        tickers, start_date, end_date, raise_on_error=True
    )


@dataclass(frozen=True)
class MarketTarget:
    ticker_id: int
    symbol: str
    provider_symbol: str
    currency: str
    security_name: str
    first_owned_date: date
    latest_market_date: date | None

    def fetch_start(self) -> date:
        if self.latest_market_date is None:
            return self.first_owned_date
        return self.latest_market_date + timedelta(days=1)


@dataclass(frozen=True)
class MarketSyncResult:
    tickers: int
    rows: int
    skipped: int = 0
    error: str | None = None
    failed_symbols: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.error is None


def get_market_targets(
    db_path: Path | str = DATABASE_PATH,
    symbols: Iterable[str] | None = None,
) -> list[MarketTarget]:
    """Return portfolio tickers with ownership and synchronization boundaries."""
    requested = {str(symbol).strip().upper() for symbol in symbols or [] if str(symbol).strip()}
    rows = get_shared_connection(db_path).execute(
        """
        WITH owned_dates AS (
            SELECT ticker_id, MIN(transaction_date) AS first_owned_date
            FROM (
                SELECT ticker_id, transaction_date FROM transactions
                UNION ALL
                SELECT ticker_id, transaction_date FROM email_transactions
                WHERE ticker_id IS NOT NULL
                UNION ALL
                SELECT ticker_id, transaction_date FROM activities
                WHERE ticker_id IS NOT NULL
            ) owned
            GROUP BY ticker_id
        ),
        latest_history AS (
            SELECT ticker_id, MAX(record_date) AS latest_market_date
            FROM historical_records
            GROUP BY ticker_id
        )
        SELECT
            t.ticker_id,
            t.ticker_symbol,
            m.provider_symbol,
            t.currency,
            t.security_name,
            o.first_owned_date,
            h.latest_market_date
        FROM owned_dates o
        JOIN tickers t USING (ticker_id)
        JOIN ticker_provider_mappings m
          ON m.ticker_id = t.ticker_id
         AND m.provider = 'yahoo'
         AND m.verification_status = 'verified'
        LEFT JOIN latest_history h USING (ticker_id)
        ORDER BY t.ticker_symbol, t.exchange
        """
    ).fetchall()
    targets = [
        MarketTarget(int(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]), row[5], row[6])
        for row in rows
    ]
    if not requested:
        return targets
    return [
        target for target in targets
        if target.symbol.upper() in requested or target.provider_symbol.upper() in requested
    ]


def sync_market_data(
    db_path: Path | str = DATABASE_PATH,
    symbols: Iterable[str] | None = None,
    *,
    as_of: date | None = None,
    full: bool = False,
    metadata_fetcher: MetadataFetcher = fetch_security_info,
    history_fetcher: HistoryFetcher = _fetch_security_history_strict,
) -> MarketSyncResult:
    """Fetch and atomically persist metadata and incremental price history."""
    initialize_database(db_path)
    today = as_of or date.today()
    targets = get_market_targets(db_path, symbols)
    if not targets:
        return MarketSyncResult(0, 0, 0)

    hints = [
        {"symbol": target.symbol, "currency": target.currency, "name": target.security_name}
        for target in targets
    ]
    try:
        stocks, etfs = metadata_fetcher(hints)
    except Exception as exc:
        logger.exception("Yfinance metadata synchronization fetch failed")
        return MarketSyncResult(
            len(targets), 0, error=str(exc),
            failed_symbols=tuple(target.symbol for target in targets),
        )

    frames: list[pd.DataFrame] = []
    skipped = 0
    failed_symbols: list[str] = []
    for target in targets:
        start = target.first_owned_date if full else target.fetch_start()
        if start > today:
            skipped += 1
            continue
        try:
            frame = history_fetcher([target.provider_symbol], start, today + timedelta(days=1))
            if not frame.empty:
                frames.append(frame)
        except Exception:
            failed_symbols.append(target.symbol)
            logger.exception("Yfinance history fetch failed | ticker=%s", target.symbol)
    history = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    connection = get_shared_connection(db_path)
    connection.execute("BEGIN TRANSACTION")
    try:
        ticker_ids = {target.provider_symbol.upper(): target.ticker_id for target in targets}
        upload_security_metadata(stocks, etfs, db_path, ticker_ids=ticker_ids)
        rows = upload_security_history(history, ticker_ids, db_path) if not history.empty else 0
        connection.execute("COMMIT")
    except Exception as exc:
        connection.execute("ROLLBACK")
        logger.exception("Yfinance synchronization database write failed")
        return MarketSyncResult(
            len(targets), 0, skipped, str(exc), tuple(target.symbol for target in targets)
        )

    logger.info(
        "Yfinance synchronization complete | tickers=%d | rows=%d | skipped=%d",
        len(targets), rows, skipped,
    )
    error = None
    if failed_symbols:
        error = "history fetch failed for: " + ", ".join(failed_symbols)
    return MarketSyncResult(len(targets), rows, skipped, error, tuple(failed_symbols))

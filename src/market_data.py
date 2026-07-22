"""Incremental yfinance metadata and history synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from config import BENCHMARK_TICKERS, DATABASE_PATH, FX_PAIR_SYMBOL
from database import get_shared_connection, initialize_database
from database_command import (
    upload_dividend_events,
    upload_earnings_events,
    upload_financial_snapshots,
    upload_security_history,
    upload_security_metadata,
)
from earnings_dividends_extractor import fetch_dividend_events, fetch_earnings_events
from financial_snapshots_extractor import fetch_financial_snapshots
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


@dataclass(frozen=True)
class EarningsDividendsSyncResult:
    tickers: int
    earnings_rows: int
    dividend_rows: int
    error: str | None = None
    failed_symbols: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class FinancialSnapshotsSyncResult:
    tickers: int
    snapshot_rows: int
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


def _ensure_fx_ticker(db_path: Path | str) -> int:
    """Return the ticker_id for the configured FX pair, creating it if needed.

    Modeled as a `tickers` row with `security_type='fx_rate'` so it reuses
    `historical_records` storage and forward-fill machinery, but it is not a
    portfolio holding: `analytics.py`/`position_engine.py` never treat it as
    an ownable position, and `get_market_targets` never returns it (it has no
    `ticker_provider_mappings` row and no owned transactions).
    """
    connection = get_shared_connection(db_path)
    row = connection.execute(
        "SELECT ticker_id FROM tickers WHERE ticker_symbol = ? AND exchange = 'FX'",
        [FX_PAIR_SYMBOL],
    ).fetchone()
    if row:
        return int(row[0])
    return int(
        connection.execute(
            """
            INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
            VALUES (?, 'FX', 'CAD', 'USD/CAD Exchange Rate', 'fx_rate')
            RETURNING ticker_id
            """,
            [FX_PAIR_SYMBOL],
        ).fetchone()[0]
    )


def ensure_fx_history(
    db_path: Path | str = DATABASE_PATH,
    *,
    as_of: date | None = None,
    history_fetcher: HistoryFetcher = _fetch_security_history_strict,
) -> int:
    """Incrementally fetch and store the configured FX pair's daily closes.

    Backfills from the earliest transaction of any kind in the database (so
    the historical portfolio value series has an FX rate for every date it
    might need), then fetches forward from the latest stored FX date on
    subsequent calls. Returns the number of rows written.
    """
    today = as_of or date.today()
    connection = get_shared_connection(db_path)
    ticker_id = _ensure_fx_ticker(db_path)

    earliest_row = connection.execute(
        """
        SELECT MIN(d) FROM (
            SELECT MIN(transaction_date) AS d FROM transactions
            UNION ALL
            SELECT MIN(transaction_date) FROM email_transactions
            UNION ALL
            SELECT MIN(transaction_date) FROM activities
        )
        """
    ).fetchone()
    if not earliest_row or earliest_row[0] is None:
        return 0
    earliest_owned_date = earliest_row[0]

    latest_row = connection.execute(
        "SELECT MAX(record_date) FROM historical_records WHERE ticker_id = ?", [ticker_id]
    ).fetchone()
    start = (latest_row[0] + timedelta(days=1)) if latest_row and latest_row[0] else earliest_owned_date
    if start > today:
        return 0

    history = history_fetcher([FX_PAIR_SYMBOL], start, today + timedelta(days=1))
    if history.empty:
        return 0

    connection.execute("BEGIN TRANSACTION")
    try:
        rows = upload_security_history(history, {FX_PAIR_SYMBOL: ticker_id}, db_path)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        logger.exception("FX history synchronization database write failed")
        raise
    logger.info("FX history synchronization complete | symbol=%s | rows=%d", FX_PAIR_SYMBOL, rows)
    return rows


def _ensure_benchmark_ticker(db_path: Path | str, symbol: str, name: str) -> int:
    """Return the ticker_id for a benchmark symbol, creating a synthetic row if needed.

    Looks up by symbol across any exchange first so an already-owned ticker
    (e.g. XEQT) is reused rather than duplicated -- `analytics.get_price_history`
    resolves symbols with a bare symbol match, so a second row for the same
    symbol would shadow the first. Only creates a row (mirroring the FX-pair
    pattern) when the symbol is absent entirely.
    """
    connection = get_shared_connection(db_path)
    row = connection.execute(
        "SELECT ticker_id FROM tickers WHERE UPPER(ticker_symbol) = UPPER(?) ORDER BY ticker_id LIMIT 1",
        [symbol],
    ).fetchone()
    if row:
        return int(row[0])
    return int(
        connection.execute(
            """
            INSERT INTO tickers (ticker_symbol, exchange, currency, security_name, security_type)
            VALUES (?, 'BENCHMARK', 'CAD', ?, 'benchmark')
            RETURNING ticker_id
            """,
            [symbol.upper(), name],
        ).fetchone()[0]
    )


def ensure_benchmark_history(
    db_path: Path | str = DATABASE_PATH,
    *,
    as_of: date | None = None,
    history_fetcher: HistoryFetcher = _fetch_security_history_strict,
) -> int:
    """Fetch and store daily closes for the configured benchmark tickers.

    Guarantees full portfolio-window coverage: backfills each benchmark from
    the earliest transaction of any kind (including a missing head range when
    the ticker is owned but its history only starts at first ownership), then
    fetches forward incrementally. Returns the number of rows written.
    """
    today = as_of or date.today()
    connection = get_shared_connection(db_path)

    earliest_row = connection.execute(
        """
        SELECT MIN(d) FROM (
            SELECT MIN(transaction_date) AS d FROM transactions
            UNION ALL
            SELECT MIN(transaction_date) FROM email_transactions
            UNION ALL
            SELECT MIN(transaction_date) FROM activities
        )
        """
    ).fetchone()
    if not earliest_row or earliest_row[0] is None:
        return 0
    earliest_owned_date = earliest_row[0]

    total_rows = 0
    for symbol, provider_symbol in BENCHMARK_TICKERS.items():
        ticker_id = _ensure_benchmark_ticker(db_path, symbol, f"{symbol} (benchmark)")
        bounds = connection.execute(
            "SELECT MIN(record_date), MAX(record_date) FROM historical_records WHERE ticker_id = ?",
            [ticker_id],
        ).fetchone()
        stored_min, stored_max = (bounds or (None, None))

        ranges: list[tuple[date, date]] = []
        if stored_min is None:
            ranges.append((earliest_owned_date, today + timedelta(days=1)))
        else:
            if earliest_owned_date < stored_min:
                ranges.append((earliest_owned_date, stored_min))
            if stored_max + timedelta(days=1) <= today:
                ranges.append((stored_max + timedelta(days=1), today + timedelta(days=1)))

        for start, end in ranges:
            history = history_fetcher([provider_symbol], start, end)
            if history.empty:
                continue
            connection.execute("BEGIN TRANSACTION")
            try:
                rows = upload_security_history(
                    history, {provider_symbol.upper(): ticker_id}, db_path
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                logger.exception(
                    "Benchmark history synchronization database write failed | symbol=%s", symbol
                )
                raise
            total_rows += rows
    if total_rows:
        logger.info("Benchmark history synchronization complete | rows=%d", total_rows)
    return total_rows


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
    try:
        ensure_fx_history(db_path, as_of=today, history_fetcher=history_fetcher)
    except Exception:
        # FX history backs CAD valuation of USD holdings but is not itself a
        # portfolio holding; a transient fetch failure here should not abort
        # syncing the tickers the user actually owns.
        logger.exception("FX history synchronization failed; continuing with ticker sync")
    try:
        ensure_benchmark_history(db_path, as_of=today, history_fetcher=history_fetcher)
    except Exception:
        # Benchmarks only feed the dashboard's trend overlays; same failure
        # isolation as the FX pair.
        logger.exception("Benchmark history synchronization failed; continuing with ticker sync")
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


def sync_earnings_dividends(
    db_path: Path | str = DATABASE_PATH,
    symbols: Iterable[str] | None = None,
    *,
    earnings_fetcher: Callable[[Iterable[str]], pd.DataFrame] = fetch_earnings_events,
    dividends_fetcher: Callable[[Iterable[str]], pd.DataFrame] = fetch_dividend_events,
    skip_earnings: bool = False,
    skip_dividends: bool = False,
) -> EarningsDividendsSyncResult:
    """Fetch and atomically persist company-declared earnings/dividend calendars.

    Scoped to owned tickers with a verified Yahoo mapping (reuses
    `get_market_targets`, the same scope as `sync_market_data`). This is
    company-declared market data, distinct from the user's own received
    dividend cash in `cash_transactions`/`transactions` -- the two are never
    joined or conflated. Runs on demand only; unlike OHLCV it is not part of
    the automatic post-email sync.
    """
    initialize_database(db_path)
    targets = get_market_targets(db_path, symbols)
    if not targets:
        return EarningsDividendsSyncResult(0, 0, 0)

    provider_symbols = [target.provider_symbol for target in targets]
    ticker_ids = {target.provider_symbol.upper(): target.ticker_id for target in targets}

    earnings = pd.DataFrame()
    dividends = pd.DataFrame()
    try:
        if not skip_earnings:
            earnings = earnings_fetcher(provider_symbols)
        if not skip_dividends:
            dividends = dividends_fetcher(provider_symbols)
    except Exception as exc:
        logger.exception("Earnings/dividends synchronization fetch failed")
        return EarningsDividendsSyncResult(
            len(targets), 0, 0, str(exc),
            failed_symbols=tuple(target.symbol for target in targets),
        )

    connection = get_shared_connection(db_path)
    connection.execute("BEGIN TRANSACTION")
    try:
        earnings_rows = (
            upload_earnings_events(earnings, ticker_ids, db_path) if not earnings.empty else 0
        )
        dividend_rows = (
            upload_dividend_events(dividends, ticker_ids, db_path) if not dividends.empty else 0
        )
        connection.execute("COMMIT")
    except Exception as exc:
        connection.execute("ROLLBACK")
        logger.exception("Earnings/dividends synchronization database write failed")
        return EarningsDividendsSyncResult(
            len(targets), 0, 0, str(exc),
            failed_symbols=tuple(target.symbol for target in targets),
        )

    logger.info(
        "Earnings/dividends synchronization complete | tickers=%d | earnings=%d | dividends=%d",
        len(targets), earnings_rows, dividend_rows,
    )
    return EarningsDividendsSyncResult(len(targets), earnings_rows, dividend_rows)


def sync_financial_snapshots(
    db_path: Path | str = DATABASE_PATH,
    symbols: Iterable[str] | None = None,
    *,
    snapshots_fetcher: Callable[[Iterable[str]], pd.DataFrame] = fetch_financial_snapshots,
) -> FinancialSnapshotsSyncResult:
    """Fetch and atomically persist per-quarter company financial statement data.

    Scoped to owned tickers with a verified Yahoo mapping (reuses
    `get_market_targets`, the same scope as `sync_market_data`/
    `sync_earnings_dividends`). Not date-windowed: every run re-fetches each
    ticker's currently-available quarterly window and upserts it against
    what's already stored -- yfinance's own quarterly statement endpoints
    only ever return a shallow trailing window, so accumulated depth comes
    from repeated syncs over time, not a single backfill. Runs on demand
    only; unlike OHLCV it is not part of the automatic post-email sync.
    """
    initialize_database(db_path)
    targets = get_market_targets(db_path, symbols)
    if not targets:
        return FinancialSnapshotsSyncResult(0, 0)

    provider_symbols = [target.provider_symbol for target in targets]
    ticker_ids = {target.provider_symbol.upper(): target.ticker_id for target in targets}

    try:
        snapshots = snapshots_fetcher(provider_symbols)
    except Exception as exc:
        logger.exception("Financial snapshots synchronization fetch failed")
        return FinancialSnapshotsSyncResult(
            len(targets), 0, str(exc),
            failed_symbols=tuple(target.symbol for target in targets),
        )

    connection = get_shared_connection(db_path)
    connection.execute("BEGIN TRANSACTION")
    try:
        snapshot_rows = (
            upload_financial_snapshots(snapshots, ticker_ids, db_path) if not snapshots.empty else 0
        )
        connection.execute("COMMIT")
    except Exception as exc:
        connection.execute("ROLLBACK")
        logger.exception("Financial snapshots synchronization database write failed")
        return FinancialSnapshotsSyncResult(
            len(targets), 0, str(exc),
            failed_symbols=tuple(target.symbol for target in targets),
        )

    logger.info(
        "Financial snapshots synchronization complete | tickers=%d | rows=%d",
        len(targets), snapshot_rows,
    )
    return FinancialSnapshotsSyncResult(len(targets), snapshot_rows)

"""Incremental yfinance metadata and history synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from config import (
    BENCHMARK_TICKERS,
    DATABASE_PATH,
    DIVIDENDS_REFRESH_INTERVAL_DAYS,
    EARNINGS_REFRESH_INTERVAL_DAYS,
    FINANCIAL_SNAPSHOTS_REFRESH_INTERVAL_DAYS,
    FX_PAIR_SYMBOL,
    MINIMUM_PRICE_HISTORY_DAYS,
)
from database import get_shared_connection, initialize_database
from database_command import (
    upload_dividend_events,
    upload_earnings_events,
    upload_financial_snapshots,
    upload_security_history,
    upload_security_metadata,
    upload_upcoming_dividends,
)
from earnings_dividends_extractor import (
    fetch_dividend_events,
    fetch_earnings_events,
    fetch_upcoming_dividends,
)
from financial_snapshots_extractor import fetch_financial_snapshots
from system_logger import get_logger
from yfinance_extractor import fetch_security_history, fetch_security_info


logger = get_logger(__name__)

MetadataFetcher = Callable[[Iterable[dict[str, str]]], tuple[pd.DataFrame, pd.DataFrame]]
HistoryFetcher = Callable[[Iterable[str], date, date], pd.DataFrame]


def _has_trading_weekday(start: date, end: date) -> bool:
    """Whether the half-open range `[start, end)` contains a Mon-Fri.

    A range covering only a weekend cannot yield bars, and asking yfinance for
    one logs a misleading "possibly delisted; no price data found" error. Two
    ordinary situations produce such ranges: a weekend sync (last bar Friday,
    range Sat-Sun) and a history floor that lands just before a ticker's first
    stored bar. Holidays are not modelled -- only the provably-empty weekend
    case is skipped, so this never suppresses a range that could hold data.
    """
    if (end - start).days >= 7:
        return True
    day = start
    while day < end:
        if day.weekday() < 5:
            return True
        day += timedelta(days=1)
    return False


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
    first_owned_date: date | None
    earliest_market_date: date | None
    latest_market_date: date | None
    asset_class: str = "stock"
    scope: str = "portfolio"

    def history_floor(self, today: date) -> date:
        """How far back stored history should reach.

        Ownership alone is not deep enough: a name bought last month can never
        accumulate the ~275 bars an SMA-200 or a 365-day relative-strength
        window needs. The floor is therefore the earlier of first ownership and
        `MINIMUM_PRICE_HISTORY_DAYS` ago. A research-scoped target has no
        ownership date at all (`first_owned_date is None`), so the floor is
        just `MINIMUM_PRICE_HISTORY_DAYS` ago -- still enough bars for the
        same technicals.
        """
        floor = today - timedelta(days=MINIMUM_PRICE_HISTORY_DAYS)
        if self.first_owned_date is None:
            return floor
        return min(self.first_owned_date, floor)

    def fetch_ranges(self, today: date, *, full: bool = False) -> list[tuple[date, date]]:
        """The `[start, end)` ranges to request, head gap first.

        Mirrors `ensure_benchmark_history`'s two-range shape: a head range when
        stored history starts later than the floor, plus the usual incremental
        tail. Self-limiting -- once a ticker's `earliest_market_date` reaches
        its floor the floor keeps advancing daily while the stored minimum
        stays put, so the head gap is fetched exactly once and never again.
        """
        floor = self.history_floor(today)
        end = today + timedelta(days=1)
        if full or self.earliest_market_date is None:
            return [(floor, end)] if floor <= today else []
        candidates = [
            (floor, self.earliest_market_date),
            (self.latest_market_date + timedelta(days=1), end),
        ]
        return [
            (start, stop)
            for start, stop in candidates
            if start < stop and _has_trading_weekday(start, stop)
        ]


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
    # Appended after the existing fields (rather than grouped with
    # dividend_rows) so every pre-existing positional construction of this
    # dataclass elsewhere in this module keeps binding error/failed_symbols
    # to the same argument position it always has.
    upcoming_dividend_rows: int = 0

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


RESEARCH_STATUSES = frozenset({"wishlist"})


def get_market_targets(
    db_path: Path | str = DATABASE_PATH,
    symbols: Iterable[str] | None = None,
    *,
    include_research: bool = False,
) -> list[MarketTarget]:
    """Return synchronization targets with ownership/research scope and boundaries.

    `include_research=False` (the default) reproduces the original
    owned-only behavior byte-for-byte -- `pipeline`, `yfinance-sync`,
    `earnings-dividends-sync`, and `financial-snapshots-sync` never pass this,
    so routine syncs stay scoped to what's actually held. `include_research=True`
    additionally admits tickers with a `security_status.declared_status` in
    `RESEARCH_STATUSES` (currently just `wishlist`) even though they have zero
    transactions -- the on-demand path `investment-analyst-resources` uses to
    persist real price/earnings/dividend/financials history for a research
    candidate. `avoid` and `retired` are deliberately excluded from both scopes.
    """
    requested = {str(symbol).strip().upper() for symbol in symbols or [] if str(symbol).strip()}
    research_clause = (
        "OR (s.declared_status IN (" + ", ".join("?" for _ in RESEARCH_STATUSES) + "))"
        if include_research
        else ""
    )
    params: list[Any] = list(RESEARCH_STATUSES) if include_research else []
    rows = get_shared_connection(db_path).execute(
        f"""
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
        history_bounds AS (
            SELECT
                ticker_id,
                MIN(record_date) AS earliest_market_date,
                MAX(record_date) AS latest_market_date
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
            h.earliest_market_date,
            h.latest_market_date,
            t.security_type,
            CASE WHEN o.first_owned_date IS NOT NULL THEN 'portfolio' ELSE 'research' END AS scope
        FROM tickers t
        JOIN ticker_provider_mappings m
          ON m.ticker_id = t.ticker_id
         AND m.provider = 'yahoo'
         AND m.verification_status = 'verified'
        LEFT JOIN owned_dates o ON o.ticker_id = t.ticker_id
        LEFT JOIN history_bounds h ON h.ticker_id = t.ticker_id
        LEFT JOIN security_status s ON s.ticker_id = t.ticker_id
        WHERE o.first_owned_date IS NOT NULL {research_clause}
        ORDER BY t.ticker_symbol, t.exchange
        """,
        params,
    ).fetchall()
    targets = [
        MarketTarget(
            int(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]),
            row[5], row[6], row[7], str(row[8] or "stock"), str(row[9]),
        )
        for row in rows
    ]
    if not requested:
        return targets
    return [
        target for target in targets
        if target.symbol.upper() in requested or target.provider_symbol.upper() in requested
    ]


def _stale_symbols(
    db_path: Path | str,
    targets: Iterable[MarketTarget],
    table: str,
    max_age_days: int,
    *,
    as_of: date | None = None,
) -> list[str]:
    """Symbols among `targets` whose `table.fetched_at` is missing or older than `max_age_days`.

    `table` is always one of a fixed set of internal literals passed by the
    wrapper functions below (never external input), so the f-string is safe.
    A ticker with no row at all in `table` counts as stale (never fetched).
    """
    targets = list(targets)
    if not targets:
        return []
    today = as_of or date.today()
    cutoff = datetime.combine(today, datetime.min.time()) - timedelta(days=max_age_days)
    ticker_ids = [target.ticker_id for target in targets]
    placeholders = ", ".join("?" for _ in ticker_ids)
    rows = get_shared_connection(db_path).execute(
        f"SELECT ticker_id, MAX(fetched_at) FROM {table} WHERE ticker_id IN ({placeholders}) GROUP BY ticker_id",
        ticker_ids,
    ).fetchall()
    fresh_ids = {ticker_id for ticker_id, fetched_at in rows if fetched_at is not None and fetched_at >= cutoff}
    return [target.symbol for target in targets if target.ticker_id not in fresh_ids]


def stale_earnings_symbols(
    db_path: Path | str,
    targets: Iterable[MarketTarget],
    *,
    max_age_days: int = EARNINGS_REFRESH_INTERVAL_DAYS,
    as_of: date | None = None,
) -> list[str]:
    """Owned symbols whose earnings calendar has not been refreshed within `max_age_days`.

    Excludes ETFs -- yfinance never returns an earnings date for a fund
    (`earnings_dividends_extractor` logs this as expected, not an error), so
    an ETF's `earnings_events` row count is permanently zero and `_stale_symbols`
    would otherwise mark it stale (and re-fetch it) on every single call.
    """
    equities = [target for target in targets if target.asset_class != "etf"]
    return _stale_symbols(db_path, equities, "earnings_events", max_age_days, as_of=as_of)


def stale_dividend_symbols(
    db_path: Path | str,
    targets: Iterable[MarketTarget],
    *,
    max_age_days: int = DIVIDENDS_REFRESH_INTERVAL_DAYS,
    as_of: date | None = None,
) -> list[str]:
    """Owned symbols whose dividend schedule has not been refreshed within `max_age_days`.

    Unlike earnings/financials, whether a ticker pays a dividend is not
    knowable from `asset_class` (a growth stock and a dividend payer are both
    `security_type='stock'`), so a non-dividend-paying stock's permanently
    empty `dividend_events` row still reads as stale on every call and gets
    re-fetched every pipeline run -- a bounded number of extra yfinance calls,
    not a correctness issue, but this gate does not shrink for that ticker.
    """
    return _stale_symbols(db_path, targets, "dividend_events", max_age_days, as_of=as_of)


def stale_financial_snapshot_symbols(
    db_path: Path | str,
    targets: Iterable[MarketTarget],
    *,
    max_age_days: int = FINANCIAL_SNAPSHOTS_REFRESH_INTERVAL_DAYS,
    as_of: date | None = None,
) -> list[str]:
    """Owned symbols whose quarterly financials have not been refreshed within `max_age_days`.

    Excludes ETFs for the same reason as `stale_earnings_symbols`: yfinance
    never returns quarterly statements for a fund, so an ETF's
    `financial_snapshots` row count is permanently zero.
    """
    equities = [target for target in targets if target.asset_class != "etf"]
    return _stale_symbols(db_path, equities, "financial_snapshots", max_age_days, as_of=as_of)


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
    include_research: bool = False,
    metadata_fetcher: MetadataFetcher = fetch_security_info,
    history_fetcher: HistoryFetcher = _fetch_security_history_strict,
) -> MarketSyncResult:
    """Fetch and atomically persist metadata and incremental price history.

    Scoped to owned tickers by default; pass `include_research=True` to also
    sync declared-wishlist tickers (see `get_market_targets`). `pipeline`
    never sets this, so routine syncs are unaffected.
    """
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
    targets = get_market_targets(db_path, symbols, include_research=include_research)
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
        ranges = target.fetch_ranges(today, full=full)
        if not ranges:
            skipped += 1
            continue
        try:
            for start, end in ranges:
                frame = history_fetcher([target.provider_symbol], start, end)
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
    include_research: bool = False,
    earnings_fetcher: Callable[[Iterable[str]], pd.DataFrame] = fetch_earnings_events,
    dividends_fetcher: Callable[[Iterable[str]], pd.DataFrame] = fetch_dividend_events,
    upcoming_dividends_fetcher: Callable[[Iterable[str]], pd.DataFrame] = fetch_upcoming_dividends,
    skip_earnings: bool = False,
    skip_dividends: bool = False,
    skip_upcoming: bool = False,
) -> EarningsDividendsSyncResult:
    """Fetch and atomically persist company-declared earnings/dividend calendars.

    Scoped to owned tickers with a verified Yahoo mapping by default (reuses
    `get_market_targets`, the same scope as `sync_market_data`); pass
    `include_research=True` to also cover declared-wishlist tickers. This is
    company-declared market data, distinct from the user's own received
    dividend cash in `cash_transactions`/`transactions` -- the two are never
    joined or conflated. Runs on demand only; unlike OHLCV it is not part of
    the automatic post-email sync.

    The upcoming-dividend fetch shares `dividend_events` with the historical
    fetch (see `upload_upcoming_dividends`) and is written in the same
    transaction as the other two, so a partial sync can never leave one table
    updated and another stale relative to it.
    """
    initialize_database(db_path)
    targets = get_market_targets(db_path, symbols, include_research=include_research)
    if not targets:
        return EarningsDividendsSyncResult(0, 0, 0)

    provider_symbols = [target.provider_symbol for target in targets]
    ticker_ids = {target.provider_symbol.upper(): target.ticker_id for target in targets}

    earnings = pd.DataFrame()
    dividends = pd.DataFrame()
    upcoming_dividends = pd.DataFrame()
    try:
        if not skip_earnings:
            earnings = earnings_fetcher(provider_symbols)
        if not skip_dividends:
            dividends = dividends_fetcher(provider_symbols)
        if not skip_upcoming:
            upcoming_dividends = upcoming_dividends_fetcher(provider_symbols)
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
        upcoming_dividend_rows = (
            upload_upcoming_dividends(upcoming_dividends, ticker_ids, db_path)
            if not upcoming_dividends.empty
            else 0
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
        "Earnings/dividends synchronization complete | tickers=%d | earnings=%d | dividends=%d | upcoming=%d",
        len(targets), earnings_rows, dividend_rows, upcoming_dividend_rows,
    )
    return EarningsDividendsSyncResult(
        len(targets), earnings_rows, dividend_rows, upcoming_dividend_rows=upcoming_dividend_rows
    )


def sync_financial_snapshots(
    db_path: Path | str = DATABASE_PATH,
    symbols: Iterable[str] | None = None,
    *,
    include_research: bool = False,
    snapshots_fetcher: Callable[[Iterable[str]], pd.DataFrame] = fetch_financial_snapshots,
) -> FinancialSnapshotsSyncResult:
    """Fetch and atomically persist per-quarter company financial statement data.

    Scoped to owned tickers with a verified Yahoo mapping by default (reuses
    `get_market_targets`, the same scope as `sync_market_data`/
    `sync_earnings_dividends`); pass `include_research=True` to also cover
    declared-wishlist tickers. Not date-windowed: every run re-fetches each
    ticker's currently-available quarterly window and upserts it against
    what's already stored -- yfinance's own quarterly statement endpoints
    only ever return a shallow trailing window, so accumulated depth comes
    from repeated syncs over time, not a single backfill. Runs on demand
    only; unlike OHLCV it is not part of the automatic post-email sync.
    """
    initialize_database(db_path)
    targets = get_market_targets(db_path, symbols, include_research=include_research)
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

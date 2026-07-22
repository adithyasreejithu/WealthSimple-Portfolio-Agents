from __future__ import annotations

import argparse
import os
import tempfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

from config import YFINANCE_MAX_WORKERS
from system_logger import get_logger
from yfinance_extractor import (
    _build_session,
    _create_ticker,
    _empty_frame,
    _normalize_tickers,
    _print_frame,
    _require_yfinance,
    clear_proxy_environment,
    configure_yfinance_cache,
)


logger = get_logger(__name__)

EARNINGS_EVENT_COLUMNS = [
    "Ticker",
    "ProviderSymbol",
    "ReportDate",
    "EpsEstimate",
    "EpsActual",
    "SurprisePct",
]
DIVIDEND_EVENT_COLUMNS = [
    "Ticker",
    "ProviderSymbol",
    "ExDividendDate",
    "DeclaredAmount",
]


def _fetch_one_earnings(symbol: str, yf_module: Any, session: object | None) -> list[dict[str, Any]]:
    """Fetch one ticker's earnings calendar; per-ticker failures are isolated.

    ETF/fund tickers legitimately return an empty ``earnings_dates`` (yfinance
    prints a misleading "No earnings dates found, symbol may be delisted"
    message but raises no exception -- the fund simply has no earnings events).
    Treat that as a normal, expected outcome, not a fetch failure.
    """
    try:
        client = _create_ticker(yf_module, symbol, session)
        frame = client.earnings_dates
        if frame is None or len(frame) == 0:
            logger.info(
                "No earnings dates for %s (expected for ETFs/funds; not an error)", symbol
            )
            return []
        records: list[dict[str, Any]] = []
        for report_date, row in frame.iterrows():
            records.append(
                {
                    "Ticker": symbol,
                    "ProviderSymbol": symbol,
                    "ReportDate": report_date,
                    "EpsEstimate": row.get("EPS Estimate"),
                    "EpsActual": row.get("Reported EPS"),
                    "SurprisePct": row.get("Surprise(%)"),
                }
            )
        return records
    except Exception:
        logger.exception("Failed to fetch yfinance earnings dates for %s", symbol)
        return []


def _fetch_one_dividends(symbol: str, yf_module: Any, session: object | None) -> list[dict[str, Any]]:
    """Fetch one ticker's declared dividend history; per-ticker failures isolated."""
    try:
        client = _create_ticker(yf_module, symbol, session)
        series = client.dividends
        if series is None or len(series) == 0:
            logger.info("No dividend history for %s", symbol)
            return []
        records: list[dict[str, Any]] = []
        for ex_dividend_date, declared_amount in series.items():
            records.append(
                {
                    "Ticker": symbol,
                    "ProviderSymbol": symbol,
                    "ExDividendDate": ex_dividend_date,
                    "DeclaredAmount": declared_amount,
                }
            )
        return records
    except Exception:
        logger.exception("Failed to fetch yfinance dividends for %s", symbol)
        return []


def fetch_earnings_events(tickers: Iterable[str]) -> pd.DataFrame:
    """Fetch company-declared earnings calendars for the given provider symbols."""
    normalized = _normalize_tickers(tickers)
    if not normalized:
        logger.info("No tickers provided for yfinance earnings fetch")
        return _empty_frame(EARNINGS_EVENT_COLUMNS)

    yf_module = _require_yfinance()
    session = _build_session()
    worker_count = min(YFINANCE_MAX_WORKERS, len(normalized))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(lambda symbol: _fetch_one_earnings(symbol, yf_module, session), normalized)
        )
    records = [record for batch in results for record in batch]
    logger.info(
        "YFinance earnings fetch complete | tickers=%d | rows=%d", len(normalized), len(records)
    )
    return pd.DataFrame(records, columns=EARNINGS_EVENT_COLUMNS)


def fetch_dividend_events(tickers: Iterable[str]) -> pd.DataFrame:
    """Fetch company-declared dividend schedules for the given provider symbols."""
    normalized = _normalize_tickers(tickers)
    if not normalized:
        logger.info("No tickers provided for yfinance dividend fetch")
        return _empty_frame(DIVIDEND_EVENT_COLUMNS)

    yf_module = _require_yfinance()
    session = _build_session()
    worker_count = min(YFINANCE_MAX_WORKERS, len(normalized))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(lambda symbol: _fetch_one_dividends(symbol, yf_module, session), normalized)
        )
    records = [record for batch in results for record in batch]
    logger.info(
        "YFinance dividend fetch complete | tickers=%d | rows=%d", len(normalized), len(records)
    )
    return pd.DataFrame(records, columns=DIVIDEND_EVENT_COLUMNS)


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "wealthsimple-yfinance-cache"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch yfinance company-declared earnings and dividend calendars."
    )
    parser.add_argument(
        "--tickers", nargs="+", required=True,
        help="Provider (Yahoo) ticker symbols to fetch, for example AAPL VFV.TO SHOP.TO.",
    )
    parser.add_argument("--skip-earnings", action="store_true", help="Skip the earnings calendar fetch.")
    parser.add_argument("--skip-dividends", action="store_true", help="Skip the dividend schedule fetch.")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory for yfinance cache files.")
    parser.add_argument("--ignore-proxy", action="store_true", help="Clear proxy environment variables for this run.")
    args = parser.parse_args(argv)
    if args.skip_earnings and args.skip_dividends:
        parser.error("Nothing to fetch. Remove --skip-earnings or --skip-dividends.")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run earnings/dividend extraction as a standalone or delegated CLI command."""
    args = parse_args(argv)
    if args.ignore_proxy:
        clear_proxy_environment()
    configure_yfinance_cache(args.cache_dir)

    if not args.skip_earnings:
        earnings = fetch_earnings_events(args.tickers)
        _print_frame("EARNINGS", earnings)

    if not args.skip_dividends:
        dividends = fetch_dividend_events(args.tickers)
        _print_frame("DIVIDENDS", dividends)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

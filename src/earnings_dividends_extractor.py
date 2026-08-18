from __future__ import annotations

import argparse
import os
import tempfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
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
UPCOMING_DIVIDEND_COLUMNS = [
    "Ticker",
    "ProviderSymbol",
    "ExDividendDate",
    "PayDate",
    "DeclaredAmount",
    "Frequency",
]
# Median day-gap between an issuer's last few ex-dividend dates, bucketed to a
# human frequency label. Wide, overlapping-free bands rather than exact day
# counts because real payment calendars drift by a few days each cycle.
_FREQUENCY_BANDS: list[tuple[int, int, str]] = [
    (1, 45, "monthly"),
    (46, 135, "quarterly"),
    (136, 275, "semi-annual"),
    (276, 400, "annual"),
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


def _infer_dividend_frequency(recent_ex_dates: list[Any]) -> str | None:
    """Bucket the median gap between an issuer's last few ex-dates to a label.

    Observed from the ticker's own historical `dividends` series rather than
    trusted from any single yfinance field -- there is no reliable explicit
    "frequency" field across yfinance versions, but payment spacing is
    directly observed data, not a guess. Returns `None` when fewer than two
    prior ex-dates are available or the spacing does not fall in a known band
    (irregular payers, or a fund that just started distributing).
    """
    if len(recent_ex_dates) < 2:
        return None
    ordered = sorted(recent_ex_dates)
    gaps = [(ordered[i] - ordered[i - 1]).days for i in range(1, len(ordered))]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    median_gap = sorted(gaps)[len(gaps) // 2]
    for low, high, label in _FREQUENCY_BANDS:
        if low <= median_gap <= high:
            return label
    return None


def _fetch_one_upcoming(symbol: str, yf_module: Any, session: object | None) -> list[dict[str, Any]]:
    """Fetch one ticker's next scheduled dividend, if yfinance has forecast one.

    yfinance's `calendar` attribute carries at most the *next* upcoming
    ex-dividend/payment date pair for a name well-covered by data providers;
    it is commonly empty for ETFs, funds, and smaller names, exactly like
    `earnings_dates` is for tickers without earnings (see
    `_fetch_one_earnings`) -- treated the same way here, as an expected
    empty result rather than a fetch failure.

    `calendar` does not carry a forward dividend amount, so the declared
    amount is projected from the issuer's own most recent historical
    per-payment amount (`client.dividends`, the same series
    `_fetch_one_dividends` reads), and frequency is inferred from the
    spacing of the last several ex-dates via `_infer_dividend_frequency`.
    This is a projection from the last confirmed rate, not a newly declared
    amount -- callers must present it as expected income, not confirmed.
    """
    try:
        client = _create_ticker(yf_module, symbol, session)
        calendar = client.calendar
        if not isinstance(calendar, dict) or not calendar:
            logger.info("No forward dividend calendar for %s (expected for many ETFs/funds)", symbol)
            return []
        next_ex_date = calendar.get("Ex-Dividend Date")
        if not next_ex_date or next_ex_date < date.today():
            return []
        pay_date = calendar.get("Dividend Date")

        history = client.dividends
        if history is None or len(history) == 0:
            logger.info("Upcoming ex-date found for %s but no dividend history to project an amount from", symbol)
            return []
        declared_amount = history.iloc[-1]
        recent_ex_dates = [ts.date() if hasattr(ts, "date") else ts for ts in history.tail(5).index]
        frequency = _infer_dividend_frequency(recent_ex_dates)

        return [
            {
                "Ticker": symbol,
                "ProviderSymbol": symbol,
                "ExDividendDate": next_ex_date,
                "PayDate": pay_date,
                "DeclaredAmount": declared_amount,
                "Frequency": frequency,
            }
        ]
    except Exception:
        logger.exception("Failed to fetch yfinance upcoming dividend for %s", symbol)
        return []


def fetch_upcoming_dividends(tickers: Iterable[str]) -> pd.DataFrame:
    """Fetch each ticker's next scheduled ex-dividend date, alongside (not in
    place of) `fetch_dividend_events`'s historical schedule.
    """
    normalized = _normalize_tickers(tickers)
    if not normalized:
        logger.info("No tickers provided for yfinance upcoming-dividend fetch")
        return _empty_frame(UPCOMING_DIVIDEND_COLUMNS)

    yf_module = _require_yfinance()
    session = _build_session()
    worker_count = min(YFINANCE_MAX_WORKERS, len(normalized))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(lambda symbol: _fetch_one_upcoming(symbol, yf_module, session), normalized)
        )
    records = [record for batch in results for record in batch]
    logger.info(
        "YFinance upcoming-dividend fetch complete | tickers=%d | rows=%d", len(normalized), len(records)
    )
    return pd.DataFrame(records, columns=UPCOMING_DIVIDEND_COLUMNS)


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
    parser.add_argument("--skip-dividends", action="store_true", help="Skip the historical dividend schedule fetch.")
    parser.add_argument("--skip-upcoming", action="store_true", help="Skip the upcoming ex-dividend forecast fetch.")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory for yfinance cache files.")
    parser.add_argument("--ignore-proxy", action="store_true", help="Clear proxy environment variables for this run.")
    args = parser.parse_args(argv)
    if args.skip_earnings and args.skip_dividends and args.skip_upcoming:
        parser.error(
            "Nothing to fetch. Remove --skip-earnings, --skip-dividends, or --skip-upcoming."
        )
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

    if not args.skip_upcoming:
        upcoming = fetch_upcoming_dividends(args.tickers)
        _print_frame("UPCOMING DIVIDENDS", upcoming)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

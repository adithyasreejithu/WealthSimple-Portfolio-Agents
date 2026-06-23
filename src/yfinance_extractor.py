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

from config import (
    YFINANCE_CANADIAN_SUFFIX,
    YFINANCE_DOWNLOAD_AUTO_ADJUST,
    YFINANCE_DOWNLOAD_GROUP_BY,
    YFINANCE_DOWNLOAD_THREADS,
    YFINANCE_ETF_INFO_COLUMNS,
    YFINANCE_HISTORY_COLUMNS,
    YFINANCE_MAX_WORKERS,
    YFINANCE_RETRY_QUOTE_TYPES,
    YFINANCE_SESSION_IMPERSONATE,
    YFINANCE_STOCK_INFO_COLUMNS,
)
from system_logger import get_logger

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime
    yf = None

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - yfinance can still run without the impersonated session
    curl_requests = None


STOCK_INFO_COLUMNS = YFINANCE_STOCK_INFO_COLUMNS
ETF_INFO_COLUMNS = YFINANCE_ETF_INFO_COLUMNS
HISTORY_COLUMNS = YFINANCE_HISTORY_COLUMNS

logger = get_logger(__name__)


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        value = str(ticker or "").strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def _require_yfinance() -> Any:
    if yf is None:
        raise RuntimeError("Missing yfinance dependency. Install project requirements before fetching stock data.")
    return yf


def _build_session() -> object | None:
    if curl_requests is None:
        logger.debug("curl_cffi is not installed; using yfinance default session")
        return None
    try:
        return curl_requests.Session(impersonate=YFINANCE_SESSION_IMPERSONATE)
    except Exception:
        logger.exception("Failed to create curl_cffi session; using yfinance default session")
        return None


def _create_ticker(yf_module: Any, ticker: str, session: object | None) -> Any:
    return yf_module.Ticker(ticker, session=session)


def _safe_getattr(value: object, attr: str) -> object | None:
    try:
        return getattr(value, attr, None)
    except Exception:
        logger.debug("Failed to read yfinance attribute %s", attr, exc_info=True)
        return None


def _resolve_ticker_info(ticker: str, yf_module: Any, session: object | None) -> tuple[Any, dict[str, Any]] | None:
    client = _create_ticker(yf_module, ticker, session)
    info = client.get_info() or {}
    quote_type = info.get("quoteType")

    if quote_type not in YFINANCE_RETRY_QUOTE_TYPES:
        return client, info

    if ticker.endswith(YFINANCE_CANADIAN_SUFFIX):
        return None

    fallback_ticker = f"{ticker}{YFINANCE_CANADIAN_SUFFIX}"
    fallback_client = _create_ticker(yf_module, fallback_ticker, session)
    fallback_info = fallback_client.get_info() or {}
    fallback_quote_type = fallback_info.get("quoteType")
    if fallback_quote_type in YFINANCE_RETRY_QUOTE_TYPES:
        logger.warning("No usable yfinance quote type found for %s or %s", ticker, fallback_ticker)
        return None

    logger.debug("Resolved %s with yfinance fallback ticker %s", ticker, fallback_ticker)
    return fallback_client, fallback_info


def _fetch_one_security_info(ticker: str, yf_module: Any, session: object | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        resolved = _resolve_ticker_info(ticker, yf_module, session)
        if resolved is None:
            return None, None

        client, info = resolved
        quote_type = info.get("quoteType")
        if quote_type == "EQUITY":
            return (
                {
                    "ticker": ticker,
                    "company_name": info.get("longName"),
                    "asset": quote_type,
                    "exchange": info.get("fullExchangeName"),
                    "currency": info.get("financialCurrency") or info.get("currency"),
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                },
                None,
            )

        if quote_type == "ETF":
            funds_data = _safe_getattr(client, "funds_data")
            return (
                None,
                {
                    "ticker": ticker,
                    "company_name": info.get("longName"),
                    "currency": info.get("financialCurrency") or info.get("currency"),
                    "fund_family": info.get("fundFamily"),
                    "asset": info.get("category") or info.get("fundCategory"),
                    "yield": info.get("yield"),
                    "expense_ratio": info.get("annualReportExpenseRatio"),
                    "aum": info.get("totalAssets"),
                    "nav": info.get("navPrice"),
                    "top_holdings": _safe_getattr(funds_data, "top_holdings"),
                    "sector_weights": _safe_getattr(funds_data, "sector_weightings"),
                },
            )

        logger.warning("Unknown security type for %s: %s", ticker, quote_type)
        return None, None
    except Exception:
        logger.exception("Failed to fetch yfinance security info for %s", ticker)
        return None, None


def fetch_security_info(tickers: Iterable[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    normalized_tickers = _normalize_tickers(tickers)
    if not normalized_tickers:
        logger.info("No tickers provided for yfinance security info fetch")
        return _empty_frame(STOCK_INFO_COLUMNS), _empty_frame(ETF_INFO_COLUMNS)

    yf_module = _require_yfinance()
    session = _build_session()
    stock_records: list[dict[str, Any]] = []
    etf_records: list[dict[str, Any]] = []

    worker_count = min(YFINANCE_MAX_WORKERS, len(normalized_tickers))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda ticker: _fetch_one_security_info(ticker, yf_module, session), normalized_tickers))

    for stock_record, etf_record in results:
        if stock_record is not None:
            stock_records.append(stock_record)
        if etf_record is not None:
            etf_records.append(etf_record)

    logger.info(
        "YFinance security info fetch complete | tickers=%d | stocks=%d | etfs=%d",
        len(normalized_tickers),
        len(stock_records),
        len(etf_records),
    )
    return (
        pd.DataFrame(stock_records, columns=STOCK_INFO_COLUMNS),
        pd.DataFrame(etf_records, columns=ETF_INFO_COLUMNS),
    )


def _normalize_date(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _stack_multi_ticker_history(data: pd.DataFrame) -> pd.DataFrame:
    try:
        stacked = data.stack(level=0, future_stack=True)
    except TypeError:
        stacked = data.stack(level=0)

    normalized = stacked.reset_index()
    rename_map: dict[object, str] = {}
    columns = list(normalized.columns)
    if columns:
        rename_map[columns[0]] = "Date"
    if len(columns) > 1:
        rename_map[columns[1]] = "Ticker"
    return normalized.rename(columns=rename_map)


def _normalize_history_data(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if data.empty:
        return _empty_frame(HISTORY_COLUMNS)

    if isinstance(data.columns, pd.MultiIndex):
        normalized = _stack_multi_ticker_history(data)
    else:
        normalized = data.reset_index()
        first_column = normalized.columns[0]
        normalized = normalized.rename(columns={first_column: "Date"})
        normalized["Ticker"] = tickers[0]

    for column in HISTORY_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = pd.NA

    price_columns = [column for column in HISTORY_COLUMNS if column not in {"Date", "Ticker"}]
    normalized = normalized.dropna(how="all", subset=price_columns)
    return normalized[HISTORY_COLUMNS].reset_index(drop=True)


def fetch_security_history(
    tickers: Iterable[str],
    start_date: str | date,
    end_date: str | date | None = None,
) -> pd.DataFrame:
    normalized_tickers = _normalize_tickers(tickers)
    if not normalized_tickers:
        logger.info("No tickers provided for yfinance historical fetch")
        return _empty_frame(HISTORY_COLUMNS)

    yf_module = _require_yfinance()
    resolved_end_date = _normalize_date(end_date or date.today())
    try:
        history = yf_module.download(
            tickers=normalized_tickers if len(normalized_tickers) > 1 else normalized_tickers[0],
            start=_normalize_date(start_date),
            end=resolved_end_date,
            threads=YFINANCE_DOWNLOAD_THREADS,
            auto_adjust=YFINANCE_DOWNLOAD_AUTO_ADJUST,
            group_by=YFINANCE_DOWNLOAD_GROUP_BY,
            progress=False,
        )
    except Exception:
        logger.exception("Failed to fetch yfinance historical data")
        return _empty_frame(HISTORY_COLUMNS)

    normalized = _normalize_history_data(history, normalized_tickers)
    logger.info(
        "YFinance historical fetch complete | tickers=%d | rows=%d | start=%s | end=%s",
        len(normalized_tickers),
        len(normalized),
        start_date,
        resolved_end_date,
    )
    return normalized


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "wealthsimple-yfinance-cache"


def configure_yfinance_cache(cache_dir: Path | str) -> None:
    yf_module = _require_yfinance()
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    yf_module.cache.set_cache_location(str(cache_path))
    logger.debug("Configured yfinance cache directory: %s", cache_path)


def clear_proxy_environment() -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
    logger.debug("Cleared proxy environment variables for yfinance CLI run")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch yfinance security metadata and optional historical prices.")
    parser.add_argument("--tickers", nargs="+", required=True, help="Ticker symbols to fetch, for example AAPL VFV.TO SHOP.")
    parser.add_argument("--include-history", action="store_true", help="Also fetch historical OHLCV data.")
    parser.add_argument("--start-date", help="History start date in YYYY-MM-DD format. Required with --include-history.")
    parser.add_argument("--end-date", help="History end date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--skip-info", action="store_true", help="Skip stock and ETF metadata fetch.")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory for yfinance cache files.")
    parser.add_argument("--ignore-proxy", action="store_true", help="Clear proxy environment variables for this run.")
    args = parser.parse_args(argv)

    if args.include_history and not args.start_date:
        parser.error("--start-date is required when --include-history is used.")
    if args.skip_info and not args.include_history:
        parser.error("Nothing to fetch. Remove --skip-info or add --include-history.")
    return args


def _print_frame(title: str, data: pd.DataFrame) -> None:
    print(title)
    if data.empty:
        print(f"No {title.lower()} rows returned.")
        return
    print(data.to_string(index=False))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.ignore_proxy:
        clear_proxy_environment()
    configure_yfinance_cache(args.cache_dir)

    if not args.skip_info:
        stocks, etfs = fetch_security_info(args.tickers)
        _print_frame("STOCKS", stocks)
        _print_frame("ETFS", etfs)

    if args.include_history:
        history = fetch_security_history(args.tickers, args.start_date, args.end_date)
        _print_frame("HISTORY", history)


if __name__ == "__main__":
    main()

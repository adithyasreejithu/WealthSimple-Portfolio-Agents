"""Ephemeral, JSON-safe yfinance research pull for the stock decision-support system.

This is the "first data pull" that gathers everything yfinance can supply for a
ticker so a downstream Thesis Writer / Wiki Update agent can fill
`Knowledge-Base/stocks/<TICKER>.md` (see
`docs/plans/goals/stock_decision_support_system.md`).

Scope boundary: this module only *fetches* raw provider data. It deliberately
does not compute technical indicators (RSI/MACD/moving averages), score
sentiment, or flag unusual options activity -- those interpretation layers
belong to later agents. Raw OHLCV history is fetched (via the pipeline's own
`fetch_security_history`) so a future Technical Analysis Agent can compute
indicators from it.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from config import DATABASE_PATH, YFINANCE_MAX_WORKERS  # noqa: E402
from yfinance_extractor import (  # noqa: E402
    _build_session,
    _create_ticker,
    _require_yfinance,
    configure_yfinance_cache,
    fetch_security_history,
)

# Sanity ceiling only -- tickers within it are fetched concurrently (see
# fetch_stock_research_data), not split into sequential sub-batches, so one
# invocation covering a full portfolio takes one pass, not several manual runs.
MAX_RESEARCH_BATCH_SIZE = 200
DEFAULT_HISTORY_DAYS = 400
MAX_OPTION_EXPIRATIONS = 3

# Field groups a research pull can request. Each maps to a fixed set of
# yfinance attributes -- see references/yfinance-research-contract.md.
RESEARCH_GROUPS = frozenset(
    {
        "overview",
        "valuation",
        "financials",
        "earnings",
        "analyst",
        "options",
        "news",
        "insider",
        "institutional",
        "dividends",
        "history",
    }
)
DEFAULT_GROUPS: tuple[str, ...] = (
    "overview",
    "valuation",
    "financials",
    "earnings",
    "analyst",
    "options",
    "news",
    "insider",
    "institutional",
    "dividends",
    "history",
)
# Groups that read from `Ticker.get_info()`; fetched once per ticker.
INFO_GROUPS = frozenset({"overview", "valuation", "dividends"})

OVERVIEW_INFO_KEYS = (
    "longName",
    "shortName",
    "quoteType",
    "longBusinessSummary",
    "sector",
    "industry",
    "fullExchangeName",
    "country",
    "currency",
    "financialCurrency",
    "website",
    "fullTimeEmployees",
)
VALUATION_INFO_KEYS = (
    "currentPrice",
    "marketCap",
    "enterpriseValue",
    "trailingPE",
    "forwardPE",
    "pegRatio",
    "trailingPegRatio",
    "priceToBook",
    "priceToSalesTrailing12Months",
    "enterpriseToEbitda",
    "enterpriseToRevenue",
    "freeCashflow",
    "operatingCashflow",
    "returnOnEquity",
    "returnOnAssets",
    "profitMargins",
    "grossMargins",
    "operatingMargins",
    "dividendYield",
    "targetMeanPrice",
    "recommendationKey",
)
DIVIDEND_INFO_KEYS = (
    "dividendRate",
    "dividendYield",
    "trailingAnnualDividendRate",
    "trailingAnnualDividendYield",
    "fiveYearAvgDividendYield",
    "payoutRatio",
    "exDividendDate",
    "lastDividendValue",
    "lastDividendDate",
)


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


def _info_subset(info: Mapping[str, Any] | None, keys: Iterable[str]) -> dict[str, Any]:
    info = info or {}
    return {key: _json_value(info.get(key)) for key in keys if not _is_missing(info.get(key))}


def _safe_map(parts: Mapping[str, Callable[[], Any]]) -> tuple[dict[str, Any], dict[str, str]]:
    """Fetch each labelled sub-attribute independently so one failure does not
    discard the whole group. Returns (data, per-subfield errors)."""
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, fetch in parts.items():
        try:
            data[key] = _json_value(fetch())
        except Exception as exc:  # Provider gaps are part of the output contract.
            data[key] = None
            errors[key] = f"{type(exc).__name__}: {exc}"
    return data, errors


def _fetch_overview(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _info_subset(info, OVERVIEW_INFO_KEYS), {}


def _fetch_valuation(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _info_subset(info, VALUATION_INFO_KEYS), {}


def _fetch_financials(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _safe_map(
        {
            "income_statement_annual": lambda: client.income_stmt,
            "income_statement_quarterly": lambda: client.quarterly_income_stmt,
            "balance_sheet_annual": lambda: client.balance_sheet,
            "balance_sheet_quarterly": lambda: client.quarterly_balance_sheet,
            "cash_flow_annual": lambda: client.cashflow,
            "cash_flow_quarterly": lambda: client.quarterly_cashflow,
        }
    )


def _fetch_earnings(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _safe_map(
        {
            "calendar": lambda: client.calendar,
            "earnings_dates": lambda: client.earnings_dates,
        }
    )


def _fetch_analyst(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _safe_map(
        {
            "recommendations": lambda: client.recommendations,
            "recommendations_summary": lambda: client.recommendations_summary,
            "price_targets": lambda: client.analyst_price_targets,
            "upgrades_downgrades": lambda: client.upgrades_downgrades,
        }
    )


def _option_chain_dict(chain: Any) -> dict[str, Any]:
    return {"calls": chain.calls, "puts": chain.puts}


def _fetch_options(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    expirations = list(client.options or ())
    parts: dict[str, Callable[[], Any]] = {}
    for expiry in expirations[:MAX_OPTION_EXPIRATIONS]:
        parts[f"chain_{expiry}"] = lambda e=expiry: _option_chain_dict(client.option_chain(e))
    data, errors = _safe_map(parts)
    data["expirations"] = list(expirations)
    return data, errors


def _fetch_news(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return {"headlines": _json_value(client.news)}, {}


def _fetch_insider(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _safe_map(
        {
            "transactions": lambda: client.insider_transactions,
            "purchases": lambda: client.insider_purchases,
            "roster_holders": lambda: client.insider_roster_holders,
        }
    )


def _fetch_institutional(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _safe_map(
        {
            "major_holders": lambda: client.major_holders,
            "institutional_holders": lambda: client.institutional_holders,
            "mutualfund_holders": lambda: client.mutualfund_holders,
        }
    )


def _fetch_dividends(client: Any, info: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    data, errors = _safe_map({"history": lambda: client.dividends})
    data["summary"] = _info_subset(info, DIVIDEND_INFO_KEYS)
    return data, errors


GROUP_FETCHERS: dict[str, Callable[[Any, Mapping[str, Any]], tuple[dict[str, Any], dict[str, str]]]] = {
    "overview": _fetch_overview,
    "valuation": _fetch_valuation,
    "financials": _fetch_financials,
    "earnings": _fetch_earnings,
    "analyst": _fetch_analyst,
    "options": _fetch_options,
    "news": _fetch_news,
    "insider": _fetch_insider,
    "institutional": _fetch_institutional,
    "dividends": _fetch_dividends,
}


def _default_history_fetcher(provider_symbol: str, start_date: date) -> Any:
    return fetch_security_history([provider_symbol], start_date)


def _fetch_history(provider_symbol: str, history_days: int, history_fetcher: Callable[[str, date], Any]) -> list[dict[str, Any]]:
    start_date = date.today() - timedelta(days=history_days)
    frame = history_fetcher(provider_symbol, start_date)
    if frame is None or getattr(frame, "empty", True):
        return []
    return _json_value(frame.to_dict(orient="records"))


def fetch_stock_research_data(
    requests: Iterable[Mapping[str, Any]],
    *,
    ticker_factory: Callable[[str], Any] | None = None,
    history_fetcher: Callable[[str, date], Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch the requested research groups for each verified provider symbol.

    Each request is ``{"ticker", "provider_symbol", "groups"?, "history_days"?}``.
    Per-ticker and per-subfield failures are recorded in the output rather than
    raised, so one blocked provider call never sinks the whole batch.
    """
    request_list = list(requests)
    if len(request_list) > MAX_RESEARCH_BATCH_SIZE:
        raise ValueError(f"Research batch exceeds {MAX_RESEARCH_BATCH_SIZE} tickers")

    if ticker_factory is None:
        yf_module = _require_yfinance()
        session = _build_session()
        ticker_factory = lambda symbol: _create_ticker(yf_module, symbol, session)
    if history_fetcher is None:
        history_fetcher = _default_history_fetcher

    output: list[dict[str, Any]] = []
    for request in request_list:
        ticker = str(request.get("ticker") or "").strip().upper()
        provider_symbol = str(request.get("provider_symbol") or "").strip().upper()
        groups = list(request.get("groups") or DEFAULT_GROUPS)
        history_days = int(request.get("history_days") or DEFAULT_HISTORY_DAYS)
        if not ticker or not provider_symbol or not set(groups).issubset(RESEARCH_GROUPS):
            raise ValueError("Invalid stock research request")

        item: dict[str, Any] = {
            "ticker": ticker,
            "provider_symbol": provider_symbol,
            "groups": groups,
            "data": {},
            "errors": {},
        }

        client = ticker_factory(provider_symbol) if any(group != "history" for group in groups) else None
        info: dict[str, Any] = {}
        info_error: str | None = None
        if client is not None and any(group in INFO_GROUPS for group in groups):
            try:
                info = client.get_info() or {}
            except Exception as exc:  # Info failure only fails info-backed groups.
                info_error = f"{type(exc).__name__}: {exc}"

        for group in groups:
            try:
                if group == "history":
                    item["data"][group] = _fetch_history(provider_symbol, history_days, history_fetcher)
                    continue
                if group in INFO_GROUPS and info_error is not None:
                    raise RuntimeError(f"info unavailable: {info_error}")
                data, sub_errors = GROUP_FETCHERS[group](client, info)
                item["data"][group] = data
                for sub_key, message in sub_errors.items():
                    item["errors"][f"{group}.{sub_key}"] = message
            except Exception as exc:  # Per-group failures are part of the contract.
                item["errors"][group] = f"{type(exc).__name__}: {exc}"

        output.append(item)
    return output


def _resolve_provider_symbols(tickers: list[str] | None, db_path: str | Path) -> dict[str, str | None]:
    """Return {ticker -> verified Yahoo provider_symbol} from the read-only DB.

    Mirrors the ``ticker_provider_mappings`` join used by
    read-portfolio-classification-data: only ``provider='yahoo'`` rows with
    ``verification_status='verified'`` are trusted, so this pull never does its
    own fuzzy symbol resolution.
    """
    import duckdb

    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configured database does not exist: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT t.ticker_symbol, m.provider_symbol
            FROM tickers t
            JOIN ticker_provider_mappings m ON m.ticker_id = t.ticker_id
                AND m.provider = 'yahoo' AND m.verification_status = 'verified'
            """
        ).fetchall()
    finally:
        connection.close()

    mapping = {str(symbol).upper(): (str(provider).upper() if provider else None) for symbol, provider in rows}
    if tickers:
        return {ticker.upper(): mapping.get(ticker.upper()) for ticker in tickers}
    return mapping


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "wealthsimple-yfinance-cache"


def _build_requests(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the request list from CLI args. Returns (requests, unresolved tickers)."""
    groups = args.groups or list(DEFAULT_GROUPS)

    if args.provider_symbol:
        if len(args.provider_symbol) != len(args.ticker or []):
            raise SystemExit("--provider-symbol must align one-to-one with --ticker")
        pairs = list(zip(args.ticker, args.provider_symbol))
        requests = [
            {"ticker": ticker, "provider_symbol": provider, "groups": groups, "history_days": args.history_days}
            for ticker, provider in pairs
        ]
        return requests, []

    if args.all_holdings or args.ticker:
        resolved = _resolve_provider_symbols(None if args.all_holdings else args.ticker, args.db_path)
        requests = []
        unresolved = []
        for ticker, provider in resolved.items():
            if provider:
                requests.append(
                    {"ticker": ticker, "provider_symbol": provider, "groups": groups, "history_days": args.history_days}
                )
            else:
                unresolved.append(ticker)
        return requests, unresolved

    stdin_payload = json.load(sys.stdin)
    if not isinstance(stdin_payload, list):
        raise SystemExit("stdin request must be a JSON list of {ticker, provider_symbol, ...}")
    return [dict(entry) for entry in stdin_payload], []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch yfinance research data for stock thesis pages.")
    parser.add_argument("--ticker", nargs="+", help="Portfolio ticker symbol(s) to pull research for.")
    parser.add_argument(
        "--provider-symbol",
        nargs="+",
        help="Explicit Yahoo symbol(s), aligned with --ticker. Omit to resolve from the verified DB mapping.",
    )
    parser.add_argument("--all-holdings", action="store_true", help="Pull every verified holding from the database.")
    parser.add_argument(
        "--groups",
        nargs="+",
        choices=sorted(RESEARCH_GROUPS),
        help="Research groups to fetch. Defaults to all groups.",
    )
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS, help="Days of OHLCV history to fetch.")
    parser.add_argument("--db-path", type=Path, default=DATABASE_PATH, help="Read-only DuckDB path for symbol resolution.")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory for yfinance cache files.")
    parser.add_argument("--output", type=Path, help="Write JSON output to this path instead of stdout.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_yfinance_cache(args.cache_dir)

    requests, unresolved = _build_requests(args)
    if unresolved:
        print(f"Skipping tickers without a verified Yahoo mapping: {', '.join(sorted(unresolved))}", file=sys.stderr)
    if not requests:
        print("No tickers to fetch.", file=sys.stderr)
        return 1

    payload = fetch_stock_research_data(requests)
    text = json.dumps(payload, indent=2 if args.pretty else None)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

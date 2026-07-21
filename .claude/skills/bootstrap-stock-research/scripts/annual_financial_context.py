"""Fetch annual financial statements as ephemeral first-run context for stock KB prep.

This module is invoked by the KB prep agent on a ticker's first-ever analysis run
to provide the analyst with multi-year context for the initial Company Overview
thesis. The output is ephemeral, not persisted to the database.
"""

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from system_logger import get_logger
from yfinance_extractor import (
    _build_session,
    _create_ticker,
    _require_yfinance,
    clear_proxy_environment,
    configure_yfinance_cache,
)

# Import the shared period-field computation from the pipeline module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))
from financial_snapshots_extractor import _compute_period_fields, _safe_statement

logger = get_logger(__name__)

ANNUAL_CONTEXT_SCHEMA = "annual-financial-context.v1"


def _period_end_iso(period_end_date: object) -> str:
    """Render a period-end column label as a plain YYYY-MM-DD string."""
    if hasattr(period_end_date, "date"):
        return period_end_date.date().isoformat()
    if hasattr(period_end_date, "isoformat"):
        return period_end_date.isoformat()
    return str(period_end_date)


def fetch_annual_financial_context(ticker: str) -> dict[str, Any]:
    """Fetch one ticker's ANNUAL financial statements as ephemeral,
    non-persisted first-run context for the KB prep agent.

    Unlike the quarterly pipeline sync, this is single-ticker (no batching/
    threading -- called on-demand for exactly one new ticker at a time) and
    returns a plain JSON-safe dict, not a DataFrame, since nothing downstream
    needs pandas -- the caller is an orchestrator reading stdout JSON.

    Never raises: an ETF/fund (or a fetch failure) yields an envelope with
    an empty `periods` list and a human-readable `note`, so the caller can
    always parse the result.
    """
    symbol = (ticker or "").strip().upper()
    envelope: dict[str, Any] = {
        "schema": ANNUAL_CONTEXT_SCHEMA,
        "ticker": symbol,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "period_count": 0,
        "periods": [],
        "note": None,
    }
    if not symbol:
        envelope["note"] = "No ticker provided."
        return envelope

    try:
        yf_module = _require_yfinance()
        session = _build_session()
        client = _create_ticker(yf_module, symbol, session)
        income = _safe_statement(client, "financials", symbol)
        balance = _safe_statement(client, "balance_sheet", symbol)
        cashflow = _safe_statement(client, "cashflow", symbol)
    except Exception:
        logger.exception("Failed to fetch annual financial context for %s", symbol)
        envelope["note"] = "Fetch failed; see logs."
        return envelope

    all_dates: set[object] = set()
    for frame in (income, balance, cashflow):
        if frame is not None and not frame.empty:
            all_dates.update(frame.columns)
    if not all_dates:
        logger.info(
            "No annual financial statements for %s (expected for ETFs/funds; not an error)",
            symbol,
        )
        envelope["note"] = "No annual statements returned (expected for ETFs/funds)."
        return envelope

    periods: list[dict[str, Any]] = []
    for period_end_date in sorted(all_dates):
        fields = _compute_period_fields(income, balance, cashflow, period_end_date)
        periods.append({"period_end_date": _period_end_iso(period_end_date), **fields})

    envelope["periods"] = periods
    envelope["period_count"] = len(periods)
    logger.info(
        "YFinance annual financial context fetch complete | ticker=%s | periods=%d",
        symbol, len(periods),
    )
    return envelope


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "wealthsimple-yfinance-cache"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch one ticker's annual financial statements as "
                    "ephemeral, non-persisted first-run KB context."
    )
    parser.add_argument(
        "--ticker", required=True,
        help="Single provider (Yahoo) ticker symbol, e.g. AAPL or SHOP.TO.",
    )
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory for yfinance cache files.")
    parser.add_argument("--ignore-proxy", action="store_true", help="Clear proxy environment variables for this run.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point invoked by the KB prep orchestrator on a ticker's first-ever run.

    Emits one `annual-financial-context.v1` JSON document to stdout -- never
    persisted, never written to DuckDB.
    """
    args = parse_args(argv)
    if args.ignore_proxy:
        clear_proxy_environment()
    configure_yfinance_cache(args.cache_dir)

    context = fetch_annual_financial_context(args.ticker)
    print(json.dumps(context, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

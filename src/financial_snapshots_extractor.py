from __future__ import annotations

import argparse
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

FINANCIAL_SNAPSHOT_COLUMNS = [
    "Ticker",
    "ProviderSymbol",
    "PeriodEndDate",
    "Revenue",
    "NetIncome",
    "Eps",
    "GrossMargin",
    "OperatingMargin",
    "DebtToEquity",
    "CurrentRatio",
    "FreeCashFlow",
    "Extra",
]

# Line-item label aliases tried in order, per statement, for each named
# column -- yfinance's own labels vary across versions/industries. Mirrors
# the alias-tuple pattern already used by the archived
# `.claude/skills/_archive/evaluate-stock-decision/scripts/scoring_worksheet.py`'s
# `_line_item` helper.
REVENUE_LABELS = ("Total Revenue", "TotalRevenue", "Revenue")
NET_INCOME_LABELS = ("Net Income", "NetIncome", "Net Income Common Stockholders")
DILUTED_EPS_LABELS = ("Diluted EPS", "DilutedEPS")
BASIC_EPS_LABELS = ("Basic EPS", "BasicEPS")
GROSS_PROFIT_LABELS = ("Gross Profit", "GrossProfit")
COST_OF_REVENUE_LABELS = ("Cost Of Revenue", "CostOfRevenue")
OPERATING_INCOME_LABELS = ("Operating Income", "OperatingIncome")
TOTAL_DEBT_LABELS = ("Total Debt", "TotalDebt")
STOCKHOLDERS_EQUITY_LABELS = ("Stockholders Equity", "Total Stockholder Equity", "StockholdersEquity")
CURRENT_ASSETS_LABELS = ("Current Assets", "Total Current Assets", "CurrentAssets")
CURRENT_LIABILITIES_LABELS = ("Current Liabilities", "Total Current Liabilities", "CurrentLiabilities")
FREE_CASH_FLOW_LABELS = ("Free Cash Flow", "FreeCashFlow")
OPERATING_CASH_FLOW_LABELS = ("Operating Cash Flow", "OperatingCashFlow", "Total Cash From Operating Activities")
CAPITAL_EXPENDITURE_LABELS = ("Capital Expenditure", "CapitalExpenditure")

# Every alias consumed by a named column, per statement -- everything else
# that statement returns for a period goes into the `extra` JSON blob
# instead (see financial_snapshots.extra in docs/architecture/database_schema.md).
_INCOME_CONSUMED_LABELS = frozenset(
    REVENUE_LABELS + NET_INCOME_LABELS + DILUTED_EPS_LABELS + BASIC_EPS_LABELS
    + GROSS_PROFIT_LABELS + COST_OF_REVENUE_LABELS + OPERATING_INCOME_LABELS
)
_BALANCE_CONSUMED_LABELS = frozenset(
    TOTAL_DEBT_LABELS + STOCKHOLDERS_EQUITY_LABELS
    + CURRENT_ASSETS_LABELS + CURRENT_LIABILITIES_LABELS
)
_CASHFLOW_CONSUMED_LABELS = frozenset(
    FREE_CASH_FLOW_LABELS + OPERATING_CASH_FLOW_LABELS + CAPITAL_EXPENDITURE_LABELS
)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _line_item(frame: pd.DataFrame | None, column: object, labels: tuple[str, ...]) -> float | None:
    """Return the first alias in `labels` present in `frame`'s index for `column`."""
    if frame is None or frame.empty or column not in frame.columns:
        return None
    for label in labels:
        if label in frame.index:
            value = _num(frame.at[label, column])
            if value is not None:
                return value
    return None


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _extra_line_items(
    frame: pd.DataFrame | None, column: object, consumed_labels: frozenset[str]
) -> dict[str, Any]:
    """Return every line item for `column` not already consumed by a named column."""
    if frame is None or frame.empty or column not in frame.columns:
        return {}
    return {
        str(label): _json_safe(frame.at[label, column])
        for label in frame.index
        if label not in consumed_labels
    }


def _safe_statement(client: Any, attr: str, symbol: str) -> pd.DataFrame | None:
    """Fetch one statement property, isolating failures per statement.

    An ETF/fund ticker legitimately returns an empty statement (not an
    exception) -- confirmed live for CDZ.TO across all three quarterly
    statement properties. A raising statement is isolated here so it never
    blocks the other two statements for the same ticker.
    """
    try:
        return getattr(client, attr)
    except Exception:
        logger.exception("Failed to fetch %s for %s", attr, symbol)
        return None


def _compute_period_fields(
    income: pd.DataFrame | None,
    balance: pd.DataFrame | None,
    cashflow: pd.DataFrame | None,
    period_end_date: object,
) -> dict[str, Any]:
    """Derive one period's named columns + `extra` blob from the three statements.

    Shared verbatim by the quarterly (`_fetch_one_financial_snapshots`) and
    annual (`_fetch_one_annual_context`) paths so both apply identical
    computation rules (gross/operating margin, debt-to-equity, current ratio,
    the capex-sign-aware FCF fallback, and the unmapped-line-item `extra`
    capture). Keys are snake_case; the quarterly path maps them to its
    CamelCase frame columns.
    """
    revenue = _line_item(income, period_end_date, REVENUE_LABELS)
    net_income = _line_item(income, period_end_date, NET_INCOME_LABELS)
    eps = _line_item(income, period_end_date, DILUTED_EPS_LABELS)
    if eps is None:
        eps = _line_item(income, period_end_date, BASIC_EPS_LABELS)

    gross_profit = _line_item(income, period_end_date, GROSS_PROFIT_LABELS)
    if gross_profit is None:
        cost_of_revenue = _line_item(income, period_end_date, COST_OF_REVENUE_LABELS)
        if revenue is not None and cost_of_revenue is not None:
            gross_profit = revenue - cost_of_revenue
    gross_margin = (
        gross_profit / revenue
        if gross_profit is not None and revenue not in (None, 0)
        else None
    )

    operating_income = _line_item(income, period_end_date, OPERATING_INCOME_LABELS)
    operating_margin = (
        operating_income / revenue
        if operating_income is not None and revenue not in (None, 0)
        else None
    )

    debt = _line_item(balance, period_end_date, TOTAL_DEBT_LABELS)
    equity = _line_item(balance, period_end_date, STOCKHOLDERS_EQUITY_LABELS)
    debt_to_equity = debt / equity if debt is not None and equity not in (None, 0) else None

    current_assets = _line_item(balance, period_end_date, CURRENT_ASSETS_LABELS)
    current_liabilities = _line_item(balance, period_end_date, CURRENT_LIABILITIES_LABELS)
    current_ratio = (
        current_assets / current_liabilities
        if current_assets is not None and current_liabilities not in (None, 0)
        else None
    )

    free_cash_flow = _line_item(cashflow, period_end_date, FREE_CASH_FLOW_LABELS)
    if free_cash_flow is None:
        operating_cash_flow = _line_item(cashflow, period_end_date, OPERATING_CASH_FLOW_LABELS)
        capital_expenditure = _line_item(cashflow, period_end_date, CAPITAL_EXPENDITURE_LABELS)
        if operating_cash_flow is not None and capital_expenditure is not None:
            # Capital Expenditure is stored negative-as-outflow by yfinance
            # -- confirmed live against NVDA/MCD/T -- so this is addition,
            # not subtraction.
            free_cash_flow = operating_cash_flow + capital_expenditure

    extra = {
        "income_statement": _extra_line_items(income, period_end_date, _INCOME_CONSUMED_LABELS),
        "balance_sheet": _extra_line_items(balance, period_end_date, _BALANCE_CONSUMED_LABELS),
        "cash_flow": _extra_line_items(cashflow, period_end_date, _CASHFLOW_CONSUMED_LABELS),
    }

    return {
        "revenue": revenue,
        "net_income": net_income,
        "eps": eps,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "debt_to_equity": debt_to_equity,
        "current_ratio": current_ratio,
        "free_cash_flow": free_cash_flow,
        "extra": extra,
    }


def _fetch_one_financial_snapshots(
    symbol: str, yf_module: Any, session: object | None
) -> list[dict[str, Any]]:
    """Fetch one ticker's quarterly financial statements; per-ticker failures are isolated.

    ETF/fund tickers legitimately return empty quarterly statements -- treat
    that as a normal, expected outcome, not a fetch failure, mirroring the
    confirmed `earnings_dates` behavior for fund tickers.
    """
    try:
        client = _create_ticker(yf_module, symbol, session)
        income = _safe_statement(client, "quarterly_income_stmt", symbol)
        balance = _safe_statement(client, "quarterly_balance_sheet", symbol)
        cashflow = _safe_statement(client, "quarterly_cashflow", symbol)

        all_dates: set[object] = set()
        for frame in (income, balance, cashflow):
            if frame is not None and not frame.empty:
                all_dates.update(frame.columns)
        if not all_dates:
            logger.info(
                "No quarterly financial statements for %s (expected for ETFs/funds; not an error)",
                symbol,
            )
            return []

        records: list[dict[str, Any]] = []
        for period_end_date in sorted(all_dates):
            fields = _compute_period_fields(income, balance, cashflow, period_end_date)
            records.append(
                {
                    "Ticker": symbol,
                    "ProviderSymbol": symbol,
                    "PeriodEndDate": period_end_date,
                    "Revenue": fields["revenue"],
                    "NetIncome": fields["net_income"],
                    "Eps": fields["eps"],
                    "GrossMargin": fields["gross_margin"],
                    "OperatingMargin": fields["operating_margin"],
                    "DebtToEquity": fields["debt_to_equity"],
                    "CurrentRatio": fields["current_ratio"],
                    "FreeCashFlow": fields["free_cash_flow"],
                    "Extra": fields["extra"],
                }
            )
        return records
    except Exception:
        logger.exception("Failed to fetch yfinance financial snapshots for %s", symbol)
        return []


def fetch_financial_snapshots(tickers: Iterable[str]) -> pd.DataFrame:
    """Fetch per-quarter company financial statement data for the given provider symbols."""
    normalized = _normalize_tickers(tickers)
    if not normalized:
        logger.info("No tickers provided for yfinance financial snapshots fetch")
        return _empty_frame(FINANCIAL_SNAPSHOT_COLUMNS)

    yf_module = _require_yfinance()
    session = _build_session()
    worker_count = min(YFINANCE_MAX_WORKERS, len(normalized))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(
                lambda symbol: _fetch_one_financial_snapshots(symbol, yf_module, session), normalized
            )
        )
    records = [record for batch in results for record in batch]
    logger.info(
        "YFinance financial snapshots fetch complete | tickers=%d | rows=%d",
        len(normalized), len(records),
    )
    return pd.DataFrame(records, columns=FINANCIAL_SNAPSHOT_COLUMNS)


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "wealthsimple-yfinance-cache"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch yfinance per-quarter company financial statement data."
    )
    parser.add_argument(
        "--tickers", nargs="+", required=True,
        help="Provider (Yahoo) ticker symbols to fetch, for example AAPL VFV.TO SHOP.TO.",
    )
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir(), help="Directory for yfinance cache files.")
    parser.add_argument("--ignore-proxy", action="store_true", help="Clear proxy environment variables for this run.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run financial-snapshots extraction as a standalone or delegated CLI command."""
    args = parse_args(argv)
    if args.ignore_proxy:
        clear_proxy_environment()
    configure_yfinance_cache(args.cache_dir)

    snapshots = fetch_financial_snapshots(args.tickers)
    _print_frame("FINANCIAL SNAPSHOTS", snapshots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

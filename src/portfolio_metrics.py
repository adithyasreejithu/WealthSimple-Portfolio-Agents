"""Pure financial calculations used by the database-backed analytics layer.

The vetted formulas are adapted from ``ref/portfolio_metrics.py``. Policy and
allocation-advice functions are intentionally excluded because this repository
does not contain the reference policy configuration.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pandas as pd

from config import ANNUALIZATION_PERIODS, DEFAULT_RISK_FREE_RATE, WEALTHSIMPLE_FX_FEE_RATE


def estimate_wealthsimple_fx_fee_cad(transaction_type: str, cad_amount: Decimal) -> Decimal:
    """Extract the configured fee from a gross BUY debit or net SELL credit."""
    kind = str(transaction_type).upper()
    if cad_amount <= 0:
        return Decimal("0")
    if kind == "BUY":
        return cad_amount - (cad_amount / (Decimal("1") + WEALTHSIMPLE_FX_FEE_RATE))
    if kind == "SELL":
        return (cad_amount / (Decimal("1") - WEALTHSIMPLE_FX_FEE_RATE)) - cad_amount
    return Decimal("0")


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    return float(value)


def _sorted_historical_values(values: list[dict]) -> list[dict]:
    frame = pd.DataFrame(values)
    if frame.empty or "date" not in frame.columns:
        return values
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    return frame.sort_values("date", kind="stable").to_dict(orient="records")


def calculate_position_weights(holdings: list[dict]) -> dict[str, dict]:
    """Return holdings-only portfolio weights by ticker."""
    total = sum(_safe_float(row.get("market_value")) for row in holdings)
    if total <= 0:
        return {}
    return {
        row["ticker_symbol"]: {
            "ticker": row["ticker_symbol"],
            "market_value": _safe_float(row.get("market_value")),
            "weight": _safe_float(row.get("market_value")) / total,
        }
        for row in holdings
    }


def calculate_unrealized_gain_percent(position: dict) -> dict:
    """Return unrealized dollar and percentage gain for one position."""
    market_value = _safe_float(position.get("market_value"))
    cost_basis = _safe_float(position.get("cost_basis"))
    gain = market_value - cost_basis
    return {
        "ticker": position.get("ticker_symbol"),
        "market_value": market_value,
        "cost_basis": cost_basis,
        "unrealized_gain": gain,
        "unrealized_gain_percent": gain / cost_basis if cost_basis > 0 else 0.0,
    }


def calculate_total_return(historical_values: list[dict]) -> dict:
    """Calculate simple return from the first to last positive portfolio value."""
    clean = [
        row for row in _sorted_historical_values(historical_values)
        if _safe_float(row.get("portfolio_value")) > 0
    ]
    if len(clean) < 2:
        return {"total_return": 0.0, "start_value": 0.0, "end_value": 0.0}
    start = _safe_float(clean[0]["portfolio_value"])
    end = _safe_float(clean[-1]["portfolio_value"])
    return {"total_return": (end / start) - 1, "start_value": start, "end_value": end}


def _daily_returns(historical_values: list[dict]) -> pd.Series:
    values = pd.Series(
        [_safe_float(row.get("portfolio_value")) for row in _sorted_historical_values(historical_values)],
        dtype="float64",
    )
    return values[values > 0].pct_change().dropna()


def calculate_volatility(
    historical_values: list[dict], periods_per_year: int = ANNUALIZATION_PERIODS
) -> dict:
    """Calculate daily and annualized portfolio volatility."""
    returns = _daily_returns(historical_values)
    if returns.empty:
        return {"volatility": 0.0, "daily_volatility": 0.0}
    daily = float(returns.std(ddof=0))
    return {"volatility": daily * math.sqrt(periods_per_year), "daily_volatility": daily}


def calculate_max_drawdown(historical_values: list[dict]) -> dict:
    """Calculate the worst peak-to-trough decline."""
    values = pd.Series(
        [_safe_float(row.get("portfolio_value")) for row in _sorted_historical_values(historical_values)],
        dtype="float64",
    )
    values = values[values > 0]
    if values.empty:
        return {"max_drawdown": 0.0}
    return {"max_drawdown": float(((values / values.cummax()) - 1).min())}


def calculate_sharpe_ratio(
    historical_values: list[dict],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = ANNUALIZATION_PERIODS,
) -> dict:
    """Calculate the reference implementation's annualized Sharpe ratio."""
    returns = _daily_returns(historical_values)
    if returns.empty:
        return {"sharpe_ratio": 0.0}
    daily_volatility = returns.std(ddof=0)
    if daily_volatility == 0 or pd.isna(daily_volatility):
        return {"sharpe_ratio": 0.0}
    excess = returns - (risk_free_rate / periods_per_year)
    return {"sharpe_ratio": float((excess.mean() / daily_volatility) * math.sqrt(periods_per_year))}


def financial_metrics_summary(holdings: list[dict], historical_values: list[dict]) -> dict:
    """Compose all non-policy reference metrics without database access."""
    return {
        "weights_by_ticker": calculate_position_weights(holdings),
        "unrealized_gains": {
            row["ticker_symbol"]: calculate_unrealized_gain_percent(row) for row in holdings
        },
        "risk_metrics": {
            "total_return": calculate_total_return(historical_values),
            "volatility": calculate_volatility(historical_values),
            "max_drawdown": calculate_max_drawdown(historical_values),
            "sharpe_ratio": calculate_sharpe_ratio(historical_values),
        },
    }

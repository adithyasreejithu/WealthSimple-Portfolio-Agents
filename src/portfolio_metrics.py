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


def _returns_series(returns: list[dict]) -> pd.Series:
    """Extract a plain float return series from an adjusted-returns list, sorted by date."""
    if not returns:
        return pd.Series(dtype="float64")
    frame = pd.DataFrame(returns).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date", kind="stable")
    return pd.Series([_safe_float(v) for v in frame["return"]], dtype="float64")


def calculate_volatility(
    historical_values: list[dict], periods_per_year: int = ANNUALIZATION_PERIODS
) -> dict:
    """Calculate daily and annualized volatility from a raw valuation series (legacy path)."""
    returns = _daily_returns(historical_values)
    if returns.empty:
        return {"volatility": 0.0, "daily_volatility": 0.0}
    daily = float(returns.std(ddof=0))
    return {"volatility": daily * math.sqrt(periods_per_year), "daily_volatility": daily}


def calculate_max_drawdown(historical_values: list[dict]) -> dict:
    """Calculate the worst peak-to-trough decline from a raw valuation series (legacy path)."""
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
    """Calculate annualized Sharpe ratio from a raw valuation series (legacy path)."""
    returns = _daily_returns(historical_values)
    if returns.empty:
        return {"sharpe_ratio": 0.0}
    daily_volatility = returns.std(ddof=0)
    if daily_volatility == 0 or pd.isna(daily_volatility):
        return {"sharpe_ratio": 0.0}
    excess = returns - (risk_free_rate / periods_per_year)
    return {"sharpe_ratio": float((excess.mean() / daily_volatility) * math.sqrt(periods_per_year))}


def calculate_adjusted_daily_returns(
    historical_values: list[dict], external_flows: list[dict]
) -> list[dict]:
    """Approximate time-weighted daily returns by removing external cash flows.

    ``r_t = (V_t - V_{t-1} - F_t) / V_{t-1}`` so contributions and withdrawals
    do not get counted as investment performance. Rows where the prior
    valuation is not positive are skipped because no meaningful return exists.
    """
    values = _sorted_historical_values(historical_values)
    if len(values) < 2:
        return []
    flow_by_date: dict[str, float] = {}
    for flow in external_flows:
        key = str(pd.to_datetime(flow.get("date"), errors="coerce").date()) if flow.get("date") is not None else None
        if key is None:
            continue
        flow_by_date[key] = flow_by_date.get(key, 0.0) + _safe_float(flow.get("amount"))

    results: list[dict] = []
    previous_value: float | None = None
    previous_date = None
    for row in values:
        current_date = row["date"]
        current_value = _safe_float(row.get("portfolio_value"))
        date_key = str(current_date.date()) if hasattr(current_date, "date") else str(current_date)
        flow = flow_by_date.get(date_key, 0.0)
        if previous_value is not None and previous_value > 0:
            results.append(
                {
                    "date": current_date,
                    "return": (current_value - previous_value - flow) / previous_value,
                    "value": current_value,
                    "flow": flow,
                }
            )
        previous_value = current_value
        previous_date = current_date
    return results


def build_wealth_index(adjusted_returns: list[dict]) -> list[dict]:
    """Build a cumulative-product wealth index (base 1.0) from adjusted returns."""
    if not adjusted_returns:
        return []
    sorted_returns = sorted(adjusted_returns, key=lambda row: row["date"])
    index = 1.0
    wealth_index: list[dict] = []
    for row in sorted_returns:
        index *= 1.0 + _safe_float(row.get("return"))
        wealth_index.append({"date": row["date"], "index": index})
    return wealth_index


def calculate_twr_total_return(adjusted_returns: list[dict]) -> dict:
    """Calculate the geometrically-linked time-weighted total return."""
    if not adjusted_returns:
        return {"available": False, "reason": "No adjusted return periods available.", "total_return": None}
    wealth_index = build_wealth_index(adjusted_returns)
    total_return = wealth_index[-1]["index"] - 1.0
    start_date = adjusted_returns[0]["date"]
    end_date = adjusted_returns[-1]["date"]
    span_days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days
    result: dict[str, Any] = {"available": True, "total_return": total_return, "span_days": span_days}
    if span_days >= 365:
        years = span_days / 365.0
        result["annualized_return"] = (1.0 + total_return) ** (1.0 / years) - 1.0
    else:
        result["annualized_return"] = None
    return result


def calculate_adjusted_volatility(
    adjusted_returns: list[dict], periods_per_year: int = ANNUALIZATION_PERIODS
) -> dict:
    """Calculate daily and annualized volatility from cash-flow-adjusted returns."""
    returns = _returns_series(adjusted_returns)
    if returns.empty:
        return {"available": False, "reason": "No adjusted return periods available.", "volatility": None}
    daily = float(returns.std(ddof=0))
    return {"available": True, "volatility": daily * math.sqrt(periods_per_year), "daily_volatility": daily}


def calculate_adjusted_sharpe_ratio(
    adjusted_returns: list[dict],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = ANNUALIZATION_PERIODS,
) -> dict:
    """Calculate annualized Sharpe ratio from cash-flow-adjusted returns."""
    returns = _returns_series(adjusted_returns)
    if returns.empty:
        return {"available": False, "reason": "No adjusted return periods available.", "sharpe_ratio": None}
    daily_volatility = returns.std(ddof=0)
    if daily_volatility == 0 or pd.isna(daily_volatility):
        return {"available": False, "reason": "Adjusted returns have zero volatility.", "sharpe_ratio": None}
    excess = returns - (risk_free_rate / periods_per_year)
    ratio = float((excess.mean() / daily_volatility) * math.sqrt(periods_per_year))
    return {"available": True, "sharpe_ratio": ratio}


def calculate_sortino_ratio(
    adjusted_returns: list[dict],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = ANNUALIZATION_PERIODS,
) -> dict:
    """Calculate annualized Sortino ratio using downside deviation of adjusted returns."""
    returns = _returns_series(adjusted_returns)
    if returns.empty:
        return {"available": False, "reason": "No adjusted return periods available.", "sortino_ratio": None}
    daily_rf = risk_free_rate / periods_per_year
    downside = returns[returns < daily_rf] - daily_rf
    if downside.empty:
        return {"available": False, "reason": "No downside periods to compute Sortino ratio.", "sortino_ratio": None}
    downside_deviation = float(math.sqrt((downside**2).mean()))
    if downside_deviation == 0:
        return {"available": False, "reason": "Downside deviation is zero.", "sortino_ratio": None}
    excess_mean = float((returns - daily_rf).mean())
    ratio = (excess_mean / downside_deviation) * math.sqrt(periods_per_year)
    return {"available": True, "sortino_ratio": ratio}


def calculate_drawdown_details(wealth_index: list[dict]) -> dict:
    """Calculate max drawdown with peak/trough/recovery dates from a wealth index."""
    if not wealth_index:
        return {
            "available": False,
            "reason": "No adjusted return periods available.",
            "max_drawdown": None,
        }
    frame = pd.DataFrame(wealth_index)
    frame["cummax"] = frame["index"].cummax()
    frame["drawdown"] = (frame["index"] / frame["cummax"]) - 1.0
    trough_position = int(frame["drawdown"].idxmin())
    max_drawdown = float(frame.loc[trough_position, "drawdown"])
    trough_date = frame.loc[trough_position, "date"]
    peak_value = frame.loc[trough_position, "cummax"]
    peak_rows = frame.loc[: trough_position]
    peak_date = peak_rows.loc[peak_rows["index"] == peak_value, "date"].iloc[-1]
    recovery_rows = frame.loc[trough_position:]
    recovered = recovery_rows[recovery_rows["index"] >= peak_value]
    recovery_date = recovered["date"].iloc[0] if not recovered.empty else None
    days_to_recover = (
        (pd.to_datetime(recovery_date) - pd.to_datetime(trough_date)).days if recovery_date is not None else None
    )
    current_drawdown = float(frame["drawdown"].iloc[-1])
    return {
        "available": True,
        "max_drawdown": max_drawdown,
        "peak_date": peak_date,
        "trough_date": trough_date,
        "recovery_date": recovery_date,
        "days_to_recover": days_to_recover,
        "current_drawdown": current_drawdown,
    }


def calculate_concentration(weights_by_ticker: dict[str, dict]) -> dict:
    """Calculate concentration metrics: top-N weights, HHI, and single-name max."""
    if not weights_by_ticker:
        return {
            "available": False,
            "reason": "No current holdings to assess concentration.",
            "top_1": None,
            "top_5": None,
            "top_10": None,
            "hhi": None,
            "max_single_name_weight": None,
            "max_single_name_ticker": None,
        }
    sorted_weights = sorted(
        (_safe_float(row.get("weight")) for row in weights_by_ticker.values()), reverse=True
    )
    top_ticker, top_row = max(weights_by_ticker.items(), key=lambda item: _safe_float(item[1].get("weight")))
    return {
        "available": True,
        "top_1": sum(sorted_weights[:1]),
        "top_5": sum(sorted_weights[:5]),
        "top_10": sum(sorted_weights[:10]),
        "hhi": sum(w * w for w in sorted_weights),
        "max_single_name_weight": _safe_float(top_row.get("weight")),
        "max_single_name_ticker": top_ticker,
    }


def calculate_benchmark_stats(
    portfolio_returns: list[dict],
    benchmark_returns: list[dict],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = ANNUALIZATION_PERIODS,
    minimum_overlap: int = 20,
) -> dict | None:
    """Calculate active return, tracking error, information ratio, alpha, and beta.

    Returns ``None`` when there are fewer than ``minimum_overlap`` overlapping
    dates between the portfolio and benchmark return series, since the
    resulting statistics would not be reliable.
    """
    if not portfolio_returns or not benchmark_returns:
        return None
    port_frame = pd.DataFrame(portfolio_returns)[["date", "return"]].rename(columns={"return": "portfolio"})
    bench_frame = pd.DataFrame(benchmark_returns)[["date", "return"]].rename(columns={"return": "benchmark"})
    port_frame["date"] = pd.to_datetime(port_frame["date"], errors="coerce")
    bench_frame["date"] = pd.to_datetime(bench_frame["date"], errors="coerce")
    merged = port_frame.merge(bench_frame, on="date", how="inner").dropna()
    if len(merged) < minimum_overlap:
        return None
    rp = merged["portfolio"].astype(float)
    rb = merged["benchmark"].astype(float)
    daily_rf = risk_free_rate / periods_per_year
    active = rp - rb
    variance_b = rb.var(ddof=0)
    beta = float((rp.cov(rb)) / variance_b) if variance_b else None
    alpha = (
        float((rp.mean() - daily_rf) - beta * (rb.mean() - daily_rf)) * periods_per_year
        if beta is not None
        else None
    )
    tracking_error = float(active.std(ddof=0) * math.sqrt(periods_per_year))
    active_return = float(active.mean() * periods_per_year)
    information_ratio = active_return / tracking_error if tracking_error else None
    return {
        "overlap_days": int(len(merged)),
        "active_return": active_return,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "beta": beta,
        "alpha": alpha,
    }


def calculate_rebalance_drift(
    actual_group_weights: dict[str, float], allocation_targets: dict[str, dict] | None
) -> dict:
    """Compare actual group weights against configured target/min/max bands."""
    if not allocation_targets:
        return {"available": False, "reason": "No allocation targets configured.", "groups": []}
    groups: list[dict] = []
    all_group_names = set(allocation_targets) | set(actual_group_weights)
    for group in sorted(all_group_names):
        target = allocation_targets.get(group, {})
        target_percent = target.get("target_percent")
        min_percent = target.get("min_percent")
        max_percent = target.get("max_percent")
        actual_percent = actual_group_weights.get(group, 0.0) * 100.0
        if target_percent is None:
            drift_pp = None
            rebalance_needed = False
        else:
            drift_pp = actual_percent - target_percent
            below_min = min_percent is not None and actual_percent < min_percent
            above_max = max_percent is not None and actual_percent > max_percent
            rebalance_needed = bool(below_min or above_max)
        groups.append(
            {
                "group": group,
                "target_percent": target_percent,
                "min_percent": min_percent,
                "max_percent": max_percent,
                "actual_percent": actual_percent,
                "drift_pp": drift_pp,
                "rebalance_needed": rebalance_needed,
            }
        )
    return {"available": True, "groups": groups}


def calculate_weighted_mer(holdings_with_expense_ratio: list[dict]) -> dict:
    """Calculate the market-value-weighted expense ratio across ETF holdings.

    ``holdings_with_expense_ratio`` entries need ``market_value`` and
    ``expense_ratio`` (``None`` when unknown for that holding).
    """
    total_value = sum(_safe_float(row.get("market_value")) for row in holdings_with_expense_ratio)
    covered = [row for row in holdings_with_expense_ratio if row.get("expense_ratio") is not None]
    covered_value = sum(_safe_float(row.get("market_value")) for row in covered)
    if total_value <= 0 or not covered:
        return {"available": False, "reason": "No ETF holdings with a known expense ratio.", "weighted_mer": None}
    weighted = sum(
        _safe_float(row.get("market_value")) * _safe_float(row.get("expense_ratio")) for row in covered
    ) / covered_value
    return {
        "available": True,
        "weighted_mer": weighted,
        "coverage_percent": covered_value / total_value,
    }


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

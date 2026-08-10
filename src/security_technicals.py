"""Per-security technical indicators: moving averages, drawdown, volatility,
relative strength, and beta/alpha vs a benchmark.

The per-security counterpart to `portfolio_metrics.py`'s portfolio-level risk
tiles, which take a whole-portfolio valuation series (`date`/`portfolio_value`
rows) feeding the dashboard's `/portfolio` page. This module takes one
ticker's OHLCV rows instead -- the same shape `historical_records` and
`investment-analyst-resources`'s `db_bundle["prices"]["rows"]` already use
(`record_date`, `close`). See
docs/plans/implementation/phase-2/design-decisions.md for why these stay two
modules: `portfolio_metrics.py` and `analytics.py` are not modified by this
one.

Convention parity with `portfolio_metrics.py`, so the two are comparable even
though nothing here imports it except `beta_alpha`:
- `ddof=0` for standard deviation (population, not sample)
- annualization by `math.sqrt(ANNUALIZATION_PERIODS)`, both read from
  `config.py`, the same source `portfolio_metrics.py` uses
- computed from `close`, not `adjusted_close` -- matches the existing
  benchmark convention documented in `docs/architecture/dashboard_api.md`
  ("raw CAD closes"), not a new convention invented here

Every function is pure math over an already-fetched OHLCV row list. Nothing
here fetches data. Every function returns `None` (or an all-`None` dict) on
insufficient input rather than raising -- short history, no overlap with the
benchmark series, and non-positive prices are normal, expected conditions for
many tickers, not data failures.

**Runnable entry point:** the `security-technicals` skill
(`.claude/skills/security-technicals/`) wraps these functions with a CLI, the
DuckDB read, run-workspace attachment, evidence/audit registration, and a
completeness trace. That is how an agent invokes this; importing this module
directly is for other Python (the Phase 3 worksheet builder, a future
dashboard route). The math stays here rather than inside the skill precisely
so those `src/` consumers never have to import from `.claude/` -- see
docs/plans/implementation/phase-2/design-decisions.md, Decision 4.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from config import ANNUALIZATION_PERIODS, DEFAULT_RISK_FREE_RATE
from portfolio_metrics import calculate_benchmark_stats

DEFAULT_MOVING_AVERAGE_WINDOWS: tuple[int, ...] = (50, 200)
DEFAULT_RELATIVE_STRENGTH_WINDOW_DAYS = 365
DEFAULT_MINIMUM_BETA_OVERLAP = 20


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


def _price_series(rows: list[dict] | None) -> pd.Series:
    """Ascending-date close-price series from OHLCV rows. Rows with a missing
    date/close or a non-positive close are dropped, not zero-filled."""
    if not rows:
        return pd.Series(dtype="float64")
    frame = pd.DataFrame(rows).copy()
    if "record_date" not in frame.columns or "close" not in frame.columns:
        return pd.Series(dtype="float64")
    frame["record_date"] = pd.to_datetime(frame["record_date"], errors="coerce")
    frame["close"] = frame["close"].map(_num)
    frame = frame.dropna(subset=["record_date", "close"])
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return pd.Series(dtype="float64")
    frame = frame.sort_values("record_date", kind="stable")
    return pd.Series(frame["close"].to_numpy(), index=pd.DatetimeIndex(frame["record_date"]), dtype="float64")


def _daily_returns(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype="float64")
    return series.pct_change().dropna()


def moving_averages(
    rows: list[dict] | None, windows: tuple[int, ...] = DEFAULT_MOVING_AVERAGE_WINDOWS
) -> dict[str, Any]:
    """Latest simple moving average for each window. A window with fewer
    trading days of history than its length returns `None` rather than an
    average over a shorter series silently presented as the requested
    window."""
    series = _price_series(rows)
    result: dict[str, Any] = {"latest_close": float(series.iloc[-1]) if not series.empty else None}
    for window in windows:
        key = f"sma_{window}d"
        result[key] = float(series.iloc[-window:].mean()) if len(series) >= window else None
    return result


def max_drawdown(rows: list[dict] | None) -> dict[str, Any]:
    """Worst peak-to-trough decline in the close-price series. Mirrors
    `portfolio_metrics.calculate_max_drawdown`'s cummax formula over one
    ticker's prices instead of a whole-portfolio valuation series."""
    series = _price_series(rows)
    if series.empty:
        return {"max_drawdown": None}
    return {"max_drawdown": float(((series / series.cummax()) - 1).min())}


def annualized_volatility(
    rows: list[dict] | None, periods_per_year: int = ANNUALIZATION_PERIODS
) -> dict[str, Any]:
    """Daily and annualized volatility from close-to-close daily returns.
    Mirrors `portfolio_metrics.calculate_volatility`'s formula (`ddof=0`,
    `x sqrt(periods_per_year)`) over one ticker's prices."""
    returns = _daily_returns(_price_series(rows))
    if returns.empty:
        return {"volatility": None, "daily_volatility": None}
    daily = float(returns.std(ddof=0))
    return {"volatility": daily * math.sqrt(periods_per_year), "daily_volatility": daily}


def relative_strength(
    rows: list[dict] | None,
    benchmark_rows: list[dict] | None,
    window_days: int = DEFAULT_RELATIVE_STRENGTH_WINDOW_DAYS,
) -> dict[str, Any]:
    """Ticker's trailing total return minus the benchmark's over the same
    calendar window ending at the ticker's latest date (positive = ticker
    outperforming). Either leg is `None` when its own series has no point at
    or before the window's start -- insufficient history is reported as
    such, not silently computed over whatever shorter window happened to be
    available."""
    ticker_series = _price_series(rows)
    benchmark_series = _price_series(benchmark_rows)
    if ticker_series.empty or benchmark_series.empty:
        return {"excess_return": None, "ticker_return": None, "benchmark_return": None}
    cutoff = ticker_series.index.max() - pd.Timedelta(days=window_days)

    def _window_return(series: pd.Series) -> float | None:
        past = series[series.index <= cutoff]
        if past.empty:
            return None
        start = float(past.iloc[-1])
        if not start:
            return None
        return float(series.iloc[-1]) / start - 1.0

    ticker_return = _window_return(ticker_series)
    benchmark_return = _window_return(benchmark_series)
    excess = (
        ticker_return - benchmark_return
        if ticker_return is not None and benchmark_return is not None
        else None
    )
    return {
        "excess_return": excess,
        "ticker_return": ticker_return,
        "benchmark_return": benchmark_return,
    }


def _returns_rows(series: pd.Series) -> list[dict[str, Any]]:
    returns = _daily_returns(series)
    return [{"date": index.date().isoformat(), "return": float(value)} for index, value in returns.items()]


def beta_alpha(
    rows: list[dict] | None,
    benchmark_rows: list[dict] | None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    periods_per_year: int = ANNUALIZATION_PERIODS,
    minimum_overlap: int = DEFAULT_MINIMUM_BETA_OVERLAP,
) -> dict[str, Any] | None:
    """Beta and alpha vs the benchmark, by importing (not reimplementing)
    `portfolio_metrics.calculate_benchmark_stats` -- see
    docs/plans/implementation/phase-2/design-decisions.md, Decision 1. That
    function already carries the conventions that matter (inner-join date
    alignment, the `minimum_overlap` guard, risk-free handling), so this is
    one import rather than a second beta implementation. Returns `None` on
    fewer than `minimum_overlap` overlapping trading days, exactly as the
    imported function does for the portfolio-level case."""
    ticker_returns = _returns_rows(_price_series(rows))
    benchmark_returns = _returns_rows(_price_series(benchmark_rows))
    return calculate_benchmark_stats(
        ticker_returns,
        benchmark_returns,
        risk_free_rate=risk_free_rate,
        periods_per_year=periods_per_year,
        minimum_overlap=minimum_overlap,
    )


def compute_security_technicals(
    rows: list[dict] | None,
    benchmark_rows: list[dict] | None,
    *,
    moving_average_windows: tuple[int, ...] = DEFAULT_MOVING_AVERAGE_WINDOWS,
    relative_strength_window_days: int = DEFAULT_RELATIVE_STRENGTH_WINDOW_DAYS,
) -> dict[str, Any]:
    """Aggregate every technical for one ticker against one benchmark series
    (`config.DEFAULT_BENCHMARK_SYMBOL`'s `historical_records` rows). Never
    raises -- each section is isolated so one failing metric does not blank
    the rest, matching `derived_metrics.compute_live_metrics`'s discipline."""
    result: dict[str, Any] = {}
    try:
        result["moving_averages"] = moving_averages(rows, moving_average_windows)
    except Exception:  # best-effort; never sink the caller
        result["moving_averages"] = None
    try:
        result["max_drawdown"] = max_drawdown(rows)
    except Exception:
        result["max_drawdown"] = None
    try:
        result["volatility"] = annualized_volatility(rows)
    except Exception:
        result["volatility"] = None
    try:
        result["relative_strength"] = relative_strength(rows, benchmark_rows, relative_strength_window_days)
    except Exception:
        result["relative_strength"] = None
    try:
        result["beta_alpha"] = beta_alpha(rows, benchmark_rows)
    except Exception:
        result["beta_alpha"] = None
    return result

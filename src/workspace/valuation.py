"""Deterministic valuation-method calculators for the investment worksheet.

Every method returns a `{low, mid, high}` per-share range, never a single
point (`investment_thesis_schema.md` §7: "a single point target without a
range is invalid"). A method is omitted entirely -- not filled with a guess
-- when its required inputs are missing.

**Placeholder methodology, stated up front:** no peer-multiple dataset or
DCF growth/discount-rate assumption source exists yet (both are Phase 4+
analyst-supplied or Phase 6 company-research inputs). Every method here
therefore reconstructs the metric the *current* market-implied multiple
already prices in (so `mid` always equals today's market value by
construction) and applies `DEFAULT_SENSITIVITY_BAND` around that multiple to
produce `low`/`high`. This is a sensitivity band on today's price, not an
independent valuation opinion -- it exists so the worksheet always carries a
structured range for the (not-yet-built) analyst to interpret or override,
never so it can be mistaken for a real DCF or peer-comparison output. See
`docs/plans/implementation/phase-3/design-decisions.md`.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SENSITIVITY_BAND = 0.15  # +/-15% around the current multiple


def _shares_outstanding(market_cap: float | None, price: float | None) -> float | None:
    if not market_cap or not price:
        return None
    return market_cap / price


def _range(low_equity: float, mid_equity: float, high_equity: float, shares: float) -> dict[str, float]:
    values = sorted((low_equity / shares, mid_equity / shares, high_equity / shares))
    return {"low": round(values[0], 2), "mid": round(values[1], 2), "high": round(values[2], 2)}


def _earnings_multiple(
    live_valuation: dict[str, Any], *, band: float = DEFAULT_SENSITIVITY_BAND
) -> dict[str, Any] | None:
    eps = live_valuation.get("trailingEps")
    multiple = live_valuation.get("trailingPE")
    if not eps or eps <= 0 or not multiple or multiple <= 0:
        return None
    return {
        "method": "earnings_multiple",
        "base_metric": "trailing EPS",
        "base_period": "trailing_twelve_months",
        "normalization_adjustments": ["current_multiple_sensitivity_band"],
        "assumptions": {
            "growth": None,
            "margin": None,
            "discount_rate": None,
            "terminal": None,
        },
        "resulting_equity_value_per_share": {
            "low": round(eps * multiple * (1 - band), 2),
            "mid": round(eps * multiple, 2),
            "high": round(eps * multiple * (1 + band), 2),
        },
        "sensitivity_note": (
            f"+/-{band:.0%} band on the current trailing P/E ({multiple:.1f}x); "
            "not a growth/discount-rate-based estimate."
        ),
    }


def _fcf_yield(
    derived: dict[str, Any], live_valuation: dict[str, Any], *, band: float = DEFAULT_SENSITIVITY_BAND
) -> dict[str, Any] | None:
    fcf_yield = derived.get("fcf_yield")
    free_cash_flow = live_valuation.get("freeCashflow")
    market_cap = live_valuation.get("marketCap")
    price = live_valuation.get("currentPrice")
    shares = _shares_outstanding(market_cap, price)
    if not fcf_yield or fcf_yield <= 0 or not free_cash_flow or not shares:
        return None
    low_equity = free_cash_flow / (fcf_yield * (1 + band))
    mid_equity = free_cash_flow / fcf_yield
    high_equity = free_cash_flow / (fcf_yield * (1 - band))
    return {
        "method": "fcf_yield",
        "base_metric": "free cash flow",
        "base_period": "trailing_twelve_months",
        "normalization_adjustments": ["current_multiple_sensitivity_band"],
        "assumptions": {"growth": None, "margin": None, "discount_rate": None, "terminal": None},
        "resulting_equity_value_per_share": _range(low_equity, mid_equity, high_equity, shares),
        "sensitivity_note": (
            f"+/-{band:.0%} band on the current FCF yield ({fcf_yield:.2%}); "
            "not a growth/discount-rate-based estimate."
        ),
    }


def _ev_based(
    method: str,
    base_metric: str,
    multiple: float | None,
    enterprise_value: float | None,
    market_cap: float | None,
    price: float | None,
    *,
    band: float = DEFAULT_SENSITIVITY_BAND,
) -> dict[str, Any] | None:
    shares = _shares_outstanding(market_cap, price)
    if not multiple or multiple <= 0 or not enterprise_value or not market_cap or not shares:
        return None
    implied_base = enterprise_value / multiple
    net_debt_component = enterprise_value - market_cap
    low_equity = implied_base * multiple * (1 - band) - net_debt_component
    mid_equity = enterprise_value - net_debt_component
    high_equity = implied_base * multiple * (1 + band) - net_debt_component
    return {
        "method": method,
        "base_metric": base_metric,
        "base_period": "trailing_twelve_months",
        "normalization_adjustments": ["current_multiple_sensitivity_band", "ev_to_equity_bridge"],
        "assumptions": {"growth": None, "margin": None, "discount_rate": None, "terminal": None},
        "resulting_equity_value_per_share": _range(low_equity, mid_equity, high_equity, shares),
        "sensitivity_note": (
            f"+/-{band:.0%} band on the current {method.replace('_', '/')} multiple ({multiple:.1f}x); "
            "not a growth/discount-rate-based estimate."
        ),
    }


def build_valuation_methods(
    live_valuation: dict[str, Any] | None,
    derived: dict[str, Any] | None,
    *,
    band: float = DEFAULT_SENSITIVITY_BAND,
    currency: str = "USD",
) -> list[dict[str, Any]]:
    """Every method Phase 3's inputs support, currency-tagged, `None`-safe.

    `live_valuation` is `bundle["live"]["valuation"]`; `derived` is
    `bundle["derived"]`. A method absent from the return list means its
    inputs were not available -- never a fabricated entry.
    """
    live_valuation = live_valuation or {}
    derived = derived or {}
    market_cap = live_valuation.get("marketCap")
    price = live_valuation.get("currentPrice")
    enterprise_value = live_valuation.get("enterpriseValue")

    methods = [
        _earnings_multiple(live_valuation, band=band),
        _fcf_yield(derived, live_valuation, band=band),
        _ev_based(
            "ev_revenue", "revenue",
            live_valuation.get("enterpriseToRevenue") or derived.get("ev_to_revenue"),
            enterprise_value, market_cap, price, band=band,
        ),
        _ev_based(
            "ev_ebitda", "EBITDA",
            live_valuation.get("enterpriseToEbitda") or derived.get("ev_to_ebitda"),
            enterprise_value, market_cap, price, band=band,
        ),
    ]
    computed = [method for method in methods if method is not None]
    for method in computed:
        method["currency"] = currency
    return computed

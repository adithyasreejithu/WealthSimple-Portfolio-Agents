"""Deterministic bull/base/bear scenario templates for the investment worksheet.

Derives a `Scenario` triple from `valuation.py`'s computed method ranges --
bear from the lowest `low` across methods, bull from the highest `high`,
base from the method whose `mid` sits at the median -- with a fixed
25/50/25 probability split (`investment_thesis_schema.md` §8 requires the
three to sum to exactly 1.0; this split leaves no floating-point remainder).

Like `valuation.py`, this is an explicit **placeholder template**, not an
analyst-authored scenario: no independent revenue/margin projection exists
yet, so `revenue_assumption`/`margin_assumption` say so rather than
implying one. The (not-yet-built) Phase 4 analyst is expected to overwrite
these with real assumptions; this module exists so the worksheet always
carries a schema-valid scaffold to overwrite, never a gap where scenarios
should be.
"""

from __future__ import annotations

from typing import Any

DEFAULT_PROBABILITIES = {"bull": 0.25, "base": 0.50, "bear": 0.25}

_PLACEHOLDER_ASSUMPTION = (
    "current market-implied multiple, no independent projection (Phase 3 placeholder)"
)


def _scenario(
    method_entry: dict[str, Any], key: str, probability: float, horizon: str, current_price: float
) -> dict[str, Any]:
    fair_value = method_entry["resulting_equity_value_per_share"][key]
    return {
        "probability": probability,
        "horizon": horizon,
        "revenue_assumption": _PLACEHOLDER_ASSUMPTION,
        "margin_assumption": "n/a",
        "dilution_assumption": "n/a",
        "valuation_method": method_entry["method"],
        "fair_value_per_share": round(fair_value, 2),
        "expected_return_pct": round((fair_value - current_price) / current_price, 4),
        "conditions": [],
    }


def build_scenario_templates(
    valuation_methods: list[dict[str, Any]] | None,
    current_price: float | None,
    horizon: str,
) -> dict[str, Any] | None:
    """`{bull, base, bear}` scenario dicts, or `None` when there is nothing to
    scaffold from (no valuation methods computed, or no current price to
    measure expected return against)."""
    methods = [m for m in (valuation_methods or []) if m.get("resulting_equity_value_per_share")]
    if not methods or not current_price:
        return None

    by_low = min(methods, key=lambda m: m["resulting_equity_value_per_share"]["low"])
    by_high = max(methods, key=lambda m: m["resulting_equity_value_per_share"]["high"])
    by_mid = sorted(methods, key=lambda m: m["resulting_equity_value_per_share"]["mid"])
    base_method = by_mid[(len(by_mid) - 1) // 2]

    return {
        "bull": _scenario(by_high, "high", DEFAULT_PROBABILITIES["bull"], horizon, current_price),
        "base": _scenario(base_method, "mid", DEFAULT_PROBABILITIES["base"], horizon, current_price),
        "bear": _scenario(by_low, "low", DEFAULT_PROBABILITIES["bear"], horizon, current_price),
    }

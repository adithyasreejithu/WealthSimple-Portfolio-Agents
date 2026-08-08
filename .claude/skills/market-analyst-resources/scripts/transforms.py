"""Mechanical transforms declared per-indicator in the registry.

Per docs/plans/market-analyst-resources-skill.md: the skill computes only
what the owner declared in `market-indicators.yml` -- never a threshold, a
label, or a choice the skill makes itself. `series_id` always stores the
raw, as-published value; every transform here runs at *read* time over that
raw history, so changing a transform in the registry never requires a
backfill or rewrite.

Transform offsets are counted in **observations**, not calendar days: `history`
is filtered to non-null values first (a suppressed observation has nothing to
offset against), then `yoy_pct`/`mom_pct`/`annualized_3m` step back a number
of observations implied by the indicator's declared cadence (12 for monthly,
4 for quarterly, ...), and `pct_change(w)`/`zscore(w)`/`percentile_rank(w)`
step back exactly `w` observations. This assumes a series' own history is
regularly spaced at its declared cadence, which is the registry's job to get
right, not this module's.
"""

from __future__ import annotations

import re
import statistics
from datetime import date
from typing import Any, NamedTuple

UNARY_TRANSFORM_NAMES = frozenset(
    {"none", "yoy_pct", "mom_pct", "annualized_3m", "pct_change", "zscore", "percentile_rank"}
)
DERIVED_OPS = frozenset({"spread", "ratio"})

# Observations per year implied by a declared cadence -- used only by the
# calendar-shaped transforms (yoy_pct, mom_pct, annualized_3m). Windowed
# transforms (pct_change(w) etc.) take their period count directly from `w`
# and never consult this table.
CADENCE_PERIODS_PER_YEAR = {
    "daily": 252, "weekly": 52, "monthly": 12, "quarterly": 4, "annual": 1,
}

_WINDOWED_PATTERN = re.compile(r"^(pct_change|zscore|percentile_rank)\((\d+)\)$")


class TransformResult(NamedTuple):
    value: float | None
    basis_date: date | None
    status: str  # "ok" | "insufficient_history"


def parse_transform(spec: str) -> tuple[str, int | None]:
    """`"yoy_pct"` -> `("yoy_pct", None)`; `"zscore(252)"` -> `("zscore", 252)`."""
    spec = spec.strip()
    match = _WINDOWED_PATTERN.match(spec)
    if match:
        return match.group(1), int(match.group(2))
    return spec, None


def _non_null(history: list[tuple[date, float | None]]) -> list[tuple[date, float]]:
    """Oldest-first, nulls dropped -- see module docstring on why null
    (suppressed) observations cannot participate in index-offset math."""
    return [(obs_date, value) for obs_date, value in history if value is not None]


def _step_back(clean: list[tuple[date, float]], periods: int) -> tuple[date, float] | None:
    if len(clean) <= periods:
        return None
    return clean[-1 - periods]


def apply_unary_transform(
    name: str, window: int | None, history: list[tuple[date, float | None]], *, cadence: str
) -> TransformResult:
    """`history` is oldest-first `(obs_date, value)` pairs for one series."""
    clean = _non_null(history)
    if not clean:
        return TransformResult(None, None, "insufficient_history")
    latest_date, latest_value = clean[-1]

    if name == "none":
        return TransformResult(latest_value, latest_date, "ok")

    if name in ("yoy_pct", "mom_pct", "annualized_3m"):
        periods_per_year = CADENCE_PERIODS_PER_YEAR[cadence]
        if name == "yoy_pct":
            periods = periods_per_year
        elif name == "mom_pct":
            periods = 1
        else:
            periods = max(1, round(periods_per_year * 0.25))  # ~3 months
        prior = _step_back(clean, periods)
        if prior is None or prior[1] == 0:
            return TransformResult(None, latest_date, "insufficient_history")
        pct = (latest_value - prior[1]) / abs(prior[1])
        if name == "annualized_3m":
            years = periods / periods_per_year
            pct = (1 + pct) ** (1 / years) - 1
        return TransformResult(pct * 100.0, latest_date, "ok")

    if name in ("pct_change", "zscore", "percentile_rank"):
        if window is None:
            raise ValueError(f"transform {name!r} requires a window, e.g. '{name}(63)'")
        if name == "pct_change":
            prior = _step_back(clean, window)
            if prior is None or prior[1] == 0:
                return TransformResult(None, latest_date, "insufficient_history")
            pct = (latest_value - prior[1]) / abs(prior[1])
            return TransformResult(pct * 100.0, latest_date, "ok")

        trailing = [value for _, value in clean[-window:]]
        if len(trailing) < max(2, window // 2):
            return TransformResult(None, latest_date, "insufficient_history")
        if name == "zscore":
            mean = statistics.fmean(trailing)
            stdev = statistics.pstdev(trailing)
            if stdev == 0:
                return TransformResult(0.0, latest_date, "ok")
            return TransformResult((latest_value - mean) / stdev, latest_date, "ok")

        rank = sum(1 for value in trailing if value <= latest_value)
        return TransformResult(100.0 * rank / len(trailing), latest_date, "ok")

    raise ValueError(f"unknown transform: {name!r}")


class DerivedResult(NamedTuple):
    value: float | None
    basis_date_a: date | None
    basis_date_b: date | None
    status: str  # "ok" | "input_missing" | "stale_leg"


def _locf_lookup(
    series: list[tuple[date, float]], anchor: date, max_carry_days: int
) -> tuple[float, date] | None:
    """Last value at or before `anchor`, carried forward only within
    `max_carry_days` -- beyond that the leg is too stale to combine."""
    candidates = [(obs_date, value) for obs_date, value in series if obs_date <= anchor]
    if not candidates:
        return None
    obs_date, value = max(candidates, key=lambda item: item[0])
    if (anchor - obs_date).days > max_carry_days:
        return None
    return value, obs_date


def compute_derived(
    op: str,
    series_a: list[tuple[date, float | None]],
    series_b: list[tuple[date, float | None]],
    *,
    max_carry_days: int,
) -> DerivedResult:
    """`series_a` is the anchor (denser/primary) leg by registry convention
    -- `inputs: [A, B]` means A's latest date sets the comparison date and B
    is carried forward (LOCF) onto it, bounded by `max_carry_days`."""
    clean_a = _non_null(series_a)
    clean_b = _non_null(series_b)
    if not clean_a:
        return DerivedResult(None, None, None, "input_missing")
    anchor_date, value_a = clean_a[-1]
    looked_up = _locf_lookup(clean_b, anchor_date, max_carry_days)
    if looked_up is None:
        return DerivedResult(None, anchor_date, None, "input_missing")
    value_b, basis_date_b = looked_up
    status = "ok" if basis_date_b == anchor_date else "stale_leg"

    if op == "spread":
        return DerivedResult(value_a - value_b, anchor_date, basis_date_b, status)
    if op == "ratio":
        if value_b == 0:
            return DerivedResult(None, anchor_date, basis_date_b, "input_missing")
        return DerivedResult(value_a / value_b, anchor_date, basis_date_b, status)
    raise ValueError(f"unknown derived op: {op!r}")

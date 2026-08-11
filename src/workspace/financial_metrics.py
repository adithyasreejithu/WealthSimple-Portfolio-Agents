"""Quarterly financial-trajectory trend table for the investment worksheet.

Turns `bundle["db"]["financials"]` -- the quarterly `financial_snapshots`
rows the `investment-analyst-resources` bundle already carries -- into a
compact trend table with quarter-over-quarter deltas this module computes
itself. The ratios Phase 2 already computes (`roic`, `net_debt_to_ebitda`,
`debt_to_equity`, `current_ratio`, `revenue_growth_yoy`,
`net_income_latest_quarter`) are surfaced **by reference** from
`bundle["derived"]`, not recomputed here -- the same "import the existing
math, don't reimplement" discipline `src/security_technicals.py` uses for
beta/alpha (see `docs/plans/implementation/phase-2/design-decisions.md`,
Decision 1).

No annual-cadence fallback and no segment/geography breakdown -- those need
filing extraction (`company-research-resources`, Phase 6) and are recorded
as explicit `data_limitations` rather than silently absent.
"""

from __future__ import annotations

from typing import Any

_TREND_FIELDS = ("revenue", "gross_margin", "operating_margin", "free_cash_flow")
_QOQ_FIELDS = ("revenue", "free_cash_flow")
_DERIVED_REFERENCE_FIELDS = (
    "roic",
    "net_debt_to_ebitda",
    "debt_to_equity",
    "current_ratio",
    "revenue_growth_yoy",
    "net_income_latest_quarter",
)

DATA_LIMITATIONS = (
    "quarterly financial_snapshots only -- no annual-history fallback yet (Phase 6+)",
    "no segment or geography breakdown -- requires filing extraction (Phase 6)",
)


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous), 4)


def build_financial_trajectory(
    financials: list[dict[str, Any]] | None,
    derived: dict[str, Any] | None,
) -> dict[str, Any]:
    """Quarterly trend rows plus the Phase-2 ratios, by reference.

    `financials` is `bundle["db"]["financials"]` (oldest first, per
    `db_resources.read_financial_snapshots`). `derived` is
    `bundle["derived"]`. Never raises; a period missing a field is skipped
    from that field's delta rather than treated as zero.
    """
    financials = financials or []
    derived = derived or {}

    periods: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for row in financials:
        entry: dict[str, Any] = {"period_end_date": row.get("period_end_date")}
        for field_name in _TREND_FIELDS:
            entry[field_name] = row.get(field_name)
        for field_name in _QOQ_FIELDS:
            entry[f"{field_name}_qoq_pct"] = (
                _pct_change(row.get(field_name), previous.get(field_name))
                if previous is not None
                else None
            )
        periods.append(entry)
        previous = row

    latest = financials[-1] if financials else None

    return {
        "periods": periods,
        "period_count": len(periods),
        "cadence": "quarterly",
        "latest_period_end": latest.get("period_end_date") if latest else None,
        "ratios_ref": {name: derived.get(name) for name in _DERIVED_REFERENCE_FIELDS},
        "data_limitations": list(DATA_LIMITATIONS),
    }

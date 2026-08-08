"""Per-indicator freshness gate + the sole write path (fetch + persist).

Age-in-days cadences (the sibling `investment-analyst-resources` skill's
approach) are wrong for macro: a short threshold makes a monthly series
permanently "stale" between releases, a long one never fires on release day.
Instead:

    next_expected = last_obs_date + cadence_period + publication_lag_days
    due           = today >= next_expected

`publication_lag_days` is one owner-authored integer per indicator in the
registry -- about 90% of a release calendar for zero infrastructure. Priced
daily indicators (VIX, sector ETFs, FX, commodities) use a business-day
boundary instead, since "next day" isn't meaningful across a weekend.

Backfill is a gate verdict (`due_reason: "backfill"`), not a first-run flag:
the gate compares each indicator's stored coverage start against a target
anchored on the registry's own `updated` date (fixed), not on "today" (which
would make the backfill requirement creep backward forever as time passes
and incorrectly re-trigger a satisfied backfill years later).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))

import macro_store  # noqa: E402
import sources  # noqa: E402
from registry import Indicator, Registry  # noqa: E402
from transforms import CADENCE_PERIODS_PER_YEAR  # noqa: E402

# Approximate calendar days per cadence period -- used only for scheduling
# math (next_expected, backfill targets, revision-lookback windows), never
# for the transform math in transforms.py, which counts observations.
CADENCE_APPROX_DAYS = {
    "daily": 1, "weekly": 7, "monthly": 30, "quarterly": 91, "annual": 365,
}

GRACE_DAYS = 5  # statistical series: how late past next_expected before "overdue"
PRICED_GRACE_BUSINESS_DAYS = 2  # widened from 1 -- proxies span TSX/NYSE/CME/FX holidays
PRICED_OVERDUE_CALENDAR_DAYS = 5
REVISION_LOOKBACK_PERIODS = 2  # re-fetch this many trailing periods to catch late revisions
CADENCE_MISMATCH_RATIO = 2.0


def _years_before(anchor: date, years: int) -> date:
    try:
        return anchor.replace(year=anchor.year - years)
    except ValueError:  # Feb 29 with no leap year at the target
        return anchor.replace(year=anchor.year - years, day=28)


def _last_business_day(today: date) -> date:
    # Weekday-only approximation, same rule the sibling skill's
    # freshness_gate.py documents -- the real failure mode guarded against is
    # a multi-day-old cache, not a single missed exchange holiday.
    offset = {0: 3, 6: 2}.get(today.weekday(), 1)
    return today - timedelta(days=offset)


def _gate_one(
    indicator: Indicator, connection: duckdb.DuckDBPyConnection | None, registry: Registry, *, today: date
) -> dict[str, Any]:
    if not indicator.is_configured:
        return {"id": indicator.id, "configured": False, "due": False, "due_reason": "not_configured"}

    if connection is None:
        return {"id": indicator.id, "configured": True, "due": True, "due_reason": "backfill", "reason": "no_database_yet"}

    cadence_days = CADENCE_APPROX_DAYS[indicator.cadence]
    coverage_start = macro_store.get_coverage_start(connection, indicator.id)
    required_start = _years_before(registry.updated, indicator.backfill_years)
    tolerance = timedelta(days=cadence_days * 2)
    if coverage_start is None or coverage_start > required_start + tolerance:
        return {
            "id": indicator.id, "configured": True, "due": True, "due_reason": "backfill",
            "coverage_start": coverage_start, "required_start": required_start,
        }

    latest = macro_store.get_latest(connection, indicator.id)
    last_obs_date = latest["obs_date"] if latest else None

    if indicator.kind == "priced" and indicator.cadence == "daily":
        boundary = _last_business_day(today) - timedelta(days=PRICED_GRACE_BUSINESS_DAYS)
        due = last_obs_date is None or last_obs_date < boundary
        overdue = last_obs_date is not None and (today - last_obs_date).days > PRICED_OVERDUE_CALENDAR_DAYS
        next_expected = (last_obs_date + timedelta(days=1)) if last_obs_date else today
    else:
        next_expected = (
            (last_obs_date + timedelta(days=cadence_days)) if last_obs_date else registry.updated
        ) + timedelta(days=indicator.publication_lag_days)
        due = last_obs_date is None or today >= next_expected
        overdue = last_obs_date is not None and today > next_expected + timedelta(days=GRACE_DAYS)

    forced_events = registry.events_forcing(indicator.domain, today=today)
    if forced_events:
        due = True

    observed_cadence_days = macro_store.get_observed_cadence_days(connection, indicator.id)
    cadence_mismatch = (
        observed_cadence_days is not None and observed_cadence_days > cadence_days * CADENCE_MISMATCH_RATIO
    )

    return {
        "id": indicator.id, "configured": True, "due": due,
        "due_reason": "scheduled" if due else None,
        "last_obs_date": last_obs_date, "next_expected": next_expected,
        "overdue": overdue, "cadence_mismatch": cadence_mismatch,
        "observed_cadence_days": observed_cadence_days,
        "forced_by_events": [event.label for event in forced_events],
    }


def compute_gate(
    registry: Registry, connection: duckdb.DuckDBPyConnection | None, *, today: date | None = None
) -> list[dict[str, Any]]:
    """Read-only. `connection` may be `None` (market.duckdb doesn't exist
    yet) -- every configured indicator is then reported due for backfill."""
    run_date = today or date.today()
    return [_gate_one(indicator, connection, registry, today=run_date) for indicator in registry.indicators]


def _fetch_window(gate_result: dict[str, Any], indicator: Indicator, registry: Registry, *, today: date) -> tuple[date, date]:
    if gate_result["due_reason"] == "backfill":
        start = gate_result.get("required_start") or _years_before(today, indicator.backfill_years)
        return start, today
    last_obs_date = gate_result.get("last_obs_date")
    cadence_days = CADENCE_APPROX_DAYS[indicator.cadence]
    lookback_days = cadence_days * REVISION_LOOKBACK_PERIODS if indicator.kind == "statistical" else 5
    start = (last_obs_date - timedelta(days=lookback_days)) if last_obs_date else _years_before(today, indicator.backfill_years)
    return start, today


def refresh_due_indicators(
    registry: Registry,
    connection: duckdb.DuckDBPyConnection,
    gate_results: list[dict[str, Any]],
    *,
    today: date | None = None,
    only_domains: set[str] | None = None,
    max_indicators: int | None = None,
) -> list[dict[str, Any]]:
    """The sole write path. Commits **per indicator** (mirroring
    `ensure_benchmark_history`'s per-symbol transactions in the pipeline's
    `market_data.py`), so a failure on one indicator does not roll back
    everything already fetched in this batch. Errors are reported in the
    returned list, never swallowed.

    `only_domains`/`max_indicators` slice a large initial backfill across
    several invocations instead of holding the write lock for the whole
    registry in one call -- see docs/plans/market-analyst-resources-skill.md
    §4.
    """
    run_date = today or date.today()
    now = datetime.combine(run_date, datetime.min.time())
    results: list[dict[str, Any]] = []
    due_by_id = {result["id"]: result for result in gate_results if result.get("due")}
    processed = 0

    for indicator in registry.indicators:
        gate_result = due_by_id.get(indicator.id)
        if gate_result is None or not indicator.is_configured:
            continue
        if only_domains is not None and indicator.domain not in only_domains:
            continue
        if max_indicators is not None and processed >= max_indicators:
            break
        processed += 1
        source_spec = registry.sources[indicator.source]
        start, end = _fetch_window(gate_result, indicator, registry, today=run_date)

        connection.execute("BEGIN TRANSACTION")
        try:
            raw_points = sources.fetch(source_spec["adapter"], indicator.series_ref, start, end)
            written = {"new": 0, "confirmed": 0, "revised": 0}
            for obs_date, value in raw_points:
                scaled = value * indicator.scale if indicator.kind == "priced" else value
                outcome = macro_store.upsert_observation(
                    connection,
                    series_id=indicator.id, obs_date=obs_date, value=scaled, status="ok",
                    source_id=indicator.source, units=indicator.units, now=now,
                )
                written[outcome["action"]] += 1
            connection.execute("COMMIT")
            results.append({
                "id": indicator.id, "status": "ok", "fetched": len(raw_points),
                "new": written["new"], "revised": written["revised"], "window": [start.isoformat(), end.isoformat()],
            })
        except sources.SourceConfigError as exc:
            connection.execute("ROLLBACK")
            results.append({"id": indicator.id, "status": "config_error", "error": str(exc)})
        except sources.SourceTransientError as exc:
            connection.execute("ROLLBACK")
            results.append({"id": indicator.id, "status": "fetch_failed", "error": str(exc)})
        except Exception as exc:  # keep the batch alive; report, never swallow silently
            connection.execute("ROLLBACK")
            results.append({"id": indicator.id, "status": "error", "error": str(exc)})

    return results

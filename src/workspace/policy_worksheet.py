"""The portfolio-policy worksheet builder -- Phase 11's "Python calculates"
layer, mirroring `investment_worksheet.py`'s role for Phase 4.

Given a run and a subject ticker, computes the current portfolio's exposure
and evaluates the two checks `Knowledge-Base/ref/policy_v1_1.yaml` actually
defines for a single security:

- **single_name_cap** -- the security's current weight against
  `SINGLE_NAME_MAX_WEIGHT` (`src/config.py`, backing `policy_v1_1.yaml`'s
  `constraints.single_name_max_percent: 10`), reusing the exact
  exempt-broad-market-ETF carve-out `analytics.portfolio_report`'s
  concentration check already applies.
- **group_allocation_target** -- the security's classifier group's current
  weight against that group's `allocation_targets.max_percent`
  (`analytics.load_allocation_targets` + `calculate_rebalance_drift`).

Sector, look-through-sector, and currency exposure
(`analytics.get_sector_allocation` / `get_look_through_sector_exposure` /
`get_currency_exposure`) are carried through as informational
`portfolio_context` only -- `policy_v1_1.yaml` defines no cap for either, so
this module never invents one (the package's "never invent" rule applies to
policy the same way it applies to evidence).

Also carries `price_and_market_context` (Phase 11 extension): the trailing
365-day close range (`latest_close`/`week52_low`/`week52_high`) from stored
history, via `analytics.get_price_history` -- the one piece of price data
this worksheet exposes, so the Portfolio Manager can ground an
`order_guidance` price citation (`models.OrderGuidance`) in something real
without reading the investment-analyst's worksheet or resource bundle
directly. This is deliberately narrower than `investment_worksheet.py`'s own
`price_and_market_context` block (no moving averages, drawdown, or
volatility) -- those live in the `security-technicals` artifact, which this
module does not read.

Reads the live pipeline database fresh on every call -- portfolio state is
not run-scoped, there is one live portfolio, not one per run -- and never
touches `Knowledge-Base/` or fetches anything live itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import analytics
from config import DATABASE_PATH, SINGLE_NAME_MAX_WEIGHT
from portfolio_metrics import calculate_concentration, calculate_position_weights, calculate_rebalance_drift

from . import audit as audit_module
from . import evidence as evidence_module
from .paths import relative_to_run, utc_now_iso

POLICY_WORKSHEET_SCHEMA = "portfolio-policy-worksheet.v1"


def _json_safe_weights(mapping: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`analytics.py`'s `_weights_from_totals`-shaped dicts carry `market_value`
    as a `Decimal` (portfolio money math is exact); this worksheet is written
    as JSON, so cast it to `float` here rather than upstream, where the
    precision is still needed."""
    return {
        label: {**info, "market_value": float(info["market_value"])}
        for label, info in mapping.items()
    }


def _single_name_check(
    *, ticker: str, current_weight_pct: float, is_exempt: bool, exemption_note: str | None,
    concentration_available: bool, concentration_reason: str | None,
) -> dict[str, Any]:
    if not concentration_available:
        return {
            "name": "single_name_cap", "result": "unavailable",
            "detail": concentration_reason or "concentration unavailable",
        }
    if is_exempt:
        return {"name": "single_name_cap", "result": "pass", "detail": exemption_note}
    limit_pct = float(SINGLE_NAME_MAX_WEIGHT) * 100.0
    breached = current_weight_pct > limit_pct
    return {
        "name": "single_name_cap",
        "result": "fail" if breached else "pass",
        "detail": (
            f"{ticker} at {current_weight_pct:.2f}% of portfolio vs. the "
            f"{limit_pct:.1f}% single_name_max_percent limit."
        ),
    }


def _group_allocation_check(
    *, ticker: str, group: str | None, drift: dict[str, Any],
) -> dict[str, Any]:
    if group is None:
        return {
            "name": "group_allocation_target", "result": "unavailable",
            "detail": f"{ticker} has no portfolio_classifications row -- run classify-portfolio first.",
        }
    if not drift.get("available"):
        return {"name": "group_allocation_target", "result": "unavailable", "detail": drift.get("reason")}
    row = next((g for g in drift["groups"] if g["group"] == group), None)
    if row is None or row.get("max_percent") is None:
        return {
            "name": "group_allocation_target", "result": "unavailable",
            "detail": f"group {group!r} has no configured max_percent in policy_v1_1.yaml.",
        }
    above_max = row["actual_percent"] > row["max_percent"]
    return {
        "name": "group_allocation_target",
        "result": "fail" if above_max else "pass",
        "detail": (
            f"{group} group at {row['actual_percent']:.2f}% vs. its "
            f"{row['max_percent']}% max_percent target."
        ),
    }


def _price_and_market_context(ticker: str, db_path: str) -> dict[str, Any]:
    """Trailing-365-day close range for this security, from stored history
    only -- reuses `analytics.get_price_history` (the same DB-only series the
    dashboard charts), rather than a second copy of the query. This is the
    Portfolio Manager's only price data: a real number to ground an
    `order_guidance` citation in, never an opinion on value. Absent history
    (no rows, or an unresolved symbol) returns all-`None` rather than raising
    -- this block is optional context, not a required input."""
    empty = {"latest_close": None, "week52_low": None, "week52_high": None}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date()
    history = analytics.get_price_history(ticker, db_path, date_from=cutoff)
    if not history or not history["points"]:
        return empty
    closes = [point["close"] for point in history["points"]]
    return {
        "latest_close": closes[-1],
        "week52_low": min(closes),
        "week52_high": max(closes),
    }


def build_policy_worksheet(
    *, run_id: str, ticker: str, db_path: str = DATABASE_PATH, as_of: str | None = None,
) -> dict[str, Any]:
    """Pure computation over a fresh DB read -- no filesystem writes here;
    see `build_policy_worksheet_for_run` for the I/O wrapper."""
    ticker = ticker.upper()

    holding_objs = analytics.get_holdings(db_path)
    holdings = [asdict(h) for h in holding_objs]
    weights_by_ticker = calculate_position_weights(holdings)
    for row in holdings:
        row["weight"] = weights_by_ticker.get(row["ticker_symbol"], {}).get("weight", 0.0)
    concentration = calculate_concentration(weights_by_ticker)

    group_allocation = analytics.get_group_allocation(db_path, holding_objs)
    sector_allocation = analytics.get_sector_allocation(db_path, holding_objs)
    look_through = analytics.get_look_through_sector_exposure(db_path, holding_objs)
    currency_exposure = analytics.get_currency_exposure(holding_objs)
    classifications_by_id = group_allocation.get("classifications", {})

    held = next((h for h in holding_objs if h.ticker_symbol.upper() == ticker), None)
    if held is not None:
        ticker_id = held.ticker_id
        current_weight_pct = weights_by_ticker.get(held.ticker_symbol, {}).get("weight", 0.0) * 100.0
        security_type = held.security_type
        classification = classifications_by_id.get(ticker_id) or analytics.get_classification(ticker_id, db_path)
    else:
        ticker_id = analytics.get_ticker_id(ticker, db_path)
        current_weight_pct = 0.0
        security_type = None
        classification = analytics.get_classification(ticker_id, db_path) if ticker_id is not None else None
    group = classification["primary_group"] if classification else None

    # Same carve-out `analytics.portfolio_report` applies portfolio-wide: the
    # single-name cap targets idiosyncratic single-company risk, so a broad-
    # market ETF classified Core is not a "concentrated bet" the way a single
    # stock at the same weight would be.
    is_exempt = security_type == "etf" and group == "Core"
    exemption_note = (
        f"{ticker} is a Core-classified broad-market ETF, exempt from the single-name cap."
        if is_exempt else None
    )

    allocation_targets = analytics.load_allocation_targets()
    actual_group_weights = {g: info["weight"] for g, info in group_allocation["by_group"].items()}
    drift = calculate_rebalance_drift(actual_group_weights, allocation_targets)

    policy_checks = [
        _single_name_check(
            ticker=ticker, current_weight_pct=current_weight_pct, is_exempt=is_exempt,
            exemption_note=exemption_note, concentration_available=concentration["available"],
            concentration_reason=concentration.get("reason"),
        ),
        _group_allocation_check(ticker=ticker, group=group, drift=drift),
    ]

    return {
        "schema": POLICY_WORKSHEET_SCHEMA,
        "run_id": run_id,
        "as_of": as_of,
        "subject": {
            "ticker": ticker, "ticker_id": ticker_id, "primary_group": group,
            "currently_held": held is not None,
        },
        "current_weight_pct": current_weight_pct,
        "policy_checks": policy_checks,
        "price_and_market_context": _price_and_market_context(ticker, db_path),
        "portfolio_context": {
            "by_group": _json_safe_weights(group_allocation["by_group"]),
            "by_sector": _json_safe_weights(sector_allocation["by_sector"]),
            "look_through_sector": look_through,
            "by_currency": _json_safe_weights(currency_exposure),
        },
        "policy_source": {
            "single_name_max_percent": float(SINGLE_NAME_MAX_WEIGHT) * 100.0,
            "allocation_targets": allocation_targets,
        },
    }


def build_policy_worksheet_for_run(
    run_dir: Path, *, run_id: str, ticker: str, db_path: str = DATABASE_PATH,
    as_of: str | None = None, now: datetime | None = None,
) -> tuple[dict[str, Any], Path, str]:
    """Build the worksheet, write it to `calculations/`, register it as
    `policy_worksheet` evidence, and append a `policy_worksheet_built`
    audit event. Returns `(worksheet, worksheet_path, sha256_hash)` -- the
    hash a `DecisionProposal.policy_worksheet_ref.hash` will cite."""
    ticker_upper = ticker.upper()
    worksheet = build_policy_worksheet(run_id=run_id, ticker=ticker_upper, db_path=db_path, as_of=as_of)

    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y-%m-%dT%H%M%SZ")
    calculations_dir = run_dir / "calculations"
    calculations_dir.mkdir(parents=True, exist_ok=True)
    worksheet_path = calculations_dir / f"{ticker_upper}-{stamp}-policy-worksheet.json"

    worksheet_json = json.dumps(worksheet, indent=2, sort_keys=False, ensure_ascii=False)
    # `write_bytes`, not `write_text`: keeps the sha256 computed from the
    # in-memory string equal to the bytes on disk on every platform -- same
    # reasoning as `investment_worksheet.build_worksheet_for_run`.
    worksheet_path.write_bytes(worksheet_json.encode("utf-8"))
    worksheet_hash = "sha256:" + hashlib.sha256(worksheet_json.encode("utf-8")).hexdigest()

    evidence_module.register(
        run_dir, run_id=run_id, evidence_type="policy_worksheet", source_name="policy_worksheet",
        status="available", artifact=worksheet_path, retrieved_at=utc_now_iso(),
        collection_method="deterministic_transform",
    )
    audit_module.append_event(
        run_dir, run_id=run_id, event="policy_worksheet_built", actor="policy_worksheet",
        artifact=relative_to_run(run_dir, worksheet_path),
        details={"ticker": ticker_upper, "policy_checks": worksheet["policy_checks"]},
    )

    return worksheet, worksheet_path, worksheet_hash

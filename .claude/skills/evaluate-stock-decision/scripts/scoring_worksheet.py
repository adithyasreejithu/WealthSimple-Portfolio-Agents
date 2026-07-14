"""Build the deterministic scoring worksheet the analyst fills in.

Given the decision rubric and one or more research source files (today:
`fetch-stock-research-data` output and the classify-portfolio JSON), this script
does everything that is *not* judgment:

- resolves each gate/dimension's cited evidence fields to concrete values,
- computes the derived metrics the rubric references (FCF yield, YoY revenue
  growth, debt/equity, current ratio, price returns, put/call OI ratio, ...),
- works out the position context (held / weight / role / dividend payer),
- and emits a worksheet JSON with empty `result`/`score` slots for the LLM.

Criteria whose source was not supplied on the command line are marked
`unknown: true` rather than raising, so future sources degrade gracefully. The
LLM (the stock-analyst agent) reads this worksheet, fills every gate result and
dimension score with citations, writes the narratives, and hands the completed
recommendation to `validate_recommendation.py`. This module never scores.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import rubric as rubric_mod


class WorksheetError(ValueError):
    pass


# --- source loading ---------------------------------------------------------

def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorksheetError(f"source file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorksheetError(f"invalid JSON in {path}: {exc}") from exc


def _find_research_item(payload: Any, ticker: str) -> dict | None:
    """The yfinance pull is a list of per-ticker items; find this ticker's."""
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return None
    for item in payload:
        if isinstance(item, dict) and str(item.get("ticker", "")).upper() == ticker:
            return item
    return None


def _find_holding(payload: Any, ticker: str) -> dict | None:
    holdings = payload.get("holdings") if isinstance(payload, dict) else None
    if not isinstance(holdings, list):
        return None
    for holding in holdings:
        if isinstance(holding, dict) and str(holding.get("ticker", "")).upper() == ticker:
            return holding
    return None


def _resolve_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# --- derived-metric helpers -------------------------------------------------

def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _statement_periods(statement: Any) -> list[dict]:
    """yfinance statements serialize as {period_date: {line_item: value}}.

    Return the per-period column dicts, most recent first.
    """
    if not isinstance(statement, dict) or not statement:
        return []
    try:
        keys = sorted(statement.keys(), reverse=True)
    except TypeError:
        keys = list(statement.keys())
    return [statement[k] for k in keys if isinstance(statement[k], dict)]


def _line_item(period: dict, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        if label in period:
            value = _num(period[label])
            if value is not None:
                return value
    return None


def _fcf_yield(item: dict) -> float | None:
    fcf = _num(_resolve_path(item, "data.valuation.freeCashflow"))
    mcap = _num(_resolve_path(item, "data.valuation.marketCap"))
    if fcf is None or not mcap:
        return None
    return fcf / mcap


def _revenue_growth_yoy(item: dict) -> float | None:
    periods = _statement_periods(_resolve_path(item, "data.financials.income_statement_annual"))
    if len(periods) < 2:
        return None
    latest = _line_item(periods[0], ("Total Revenue", "TotalRevenue", "Revenue"))
    prior = _line_item(periods[1], ("Total Revenue", "TotalRevenue", "Revenue"))
    if latest is None or not prior:
        return None
    return (latest - prior) / abs(prior)


def _net_income_latest(item: dict) -> float | None:
    periods = _statement_periods(_resolve_path(item, "data.financials.income_statement_annual"))
    if not periods:
        return None
    return _line_item(periods[0], ("Net Income", "NetIncome", "Net Income Common Stockholders"))


def _debt_to_equity(item: dict) -> float | None:
    periods = _statement_periods(_resolve_path(item, "data.financials.balance_sheet_annual"))
    if not periods:
        return None
    debt = _line_item(periods[0], ("Total Debt", "TotalDebt"))
    equity = _line_item(periods[0], ("Stockholders Equity", "Total Stockholder Equity", "StockholdersEquity"))
    if debt is None or not equity:
        return None
    return debt / equity


def _current_ratio(item: dict) -> float | None:
    periods = _statement_periods(_resolve_path(item, "data.financials.balance_sheet_annual"))
    if not periods:
        return None
    assets = _line_item(periods[0], ("Current Assets", "Total Current Assets", "CurrentAssets"))
    liabilities = _line_item(periods[0], ("Current Liabilities", "Total Current Liabilities", "CurrentLiabilities"))
    if assets is None or not liabilities:
        return None
    return assets / liabilities


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _price_return(item: dict, days: int) -> float | None:
    history = _resolve_path(item, "data.history")
    if not isinstance(history, list) or len(history) < 2:
        return None
    points: list[tuple[date, float]] = []
    for record in history:
        if not isinstance(record, dict):
            continue
        day = _parse_date(record.get("Date") or record.get("date"))
        close = _num(record.get("Close") or record.get("close") or record.get("Adj Close"))
        if day is not None and close is not None and close > 0:
            points.append((day, close))
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p[0])
    latest_day, latest_close = points[-1]
    cutoff = latest_day - timedelta(days=days)
    past = [p for p in points if p[0] <= cutoff]
    past_close = past[-1][1] if past else points[0][1]
    if not past_close:
        return None
    return latest_close / past_close - 1.0


def _net_insider_shares(item: dict) -> float | None:
    purchases = _resolve_path(item, "data.insider.purchases")
    if not isinstance(purchases, dict):
        return None
    # yfinance insider_purchases serializes with a row label like
    # "Net Shares Purchased (Sold)"; scan every column dict for it.
    for column in purchases.values():
        if isinstance(column, dict):
            for label, value in column.items():
                if isinstance(label, str) and "net shares" in label.lower():
                    num = _num(value)
                    if num is not None:
                        return num
    return None


def _put_call_oi_ratio(item: dict) -> float | None:
    options = _resolve_path(item, "data.options")
    if not isinstance(options, dict):
        return None
    chains = [key for key in options if key.startswith("chain_")]
    if not chains:
        return None
    chain = options[sorted(chains)[0]]
    if not isinstance(chain, dict):
        return None

    def _sum_oi(side: Any) -> float | None:
        oi_col = _resolve_path(side, "openInterest") if isinstance(side, dict) else None
        if not isinstance(oi_col, dict):
            return None
        total = 0.0
        seen = False
        for value in oi_col.values():
            num = _num(value)
            if num is not None:
                total += num
                seen = True
        return total if seen else None

    calls = _sum_oi(chain.get("calls"))
    puts = _sum_oi(chain.get("puts"))
    if not calls or puts is None:
        return None
    return puts / calls


def _groups_ok_count(item: dict) -> int:
    """How many of the requested yfinance groups came back without a top-level
    error. Sub-field errors ('group.subfield') do not disqualify the group."""
    groups = item.get("groups") or list((item.get("data") or {}).keys())
    errors = item.get("errors") or {}
    failed = {key for key in errors if "." not in key}
    return len([g for g in groups if g not in failed])


DERIVED_METRICS = {
    "fcf_yield": _fcf_yield,
    "revenue_growth_yoy": _revenue_growth_yoy,
    "net_income_latest": _net_income_latest,
    "debt_to_equity": _debt_to_equity,
    "current_ratio": _current_ratio,
    "return_30d": lambda item: _price_return(item, 30),
    "return_90d": lambda item: _price_return(item, 90),
    "return_365d": lambda item: _price_return(item, 365),
    "net_insider_shares": _net_insider_shares,
    "put_call_oi_ratio": _put_call_oi_ratio,
    "groups_ok_count": _groups_ok_count,
}


def compute_derived_metrics(item: dict | None) -> dict[str, Any]:
    if not item:
        return {name: None for name in DERIVED_METRICS}
    metrics: dict[str, Any] = {}
    for name, fn in DERIVED_METRICS.items():
        try:
            metrics[name] = fn(item)
        except Exception:  # derived metrics are best-effort; never sink the run
            metrics[name] = None
    return metrics


# --- evidence resolution ----------------------------------------------------

def _resolve_evidence(field: str, contexts: dict[str, Any], derived: dict[str, Any]) -> dict:
    source, _, path = field.partition(":")
    entry = {"source": source, "field": field, "value": None, "available": False}
    if source == "derived":
        if "derived" in contexts and path in derived:
            entry["value"] = derived.get(path)
            entry["available"] = entry["value"] is not None
        return entry
    if source not in contexts:
        return entry  # source not supplied -> unavailable, not an error
    ctx = contexts[source]
    if source == "classification":
        holding = ctx
        key = path.split(".", 1)[1] if "." in path else path
        value = None
        if isinstance(holding, dict):
            value = holding.get(key)
            if value is None and isinstance(holding.get("fields"), dict):
                value = holding["fields"].get(key)
        entry["value"] = value
    else:
        entry["value"] = _resolve_path(ctx, path)
    entry["available"] = entry["value"] is not None
    return entry


def _entry_slot(spec: dict, contexts: dict[str, Any], derived: dict[str, Any], *, kind: str) -> dict:
    evidence = [_resolve_evidence(f, contexts, derived) for f in (spec.get("evidence_fields") or [])]
    unknown = bool(evidence) and not any(e["available"] for e in evidence)
    slot = {
        "id": spec["id"],
        "applies_when": spec.get("applies_when", rubric_mod.APPLIES_ALWAYS),
        "evidence": evidence,
        "unknown": unknown,
    }
    if kind == "gate":
        slot["description"] = spec.get("description", "")
        slot["fail_when"] = spec.get("fail_when", "")
        slot["result"] = None  # LLM fills: pass | fail | unknown
    else:
        slot["weight"] = spec["weight"]
        slot["section"] = spec.get("section")
        slot["anchors"] = spec.get("anchors", {})
        slot["score"] = None  # LLM fills: 1..5 or "unknown"
        slot["rationale"] = None
    return slot


# --- position context -------------------------------------------------------

def _dividend_payer(item: dict | None) -> bool:
    if not item:
        return False
    for path in ("data.valuation.dividendYield", "data.dividends.summary.dividendRate", "data.dividends.summary.dividendYield"):
        value = _num(_resolve_path(item, path))
        if value and value > 0:
            return True
    return False


def _position_context(ticker: str, holding: dict | None, item: dict | None, args) -> dict:
    role = None
    weight = None
    held = False
    if holding:
        held = True
        role = holding.get("primary_group")
        weight = holding.get("position_weight")
        if weight is None and isinstance(holding.get("fields"), dict):
            weight = holding["fields"].get("current_weight_percent")
    if args.role:
        role = args.role
    if args.held is not None:
        held = args.held
    dividend_payer = _dividend_payer(item)
    return {
        "held": held,
        "portfolio_role": role,
        "weight_pct": weight,
        "dividend_payer": dividend_payer,
        "income_role": (role == "Income"),
        "page_exists": args.page_exists,
        "current_decision": args.current_decision,
    }


def build_worksheet(ticker: str, rubric: dict, sources: dict[str, tuple[str, Any]], args) -> dict:
    ticker = ticker.upper()
    research_payload = sources.get("yfinance", (None, None))[1]
    item = _find_research_item(research_payload, ticker) if research_payload is not None else None
    holding = None
    if "classification" in sources:
        holding = _find_holding(sources["classification"][1], ticker)

    derived = compute_derived_metrics(item)
    contexts: dict[str, Any] = {}
    if item is not None:
        contexts["yfinance"] = item
    if research_payload is not None:
        contexts["derived"] = derived  # derived metrics depend on the yfinance pull
    if holding is not None:
        contexts["classification"] = holding

    position = _position_context(ticker, holding, item, args)

    research_sources = {}
    for name, (path, _payload) in sources.items():
        entry = {"path": path}
        if name == "yfinance" and item is not None:
            errors = item.get("errors") or {}
            failed = {k: v for k, v in errors.items() if "." not in k}
            groups = item.get("groups") or list((item.get("data") or {}).keys())
            entry["groups_ok"] = [g for g in groups if g not in failed]
            entry["groups_failed"] = failed
        research_sources[name] = entry

    gates = rubric_mod.applicable_gates(rubric, dividend_payer=position["dividend_payer"], income_role=position["income_role"])
    dims = rubric_mod.applicable_dimensions(rubric, dividend_payer=position["dividend_payer"], income_role=position["income_role"])

    return {
        "schema": "scoring-worksheet.v1",
        "ticker": ticker,
        "generated": date.today().isoformat(),
        "rubric_version": rubric.get("version"),
        "research_sources": research_sources,
        "position": position,
        "derived_metrics": derived,
        "gates": [_entry_slot(g, contexts, derived, kind="gate") for g in gates],
        "dimensions": [_entry_slot(d, contexts, derived, kind="dimension") for d in dims],
        "instructions": (
            "Fill every gate 'result' (pass|fail|unknown) and every dimension 'score' "
            "(1-5 or \"unknown\") with 1-3 evidence citations and a rationale. Missing "
            "evidence stays unknown -- never guess. Do not compute the weighted score or "
            "pick the action; validate_recommendation.py recomputes them from the rubric."
        ),
    }


def _parse_sources(pairs: list[str]) -> dict[str, tuple[str, Any]]:
    sources: dict[str, tuple[str, Any]] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise WorksheetError(f"--source expects id=path, got '{pair}'")
        name, path = pair.split("=", 1)
        sources[name.strip()] = (path.strip(), _load_json(Path(path.strip())))
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the deterministic scoring worksheet for a ticker.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="id=path",
        help="Research source as id=path (e.g. yfinance=research.json, classification=portfolio-classification.json). Repeatable.",
    )
    parser.add_argument("--rubric", type=Path, default=None, help="Rubric path (defaults to the shipped decision-rubric.yml).")
    parser.add_argument("--role", default=None, help="Override the portfolio role (else taken from classification).")
    parser.add_argument("--held", type=lambda s: s.lower() in ("1", "true", "yes"), default=None, help="Override held state.")
    parser.add_argument("--page-exists", dest="page_exists", type=lambda s: s.lower() in ("1", "true", "yes"), default=None)
    parser.add_argument("--current-decision", dest="current_decision", default=None)
    parser.add_argument("--output", type=Path, help="Write worksheet JSON here instead of stdout.")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    try:
        rubric = rubric_mod.load_rubric(args.rubric)
        errors = rubric_mod.validate_rubric(rubric, rubric_mod.load_framework())
        if errors:
            for err in errors:
                print(f"rubric error: {err}", file=sys.stderr)
            return 1
        sources = _parse_sources(args.source)
        worksheet = build_worksheet(args.ticker, rubric, sources, args)
    except (WorksheetError, rubric_mod.RubricError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(worksheet, indent=2 if args.pretty else None)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote worksheet: {args.output}")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

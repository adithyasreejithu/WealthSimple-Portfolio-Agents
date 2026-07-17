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

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import kb_pages  # noqa: E402
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


def _has_data(value: Any) -> bool:
    """Whether a resolved value carries real evidence.

    Many yfinance groups come back structurally EMPTY for ETFs (an empty dict of
    line-item tables, or `{"expirations": []}`) while `errors` stays `{}`. Treat
    those as absent so counts and evidence availability reflect reality. Numbers
    (including 0 / 0.0) and non-empty strings are data.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_data(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_data(v) for v in value)
    return True  # numbers (incl. 0/0.0), bools, other scalars


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


# yfinance grade actions in the upgrades_downgrades Action column.
_UPGRADE_ACTIONS = ("up", "upgrade")
_DOWNGRADE_ACTIONS = ("down", "downgrade")


def _analyst_actions(item: dict) -> list[tuple[date, str]]:
    """(date, action) rows from the upgrades_downgrades table.

    The table serializes column-oriented ({column: {grade_date: value}}); the
    Action column carries yfinance grade actions ("up", "down", "init", ...).
    """
    table = _resolve_path(item, "data.analyst.upgrades_downgrades")
    if not isinstance(table, dict):
        return []
    actions = table.get("Action") or table.get("action")
    if not isinstance(actions, dict):
        return []
    rows: list[tuple[date, str]] = []
    for key, value in actions.items():
        day = _parse_date(key)
        if day is not None and isinstance(value, str):
            rows.append((day, value.strip().lower()))
    return rows


def _revision_count(item: dict, kinds: tuple[str, ...], days: int) -> int | None:
    rows = _analyst_actions(item)
    if not rows:
        return None
    cutoff = date.today() - timedelta(days=days)
    return sum(1 for day, action in rows if day >= cutoff and action in kinds)


def _net_revisions_365d(item: dict) -> int | None:
    ups = _revision_count(item, _UPGRADE_ACTIONS, 365)
    downs = _revision_count(item, _DOWNGRADE_ACTIONS, 365)
    if ups is None or downs is None:
        return None
    return ups - downs


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


def _spot(item: dict) -> float | None:
    """Current price: valuation.currentPrice, falling back to the last close in
    history (ETFs frequently lack currentPrice but always carry OHLCV)."""
    price = _num(_resolve_path(item, "data.valuation.currentPrice"))
    if price and price > 0:
        return price
    history = _resolve_path(item, "data.history")
    if isinstance(history, list):
        for record in reversed(history):
            if isinstance(record, dict):
                close = _num(record.get("Close") or record.get("close") or record.get("Adj Close"))
                if close and close > 0:
                    return close
    return None


def _nearest_chain(item: dict) -> dict | None:
    """The soonest-expiry option chain ({"calls": {...}, "puts": {...}})."""
    return _chain_at(item, 0)


def _farthest_chain(item: dict) -> dict | None:
    """The latest of the (<=3) fetched option chains, for term-structure reads."""
    return _chain_at(item, -1)


def _chain_at(item: dict, index: int) -> dict | None:
    options = _resolve_path(item, "data.options")
    if not isinstance(options, dict):
        return None
    chains = sorted(key for key in options if key.startswith("chain_"))
    if not chains:
        return None
    chain = options[chains[index]]
    return chain if isinstance(chain, dict) else None


def _chain_rows(side: Any) -> list[dict]:
    """Turn a column-dict side ({column: {row_index: value}}) into row dicts."""
    if not isinstance(side, dict):
        return []
    columns = {col: values for col, values in side.items() if isinstance(values, dict)}
    if not columns:
        return []
    row_keys: list = []
    for values in columns.values():
        for key in values:
            if key not in row_keys:
                row_keys.append(key)
    rows = []
    for key in row_keys:
        rows.append({col: values.get(key) for col, values in columns.items()})
    return rows


def _sum_column(side: Any, column: str) -> float | None:
    total = 0.0
    seen = False
    for row in _chain_rows(side):
        num = _num(row.get(column))
        if num is not None:
            total += num
            seen = True
    return total if seen else None


def _put_call_volume_ratio(item: dict) -> float | None:
    chain = _nearest_chain(item)
    if chain is None:
        return None
    calls = _sum_column(chain.get("calls"), "volume")
    puts = _sum_column(chain.get("puts"), "volume")
    if not calls or puts is None:
        return None
    return puts / calls


def _atm_iv(chain: dict | None, spot: float | None) -> float | None:
    """Average call/put implied volatility at the strike nearest spot."""
    if chain is None or not spot:
        return None
    ivs = []
    for side_name in ("calls", "puts"):
        rows = _chain_rows(chain.get(side_name))
        best_iv = None
        best_dist = None
        for row in rows:
            strike = _num(row.get("strike"))
            iv = _num(row.get("impliedVolatility"))
            if strike is None or iv is None:
                continue
            dist = abs(strike - spot)
            if best_dist is None or dist < best_dist:
                best_dist, best_iv = dist, iv
        if best_iv is not None:
            ivs.append(best_iv)
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def _atm_iv_near(item: dict) -> float | None:
    return _atm_iv(_nearest_chain(item), _spot(item))


def _atm_iv_far(item: dict) -> float | None:
    near = _nearest_chain(item)
    far = _farthest_chain(item)
    if near is None or far is None or near is far:
        return None
    return _atm_iv(far, _spot(item))


def _iv_at_target(rows: list[dict], target: float, spot: float) -> float | None:
    """IV of the row whose strike is closest to `target`, within 15% of spot."""
    best_iv = None
    best_dist = None
    for row in rows:
        strike = _num(row.get("strike"))
        iv = _num(row.get("impliedVolatility"))
        if strike is None or iv is None:
            continue
        dist = abs(strike - target)
        if dist <= 0.15 * spot and (best_dist is None or dist < best_dist):
            best_dist, best_iv = dist, iv
    return best_iv


def _iv_skew(item: dict) -> float | None:
    """OTM put IV (~7% below spot) minus OTM call IV (~7% above spot). Positive =
    downside protection is bid up relative to upside calls."""
    chain = _nearest_chain(item)
    spot = _spot(item)
    if chain is None or not spot:
        return None
    put_iv = _iv_at_target(_chain_rows(chain.get("puts")), 0.93 * spot, spot)
    call_iv = _iv_at_target(_chain_rows(chain.get("calls")), 1.07 * spot, spot)
    if put_iv is None or call_iv is None:
        return None
    return put_iv - call_iv


def _max_oi_strike(item: dict, side_name: str) -> float | None:
    chain = _nearest_chain(item)
    if chain is None:
        return None
    best_strike = None
    best_oi = None
    for row in _chain_rows(chain.get(side_name)):
        strike = _num(row.get("strike"))
        oi = _num(row.get("openInterest"))
        if strike is None or oi is None:
            continue
        if best_oi is None or oi > best_oi:
            best_oi, best_strike = oi, strike
    return best_strike


def _groups_ok_count(item: dict) -> int:
    """How many of the requested yfinance groups came back with usable data: no
    top-level error AND non-empty payload. Structurally-empty groups (ETFs return
    empty financials/earnings/etc. with errors={}) do not count. Sub-field errors
    ('group.subfield') alone do not disqualify a group that still has data."""
    groups = item.get("groups") or list((item.get("data") or {}).keys())
    errors = item.get("errors") or {}
    failed = {key for key in errors if "." not in key}
    data = item.get("data") or {}
    return len([g for g in groups if g not in failed and _has_data(data.get(g))])


DERIVED_METRICS = {
    "fcf_yield": _fcf_yield,
    "revenue_growth_yoy": _revenue_growth_yoy,
    "net_income_latest": _net_income_latest,
    "debt_to_equity": _debt_to_equity,
    "current_ratio": _current_ratio,
    "return_30d": lambda item: _price_return(item, 30),
    "return_90d": lambda item: _price_return(item, 90),
    "return_365d": lambda item: _price_return(item, 365),
    "upgrades_90d": lambda item: _revision_count(item, _UPGRADE_ACTIONS, 90),
    "downgrades_90d": lambda item: _revision_count(item, _DOWNGRADE_ACTIONS, 90),
    "net_revisions_365d": _net_revisions_365d,
    "net_insider_shares": _net_insider_shares,
    "put_call_oi_ratio": _put_call_oi_ratio,
    "put_call_volume_ratio": _put_call_volume_ratio,
    "atm_iv_near": _atm_iv_near,
    "atm_iv_far": _atm_iv_far,
    "iv_skew": _iv_skew,
    "max_oi_call_strike": lambda item: _max_oi_strike(item, "calls"),
    "max_oi_put_strike": lambda item: _max_oi_strike(item, "puts"),
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

# Backstop against unbounded provider tables: an embedded evidence value is
# capped to the newest MAX_EVIDENCE_ROWS rows and MAX_EVIDENCE_CHARS serialized
# chars. Citations still validate -- validate_recommendation.py resolves them
# against the source files, not the worksheet embedding.
MAX_EVIDENCE_ROWS = 15
MAX_EVIDENCE_CHARS = 4000


def _table_row_keys(table: dict) -> list[str] | None:
    """Row keys of a column-oriented, date-indexed table ({column: {date: value}}).

    Returns None for anything else -- notably financial statements, which nest
    the other way around ({period: {line_item: value}})."""
    columns = list(table.values())
    if not columns or not all(isinstance(c, dict) and c for c in columns):
        return None
    keys: set[str] = set()
    for column in columns:
        keys.update(k for k in column.keys() if isinstance(k, str))
    if not keys or not all(_parse_date(k) is not None for k in keys):
        return None
    return sorted(keys)


def _cap_evidence_value(value: Any) -> Any:
    capped = value
    if isinstance(value, list) and len(value) > MAX_EVIDENCE_ROWS:
        capped = {"rows": value[:MAX_EVIDENCE_ROWS], "truncated": True, "total_rows": len(value)}
    elif isinstance(value, dict):
        row_keys = _table_row_keys(value)
        if row_keys is not None and len(row_keys) > MAX_EVIDENCE_ROWS:
            keep = set(row_keys[-MAX_EVIDENCE_ROWS:])  # ISO-sortable: newest last
            capped = {
                "rows": {
                    column: {k: v for k, v in cells.items() if k in keep}
                    for column, cells in value.items()
                    if isinstance(cells, dict)
                },
                "truncated": True,
                "total_rows": len(row_keys),
            }
    text = json.dumps(capped, default=str)
    if len(text) > MAX_EVIDENCE_CHARS:
        return {"truncated": True, "total_chars": len(text), "preview": text[:MAX_EVIDENCE_CHARS]}
    return capped


def _resolve_evidence(field: str, contexts: dict[str, Any], derived: dict[str, Any]) -> dict:
    source, _, path = field.partition(":")
    entry = {"source": source, "field": field, "value": None, "available": False}
    if source == "derived":
        if "derived" in contexts and path in derived:
            entry["value"] = derived.get(path)
            entry["available"] = _has_data(entry["value"])
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
    entry["available"] = _has_data(entry["value"])
    entry["value"] = _cap_evidence_value(entry["value"])
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

CLASSIFICATION_MAX_AGE_DAYS = 7


def _classification_freshness(payload: Any) -> tuple[str | None, bool | None]:
    """generated_at and staleness (> CLASSIFICATION_MAX_AGE_DAYS) of the
    classify-portfolio output, surfaced here so the data-prep agent never has to
    open the classification JSON itself."""
    generated = payload.get("generated_at") if isinstance(payload, dict) else None
    if not isinstance(generated, str):
        return None, None
    day = _parse_date(generated)
    if day is None:
        return generated, None
    return generated, (date.today() - day).days > CLASSIFICATION_MAX_AGE_DAYS


def _load_prior_decision(path: Path | None) -> dict | None:
    """Deterministically extract the last recorded decision from the ticker's
    current thesis page: last Decision History row (date, action, verdict) +
    Status block's Confidence/Time Horizon. None if no path, missing file, or
    unparseable page. History fields are individually None if the page has a
    Status block but no history rows yet (new page) -- Status data is kept in
    that case rather than discarded.

    This replaces the stock-analyst agent digging through old dated artifacts
    and the thesis page's Decision History table itself for verdict_vs_previous
    context -- see docs/plans/stock-analyst-token-reduction.md.
    """
    if not path or not Path(path).exists():
        return None
    try:
        _, body = kb_pages.parse_page_file(Path(path))
    except kb_pages.KBPageError:
        return None
    row = kb_pages.last_decision_history_row(body) or {}
    status = kb_pages.parse_status_block(body)
    if not row and not status:
        return None
    return {
        "date": row.get("date"),
        "action": row.get("action"),
        "verdict": row.get("verdict"),
        "confidence": status.get("confidence"),
        "time_horizon": status.get("time_horizon"),
    }


def _dividend_payer(item: dict | None) -> bool:
    if not item:
        return False
    for path in ("data.valuation.dividendYield", "data.dividends.summary.dividendRate", "data.dividends.summary.dividendYield"):
        value = _num(_resolve_path(item, path))
        if value and value > 0:
            return True
    return False


def _detect_asset_class(holding: dict | None, item: dict | None) -> str | None:
    """Classify the security as 'etf' or 'stock'.

    Prefer the classification holding's own asset_class; fall back to yfinance
    overview.quoteType ("ETF"/"MUTUALFUND" -> etf, anything else -> stock).
    Returns None when nothing is available (caller defaults to equity behavior).
    """
    if holding and isinstance(holding.get("fields"), dict):
        raw = holding["fields"].get("asset_class")
        if isinstance(raw, str) and raw.strip():
            return "etf" if raw.strip().lower() in ("etf", "mutualfund", "fund") else "stock"
    if item is not None:
        quote_type = _resolve_path(item, "data.overview.quoteType")
        if isinstance(quote_type, str) and quote_type.strip():
            return "etf" if quote_type.strip().upper() in ("ETF", "MUTUALFUND") else "stock"
    return None


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
    asset_class = _detect_asset_class(holding, item)
    if getattr(args, "asset_class", None):
        asset_class = args.asset_class
    thesis_page = getattr(args, "thesis_page", None)
    page_exists = args.page_exists
    if page_exists is None and thesis_page is not None:
        page_exists = Path(thesis_page).exists()
    return {
        "held": held,
        "portfolio_role": role,
        "weight_pct": weight,
        "dividend_payer": dividend_payer,
        "income_role": (role == "Income"),
        "asset_class": asset_class,
        "is_etf": asset_class == "etf",
        "page_exists": page_exists,
        "prior_decision": _load_prior_decision(thesis_page),
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
    if "classification" in sources:
        generated_at, stale = _classification_freshness(sources["classification"][1])
        position["classification_generated_at"] = generated_at
        position["classification_stale"] = stale

    research_sources = {}
    for name, (path, _payload) in sources.items():
        entry = {"path": path}
        if name == "yfinance" and item is not None:
            errors = item.get("errors") or {}
            failed = {k: v for k, v in errors.items() if "." not in k}
            groups = item.get("groups") or list((item.get("data") or {}).keys())
            data = item.get("data") or {}
            # A group is "ok" only if it neither errored nor came back empty
            # (ETFs return several structurally-empty groups with errors={}).
            entry["groups_ok"] = [g for g in groups if g not in failed and _has_data(data.get(g))]
            entry["groups_empty"] = [g for g in groups if g not in failed and not _has_data(data.get(g))]
            entry["groups_failed"] = failed
        research_sources[name] = entry

    is_etf = position["is_etf"]
    gates = rubric_mod.applicable_gates(
        rubric, dividend_payer=position["dividend_payer"], income_role=position["income_role"], is_etf=is_etf
    )
    dims = rubric_mod.applicable_dimensions(
        rubric, dividend_payer=position["dividend_payer"], income_role=position["income_role"], is_etf=is_etf
    )

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


def _format_summary(worksheet: dict) -> str:
    """Compact data-prep summary printed after writing the worksheet, so the
    stock-data-prep agent can report without opening any of the JSON artifacts."""
    position = worksheet.get("position") or {}
    lines = [
        "Data-prep summary:",
        "  ticker: {} | rubric: {} | track: {} | asset_class: {} | dividend_payer: {}".format(
            worksheet.get("ticker"),
            worksheet.get("rubric_version"),
            "etf" if position.get("is_etf") else "equity",
            position.get("asset_class") or "unknown",
            position.get("dividend_payer"),
        ),
        "  position: held={} weight={} role={}".format(
            position.get("held"), position.get("weight_pct"), position.get("portfolio_role")
        ),
    ]
    if "classification_stale" in position:
        state = {True: "STALE (>7 days) -- rerun classify-portfolio", False: "fresh", None: "unknown age"}[
            position.get("classification_stale")
        ]
        lines.append(f"  classification: generated_at={position.get('classification_generated_at')} ({state})")
    prior = position.get("prior_decision")
    if prior and (prior.get("date") or prior.get("action")):
        lines.append(
            "  prior decision: {} {} ({}, {} confidence)".format(
                prior.get("date"), prior.get("action"), prior.get("verdict"), prior.get("confidence")
            )
        )
    else:
        lines.append("  prior decision: none (new page)")
    for name, entry in (worksheet.get("research_sources") or {}).items():
        if "groups_ok" not in entry:
            continue
        lines.append(f"  {name} groups ok ({len(entry['groups_ok'])}): {', '.join(entry['groups_ok']) or '-'}")
        if entry.get("groups_empty"):
            lines.append(
                f"  {name} groups empty ({len(entry['groups_empty'])}): {', '.join(entry['groups_empty'])}"
                " -- expected for an ETF's equity-only groups"
            )
        if entry.get("groups_failed"):
            failures = "; ".join(f"{g}: {msg}" for g, msg in entry["groups_failed"].items())
            lines.append(f"  {name} groups failed ({len(entry['groups_failed'])}): {failures}")
    lines.append(
        "  gates: {} | dimensions: {}".format(len(worksheet.get("gates") or []), len(worksheet.get("dimensions") or []))
    )
    return "\n".join(lines)


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
    parser.add_argument(
        "--asset-class",
        dest="asset_class",
        choices=["stock", "etf"],
        default=None,
        help="Override the detected asset class (else from classification asset_class / yfinance quoteType).",
    )
    parser.add_argument("--held", type=lambda s: s.lower() in ("1", "true", "yes"), default=None, help="Override held state.")
    parser.add_argument("--page-exists", dest="page_exists", type=lambda s: s.lower() in ("1", "true", "yes"), default=None)
    parser.add_argument(
        "--thesis-page",
        dest="thesis_page",
        type=Path,
        default=None,
        help=(
            "Path to the ticker's current stocks/TICKER.md, if it exists. Used to derive "
            "page_exists (when --page-exists is not given) and position.prior_decision "
            "(last Decision History row + Status confidence/time horizon) so the analyst "
            "never has to read the page's history itself."
        ),
    )
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
        print(_format_summary(worksheet))
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

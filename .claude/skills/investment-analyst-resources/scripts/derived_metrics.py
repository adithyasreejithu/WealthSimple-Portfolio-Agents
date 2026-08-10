"""Deterministic derived metrics for the investment-analyst-resources skill.

Turns raw data this skill already has in hand into the interpreted signal a
future analyst actually reads -- FCF yield, price returns, analyst revision
counts, net insider shares, and options positioning (put/call ratios,
near/far ATM implied vol, IV skew, max-OI strikes). No judgment: every
function is pure math over already-fetched fields, never fetches anything
itself, and never raises -- matching `evaluate-stock-decision/scripts/
scoring_worksheet.py`'s `compute_derived_metrics` discipline exactly.

Two entry points, split by source shape:

- `compute_live_metrics` operates on this skill's `live_data` dict (the
  `{group: payload}` shape `_live_top_up` returns), for the 7 metrics that
  read the `options`/`analyst`/`insider`/`valuation` groups -- the same
  fetch code v1 uses, so the raw shapes are identical. Formulas are ported
  from `scoring_worksheet.py`, not imported -- see
  docs/plans/investment-analyst-resources-skill.md's follow-up plan for why
  duplicating pure math here (rather than reaching into another skill's
  private functions) keeps the two research tracks independent.
- `compute_db_metrics` operates on `db_bundle` (this skill's DB read), for
  the 4 metrics v1 computes from raw annual statements / raw price history
  that this skill never fetches live -- computed here instead from
  `financial_snapshots` (quarterly) and `historical_records`. These are
  deliberately *not* v1's exact metrics (different cadence -- quarterly vs
  annual), so the quarterly ones get distinct field names
  (`net_income_latest_quarter`, not `net_income_latest`) rather than
  silently implying parity with v1's annual figure.

Phase 2 (see docs/plans/implementation/phase-2/design-decisions.md) added six
normalized ratios without a new data source. Four are live pass-throughs of
provider-computed fields `fetch-stock-research-data` already fetches in the
`valuation` group but this skill never surfaced: `ev_to_ebitda`,
`ev_to_revenue`, `roe`, `peg_ratio`. Two are DB-sourced from
`financial_snapshots.extra` line items that survive today's `_extra_line_items`
capture (`Net Debt`, `EBITDA`, `EBIT`, `Tax Provision`, `Pretax Income`,
`Invested Capital` -- confirmed present for real portfolio tickers, not
assumed): `net_debt_to_ebitda`, `roic`. Deliberately not `debt_to_ebitda`
(gross Total Debt is a *consumed* balance-sheet label -- see
`_BALANCE_CONSUMED_LABELS` -- so it is dropped before `extra` is built and is
not recoverable from this table; Net Debt is not consumed and is the
leverage figure actually available here) and not a conflict with the
existing quarterly `_debt_to_equity`/`_current_ratio` -- neither reads Net
Debt, EBITDA, EBIT, or Invested Capital, so there are two ratio pairs from
one table with no overlapping inputs, not two implementations of the same
ratio.

Both entry points also take an optional `quote`
(`investment_analyst_resources._fetch_latest_quote`'s result: a lightweight
`fast_info` pull, distinct from and fresher than the `valuation` group's
`get_info()`-sourced `currentPrice`). When given, it becomes the "current
price" for the metrics that need one -- `_spot` (options ATM/skew, in place
of `valuation.currentPrice`) and `_return_over` (`return_30d`/`90d`/`365d`,
extending the DB-sourced price series with today's actual price instead of
stopping at the last stored close). Every other metric ignores it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
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


# --- live-sourced: valuation --------------------------------------------

def _fcf_yield(live_data: dict[str, Any]) -> float | None:
    valuation = live_data.get("valuation") or {}
    fcf = _num(valuation.get("freeCashflow"))
    mcap = _num(valuation.get("marketCap"))
    if fcf is None or not mcap:
        return None
    return fcf / mcap


def _valuation_num(live_data: dict[str, Any], key: str) -> float | None:
    return _num((live_data.get("valuation") or {}).get(key))


def _ev_to_ebitda(live_data: dict[str, Any]) -> float | None:
    """Pass-through of the provider's own `enterpriseToEbitda` -- already
    fetched by `fetch-stock-research-data`'s `valuation` group
    (`VALUATION_INFO_KEYS`) but never surfaced by this skill until now. No
    recomputation: reporting the provider's figure as-is, including when it
    is negative (negative EBITDA), is truer to "no judgment" than silently
    filtering it out."""
    return _valuation_num(live_data, "enterpriseToEbitda")


def _ev_to_revenue(live_data: dict[str, Any]) -> float | None:
    """Pass-through of `enterpriseToRevenue`, same rationale as `_ev_to_ebitda`."""
    return _valuation_num(live_data, "enterpriseToRevenue")


def _roe(live_data: dict[str, Any]) -> float | None:
    """Pass-through of `returnOnEquity`, same rationale as `_ev_to_ebitda`."""
    return _valuation_num(live_data, "returnOnEquity")


def _peg_ratio(live_data: dict[str, Any]) -> float | None:
    """Prefers `trailingPegRatio` (built from realized trailing earnings
    growth) over `pegRatio` (mixes in forward estimates) when both are
    present, since the trailing figure is the more verifiable of the two;
    falls back to `pegRatio` when trailing is unavailable rather than
    reporting nothing."""
    trailing = _valuation_num(live_data, "trailingPegRatio")
    return trailing if trailing is not None else _valuation_num(live_data, "pegRatio")


def _spot(live_data: dict[str, Any], quote: dict[str, Any] | None = None) -> float | None:
    """Current price for options ATM/skew math. Prefers the dedicated live
    quote (`investment_analyst_resources._fetch_latest_quote`, a lightweight
    `fast_info` pull) over `valuation.currentPrice` from the heavier
    `get_info()`-backed live top-up -- both are "live", but the quote is the
    more recent of the two and is what position value/weight/return-window
    math also now key off, so options math stays consistent with them
    instead of quietly using a second, different "current price"."""
    if quote is not None:
        price = _num(quote.get("price"))
        if price and price > 0:
            return price
    price = _num((live_data.get("valuation") or {}).get("currentPrice"))
    return price if price and price > 0 else None


# --- live-sourced: options -----------------------------------------------

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
    return [{col: values.get(key) for col, values in columns.items()} for key in row_keys]


def _sum_column(side: Any, column: str) -> float | None:
    total = 0.0
    seen = False
    for row in _chain_rows(side):
        num = _num(row.get(column))
        if num is not None:
            total += num
            seen = True
    return total if seen else None


def _chain_at(live_data: dict[str, Any], index: int) -> dict | None:
    options = live_data.get("options")
    if not isinstance(options, dict):
        return None
    chains = sorted(key for key in options if key.startswith("chain_"))
    if not chains:
        return None
    chain = options[chains[index]]
    return chain if isinstance(chain, dict) else None


def _nearest_chain(live_data: dict[str, Any]) -> dict | None:
    return _chain_at(live_data, 0)


def _farthest_chain(live_data: dict[str, Any]) -> dict | None:
    return _chain_at(live_data, -1)


def _put_call_oi_ratio(live_data: dict[str, Any]) -> float | None:
    chain = _nearest_chain(live_data)
    if chain is None:
        return None
    calls = _sum_column(chain.get("calls"), "openInterest")
    puts = _sum_column(chain.get("puts"), "openInterest")
    if not calls or puts is None:
        return None
    return puts / calls


def _put_call_volume_ratio(live_data: dict[str, Any]) -> float | None:
    chain = _nearest_chain(live_data)
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
    return sum(ivs) / len(ivs) if ivs else None


def _atm_iv_near(live_data: dict[str, Any], quote: dict[str, Any] | None = None) -> float | None:
    return _atm_iv(_nearest_chain(live_data), _spot(live_data, quote))


def _atm_iv_far(live_data: dict[str, Any], quote: dict[str, Any] | None = None) -> float | None:
    near = _nearest_chain(live_data)
    far = _farthest_chain(live_data)
    if near is None or far is None or near is far:
        return None
    return _atm_iv(far, _spot(live_data, quote))


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


def _iv_skew(live_data: dict[str, Any], quote: dict[str, Any] | None = None) -> float | None:
    """OTM put IV (~7% below spot) minus OTM call IV (~7% above spot). Positive =
    downside protection is bid up relative to upside calls."""
    chain = _nearest_chain(live_data)
    spot = _spot(live_data, quote)
    if chain is None or not spot:
        return None
    put_iv = _iv_at_target(_chain_rows(chain.get("puts")), 0.93 * spot, spot)
    call_iv = _iv_at_target(_chain_rows(chain.get("calls")), 1.07 * spot, spot)
    if put_iv is None or call_iv is None:
        return None
    return put_iv - call_iv


def _max_oi_strike(live_data: dict[str, Any], side_name: str) -> float | None:
    chain = _nearest_chain(live_data)
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


# --- live-sourced: analyst revisions ---------------------------------------

_UPGRADE_ACTIONS = ("up", "upgrade")
_DOWNGRADE_ACTIONS = ("down", "downgrade")


def _analyst_actions(live_data: dict[str, Any]) -> list[tuple[date, str]]:
    """(date, action) rows from the upgrades_downgrades table.

    The table serializes column-oriented ({column: {grade_date: value}}); the
    Action column carries yfinance grade actions ("up", "down", "init", ...).
    """
    table = (live_data.get("analyst") or {}).get("upgrades_downgrades")
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


def _revision_count(live_data: dict[str, Any], kinds: tuple[str, ...], days: int) -> int | None:
    rows = _analyst_actions(live_data)
    if not rows:
        return None
    cutoff = date.today() - timedelta(days=days)
    return sum(1 for day, action in rows if day >= cutoff and action in kinds)


def _net_revisions_365d(live_data: dict[str, Any]) -> int | None:
    ups = _revision_count(live_data, _UPGRADE_ACTIONS, 365)
    downs = _revision_count(live_data, _DOWNGRADE_ACTIONS, 365)
    if ups is None or downs is None:
        return None
    return ups - downs


# --- live-sourced: insider --------------------------------------------------

def _net_insider_shares(live_data: dict[str, Any]) -> float | None:
    purchases = (live_data.get("insider") or {}).get("purchases")
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


LIVE_METRICS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "fcf_yield": _fcf_yield,
    "ev_to_ebitda": _ev_to_ebitda,
    "ev_to_revenue": _ev_to_revenue,
    "roe": _roe,
    "peg_ratio": _peg_ratio,
    "put_call_oi_ratio": _put_call_oi_ratio,
    "put_call_volume_ratio": _put_call_volume_ratio,
    "atm_iv_near": _atm_iv_near,
    "atm_iv_far": _atm_iv_far,
    "iv_skew": _iv_skew,
    "max_oi_call_strike": lambda ld: _max_oi_strike(ld, "calls"),
    "max_oi_put_strike": lambda ld: _max_oi_strike(ld, "puts"),
    "upgrades_90d": lambda ld: _revision_count(ld, _UPGRADE_ACTIONS, 90),
    "downgrades_90d": lambda ld: _revision_count(ld, _DOWNGRADE_ACTIONS, 90),
    "net_revisions_365d": _net_revisions_365d,
    "net_insider_shares": _net_insider_shares,
}

# Metrics whose formula reads the current spot price -- these get the
# optional live quote passed through; every other live metric ignores it.
_QUOTE_AWARE_LIVE_METRICS = frozenset({"atm_iv_near", "atm_iv_far", "iv_skew"})


def compute_live_metrics(live_data: dict[str, Any], quote: dict[str, Any] | None = None) -> dict[str, Any]:
    live_data = live_data or {}
    metrics: dict[str, Any] = {}
    for name, fn in LIVE_METRICS.items():
        try:
            metrics[name] = fn(live_data, quote) if name in _QUOTE_AWARE_LIVE_METRICS else fn(live_data)
        except Exception:  # best-effort; never sink the run
            metrics[name] = None
    return metrics


# --- DB-sourced: financials --------------------------------------------

_YOY_WINDOW_MIN_DAYS = 300
_YOY_WINDOW_MAX_DAYS = 430


def _latest_financial_row(db_bundle: dict[str, Any]) -> dict | None:
    rows = db_bundle.get("financials") or []
    return rows[-1] if rows else None


def _debt_to_equity(db_bundle: dict[str, Any]) -> float | None:
    row = _latest_financial_row(db_bundle)
    return _num(row.get("debt_to_equity")) if row else None


def _current_ratio(db_bundle: dict[str, Any]) -> float | None:
    row = _latest_financial_row(db_bundle)
    return _num(row.get("current_ratio")) if row else None


def _extra_num(row: dict | None, section: str, label: str) -> float | None:
    """Read one raw line item out of a `financial_snapshots` row's `extra`
    blob (`db_resources.read_financial_snapshots` already parses it from JSON
    into a dict). `section` is `income_statement`, `balance_sheet`, or
    `cash_flow`; `label` is the exact yfinance row label."""
    if not row:
        return None
    extra = row.get("extra") or {}
    section_data = extra.get(section) or {}
    return _num(section_data.get(label))


def _net_debt_to_ebitda(db_bundle: dict[str, Any]) -> float | None:
    """Net Debt / EBITDA, both read from the latest quarter's `extra` line
    items. Not gross Debt/EBITDA: yfinance's `Total Debt` label is consumed
    by `_debt_to_equity`'s extraction (see `_BALANCE_CONSUMED_LABELS`) and so
    is dropped before `extra` is built -- it is not recoverable from this
    table. `Net Debt` is never consumed and is the leverage figure this table
    can actually support. `None` when EBITDA is missing or non-positive,
    since a leverage multiple over zero or negative EBITDA is not meaningful."""
    row = _latest_financial_row(db_bundle)
    net_debt = _extra_num(row, "balance_sheet", "Net Debt")
    ebitda = _extra_num(row, "income_statement", "EBITDA")
    if net_debt is None or not ebitda or ebitda <= 0:
        return None
    return net_debt / ebitda


def _roic(db_bundle: dict[str, Any]) -> float | None:
    """Return on invested capital: NOPAT / Invested Capital, both read from
    the latest quarter's `extra` line items -- `EBIT`, `Tax Provision`,
    `Pretax Income`, and `Invested Capital` (the last is yfinance's own
    computed figure, not re-derived here from debt+equity-cash, since raw
    Total Debt and Stockholders Equity are not recoverable from this table --
    see `_net_debt_to_ebitda`). Tax rate = Tax Provision / Pretax Income is
    not clamped to [0, 1]: an unusual quarter (a tax benefit, or a loss
    quarter) can legitimately produce a rate outside that range, and
    clamping would be a judgment call this function does not make. `None`
    when Pretax Income is missing or non-positive, since the tax rate is
    undefined for a loss quarter, or when Invested Capital is missing or
    non-positive."""
    row = _latest_financial_row(db_bundle)
    ebit = _extra_num(row, "income_statement", "EBIT")
    tax_provision = _extra_num(row, "income_statement", "Tax Provision")
    pretax_income = _extra_num(row, "income_statement", "Pretax Income")
    invested_capital = _extra_num(row, "balance_sheet", "Invested Capital")
    if ebit is None or tax_provision is None or not pretax_income or pretax_income <= 0:
        return None
    if not invested_capital or invested_capital <= 0:
        return None
    tax_rate = tax_provision / pretax_income
    nopat = ebit * (1 - tax_rate)
    return nopat / invested_capital


def _net_income_latest_quarter(db_bundle: dict[str, Any]) -> float | None:
    row = _latest_financial_row(db_bundle)
    return _num(row.get("net_income")) if row else None


def _revenue_growth_yoy(db_bundle: dict[str, Any]) -> float | None:
    """Same-quarter-prior-year revenue growth from `financial_snapshots`
    rows (ascending by period_end_date). Not v1's annual-over-annual figure
    -- this is quarterly, the natural cadence for this skill's DB source."""
    rows = db_bundle.get("financials") or []
    if len(rows) < 2:
        return None
    latest = rows[-1]
    latest_date = latest.get("period_end_date")
    latest_revenue = _num(latest.get("revenue"))
    if latest_date is None or latest_revenue is None:
        return None
    prior = None
    best_dist = None
    for row in rows[:-1]:
        row_date = row.get("period_end_date")
        if row_date is None:
            continue
        dist_days = (latest_date - row_date).days
        if _YOY_WINDOW_MIN_DAYS <= dist_days <= _YOY_WINDOW_MAX_DAYS:
            if best_dist is None or abs(dist_days - 365) < best_dist:
                best_dist = abs(dist_days - 365)
                prior = row
    if prior is None:
        return None
    prior_revenue = _num(prior.get("revenue"))
    if not prior_revenue:
        return None
    return (latest_revenue - prior_revenue) / abs(prior_revenue)


# --- DB-sourced: price returns ------------------------------------------

def _quote_point(quote: dict[str, Any] | None) -> tuple[date, float] | None:
    if quote is None:
        return None
    price = _num(quote.get("price"))
    as_of = quote.get("as_of")
    quote_date = as_of.date() if isinstance(as_of, datetime) else as_of if isinstance(as_of, date) else None
    if not price or quote_date is None:
        return None
    return (quote_date, price)


def _return_over(db_bundle: dict[str, Any], days: int, quote: dict[str, Any] | None = None) -> float | None:
    """Return over the trailing `days`. When a live quote is available and at
    least as recent as the last stored close, it becomes the series'
    "today" endpoint instead of that (possibly a day or more stale) DB row
    -- ties on the same calendar date favor the quote, since it's the
    fresher of the two.

    Returns `None` when history does not reach back `days` -- i.e. no point
    exists at or before the cutoff. Falling back to the earliest available
    point here previously made `return_30d`/`return_90d`/`return_365d`
    silently identical whenever history was shorter than 365 days, since all
    three windows resolved to the same earliest-point fallback instead of
    reporting that the wider windows had insufficient history."""
    rows = (db_bundle.get("prices") or {}).get("rows") or []
    points = [
        (row["record_date"], _num(row.get("close")))
        for row in rows
        if row.get("record_date") is not None and _num(row.get("close"))
    ]
    quote_point = _quote_point(quote)
    if quote_point is not None and (not points or quote_point[0] >= max(p[0] for p in points)):
        points.append(quote_point)
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p[0])
    latest_day, latest_close = points[-1]
    cutoff = latest_day - timedelta(days=days)
    past = [p for p in points if p[0] <= cutoff]
    if not past:
        return None
    past_close = past[-1][1]
    if not past_close:
        return None
    return latest_close / past_close - 1.0


DB_METRICS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "debt_to_equity": _debt_to_equity,
    "current_ratio": _current_ratio,
    "net_debt_to_ebitda": _net_debt_to_ebitda,
    "roic": _roic,
    "net_income_latest_quarter": _net_income_latest_quarter,
    "revenue_growth_yoy": _revenue_growth_yoy,
    "return_30d": lambda db, quote=None: _return_over(db, 30, quote),
    "return_90d": lambda db, quote=None: _return_over(db, 90, quote),
    "return_365d": lambda db, quote=None: _return_over(db, 365, quote),
}

# Metrics whose formula needs "today's" price -- the live quote extends
# their series when it's fresher than the last stored close.
_QUOTE_AWARE_DB_METRICS = frozenset({"return_30d", "return_90d", "return_365d"})


def compute_db_metrics(db_bundle: dict[str, Any], quote: dict[str, Any] | None = None) -> dict[str, Any]:
    db_bundle = db_bundle or {}
    metrics: dict[str, Any] = {}
    for name, fn in DB_METRICS.items():
        try:
            metrics[name] = fn(db_bundle, quote) if name in _QUOTE_AWARE_DB_METRICS else fn(db_bundle)
        except Exception:  # best-effort; never sink the run
            metrics[name] = None
    return metrics

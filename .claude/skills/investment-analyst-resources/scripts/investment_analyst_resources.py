"""Investment Analyst Resources -- DB-first, live-top-up resource bundle for one ticker.

Deterministic data layer for the v2 investment-analyst track's Phase 1 (see
docs/plans/investment-analyst-resources-skill.md). No judgment happens here:
this script resolves a ticker, checks per-domain freshness, refreshes only
what's overdue, reads everything DuckDB already has, tops up with a narrow
live yfinance pull for the groups DuckDB cannot hold, and emits a small
stdout digest plus an on-disk bundle for the next script to read.

Three modes share the module but never share a DuckDB lock (see
freshness_gate.py's docstring for why): `gate` and `read` are read-only and
safe to fan out across parallel agents; `refresh` is the sole writer and
must run once, sequentially. The default (no --mode) runs gate -> refresh ->
read in one process for single-agent use, closing the write connection
before reopening read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

SKILL_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "fetch-stock-research-data" / "scripts"))

import db_resources  # noqa: E402
import derived_metrics  # noqa: E402
import freshness_gate  # noqa: E402
import skill_trace  # noqa: E402
from config import DATABASE_PATH  # noqa: E402
from database import close_connection  # noqa: E402
from database_command import set_security_status  # noqa: E402
from fetch_stock_research_data import fetch_stock_research_data  # noqa: E402
from market_data import RESEARCH_STATUSES  # noqa: E402
from yfinance_extractor import _build_session, _create_ticker, _require_yfinance  # noqa: E402

SKILL_NAME = "investment-analyst-resources"

SCHEMA = "investment-analyst-resources.v1"

# The four heaviest yfinance groups (history, financials, earnings,
# dividends) are deliberately excluded -- the DB already serves them, often
# with more depth than a single live pull. See the coverage analysis in
# docs/plans/investment-analyst-resources-skill.md Part 1.
LIVE_TOP_UP_GROUPS = (
    "overview", "valuation", "analyst", "options", "news",
    "insider", "institutional", "funds",
)

DEFAULT_OUTPUT_DIR = ROOT / "exports" / "investment-analyst-resources"
DIGEST_SOFT_LIMIT_BYTES = 8192


def _due_by_domain(gate_results: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_domain: dict[str, list[str]] = {}
    for result in gate_results:
        if not result.get("resolved") or not result.get("can_refresh"):
            continue
        for domain in result.get("due_domains", []):
            by_domain.setdefault(domain, []).append(result["provider_symbol"])
    return by_domain


def _run_gate(tickers: list[str], db_path: Path, *, force: bool, run_date: date) -> list[dict[str, Any]]:
    connection = db_resources.connect_read_only(db_path)
    try:
        db_resources.validate_database(connection)
        return [
            freshness_gate.compute_freshness(connection, ticker, force=force, run_date=run_date)
            for ticker in tickers
        ]
    finally:
        connection.close()


def _run_refresh(gate_results: list[dict[str, Any]], db_path: Path) -> dict[str, Any]:
    due_by_domain = _due_by_domain(gate_results)
    if not due_by_domain:
        return {}
    # This skill is the on-demand research path, so a declared-wishlist
    # ticker's provider symbol -- already cleared through compute_freshness's
    # owned-or-research can_refresh gate above -- is always eligible here.
    return freshness_gate.refresh_domains(due_by_domain, db_path, include_research=True)


def _is_refreshable_subject(gate_result: dict[str, Any]) -> bool:
    """Owned or a declared research candidate -- the scope that gets real,
    persisted DuckDB market data. See `market_data.get_market_targets`."""
    subject = gate_result.get("subject") or {}
    return bool(subject.get("owned")) or subject.get("declared_status") in RESEARCH_STATUSES


def _register_wishlist_candidates(
    gate_results: list[dict[str, Any]], db_path: Path, rationale: str | None
) -> bool:
    """Declare a wishlist research status for every ticker that cannot yet
    be refreshed -- both a ticker with no `tickers` row at all
    (`subject.status == "unknown"`, `resolved: False`) and one that already
    has a row but no declaration (`resolved: True`, `subject.status ==
    "unknown"` all the same, since `db_resources.resolve_subject` folds
    "never found" and "found but undeclared" into the same `unknown`
    verdict). Deliberately does **not** touch a ticker already declared
    `avoid`/`retired` -- that is a considered decision this flag must not
    silently override -- nor an already-owned or already-wishlist ticker,
    which are refreshable already.

    The one write this skill performs outside `refresh_domains` itself.
    Called only from the refresh phase (`--mode refresh` or the default
    gate->refresh->read sequence) -- `--mode gate`/`--mode read` never call
    this, matching the module's "refresh is the sole writer" contract.
    Requires `--register-wishlist`; without it a ticker that cannot be
    refreshed stays as-is, with a gap naming the manual `database status`
    command. Returns True when at least one ticker was registered, so the
    caller knows to re-gate before proceeding.
    """
    registered = False
    try:
        for result in gate_results:
            if result.get("can_refresh"):
                continue
            subject = result.get("subject") or {}
            if subject.get("status") != "unknown":
                continue
            ticker = result["ticker"]
            try:
                set_security_status(
                    ticker, "wishlist", db_path=db_path,
                    rationale=rationale, declared_by=SKILL_NAME,
                )
                print(f"note: registered {ticker} as a wishlist research candidate", file=sys.stderr)
                registered = True
            except ValueError as exc:
                print(f"# warning: could not register {ticker}: {exc}", file=sys.stderr)
    finally:
        # set_security_status writes through the read-write shared
        # connection; a caller re-gating with a read-only connection right
        # after would otherwise hit DuckDB's single-writer lock. Same
        # discipline as `freshness_gate.refresh_domains`'s own `finally`.
        close_connection()
    return registered


def _live_top_up(ticker: str, provider_symbol: str | None) -> tuple[dict[str, Any], dict[str, str]]:
    if provider_symbol is None:
        return {}, {"_top_up": "no verified provider_symbol -- skipped"}
    payload = fetch_stock_research_data(
        [{"ticker": ticker, "provider_symbol": provider_symbol, "groups": list(LIVE_TOP_UP_GROUPS)}]
    )
    item = payload[0] if payload else {"data": {}, "errors": {"_top_up": "empty response"}}
    return item.get("data", {}), item.get("errors", {})


def _fetch_latest_quote(provider_symbol: str | None) -> dict[str, Any] | None:
    """A lightweight, dedicated "current price" pull via yfinance's
    `fast_info` -- distinct from and cheaper than the full `get_info()` call
    the `valuation` live-top-up group already makes, and independent of
    `--no-live` so a pure DB-only run still gets an accurate current price
    (the whole motivation for adding this pull). Reuses this repo's existing
    yfinance client-setup helpers rather than reinventing session/cache
    handling. Never raises -- returns None on any failure or missing symbol,
    same discipline as every other fetch in this skill.

    `as_of` is the retrieval wall-clock time: `fast_info` carries no
    per-field timestamp of its own, so the pull time is the only honest
    "as of" this skill can attach to it.
    """
    if not provider_symbol:
        return None
    try:
        yf_module = _require_yfinance()
        session = _build_session()
        client = _create_ticker(yf_module, provider_symbol, session)
        info = client.fast_info
        price = info.get("lastPrice")
        if price is None:
            return None
        return {
            "price": float(price),
            "as_of": datetime.now(),
            "source": "fast_info",
            "previous_close": info.get("previousClose"),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "year_high": info.get("yearHigh"),
            "year_low": info.get("yearLow"),
        }
    except Exception:  # a quote failure is a gap, never fatal to the run
        return None


def _financials_digest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"periods": 0, "latest_period_end": None}
    latest = rows[-1]
    return {
        "periods": len(rows),
        "latest_period_end": latest.get("period_end_date"),
        "latest_revenue": latest.get("revenue"),
        "latest_net_income": latest.get("net_income"),
        "latest_eps": latest.get("eps"),
    }


def _earnings_digest(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    if not rows:
        return {"events": 0, "next_report_date": None, "last_reported": None}
    upcoming = [r for r in rows if r.get("report_date") and r["report_date"] >= today]
    reported = [r for r in rows if r.get("eps_actual") is not None]
    return {
        "events": len(rows),
        "next_report_date": upcoming[0]["report_date"] if upcoming else None,
        "last_reported": reported[-1] if reported else None,
    }


def _dividends_digest(dividends: dict[str, Any]) -> dict[str, Any]:
    declared = dividends.get("declared") or []
    received = dividends.get("received") or {}
    return {
        "declared_count": len(declared),
        "latest_declared": declared[-1] if declared else None,
        "total_received_cad": received.get("total_received_cad"),
        "last_payment_date": received.get("last_payment_date"),
    }


def _live_digest(live_data: dict[str, Any], live_errors: dict[str, str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for group in LIVE_TOP_UP_GROUPS:
        if group in live_errors:
            summary[group] = {"status": "failed", "error": live_errors[group]}
        elif group in live_data and live_data[group]:
            summary[group] = {"status": "ok"}
        else:
            summary[group] = {"status": "empty"}
    return summary


def build_digest(
    ticker: str,
    gate_result: dict[str, Any],
    refresh_summary: dict[str, Any],
    db_bundle: dict[str, Any],
    live_data: dict[str, Any],
    live_errors: dict[str, str],
    derived: dict[str, Any],
    quote: dict[str, Any] | None,
    *,
    no_live: bool,
    no_quote: bool,
    today: date,
) -> dict[str, Any]:
    position = db_bundle.get("position")
    portfolio_context = db_bundle.get("portfolio_context")
    prices = db_bundle.get("prices") or {}
    subject = gate_result.get("subject") or {}
    owned = bool(subject.get("owned"))
    refreshable = _is_refreshable_subject(gate_result)

    gaps = list(gate_result.get("gaps", []))
    # A research (wishlist) or unknown subject has no position by design --
    # only an owned subject missing one is a real gap.
    if owned and position is None:
        gaps.append("no position_snapshots row -- ticker not currently held")
    if owned and portfolio_context is None:
        gaps.append(
            "ticker not currently held and no portfolio-classification.json entry -- role/weight/value unavailable"
        )
    if refreshable and not prices.get("count"):
        gaps.append("no historical_records rows")

    domain_verdicts = {
        name: {"stale": info.get("stale"), "last": info.get("last")}
        for name, info in (gate_result.get("domains") or {}).items()
    }

    return {
        "schema": SCHEMA,
        "ticker": ticker,
        "subject": subject,
        "asset_class": gate_result.get("asset_class"),
        "as_of": today.isoformat(),
        "earnings_window": gate_result.get("earnings_window", False),
        "freshness": domain_verdicts,
        "refreshed": {domain: result for domain, result in refresh_summary.items()},
        "position": position,
        "ledger_summary": db_bundle.get("ledger_summary"),
        "portfolio_context": portfolio_context,
        "prices": {k: v for k, v in prices.items() if k != "rows"},
        "financials": _financials_digest(db_bundle.get("financials") or []),
        "earnings": _earnings_digest(db_bundle.get("earnings") or [], today),
        "dividends": _dividends_digest(db_bundle.get("dividends") or {}),
        "classification": db_bundle.get("classification"),
        "stock_details": db_bundle.get("stock_details"),
        "etf_details": (
            {k: v for k, v in (db_bundle.get("etf_details") or {}).items()
             if k not in ("top_holdings", "sector_weights")}
            if db_bundle.get("etf_details") else None
        ),
        "live": {"skipped": True} if no_live else _live_digest(live_data, live_errors),
        "derived": derived,
        "quote": {"skipped": True} if no_quote else quote,
        "gaps": gaps,
    }


def build_bundle(
    ticker: str,
    gate_result: dict[str, Any],
    refresh_summary: dict[str, Any],
    db_bundle: dict[str, Any],
    live_data: dict[str, Any],
    live_errors: dict[str, str],
    derived: dict[str, Any],
    quote: dict[str, Any] | None,
    *,
    no_quote: bool,
    today: date,
    db_cache_ref: str | None = None,
    live_cache_ref: str | None = None,
) -> dict[str, Any]:
    """Assemble the full bundle written to disk.

    `db_cache_ref`/`live_cache_ref` are run-relative `cache/` paths (see
    `process_ticker`): when given, the bulky raw `db`/`live` payloads are
    replaced with a pointer to where they were cached instead of being
    embedded here. Every non-run caller (tests calling this directly,
    `--no-run`/custom `--db-path` invocations) omits them and gets the old,
    fully-inlined bundle unchanged -- there is no `cache/` directory to point
    into outside a run.
    """
    return {
        "schema": SCHEMA,
        "ticker": ticker,
        "subject": gate_result.get("subject"),
        "provider_symbol": gate_result.get("provider_symbol"),
        "asset_class": gate_result.get("asset_class"),
        "as_of": today.isoformat(),
        "earnings_window": gate_result.get("earnings_window", False),
        "freshness": gate_result.get("domains"),
        "refreshed": refresh_summary,
        "db": db_bundle if db_cache_ref is None else {"cached": True, "cache_path": db_cache_ref},
        "live": live_data if live_cache_ref is None else {"cached": True, "cache_path": live_cache_ref},
        "derived": derived,
        "quote": {"skipped": True} if no_quote else quote,
        "errors": live_errors,
        "gaps": gate_result.get("gaps", []),
    }


def _write_bundle(ticker: str, bundle: dict[str, Any], output: Path | None, today: date, pretty: bool) -> Path:
    path = output or (DEFAULT_OUTPUT_DIR / f"{ticker}-{today.isoformat()}-resources.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(db_resources.to_json_safe(bundle), indent=2)
    path.write_text(text, encoding="utf-8")
    return path


# --- completeness trace ------------------------------------------------

def _owned(gate_result: dict[str, Any]) -> bool:
    return bool(gate_result.get("owned"))


def _refreshable(gate_result: dict[str, Any]) -> bool:
    """Owned or a declared research (wishlist) candidate -- the scope that
    gets real, DB-refreshed prices/financials/earnings/details/classification,
    per `market_data.get_market_targets` and (for classification)
    `classify_portfolio()`'s wishlist widening. `position`/`ledger_summary`/
    `portfolio_context` stay gated on `_owned` alone -- those are genuinely
    ownership-only concepts a research subject never has, by design, not a
    gap. `classification` is *not* one of them: `classify-portfolio` now
    classifies declared-wishlist tickers too, so a research subject's
    classification is a real refreshable field, not a structural absence.
    """
    if _owned(gate_result):
        return True
    subject = gate_result.get("subject") or {}
    return subject.get("declared_status") in RESEARCH_STATUSES


def _is_etf(gate_result: dict[str, Any]) -> bool:
    return gate_result.get("asset_class") == "etf"


def _is_stock(gate_result: dict[str, Any]) -> bool:
    return gate_result.get("asset_class") == "stock"


def _live_group_ok(digest: dict[str, Any], group: str) -> bool:
    """Did this live group come back with anything at all on this run?

    Coarse -- the digest only carries per-group status. Enough for the
    `live.*` domain itself, but not for the derived metrics: yfinance reports
    an `options` group for a ticker whose chain list is empty, so this returns
    True in cases where there is still nothing to compute from. The
    `_has_*` predicates below check the actual inputs instead.
    """
    live = digest.get("live") or {}
    if live.get("skipped"):
        return False
    return (live.get(group) or {}).get("status") == "ok"


# Applicability for derived metrics is decided against the *exact inputs the
# formula reads*, by calling `derived_metrics`' own accessors. Anything looser
# (such as the group's status) misclassifies a present-but-empty group -- the
# case that made a healthy TSX run report eleven phantom gaps -- and anything
# hand-rolled here could drift from the metric it is meant to describe.

def _has_options_chain(live_data: dict[str, Any]) -> bool:
    return derived_metrics._nearest_chain(live_data or {}) is not None


def _has_analyst_actions(live_data: dict[str, Any]) -> bool:
    return bool(derived_metrics._analyst_actions(live_data or {}))


def _has_insider_purchases(live_data: dict[str, Any]) -> bool:
    purchases = ((live_data or {}).get("insider") or {}).get("purchases")
    return isinstance(purchases, dict) and bool(purchases)


def _has_valuation(live_data: dict[str, Any]) -> bool:
    return bool((live_data or {}).get("valuation"))


def _price_span_days(digest: dict[str, Any]) -> int:
    """Calendar days covered by the DB price series, or 0 if unknown.

    A return window longer than the available history is not a data failure --
    a security listed six months ago has no 365-day return to fetch.
    """
    prices = digest.get("prices") or {}
    start, end = prices.get("start"), prices.get("end")
    if not start or not end:
        return 0
    try:
        start_date = date.fromisoformat(str(start)[:10])
        end_date = date.fromisoformat(str(end)[:10])
    except ValueError:
        return 0
    return max((end_date - start_date).days, 0)


def _has_financials(digest: dict[str, Any]) -> bool:
    return bool((digest.get("financials") or {}).get("periods"))


# Derived metrics are grouped by the source they are computed from, because
# applicability is a property of that source: with no options chain there is
# nothing for the seven options metrics to be missing from. Grouping also
# keeps the trace line readable -- `derived.options[7]` instead of seven
# spelled-out field paths.
_DERIVED_OPTIONS = ("put_call_oi_ratio", "put_call_volume_ratio", "atm_iv_near",
                    "atm_iv_far", "iv_skew", "max_oi_call_strike", "max_oi_put_strike")
_DERIVED_ANALYST = ("upgrades_90d", "downgrades_90d", "net_revisions_365d")
_DERIVED_INSIDER = ("net_insider_shares",)
_DERIVED_VALUATION = ("fcf_yield",)
_DERIVED_FINANCIALS = ("debt_to_equity", "current_ratio",
                       "net_income_latest_quarter", "revenue_growth_yoy")
_DERIVED_RETURNS = {"return_30d": 30, "return_90d": 90, "return_365d": 365}

# Field-level manifest, one entry per digest domain. `container` extracts the
# sub-dict to check fields against (None means "domain absent this run").
# `applicable(gate, digest, live_data)` decides whether this domain could have
# had data at all for this subject: when it returns False every field is
# reported as not-applicable and excluded from the completeness denominator,
# rather than counted as a gap. That distinction is the whole point -- an ETF
# has no balance sheet to be missing, and grading it against one produced a
# misleading score.
DOMAIN_MANIFEST: list[dict[str, Any]] = [
    {"name": "position", "fields": ["quantity", "book_value_cad", "realized_gain_cad", "computed_at"],
     "container": lambda d: d.get("position"), "applicable": lambda g, d, ld: _owned(g)},
    {"name": "ledger_summary",
     "fields": ["first_purchase_date", "latest_purchase_date", "number_of_buys", "number_of_sells"],
     "container": lambda d: d.get("ledger_summary"), "applicable": lambda g, d, ld: _owned(g)},
    {"name": "portfolio_context",
     "fields": ["role", "weight_pct", "position_market_value", "cost_basis_cad", "unrealized_gain_cad", "account_type"],
     "container": lambda d: d.get("portfolio_context"), "applicable": lambda g, d, ld: _owned(g)},
    {"name": "prices", "fields": ["latest_close", "period_return_pct", "week52_low", "week52_high"],
     "container": lambda d: d.get("prices") if (d.get("prices") or {}).get("count") else None,
     "applicable": lambda g, d, ld: _refreshable(g)},
    {"name": "financials", "fields": ["latest_period_end", "latest_revenue", "latest_net_income", "latest_eps"],
     "container": lambda d: d.get("financials") if (d.get("financials") or {}).get("periods") else None,
     "applicable": lambda g, d, ld: _refreshable(g) and not _is_etf(g)},
    {"name": "earnings", "fields": ["last_reported"],
     "container": lambda d: d.get("earnings") if (d.get("earnings") or {}).get("events") else None,
     "applicable": lambda g, d, ld: _refreshable(g) and not _is_etf(g)},
    {"name": "dividends", "fields": ["declared_count", "total_received_cad"],
     "container": lambda d: d.get("dividends"), "applicable": lambda g, d, ld: True},
    {"name": "classification", "fields": ["primary_group", "confidence", "generated_at"],
     "container": lambda d: d.get("classification"), "applicable": lambda g, d, ld: _refreshable(g)},
    {"name": "stock_details", "fields": ["sector", "industry"],
     "container": lambda d: d.get("stock_details"), "applicable": lambda g, d, ld: _refreshable(g) and not _is_etf(g)},
    {"name": "etf_details", "fields": ["fund_family", "yield", "expense_ratio", "aum", "nav"],
     "container": lambda d: d.get("etf_details"), "applicable": lambda g, d, ld: _refreshable(g) and not _is_stock(g)},
    {"name": "derived.options", "fields": list(_DERIVED_OPTIONS),
     "container": lambda d: d.get("derived"), "applicable": lambda g, d, ld: _has_options_chain(ld)},
    {"name": "derived.analyst", "fields": list(_DERIVED_ANALYST),
     "container": lambda d: d.get("derived"), "applicable": lambda g, d, ld: _has_analyst_actions(ld)},
    {"name": "derived.insider", "fields": list(_DERIVED_INSIDER),
     "container": lambda d: d.get("derived"), "applicable": lambda g, d, ld: _has_insider_purchases(ld)},
    {"name": "derived.valuation", "fields": list(_DERIVED_VALUATION),
     "container": lambda d: d.get("derived"),
     "applicable": lambda g, d, ld: not _is_etf(g) and _has_valuation(ld)},
    {"name": "derived.financials", "fields": list(_DERIVED_FINANCIALS),
     "container": lambda d: d.get("derived"),
     "applicable": lambda g, d, ld: not _is_etf(g) and _has_financials(d)},
]

# `funds` structurally errors for every stock (get_funds_data() raises for
# equities -- see fetch_stock_research_data.py's _fetch_funds), so it is
# not-applicable there, exactly as v1's yfinance-research-contract.md
# documents it: expected, not a failure.
LIVE_GROUP_NOT_APPLICABLE: dict[str, Callable[[dict[str, Any]], bool]] = {"funds": _is_stock}


def _field_ok(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def _add_return_metrics(trace: skill_trace.Trace, digest: dict[str, Any]) -> None:
    """Price returns, graded only against windows the history actually covers."""
    derived = digest.get("derived") or {}
    span = _price_span_days(digest)
    domain = trace.add("derived.prices")
    for name, window in _DERIVED_RETURNS.items():
        if span < window:
            domain.not_applicable.append(name)
        elif _field_ok(derived.get(name)):
            domain.ok.append(name)
        else:
            domain.missing.append(name)


def build_trace(
    digest: dict[str, Any],
    gate_result: dict[str, Any],
    ticker: str,
    live_data: dict[str, Any] | None = None,
) -> skill_trace.Trace:
    """Classify every field of this run's digest as ok / missing / N-A.

    Completeness is measured against what was *obtainable* for this subject,
    not against the full manifest: a domain that could not apply is reported
    separately and kept out of the denominator. See `src/skill_trace.py` for
    why that distinction matters.

    `live_data` is the raw live top-up, needed because the digest carries only
    per-group status -- and a group can be present but empty, which is the
    difference between "no options chain exists" and "the options fetch
    failed".
    """
    live_data = live_data or {}
    trace = skill_trace.Trace(
        skill=SKILL_NAME, subject=ticker, kind=gate_result.get("asset_class")
    )

    for spec in DOMAIN_MANIFEST:
        fields = spec["fields"]
        if not spec["applicable"](gate_result, digest, live_data):
            trace.add(spec["name"], not_applicable=fields)
            continue
        container = spec["container"](digest)
        if not container:
            # Applicable but absent: a real gap, not an expected emptiness.
            trace.add(spec["name"], missing=fields)
            continue
        domain = trace.add(spec["name"])
        for field_name in fields:
            target = domain.ok if _field_ok(container.get(field_name)) else domain.missing
            target.append(field_name)

    _add_return_metrics(trace, digest)

    live = digest.get("live") or {}
    live_domain = trace.add("live")
    if live.get("skipped"):
        # `--no-live` is a deliberate choice, not a failure to fetch.
        live_domain.not_applicable.extend(LIVE_TOP_UP_GROUPS)
    else:
        for group in LIVE_TOP_UP_GROUPS:
            status = (live.get(group) or {}).get("status")
            not_applicable = LIVE_GROUP_NOT_APPLICABLE.get(group)
            if status == "ok":
                live_domain.ok.append(group)
            elif not_applicable and not_applicable(gate_result):
                live_domain.not_applicable.append(group)
            elif status == "empty":
                # The source answered and had nothing -- no coverage exists to
                # be missing (no analyst follows this name, no insider filings).
                live_domain.not_applicable.append(group)
            else:
                live_domain.missing.append(group)

    quote = digest.get("quote")
    quote_domain = trace.add("quote")
    if quote is not None and quote.get("skipped"):
        quote_domain.not_applicable.extend(["price", "as_of"])
    elif quote is None:
        quote_domain.missing.extend(["price", "as_of"])
    else:
        for field_name in ("price", "as_of"):
            target = quote_domain.ok if _field_ok(quote.get(field_name)) else quote_domain.missing
            target.append(field_name)

    return trace


def _register_bundle_evidence(
    run_dir: Path,
    run_id: str,
    ticker: str,
    bundle_path: Path,
    trace: skill_trace.Trace | None,
) -> None:
    """Record the bundle in the run's evidence registry.

    Best-effort: a registry failure must not discard a data pull that
    succeeded. The status reflects what was actually obtained -- `partial`
    when the trace found real gaps -- so a downstream stage reading the
    manifest is told the truth about this evidence rather than assuming
    "present" means "complete".
    """
    try:
        from workspace import audit as audit_module
        from workspace import evidence as evidence_module

        status = "available"
        if trace is not None and trace.fields_missing:
            status = "partial"

        record = evidence_module.register(
            run_dir,
            run_id=run_id,
            evidence_type="market_data_bundle",
            source_name="duckdb+yfinance",
            status=status,
            artifact=bundle_path,
            retrieved_at=datetime.now().isoformat(timespec="seconds"),
            collection_method=SKILL_NAME,
            notes=[f"ticker={ticker}"],
        )
        audit_module.append_event(
            run_dir,
            run_id=run_id,
            event="evidence_registered",
            actor=SKILL_NAME,
            evidence_id=record.evidence_id,
            details={"type": record.evidence_type, "status": status, "ticker": ticker},
        )
    except Exception as exc:  # noqa: BLE001 -- never sink a successful pull
        print(f"# warning: could not register evidence for {ticker}: {exc}", file=sys.stderr)


def _cache_raw_payloads(
    run_dir: Path | None,
    ticker: str,
    db_bundle: dict[str, Any],
    live_data: dict[str, Any],
    *,
    today: date,
) -> tuple[str | None, str | None]:
    """Route the bulky raw `db`/`live` payloads to the run's `cache/` dir
    instead of letting them get embedded in the persisted `evidence/` bundle.

    This is the reason `cache/` exists at all: `db_bundle` carries the full
    `historical_records` price series plus every stored financials/earnings
    row, and `live_data` carries raw options chains and news articles when
    fetched -- exactly the kind of bulk payload that would otherwise sit in
    `workspace/runs/` at full size forever (runs are never deleted, and
    `evidence/` is never purged -- see `workspace/cache.py`'s module
    docstring). Neither payload is registered as evidence; only the small
    bundle that references them is.

    Returns `(db_cache_ref, live_cache_ref)`, both `None` when there is no
    run to cache into (`--no-run`, a non-default `--db-path`, or a
    read-only-mode invocation that never got here) -- `build_bundle` embeds
    the payloads inline in that case, unchanged from before this existed.
    """
    if run_dir is None:
        return None, None

    from workspace import cache as cache_module
    from workspace.paths import relative_to_run

    db_path = cache_module.write_payload(
        run_dir,
        f"{ticker}-{today.isoformat()}-db.json",
        json.dumps(db_resources.to_json_safe(db_bundle)).encode("utf-8"),
        source="duckdb",
    )
    db_cache_ref = relative_to_run(run_dir, db_path)

    live_cache_ref = None
    if live_data:
        live_path = cache_module.write_payload(
            run_dir,
            f"{ticker}-{today.isoformat()}-live.json",
            json.dumps(db_resources.to_json_safe(live_data)).encode("utf-8"),
            source="yfinance",
        )
        live_cache_ref = relative_to_run(run_dir, live_path)

    return db_cache_ref, live_cache_ref


def process_ticker(
    ticker: str,
    gate_result: dict[str, Any],
    refresh_summary: dict[str, Any],
    db_path: Path,
    *,
    no_live: bool,
    no_bundle: bool,
    no_trace: bool,
    no_quote: bool,
    output: Path | None,
    classification_json: Path,
    today: date,
    pretty: bool,
    run_dir: Path | None = None,
    run_id: str | None = None,
    trace_log_path: Path | None = None,
    workspace_status: str | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    if not gate_result.get("resolved"):
        digest = {
            "schema": SCHEMA, "ticker": ticker, "resolved": False,
            "subject": gate_result.get("subject"), "gaps": gate_result.get("gaps", []),
        }
        return {"digest": digest, "bundle_path": None}

    quote: dict[str, Any] | None = None
    if not no_quote:
        quote = _fetch_latest_quote(gate_result.get("provider_symbol"))

    connection = db_resources.connect_read_only(db_path)
    try:
        db_bundle = db_resources.read_ticker_bundle(
            connection, gate_result["ticker_id"], gate_result["ticker"],
            classification_json=classification_json, live_quote=quote,
        )
    finally:
        connection.close()

    live_data: dict[str, Any] = {}
    live_errors: dict[str, str] = {}
    if not no_live:
        live_data, live_errors = _live_top_up(ticker, gate_result.get("provider_symbol"))

    derived = {
        **derived_metrics.compute_live_metrics(live_data, quote=quote),
        **derived_metrics.compute_db_metrics(db_bundle, quote=quote),
    }

    digest = build_digest(
        ticker, gate_result, refresh_summary, db_bundle, live_data, live_errors, derived, quote,
        no_live=no_live, no_quote=no_quote, today=today,
    )

    trace: skill_trace.Trace | None = None
    if not no_trace:
        trace = build_trace(digest, gate_result, ticker, live_data)
        # Set before either `to_dict()` call -- the digest's copy of the trace
        # must carry the workspace outcome too, not just the log line.
        if workspace_status is not None:
            trace.workspace = skill_trace.WorkspaceOutcome(
                status=workspace_status, run_id=run_id, skip_reason=skip_reason
            )
        digest["trace"] = trace.to_dict()
        skill_trace.emit(
            trace,
            run_dir=run_dir,
            run_id=run_id,
            text_log_path=trace_log_path,
            jsonl_log_path=trace_log_path.with_suffix(".jsonl") if trace_log_path else None,
        )

    bundle_path = None
    if not no_bundle:
        db_cache_ref, live_cache_ref = _cache_raw_payloads(
            run_dir, ticker, db_bundle, live_data, today=today
        )
        bundle = build_bundle(
            ticker, gate_result, refresh_summary, db_bundle, live_data, live_errors, derived, quote,
            no_quote=no_quote, today=today,
            db_cache_ref=db_cache_ref, live_cache_ref=live_cache_ref,
        )
        if not no_trace:
            bundle["trace"] = digest["trace"]
        # Inside a run the bundle is that run's evidence, so it lands in the
        # run directory and gets registered; outside one, nothing changes.
        destination = output
        if destination is None and run_dir is not None:
            destination = run_dir / "evidence" / f"{ticker}-{today.isoformat()}-resources.json"
        bundle_path = _write_bundle(ticker, bundle, destination, today, pretty)
        if run_dir is not None and run_id is not None:
            _register_bundle_evidence(run_dir, run_id, ticker, bundle_path, trace)
    return {"digest": digest, "bundle_path": str(bundle_path) if bundle_path else None}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DB-first, live-top-up investment research resource bundle for one or more tickers."
    )
    parser.add_argument("--ticker", nargs="+", required=True, help="Pipeline ticker symbol(s).")
    parser.add_argument(
        "--mode", choices=["gate", "refresh", "read"], default=None,
        help="Run only one phase (for fan-out orchestration). Omit for the default gate->refresh->read sequence.",
    )
    parser.add_argument("--no-refresh", action="store_true", help="Read as-is; do not refresh stale domains.")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cadences; refresh every refreshable domain.")
    parser.add_argument(
        "--register-wishlist", action="store_true",
        help="Register any unregistered ticker as a wishlist research candidate "
             "(security_status) before proceeding, instead of failing with a "
             "gap naming the manual `database status` command. Only takes "
             "effect during the write/refresh phase -- has no effect with "
             "--mode gate/read.",
    )
    parser.add_argument(
        "--wishlist-rationale", default=None,
        help="Rationale stored alongside a --register-wishlist registration.",
    )
    parser.add_argument("--no-live", action="store_true", help="Skip the live yfinance top-up; DB-only bundle.")
    parser.add_argument(
        "--no-quote", action="store_true",
        help="Skip the live current-price pull; value/weight/returns fall back to the last stored DB close. "
        "Independent of --no-live -- omit this to keep the quote even in a --no-live run.",
    )
    parser.add_argument("--no-bundle", action="store_true", help="Print the digest only; write no bundle file.")
    parser.add_argument(
        "--no-trace", action="store_true",
        help="Skip the completeness trace (no digest 'trace' key, no log line written).",
    )
    parser.add_argument("--output", type=Path, help="Bundle output path (single-ticker runs only).")
    parser.add_argument("--db-path", type=Path, default=DATABASE_PATH, help="Override the DuckDB path (testing).")
    parser.add_argument(
        "--classification-json", type=Path, default=db_resources.DEFAULT_CLASSIFICATION_JSON,
        help="Override the classification export path (testing).",
    )
    parser.add_argument(
        "--run-id",
        help="Attach to this run workspace by name, creating it if it does not exist. "
             "A run is opened by default for any evidence-producing pull (--mode read or "
             "the default gate->refresh->read sequence) even without this flag -- pass it "
             "to name the run explicitly, e.g. to group several tickers into one run. "
             "See --no-run to opt out instead.",
    )
    parser.add_argument(
        "--no-run", action="store_true",
        help="Do not attach to or create a run workspace; write the bundle to exports/ "
             "instead, as before this became the default. The opt-out is still recorded "
             "in the completeness trace (workspace.skip_reason), so it stays auditable "
             "rather than silent.",
    )
    parser.add_argument(
        "--trace-log-path", type=Path, default=None,
        help="Override the shared trace log path (testing). The .jsonl sidecar follows it.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args(argv)
    if args.output and len(args.ticker) > 1:
        parser.error("--output requires exactly one --ticker")
    if args.no_run and args.run_id:
        parser.error("--no-run and --run-id are contradictory: --run-id asks to attach to a run")
    if args.register_wishlist and args.mode in ("gate", "read"):
        parser.error(
            "--register-wishlist has no effect with --mode gate/read -- registration only "
            "happens during the write/refresh phase (the default sequence or --mode refresh)"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return _dispatch(args)
    except db_resources.DatabaseNotReady as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _resolve_run(
    args: argparse.Namespace, tickers: list[str], *, produces_evidence: bool
) -> tuple[Path | None, str | None, str, str | None]:
    """Attach this pull to a run workspace, creating one if it does not exist.

    Returns `(run_dir, resolved_run_id, workspace_status, skip_reason)`.
    `workspace_status` is one of `"created"` / `"attached"` / `"skipped"`;
    `run_dir`/`resolved_run_id` are None exactly when it is `"skipped"`.

    Attaching is **default-on** for any invocation that actually produces
    evidence (`--mode read` or the default gate->refresh->read sequence).
    Before this, a run only existed if whoever composed the command line
    remembered to pass `--run-id` -- a judgment call an agent could skip on
    any given invocation, and did: the old `SKILL.md` even said "omit for the
    unchanged exports/ behavior." An audit trail cannot tolerate that: an
    absent run looked identical to "no pull happened." Flipping the default
    puts the decision inside the code, the same way the completeness trace
    already does (`if not no_trace`) rather than leaving it to the caller.

    Three things still skip the workspace, and each records *why* rather than
    silently doing nothing:
      - `--mode gate` / `--mode refresh` -- neither produces an evidence
        artifact, so there is nothing to register.
      - `--no-run` -- an explicit, recorded opt-out.
      - a non-default `--db-path` -- the existing test/debug convention (see
        the sibling market-analyst-resources skill's `is_default_db`), so the
        test suite does not pollute `workspace/runs/`.
    A skip is written into the trace's `workspace` field either way, so it is
    a positive, greppable record in `logs/SkillTrace.jsonl` -- never a silent
    gap indistinguishable from "nothing ran."

    Creating on miss (an explicit `--run-id` that does not exist yet, or the
    implicit default-on path) is what removes the `run create` ceremony. Its
    cost: a mistyped `--run-id` becomes a new empty run rather than an error,
    so creation always prints `note: created run workspace <id>` to stderr --
    an unfamiliar ID scrolling past is the only cue a typo happened rather
    than an attach. Never fall back to `exports/` once a run is decided: a
    quietly unregistered file is the untracked handoff the workspace exists
    to eliminate.
    """
    if not produces_evidence:
        return None, None, "skipped", "read-only mode" if args.mode == "gate" else "no evidence produced"
    if args.no_run:
        return None, None, "skipped", "--no-run"
    if args.db_path != DATABASE_PATH:
        return None, None, "skipped", "non-default-db"

    from workspace import run as run_module

    resolved, directory, created = run_module.ensure_run(
        args.run_id,
        mode="data_pull",
        subject_type="security",
        # Only name a subject when there is exactly one -- a multi-ticker pull
        # has no single subject, and picking the first would misdescribe it.
        ticker=tickers[0] if len(tickers) == 1 else None,
        question=(
            f"Ad-hoc data pull for {', '.join(tickers)}; "
            "no research question was recorded."
        ),
        trigger=SKILL_NAME,
    )
    if created:
        print(f"note: created run workspace {resolved}", file=sys.stderr)
    return directory, resolved, ("created" if created else "attached"), None


def _dispatch(args: argparse.Namespace) -> int:
    today = date.today()
    tickers = [t.upper() for t in args.ticker]
    produces_evidence = args.mode in (None, "read")
    run_dir, run_id, workspace_status, skip_reason = _resolve_run(
        args, tickers, produces_evidence=produces_evidence
    )

    if args.mode == "gate":
        results = _run_gate(tickers, args.db_path, force=args.force_refresh, run_date=today)
        print(json.dumps(db_resources.to_json_safe(results), indent=2))
        return 0

    if args.mode == "refresh":
        gate_results = _run_gate(tickers, args.db_path, force=args.force_refresh, run_date=today)
        if args.register_wishlist and _register_wishlist_candidates(
            gate_results, args.db_path, args.wishlist_rationale
        ):
            gate_results = _run_gate(tickers, args.db_path, force=args.force_refresh, run_date=today)
        summary = _run_refresh(gate_results, args.db_path)
        print(json.dumps(db_resources.to_json_safe(summary), indent=2))
        return 0

    if args.mode == "read":
        gate_results = _run_gate(tickers, args.db_path, force=False, run_date=today)
        outputs = [
            process_ticker(
                ticker, gate_result, {}, args.db_path,
                no_live=args.no_live, no_bundle=args.no_bundle, no_trace=args.no_trace, no_quote=args.no_quote,
                output=args.output, classification_json=args.classification_json,
                trace_log_path=args.trace_log_path, today=today, pretty=args.pretty,
                run_dir=run_dir, run_id=run_id,
                workspace_status=workspace_status, skip_reason=skip_reason,
            )
            for ticker, gate_result in zip(tickers, gate_results)
        ]
        for output in outputs:
            print(json.dumps(db_resources.to_json_safe(output["digest"]), indent=2 if args.pretty else None))
            if output["bundle_path"]:
                print(f"# bundle: {output['bundle_path']}", file=sys.stderr)
        return 0

    # Default: gate -> refresh -> read, one process, single-agent use.
    gate_results = _run_gate(tickers, args.db_path, force=args.force_refresh, run_date=today)
    if args.register_wishlist and _register_wishlist_candidates(
        gate_results, args.db_path, args.wishlist_rationale
    ):
        gate_results = _run_gate(tickers, args.db_path, force=args.force_refresh, run_date=today)
    refresh_by_ticker: dict[str, dict[str, Any]] = {t: {} for t in tickers}
    if not args.no_refresh:
        summary = _run_refresh(gate_results, args.db_path)
        for gate_result in gate_results:
            if gate_result.get("resolved"):
                refresh_by_ticker[gate_result["ticker"]] = {
                    domain: summary[domain] for domain in gate_result.get("due_domains", []) if domain in summary
                }
        # Re-resolve freshness against the just-refreshed DB so the digest
        # reflects post-refresh state, not the pre-refresh staleness snapshot.
        gate_results = _run_gate(tickers, args.db_path, force=False, run_date=today)

    exit_code = 0
    for ticker, gate_result in zip(tickers, gate_results):
        output = process_ticker(
            ticker, gate_result, refresh_by_ticker.get(ticker, {}), args.db_path,
            no_live=args.no_live, no_bundle=args.no_bundle, no_trace=args.no_trace, no_quote=args.no_quote,
            output=args.output, classification_json=args.classification_json,
            trace_log_path=args.trace_log_path, today=today, pretty=args.pretty,
            run_dir=run_dir, run_id=run_id,
            workspace_status=workspace_status, skip_reason=skip_reason,
        )
        digest_text = json.dumps(db_resources.to_json_safe(output["digest"]), indent=2 if args.pretty else None)
        print(digest_text)
        if output["bundle_path"]:
            print(f"# bundle: {output['bundle_path']}", file=sys.stderr)
        if len(digest_text.encode("utf-8")) > DIGEST_SOFT_LIMIT_BYTES:
            print(
                f"# warning: digest for {ticker} exceeded the {DIGEST_SOFT_LIMIT_BYTES}-byte soft limit",
                file=sys.stderr,
            )
        if not gate_result.get("resolved"):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

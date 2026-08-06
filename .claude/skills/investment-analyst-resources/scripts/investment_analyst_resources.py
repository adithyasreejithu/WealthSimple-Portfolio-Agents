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
from config import DATABASE_PATH  # noqa: E402
from fetch_stock_research_data import fetch_stock_research_data  # noqa: E402
from yfinance_extractor import _build_session, _create_ticker, _require_yfinance  # noqa: E402

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
DEFAULT_TRACE_LOG_PATH = ROOT / "logs" / "InvestmentAnalystResourcesTrace.txt"
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
    return freshness_gate.refresh_domains(due_by_domain, db_path)


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

    gaps = list(gate_result.get("gaps", []))
    if position is None:
        gaps.append("no position_snapshots row -- ticker not currently held")
    if portfolio_context is None:
        gaps.append(
            "ticker not currently held and no portfolio-classification.json entry -- role/weight/value unavailable"
        )
    if not prices.get("count"):
        gaps.append("no historical_records rows")

    domain_verdicts = {
        name: {"stale": info.get("stale"), "last": info.get("last")}
        for name, info in (gate_result.get("domains") or {}).items()
    }

    return {
        "schema": SCHEMA,
        "ticker": ticker,
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
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ticker": ticker,
        "provider_symbol": gate_result.get("provider_symbol"),
        "asset_class": gate_result.get("asset_class"),
        "as_of": today.isoformat(),
        "earnings_window": gate_result.get("earnings_window", False),
        "freshness": gate_result.get("domains"),
        "refreshed": refresh_summary,
        "db": db_bundle,
        "live": live_data,
        "derived": derived,
        "quote": {"skipped": True} if no_quote else quote,
        "errors": live_errors,
        "gaps": gate_result.get("gaps", []),
    }


def _write_bundle(ticker: str, bundle: dict[str, Any], output: Path | None, today: date, pretty: bool) -> Path:
    path = output or (DEFAULT_OUTPUT_DIR / f"{ticker}-{today.isoformat()}-resources.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(db_resources.to_json_safe(bundle), indent=2 if pretty else None)
    path.write_text(text, encoding="utf-8")
    return path


# --- completeness trace ------------------------------------------------

def _owned(gate_result: dict[str, Any]) -> bool:
    return bool(gate_result.get("owned"))


def _is_etf(gate_result: dict[str, Any]) -> bool:
    return gate_result.get("asset_class") == "etf"


def _is_stock(gate_result: dict[str, Any]) -> bool:
    return gate_result.get("asset_class") == "stock"


# Field-level manifest, one entry per digest domain. `container` extracts
# the sub-dict to check fields against (None means "domain absent this
# run"); `empty_expected` tells the difference between an absent domain
# that's normal (an ETF's financials, a watchlist ticker's position) and one
# that's a genuine gap -- generalizing `scoring_worksheet.py`'s
# `_groups_ok_count` emptiness rule from "live groups only" to every domain.
DOMAIN_MANIFEST: list[dict[str, Any]] = [
    {"name": "position", "fields": ["quantity", "book_value_cad", "realized_gain_cad", "computed_at"],
     "container": lambda d: d.get("position"), "empty_expected": lambda g: not _owned(g)},
    {"name": "ledger_summary",
     "fields": ["first_purchase_date", "latest_purchase_date", "number_of_buys", "number_of_sells"],
     "container": lambda d: d.get("ledger_summary"), "empty_expected": lambda g: not _owned(g)},
    {"name": "portfolio_context",
     "fields": ["role", "weight_pct", "position_market_value", "cost_basis_cad", "unrealized_gain_cad", "account_type"],
     "container": lambda d: d.get("portfolio_context"), "empty_expected": lambda g: not _owned(g)},
    {"name": "prices", "fields": ["latest_close", "period_return_pct", "week52_low", "week52_high"],
     "container": lambda d: d.get("prices") if (d.get("prices") or {}).get("count") else None,
     "empty_expected": lambda g: not _owned(g)},
    {"name": "financials", "fields": ["latest_period_end", "latest_revenue", "latest_net_income", "latest_eps"],
     "container": lambda d: d.get("financials") if (d.get("financials") or {}).get("periods") else None,
     "empty_expected": lambda g: not _owned(g) or _is_etf(g)},
    {"name": "earnings", "fields": ["last_reported"],
     "container": lambda d: d.get("earnings") if (d.get("earnings") or {}).get("events") else None,
     "empty_expected": lambda g: not _owned(g) or _is_etf(g)},
    {"name": "dividends", "fields": ["declared_count", "total_received_cad"],
     "container": lambda d: d.get("dividends"), "empty_expected": lambda g: False},
    {"name": "classification", "fields": ["primary_group", "confidence", "generated_at"],
     "container": lambda d: d.get("classification"), "empty_expected": lambda g: not _owned(g)},
    {"name": "stock_details", "fields": ["sector", "industry"],
     "container": lambda d: d.get("stock_details"), "empty_expected": lambda g: not _owned(g) or _is_etf(g)},
    {"name": "etf_details", "fields": ["fund_family", "yield", "expense_ratio", "aum", "nav"],
     "container": lambda d: d.get("etf_details"), "empty_expected": lambda g: not _owned(g) or _is_stock(g)},
    {"name": "derived", "fields": list(derived_metrics.LIVE_METRICS) + list(derived_metrics.DB_METRICS),
     "container": lambda d: d.get("derived"), "empty_expected": lambda g: False},
]

# `funds` structurally errors for every stock (get_funds_data() raises for
# equities -- see fetch_stock_research_data.py's _fetch_funds), so it is
# excluded from the denominator for stocks the same way v1's
# yfinance-research-contract.md documents it as expected, not a failure.
LIVE_GROUP_EMPTY_EXPECTED: dict[str, Callable[[dict[str, Any]], bool]] = {"funds": _is_stock}


def _field_ok(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def compute_completeness_trace(digest: dict[str, Any], gate_result: dict[str, Any]) -> dict[str, Any]:
    """Field- and domain-level completeness for this run's digest.

    Two levels: domain-level ok/empty-expected/failed (was this section of
    the bundle even attempted and, if not, was that normal for this
    ticker's asset class/ownership), and field-level counts within each
    attempted domain (the literal "how many fields were successfully
    populated" count). An empty-expected domain is excluded from the
    denominator entirely so it never drags down a healthy run's score.
    """
    fields_ok = 0
    fields_total = 0
    domains_ok = 0
    domains_empty = 0
    domains_failed = 0
    missing: list[str] = []

    for spec in DOMAIN_MANIFEST:
        container = spec["container"](digest)
        if not container:
            if spec["empty_expected"](gate_result):
                domains_empty += 1
            else:
                domains_failed += 1
                fields_total += len(spec["fields"])
                missing.extend(f"{spec['name']}.{field}" for field in spec["fields"])
            continue
        domains_ok += 1
        for field in spec["fields"]:
            fields_total += 1
            if _field_ok(container.get(field)):
                fields_ok += 1
            else:
                missing.append(f"{spec['name']}.{field}")

    live = digest.get("live") or {}
    if not live.get("skipped"):
        for group in LIVE_TOP_UP_GROUPS:
            status = (live.get(group) or {}).get("status")
            if status == "ok":
                domains_ok += 1
                fields_total += 1
                fields_ok += 1
            elif status == "empty":
                domains_empty += 1
            else:
                empty_expected = LIVE_GROUP_EMPTY_EXPECTED.get(group)
                if empty_expected and empty_expected(gate_result):
                    domains_empty += 1
                else:
                    domains_failed += 1
                    fields_total += 1
                    missing.append(f"live.{group}")

    # Mirrors the `live.get("skipped")` short-circuit above: `--no-quote`
    # excludes the domain entirely (not even "empty"), rather than folding
    # it into DOMAIN_MANIFEST's generic loop, which has no concept of a
    # deliberately-skipped fetch distinct from a genuinely absent one.
    quote = digest.get("quote")
    if quote is not None and not quote.get("skipped"):
        domains_ok += 1
        for field in ("price", "as_of"):
            fields_total += 1
            if _field_ok(quote.get(field)):
                fields_ok += 1
            else:
                missing.append(f"quote.{field}")
    elif quote is None:
        domains_failed += 1
        fields_total += 2
        missing.extend(["quote.price", "quote.as_of"])

    completeness_pct = (fields_ok / fields_total * 100) if fields_total else 100.0
    return {
        "fields_ok": fields_ok,
        "fields_total": fields_total,
        "completeness_pct": round(completeness_pct, 1),
        "domains_ok": domains_ok,
        "domains_empty": domains_empty,
        "domains_failed": domains_failed,
        "missing_fields": missing,
    }


def write_trace_log(ticker: str, gate_result: dict[str, Any], trace: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    missing = ",".join(trace["missing_fields"]) or "-"
    line = (
        f"{timestamp} | TRACE | ticker={ticker} | asset_class={gate_result.get('asset_class')} | "
        f"fields_ok={trace['fields_ok']} | fields_total={trace['fields_total']} | "
        f"completeness_pct={trace['completeness_pct']} | domains_ok={trace['domains_ok']} | "
        f"domains_empty={trace['domains_empty']} | domains_failed={trace['domains_failed']} | "
        f"missing={missing}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


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
    trace_log_path: Path,
    today: date,
    pretty: bool,
) -> dict[str, Any]:
    if not gate_result.get("resolved"):
        digest = {"schema": SCHEMA, "ticker": ticker, "resolved": False, "gaps": gate_result.get("gaps", [])}
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

    if not no_trace:
        trace = compute_completeness_trace(digest, gate_result)
        digest["trace"] = trace
        write_trace_log(ticker, gate_result, trace, trace_log_path)

    bundle_path = None
    if not no_bundle:
        bundle = build_bundle(
            ticker, gate_result, refresh_summary, db_bundle, live_data, live_errors, derived, quote,
            no_quote=no_quote, today=today,
        )
        if not no_trace:
            bundle["trace"] = digest["trace"]
        bundle_path = _write_bundle(ticker, bundle, output, today, pretty)
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
        "--trace-log-path", type=Path, default=DEFAULT_TRACE_LOG_PATH,
        help="Override the completeness-trace log path (testing).",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    args = parser.parse_args(argv)
    if args.output and len(args.ticker) > 1:
        parser.error("--output requires exactly one --ticker")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return _dispatch(args)
    except db_resources.DatabaseNotReady as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    today = date.today()
    tickers = [t.upper() for t in args.ticker]

    if args.mode == "gate":
        results = _run_gate(tickers, args.db_path, force=args.force_refresh, run_date=today)
        print(json.dumps(db_resources.to_json_safe(results), indent=2))
        return 0

    if args.mode == "refresh":
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

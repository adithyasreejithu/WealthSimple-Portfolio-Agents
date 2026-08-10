"""Orchestrator for the security-technicals skill.

Reads stored prices via the `read-security-price-history` dependency skill,
computes technicals via `src/security_technicals.py`, and writes the result as
a **run-workspace calculation artifact** with an evidence entry, an audit
event, and a completeness trace.

Three deliberate choices, each with a reason worth keeping:

- **The artifact goes to `calculations/`, not `evidence/`.** `evidence/` means
  a fact obtained from outside the process, carrying provenance. These numbers
  are arithmetic over data the run already holds. `workspace/paths.py` has
  created a `calculations/` directory in every run since the workspace was
  built and `workspace/manifest.py` already surfaces it as `calculation_paths`,
  but nothing wrote there until now -- this is its first producer.

- **The math is imported from `src/`, not vendored here.** Skills import `src/`
  freely; `src/` never imports `.claude/`. Keeping the pure functions in
  `src/security_technicals.py` leaves a future non-skill consumer (the Phase 3
  worksheet builder, a dashboard route) able to call them directly without a
  backwards dependency. See docs/plans/implementation/phase-2/design-decisions.md.

- **Insufficient history is `not_applicable`, never `missing`.** A 60-day-old
  holding cannot have an SMA-200; grading that as a gap would report a healthy
  run as incomplete and bury the gaps that matter. `src/skill_trace.py`'s
  docstring documents this exact failure mode -- only `ok + missing` forms the
  completeness denominator.

- **The technicals never fetch, but an optional live quote does.** SMA,
  drawdown, volatility, relative strength, and beta/alpha are all computed
  from stored `historical_records` only -- staleness there is a gap to
  report, not a reason to reach for yfinance. But the artifact's `latest_close`
  is therefore only as fresh as the last pipeline ingestion, which can be a
  stale "current price" if an agent invokes this mid-day. `_fetch_latest_quote`
  closes that one gap with the same lightweight `fast_info` pull
  `investment_analyst_resources._fetch_latest_quote` already uses, attached as
  a clearly separate `quote` field so it is never confused with the
  history-derived technicals. Opt out with `--no-quote`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "read-security-price-history" / "scripts"))

import read_price_history as price_reader  # noqa: E402
import security_technicals  # noqa: E402
import skill_trace  # noqa: E402
from config import DATABASE_PATH, DEFAULT_BENCHMARK_SYMBOL  # noqa: E402
from yfinance_extractor import _build_session, _create_ticker, _require_yfinance  # noqa: E402

SKILL_NAME = "security-technicals"
SCHEMA = "security-technicals.v1"

DEFAULT_OUTPUT_DIR = ROOT / "exports" / "security-technicals"

# Every metric the artifact reports, and the path into the computed result that
# decides whether it was obtainable. Declared once so the trace, the gaps list,
# and the digest cannot drift apart the way three hand-maintained lists would.
_TECHNICAL_FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("sma_50d", "moving_averages", "sma_50d"),
    ("sma_200d", "moving_averages", "sma_200d"),
    ("max_drawdown", "max_drawdown", "max_drawdown"),
    ("volatility", "volatility", "volatility"),
    ("relative_strength", "relative_strength", "excess_return"),
    ("beta", "beta_alpha", "beta"),
    ("alpha", "beta_alpha", "alpha"),
)


def _metric_value(technicals: dict[str, Any], section: str, key: str | None) -> Any:
    """Pull one reported metric out of the computed result.

    `beta_alpha` is `None` in full (not a dict of `None`s) below the minimum
    overlap, because `calculate_benchmark_stats` returns `None` rather than
    statistics it cannot stand behind -- so the section itself must be checked
    before indexing into it.
    """
    block = technicals.get(section)
    if block is None:
        return None
    if key is None:
        return block
    return block.get(key)


def _price_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Coverage facts only -- never the series itself.

    The artifact stays a few hundred bytes so the digest and the artifact can
    carry identical content, which is what lets the SKILL.md tell agents to
    read the digest and never open the file.
    """
    if not rows:
        return {"count": 0, "start": None, "end": None, "latest_close": None}
    return {
        "count": len(rows),
        "start": rows[0]["record_date"],
        "end": rows[-1]["record_date"],
        "latest_close": float(rows[-1]["close"]) if rows[-1].get("close") is not None else None,
    }


def _fetch_latest_quote(provider_symbol: str | None) -> dict[str, Any] | None:
    """A lightweight "current price" pull via yfinance's `fast_info`.

    Mirrors `investment_analyst_resources._fetch_latest_quote` -- same
    rationale (stored history is only as fresh as the last pipeline run;
    this closes that one gap with a single cheap call), same discipline
    (never raises; a quote failure is a gap, never fatal to the technicals
    that already computed). Trimmed to the fields this skill's digest
    actually reports; day/year range is `investment-analyst-resources`
    territory, not a price-technicals concern.
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
        }
    except Exception:  # a quote failure is a gap, never fatal to the run
        return None


def _quote_summary(quote: dict[str, Any] | None, no_quote: bool) -> dict[str, Any] | None:
    """The artifact's `quote` field: `{"skipped": True}` when opted out,
    `None` when a fetch was attempted and failed, otherwise the quote."""
    if no_quote:
        return {"skipped": True}
    return quote


def build_trace(
    ticker: str,
    rows: list[dict[str, Any]],
    benchmark_symbol: str,
    benchmark_rows: list[dict[str, Any]],
    technicals: dict[str, Any],
    quote: dict[str, Any] | None = None,
    no_quote: bool = True,
) -> skill_trace.Trace:
    """Grade this run's completeness.

    `prices`/`benchmark` are genuinely gradeable -- the history either is or is
    not stored, and its absence is a real gap. `technicals` is the opposite:
    every metric that failed did so because the history it needs does not
    exist, which is `not_applicable`, not `missing`. Nothing in this domain is
    ever graded `missing`, and that asymmetry is the whole point.

    `quote` draws a third distinction: skipped-by-request (`--no-quote`) is
    `not_applicable` -- nothing was ever attempted -- while a fetch that ran
    and came back empty (no verified provider mapping, or yfinance failed) is
    a real `missing`, because the quote was genuinely obtainable in principle.
    `no_quote` defaults `True` here (not the CLI's own default) so callers
    that build a trace directly -- every existing test in this module -- keep
    grading the quote as not-attempted unless they pass live quote data.
    """
    trace = skill_trace.Trace(skill=SKILL_NAME, subject=ticker, kind="security")

    trace.add("prices", ok=["history"] if rows else [], missing=[] if rows else ["history"])
    trace.add(
        "benchmark",
        ok=["history"] if benchmark_rows else [],
        missing=[] if benchmark_rows else [f"history:{benchmark_symbol}"],
    )

    computed, unavailable = [], []
    for name, section, key in _TECHNICAL_FIELDS:
        (computed if _metric_value(technicals, section, key) is not None else unavailable).append(name)
    trace.add("technicals", ok=computed, not_applicable=unavailable)

    quote_fields = ("price", "as_of")
    if no_quote:
        trace.add("quote", not_applicable=list(quote_fields))
    elif quote is None:
        trace.add("quote", missing=list(quote_fields))
    else:
        trace.add(
            "quote",
            ok=[f for f in quote_fields if quote.get(f) is not None],
            missing=[f for f in quote_fields if quote.get(f) is None],
        )
    return trace


def build_result(
    ticker: str,
    identity: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    benchmark_symbol: str,
    benchmark_rows: list[dict[str, Any]],
    today: date,
    quote: dict[str, Any] | None = None,
    no_quote: bool = True,
) -> dict[str, Any]:
    """Compute and assemble one ticker's artifact payload.

    An unresolvable ticker short-circuits to a `resolved: false` payload with
    one gap: nothing was obtainable, so nothing is computed and nothing is
    graded. That is a different state from "resolved but no history", which
    does grade `prices` as missing.
    """
    if identity is None:
        return {
            "schema": SCHEMA,
            "ticker": ticker,
            "as_of": today.isoformat(),
            "resolved": False,
            "gaps": [f"{ticker} is not in the tickers table"],
        }

    technicals = security_technicals.compute_security_technicals(rows, benchmark_rows)

    gaps: list[str] = []
    if not rows:
        gaps.append(f"no stored price history for {ticker}")
    if not benchmark_rows:
        gaps.append(f"no stored price history for benchmark {benchmark_symbol}")
    for name, section, key in _TECHNICAL_FIELDS:
        if _metric_value(technicals, section, key) is None:
            gaps.append(f"{name}: insufficient history")
    if not no_quote and quote is None:
        gaps.append("current-price quote unavailable")

    return {
        "schema": SCHEMA,
        "ticker": ticker,
        "as_of": today.isoformat(),
        "resolved": True,
        "security_name": identity.get("security_name"),
        "currency": identity.get("currency"),
        "benchmark": benchmark_symbol,
        "prices": _price_summary(rows),
        "benchmark_prices": _price_summary(benchmark_rows),
        "technicals": technicals,
        "quote": _quote_summary(quote, no_quote),
        "gaps": gaps,
    }


def _write_artifact(payload: dict[str, Any], destination: Path, pretty: bool) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(price_reader.to_json_safe(payload), indent=2 if pretty else None),
        encoding="utf-8",
    )
    return destination


def _register_calculation_evidence(
    run_dir: Path,
    run_id: str,
    ticker: str,
    artifact_path: Path,
    trace: skill_trace.Trace | None,
    resolved: bool,
) -> None:
    """Record the calculation in the run's evidence registry.

    Registered as `derived_calculation` rather than a data-bundle type: the
    artifact lives in `calculations/`, and the registry entry exists to give it
    a content hash and provenance, not to claim it came from outside.

    Best-effort -- a registry failure must not discard a calculation that
    succeeded, matching `investment_analyst_resources._register_bundle_evidence`.
    """
    try:
        from workspace import audit as audit_module
        from workspace import evidence as evidence_module

        status = "available"
        if not resolved:
            status = "missing"
        elif trace is not None and trace.fields_missing:
            status = "partial"

        record = evidence_module.register(
            run_dir,
            run_id=run_id,
            evidence_type="derived_calculation",
            source_name="duckdb",
            status=status,
            artifact=artifact_path,
            retrieved_at=datetime.now().isoformat(timespec="seconds"),
            collection_method=SKILL_NAME,
            notes=[f"ticker={ticker}"],
        )
        audit_module.append_event(
            run_dir,
            run_id=run_id,
            event="calculation_written",
            actor=SKILL_NAME,
            evidence_id=record.evidence_id,
            details={"type": record.evidence_type, "status": status, "ticker": ticker},
        )
    except Exception as exc:  # noqa: BLE001 -- never sink a successful calculation
        print(f"# warning: could not register calculation for {ticker}: {exc}", file=sys.stderr)


def process_ticker(
    ticker: str,
    connection: Any,
    benchmark_symbol: str,
    benchmark_rows: list[dict[str, Any]],
    *,
    today: date,
    no_trace: bool,
    no_quote: bool,
    output: Path | None,
    pretty: bool,
    trace_log_path: Path | None,
    run_dir: Path | None,
    run_id: str | None,
    workspace_status: str | None,
    skip_reason: str | None,
) -> dict[str, Any]:
    identity = price_reader.resolve_ticker(connection, ticker)
    rows = price_reader.read_security_prices(connection, identity["ticker_id"]) if identity else []

    quote: dict[str, Any] | None = None
    if identity is not None and not no_quote:
        provider_symbol = price_reader.resolve_provider_symbol(connection, identity["ticker_id"])
        quote = _fetch_latest_quote(provider_symbol)

    payload = build_result(ticker, identity, rows, benchmark_symbol, benchmark_rows, today, quote, no_quote)

    trace: skill_trace.Trace | None = None
    if not no_trace and identity is not None:
        trace = build_trace(ticker, rows, benchmark_symbol, benchmark_rows, payload["technicals"], quote, no_quote)
        if workspace_status is not None:
            trace.workspace = skill_trace.WorkspaceOutcome(
                status=workspace_status, run_id=run_id, skip_reason=skip_reason
            )
        payload["trace"] = trace.to_dict()
        skill_trace.emit(
            trace,
            run_dir=run_dir,
            run_id=run_id,
            text_log_path=trace_log_path,
            jsonl_log_path=trace_log_path.with_suffix(".jsonl") if trace_log_path else None,
        )

    destination = output
    if destination is None:
        filename = f"security-technicals-{ticker}-{today.isoformat()}.json"
        # Inside a run the artifact is that run's calculation output, so it
        # lands in `calculations/`; outside one it falls back to exports/.
        destination = (
            run_dir / "calculations" / filename if run_dir is not None else DEFAULT_OUTPUT_DIR / filename
        )
    artifact_path = _write_artifact(payload, destination, pretty)

    if run_dir is not None and run_id is not None:
        _register_calculation_evidence(
            run_dir, run_id, ticker, artifact_path, trace, payload.get("resolved", False)
        )

    return {"payload": payload, "artifact_path": artifact_path}


def _resolve_run(
    args: argparse.Namespace, tickers: list[str]
) -> tuple[Path | None, str | None, str, str | None]:
    """Attach to a run workspace, creating one if it does not exist.

    Default-on, matching `investment-analyst-resources`: an artifact that is
    not in a run is an untracked handoff, which is exactly what the workspace
    exists to eliminate. Two things skip, and each records why rather than
    silently doing nothing -- an explicit `--no-run`, and a non-default
    `--db-path` (the existing test/debug convention, so the suite never
    populates the real `workspace/runs/`).
    """
    if args.no_run:
        return None, None, "skipped", "--no-run"
    if args.db_path != DATABASE_PATH:
        return None, None, "skipped", "non-default-db"

    from workspace import run as run_module

    resolved, directory, created = run_module.ensure_run(
        args.run_id,
        mode="technicals",
        subject_type="security",
        # Only name a subject when there is exactly one -- a multi-ticker run
        # has no single subject, and picking the first would misdescribe it.
        ticker=tickers[0] if len(tickers) == 1 else None,
        question=(
            f"Price technicals for {', '.join(tickers)}; "
            "no research question was recorded."
        ),
        trigger=SKILL_NAME,
    )
    if created:
        print(f"note: created run workspace {resolved}", file=sys.stderr)
    return directory, resolved, ("created" if created else "attached"), None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Per-security price technicals from stored DuckDB history. Technicals are read-only; "
                     "an optional live current-price quote may be fetched (see --no-quote)."
    )
    parser.add_argument("--ticker", nargs="+", required=True, help="Pipeline ticker symbol(s).")
    parser.add_argument(
        "--benchmark", default=DEFAULT_BENCHMARK_SYMBOL,
        help=f"Comparison series for relative strength and beta/alpha (default: {DEFAULT_BENCHMARK_SYMBOL}).",
    )
    parser.add_argument("--output", type=Path, help="Artifact output path (single-ticker runs only).")
    parser.add_argument("--db-path", type=Path, default=DATABASE_PATH, help="Override the DuckDB path (testing).")
    parser.add_argument(
        "--run-id",
        help="Attach to this run workspace by name, creating it if it does not exist. "
             "A run is opened by default even without this flag -- pass it to name the run "
             "explicitly, e.g. to group several tickers into one run. See --no-run to opt out.",
    )
    parser.add_argument(
        "--no-run", action="store_true",
        help="Do not attach to or create a run workspace; write to --output (or exports/) instead. "
             "The opt-out is still recorded in the completeness trace (workspace.skip_reason).",
    )
    parser.add_argument(
        "--no-trace", action="store_true",
        help="Skip the completeness trace (no digest 'trace' key, no log line written).",
    )
    parser.add_argument(
        "--no-quote", action="store_true",
        help="Skip the live current-price quote (fast_info). Technicals themselves never fetch either "
             "way -- this only controls the one optional network call, on by default.",
    )
    parser.add_argument(
        "--trace-log-path", type=Path, default=None,
        help="Override the shared trace log path (testing). The .jsonl sidecar follows it.",
    )
    parser.add_argument("--pretty", action="store_true", default=True, help="Pretty-print JSON output (default: true).")
    parser.add_argument("--no-pretty", dest="pretty", action="store_false", help="Compact JSON output (single line).")
    args = parser.parse_args(argv)
    if args.output and len(args.ticker) > 1:
        parser.error("--output requires exactly one --ticker")
    if args.no_run and args.run_id:
        parser.error("--no-run and --run-id are contradictory: --run-id asks to attach to a run")
    return args


def _dispatch(args: argparse.Namespace) -> int:
    today = date.today()
    tickers = [t.upper() for t in args.ticker]
    run_dir, run_id, workspace_status, skip_reason = _resolve_run(args, tickers)

    connection = price_reader.connect_read_only(args.db_path)
    try:
        price_reader.validate_database(connection)
        # Read the benchmark once for the whole invocation -- it is the same
        # series for every ticker, and re-reading it per ticker would be a
        # pointless repeat of the largest query here.
        benchmark_symbol, benchmark_rows = price_reader.read_benchmark_prices(connection, args.benchmark)
        outputs = [
            process_ticker(
                ticker, connection, benchmark_symbol, benchmark_rows,
                today=today, no_trace=args.no_trace, no_quote=args.no_quote,
                output=args.output, pretty=args.pretty,
                trace_log_path=args.trace_log_path,
                run_dir=run_dir, run_id=run_id,
                workspace_status=workspace_status, skip_reason=skip_reason,
            )
            for ticker in tickers
        ]
    finally:
        connection.close()

    for output in outputs:
        print(json.dumps(price_reader.to_json_safe(output["payload"]), indent=2 if args.pretty else None))
        print(f"# artifact: {output['artifact_path']}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return _dispatch(args)
    except price_reader.DatabaseNotReady as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

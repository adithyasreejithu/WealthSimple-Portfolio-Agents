"""Orchestrator for the security-status skill.

Resolves one ticker's effective portfolio status -- owned, wishlist, avoid,
retired, or unknown -- from stored DuckDB state, and writes the result as a
**run-workspace calculation artifact** with an evidence entry, an audit
event, and a completeness trace.

This is the ONE shared skill both `investment-analyst` and
`investment-portfolio-manager` invoke. There is deliberately no second copy:
a change here reaches both agents by construction, so there is nothing to
keep in sync. Auditability instead comes from caller stamping -- `--actor`
is required, and every invocation writes its own artifact and registers its
own evidence row and audit event with that actor attached. Two agents
reading the same ticker in the same run therefore produce two distinct
`sources.jsonl` rows and two distinct `security_status_read` audit events,
never one shared, ambiguous record.

Three deliberate choices, each with a reason worth keeping:

- **The artifact goes to `calculations/`, not `evidence/`.** A resolved
  status is arithmetic over data the run already reaches (ownership from
  `position_snapshots`, a declaration from `security_status`), not a fact
  obtained from outside. Same reasoning as `security-technicals`.

- **`declared_status` is never written here.** This skill is read-only. The
  only write path for `security_status` is `python src/app.py database
  status`, a human/CLI-only command (see `src/database_command.py`'s
  `set_security_status`) -- no agent gets a tool that writes this table.

- **Ownership always wins over a declaration, and there is no "missing" for
  a declaration.** `analytics.resolve_security_status`'s docstring and
  `database._create_security_status_table`'s docstring both make the same
  point: an undeclared ticker is not a gap, it is the default state. A
  ticker with no row in `security_status` is graded `not_applicable`, never
  `missing` -- the same asymmetry `security-technicals` documents for
  insufficient price history.

Deliberately duplicates only what it must and reuses the rest: DB
connect/validate/identity-resolution and JSON serialization are imported
from the `read-security-price-history` dependency skill, exactly the way
`security-technicals` already does -- one boundary, two consumers, no
independent re-implementation of "how do I safely open this database."
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "read-security-price-history" / "scripts"))

import read_price_history as price_reader  # noqa: E402
import skill_trace  # noqa: E402
from config import DATABASE_PATH  # noqa: E402

SKILL_NAME = "security-status"
SCHEMA = "security-status.v1"

DEFAULT_OUTPUT_DIR = ROOT / "exports" / "security-status"

# The only two callers this shared skill has today. Kept as an explicit
# allowlist (not a free string) so a typo'd actor fails loudly at the
# argument parser rather than silently mislabeling the audit trail.
ACTORS: tuple[str, ...] = ("investment-analyst", "investment-portfolio-manager")


def _read_declaration(connection: Any, ticker_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT declared_status, rationale, declared_at, declared_by
        FROM security_status WHERE ticker_id = ?
        """,
        [ticker_id],
    ).fetchone()
    if row is None:
        return None
    return {
        "declared_status": row[0],
        "rationale": row[1],
        "declared_at": row[2],
        "declared_by": row[3],
    }


def _read_quantity(connection: Any, ticker_id: int) -> Decimal:
    """Last-computed net quantity from `position_snapshots`.

    Read-only, so this never triggers `ensure_positions_fresh`'s recompute --
    the same convention `db_resources.resolve_ticker`'s `currently_held`
    check already follows. A ticker with no snapshot row (never transacted)
    reads as zero, not a gap.
    """
    row = connection.execute(
        "SELECT quantity FROM position_snapshots WHERE ticker_id = ?",
        [ticker_id],
    ).fetchone()
    return row[0] if row is not None else Decimal("0")


def resolve_status(quantity: Decimal, declaration: dict[str, Any] | None) -> str:
    """Owned > declared > unknown. Ownership always wins.

    A wishlist ticker that gets bought becomes "owned" the moment
    `position_snapshots.quantity` turns positive -- no write to
    `security_status` required, so there is nothing here that can drift out
    of sync with a closed or opened position.
    """
    if quantity > 0:
        return "owned"
    if declaration is not None:
        return declaration["declared_status"]
    return "unknown"


def build_trace(ticker: str, resolved: bool, declaration: dict[str, Any] | None) -> skill_trace.Trace:
    """Grade this lookup's completeness.

    An unresolvable ticker grades nothing -- nothing was obtainable, so
    nothing is graded, matching `security_technicals_cli.build_trace`'s same
    short-circuit. Otherwise `identity`/`ownership` are always `ok` (both are
    always readable once the ticker resolves); `declaration` is `ok` when a
    row exists and `not_applicable` when it does not -- never `missing`,
    because declaring a status is optional by nature, not an expected fact
    this skill failed to obtain.
    """
    trace = skill_trace.Trace(skill=SKILL_NAME, subject=ticker, kind="security")
    if not resolved:
        return trace
    trace.add("identity", ok=["ticker_id"])
    trace.add("ownership", ok=["quantity"])
    if declaration is not None:
        trace.add("declaration", ok=["declared_status"])
    else:
        trace.add("declaration", not_applicable=["declared_status"])
    return trace


def build_result(
    ticker: str,
    identity: dict[str, Any] | None,
    quantity: Decimal,
    declaration: dict[str, Any] | None,
    actor: str,
    today: date,
) -> dict[str, Any]:
    """Compute and assemble one ticker's artifact payload.

    An unresolvable ticker short-circuits to a `resolved: false` payload with
    one gap, mirroring `security_technicals_cli.build_result`'s convention
    for the identical case.
    """
    if identity is None:
        return {
            "schema": SCHEMA,
            "ticker": ticker,
            "as_of": today.isoformat(),
            "actor": actor,
            "resolved": False,
            "status": "unknown",
            "gaps": [f"{ticker} is not in the tickers table"],
        }

    status = resolve_status(quantity, declaration)
    return {
        "schema": SCHEMA,
        "ticker": ticker,
        "as_of": today.isoformat(),
        "actor": actor,
        "resolved": True,
        "security_name": identity.get("security_name"),
        "status": status,
        "quantity_held": quantity,
        "declaration": declaration,
        "gaps": [],
    }


def _write_artifact(payload: dict[str, Any], destination: Path, pretty: bool) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(price_reader.to_json_safe(payload), indent=2 if pretty else None),
        encoding="utf-8",
    )
    return destination


def _register_status_evidence(
    run_dir: Path,
    run_id: str,
    ticker: str,
    actor: str,
    artifact_path: Path,
    payload: dict[str, Any],
) -> None:
    """Record this actor's read in the run's evidence registry.

    `collection_method` is stamped with the actor
    (`security-status:<actor>`), which is what lets two agents reading the
    same ticker in the same run produce two attributable rows instead of one
    ambiguous one -- see the module docstring. Best-effort, matching
    `security_technicals_cli._register_calculation_evidence`: a registry
    failure must never discard a lookup that otherwise succeeded.
    """
    try:
        from workspace import audit as audit_module
        from workspace import evidence as evidence_module

        evidence_status = "available" if payload.get("resolved") else "missing"
        record = evidence_module.register(
            run_dir,
            run_id=run_id,
            evidence_type="security_status",
            source_name="duckdb",
            status=evidence_status,
            artifact=artifact_path,
            retrieved_at=datetime.now().isoformat(timespec="seconds"),
            collection_method=f"security-status:{actor}",
            notes=[f"ticker={ticker}", f"actor={actor}"],
        )
        audit_module.append_event(
            run_dir,
            run_id=run_id,
            event="security_status_read",
            actor=actor,
            evidence_id=record.evidence_id,
            details={"ticker": ticker, "status": payload.get("status")},
        )
    except Exception as exc:  # noqa: BLE001 -- never sink a successful lookup
        print(f"# warning: could not register security status for {ticker}: {exc}", file=sys.stderr)


def process_ticker(
    ticker: str,
    connection: Any,
    actor: str,
    *,
    today: date,
    no_trace: bool,
    output: Path | None,
    pretty: bool,
    trace_log_path: Path | None,
    run_dir: Path | None,
    run_id: str | None,
    workspace_status: str | None,
    skip_reason: str | None,
) -> dict[str, Any]:
    identity = price_reader.resolve_ticker(connection, ticker)
    quantity = Decimal("0")
    declaration: dict[str, Any] | None = None
    if identity is not None:
        quantity = _read_quantity(connection, identity["ticker_id"])
        declaration = _read_declaration(connection, identity["ticker_id"])

    payload = build_result(ticker, identity, quantity, declaration, actor, today)

    if not no_trace:
        trace = build_trace(ticker, identity is not None, declaration)
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
        # actor is embedded in the filename (not just the timestamp) so two
        # actors reading the same ticker in the same second never collide on
        # `evidence.register`'s duplicate-artifact_path check.
        filename = f"{ticker}-{today.isoformat()}-status-{actor}.json"
        destination = (
            run_dir / "calculations" / filename if run_dir is not None else DEFAULT_OUTPUT_DIR / filename
        )
    artifact_path = _write_artifact(payload, destination, pretty)

    if run_dir is not None and run_id is not None:
        _register_status_evidence(run_dir, run_id, ticker, actor, artifact_path, payload)

    return {"payload": payload, "artifact_path": artifact_path}


def _resolve_run(
    args: argparse.Namespace, tickers: list[str]
) -> tuple[Path | None, str | None, str, str | None]:
    """Attach to a run workspace, creating one if it does not exist.

    Default-on, matching `security-technicals` and
    `investment-analyst-resources`: a status lookup that is not in a run is
    an untracked handoff. `--no-run` and a non-default `--db-path` are the
    two ways to skip, and each records why rather than silently doing
    nothing.
    """
    if args.no_run:
        return None, None, "skipped", "--no-run"
    if args.db_path != DATABASE_PATH:
        return None, None, "skipped", "non-default-db"

    from workspace import run as run_module

    resolved, directory, created = run_module.ensure_run(
        args.run_id,
        mode="status_check",
        subject_type="security",
        ticker=tickers[0] if len(tickers) == 1 else None,
        question=(
            f"Security status for {', '.join(tickers)}; "
            "no research question was recorded."
        ),
        trigger=SKILL_NAME,
    )
    if created:
        print(f"note: created run workspace {resolved}", file=sys.stderr)
    return directory, resolved, ("created" if created else "attached"), None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve one or more tickers' effective portfolio status "
                     "(owned/wishlist/avoid/retired/unknown) from stored DuckDB state. Read-only."
    )
    parser.add_argument("--ticker", nargs="+", required=True, help="Pipeline ticker symbol(s).")
    parser.add_argument(
        "--actor",
        required=True,
        choices=ACTORS,
        help="Calling agent, stamped into the evidence/audit record so a shared skill's "
             "invocations stay attributable per caller.",
    )
    parser.add_argument("--output", type=Path, help="Artifact output path (single-ticker runs only).")
    parser.add_argument("--db-path", type=Path, default=DATABASE_PATH, help="Override the DuckDB path (testing).")
    parser.add_argument(
        "--run-id",
        help="Attach to this run workspace by name, creating it if it does not exist. "
             "A run is opened by default even without this flag. See --no-run to opt out.",
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
        outputs = [
            process_ticker(
                ticker, connection, args.actor,
                today=today, no_trace=args.no_trace,
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

"""Market Analyst Resources -- pull, persist, and diff macro/market indicators.

No judgment happens here: this script fetches what the registry
(`Knowledge-Base/taxonomy/market-indicators.yml`) declares, applies only the
mechanical transform each indicator names, and emits a digest plus an
on-disk bundle for the (separate, not-yet-built) market-researcher agent to
interpret. See docs/plans/market-analyst-resources-skill.md.

Three modes, same reason as the sibling `investment-analyst-resources`
skill: DuckDB allows one read-write connection or many read-only ones on a
file, never both.

    gate    read-only   per-indicator freshness verdict, no writes, no fetch
    refresh read-write  the only writer -- fetches due indicators, persists
    read    mostly read-only -- builds the digest + bundle, then a brief
            separate write records the run (see `_record_run` below)

The default (no --mode) runs refresh -> read in one process, for
single-invocation use. This is a **global** skill, not per-ticker, so there
is no fan-out of parallel readers to protect the way the sibling skill's
mode split does -- the split here exists so a market refresh doesn't take
the file lock at the same moment a per-ticker `investment-analyst-resources
--mode read` (a different file) or, more importantly, another market
`--mode read` is trying to record its run.
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
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import macro_store  # noqa: E402
import market_freshness_gate  # noqa: E402
import skill_trace  # noqa: E402
from registry import Registry, RegistryError, load_registry  # noqa: E402
from transforms import apply_unary_transform, compute_derived, parse_transform  # noqa: E402

SCHEMA = "market-analyst-resources.v1"
SKILL_NAME = "market-analyst-resources"

# How each entry status maps onto the shared trace's three-way split
# (`src/skill_trace.py`). `not_configured` is the important one: those are the
# registry's `<TBD>` stubs, five domains' worth of indicators whose source has
# not been chosen yet. They are not data this run failed to fetch, so counting
# them as gaps would permanently peg completeness below 100% for a healthy run.
TRACE_OK_STATUSES = frozenset({"fetched", "ok"})
TRACE_NOT_APPLICABLE_STATUSES = frozenset({"not_configured"})
DEFAULT_OUTPUT_DIR = ROOT / "exports" / "market-analyst-resources"
DIGEST_WARN_BYTES = 8192

# Cadence classes decide which delta is the *signal* vs the *noise* -- see
# docs/plans/market-analyst-resources-skill.md §6. Monthly/quarterly series
# barely move report-to-report (delta_vs_last_report ~= 0 most weeks);
# daily series barely move observation-to-observation (delta_vs_prior_obs is
# just yesterday's wiggle).
HIGH_FREQUENCY_CADENCES = frozenset({"daily", "weekly"})


def _history_pairs(rows: list[dict[str, Any]]) -> list[tuple[date, float | None]]:
    return [(row["obs_date"], row["value"]) for row in rows]


def _cadence_class(cadence: str) -> str:
    return "high_frequency" if cadence in HIGH_FREQUENCY_CADENCES else "low_frequency"


def _transform_value(indicator, history_rows: list[dict[str, Any]]):
    name, window = parse_transform(indicator.transform)
    return apply_unary_transform(name, window, _history_pairs(history_rows), cadence=indicator.cadence)


def _completeness_status(indicator, gate_result: dict[str, Any] | None, *, has_data: bool) -> str:
    if not indicator.is_configured:
        return "not_configured"
    if not has_data:
        return "no_data"
    if gate_result is None:
        return "fetched"
    if gate_result.get("overdue"):
        return "overdue"
    if gate_result.get("due"):
        return "stale"
    return "fetched"


def build_indicator_entry(
    indicator, connection, gate_result: dict[str, Any] | None, last_run: dict[str, Any] | None
) -> dict[str, Any]:
    history_rows = macro_store.get_history(connection, indicator.id)
    current = _transform_value(indicator, history_rows)

    prior_rows = history_rows[:-1] if len(history_rows) > 1 else []
    prior = _transform_value(indicator, prior_rows) if prior_rows else None
    delta_vs_prior_obs = (
        current.value - prior.value
        if current.value is not None and prior is not None and prior.value is not None
        else None
    )

    delta_vs_last_report = None
    new_observations = None
    revised: list[dict[str, Any]] = []
    if last_run is not None:
        as_of_rows = macro_store.get_history_as_of(connection, indicator.id, last_run["created_at"])
        last_report = _transform_value(indicator, as_of_rows) if as_of_rows else None
        if current.value is not None and last_report is not None and last_report.value is not None:
            delta_vs_last_report = current.value - last_report.value
        new_observations = macro_store.get_new_observation_count_since(
            connection, indicator.id, last_run["created_at"]
        )
        revised = [
            revision
            for revision in macro_store.get_revisions_since(connection, last_run["created_at"])
            if revision["series_id"] == indicator.id
        ]

    cadence_class = _cadence_class(indicator.cadence)
    if cadence_class == "low_frequency":
        primary_delta, primary_basis = (
            (delta_vs_prior_obs, "prior_obs")
            if delta_vs_prior_obs is not None
            else (delta_vs_last_report, "last_report")
        )
    else:
        primary_delta, primary_basis = (
            (delta_vs_last_report, "last_report")
            if delta_vs_last_report is not None
            else (delta_vs_prior_obs, "prior_obs")
        )

    return {
        "id": indicator.output_id,
        "domain": indicator.domain,
        "value": current.value,
        "as_of": current.basis_date,
        "units": indicator.units,
        "cadence_class": cadence_class,
        "new_observations_since_last_report": new_observations,
        "primary_delta": primary_delta,
        "primary_delta_basis": primary_basis,
        "delta_vs_prior_obs": delta_vs_prior_obs,
        "delta_vs_last_report": delta_vs_last_report,
        "revised_since_last_report": [
            {"obs_date": r["obs_date"], "prior_value": r["prior_value"], "new_value": r["new_value"]}
            for r in revised
        ],
        "next_expected": gate_result.get("next_expected") if gate_result else None,
        "status": _completeness_status(indicator, gate_result, has_data=bool(history_rows)),
    }


def build_derived_entry(entry, connection) -> dict[str, Any]:
    id_a, id_b = entry.inputs
    series_a = _history_pairs(macro_store.get_history(connection, id_a))
    series_b = _history_pairs(macro_store.get_history(connection, id_b))
    result = compute_derived(entry.op, series_a, series_b, max_carry_days=entry.max_carry_days)
    return {
        "id": entry.id,
        "domain": entry.domain,
        "value": result.value,
        "units": entry.units,
        "inputs": list(entry.inputs),
        "basis_date_a": result.basis_date_a,
        "basis_date_b": result.basis_date_b,
        "status": result.status,
    }


def build_trace(entries: list[dict[str, Any]], subject: str) -> skill_trace.Trace:
    """Map per-entry statuses onto the shared trace shape.

    Grouped by registry domain so the log line reads `rates[6],credit[3]`
    rather than fifty indicator IDs, and so a whole unconfigured domain shows
    up as one not-applicable group.
    """
    return skill_trace.from_status_map(
        SKILL_NAME,
        subject,
        {entry["id"]: entry["status"] for entry in entries},
        ok_statuses=TRACE_OK_STATUSES,
        not_applicable_statuses=TRACE_NOT_APPLICABLE_STATUSES,
        kind="market",
        domain_of={entry["id"]: entry["domain"] for entry in entries},
    )


def _completeness_trace(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Digest-embedded summary. Retains `by_status` (the registry's own
    vocabulary is more informative here than a three-way split alone) and
    adds the shared completeness figure so this skill and its sibling report
    the same number the same way."""
    by_status: dict[str, int] = {}
    for entry in entries:
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
    trace = build_trace(entries, "digest")
    return {
        "total": len(entries),
        "by_status": by_status,
        "completeness_pct": trace.completeness_pct,
        "graded": trace.fields_graded,
        "not_applicable": trace.fields_not_applicable,
    }


def build_digest_and_bundle(
    registry: Registry,
    connection,
    gate_results: list[dict[str, Any]],
    *,
    today: date,
    domains_filter: set[str] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    gate_by_id = {result["id"]: result for result in gate_results}
    last_run = macro_store.get_last_successful_run(connection)

    indicator_entries = [
        build_indicator_entry(indicator, connection, gate_by_id.get(indicator.id), last_run)
        for indicator in registry.indicators
        if not domains_filter or indicator.domain in domains_filter
    ]
    derived_entries = [
        build_derived_entry(entry, connection)
        for entry in registry.derived
        if not domains_filter or entry.domain in domains_filter
    ]
    all_entries = indicator_entries + derived_entries

    changed: list[dict[str, Any]] = []
    unchanged: dict[str, dict[str, Any]] = {}
    for entry in all_entries:
        is_changed = (
            last_run is None
            or (entry.get("new_observations_since_last_report") or 0) > 0
            or bool(entry.get("revised_since_last_report"))
            or entry["status"] in ("no_data", "not_configured", "overdue")
        )
        if is_changed:
            changed.append(entry)
        else:
            unchanged[entry["id"]] = {"value": entry["value"], "as_of": entry.get("as_of"), "domain": entry["domain"]}

    events_upcoming = [
        {"date": event.date, "label": event.label, "forces_due": list(event.forces_due)}
        for event in registry.events
        if event.date >= today
    ]

    digest = {
        "schema": SCHEMA,
        "as_of": today,
        "registry_version": registry.version,
        "last_report_at": last_run["created_at"] if last_run else None,
        "changed": changed,
        "unchanged": unchanged,
        "events_upcoming": events_upcoming,
        "completeness": _completeness_trace(all_entries),
    }
    bundle = {
        "schema": SCHEMA,
        "as_of": today,
        "registry_version": registry.version,
        "indicators": {
            indicator.id: macro_store.get_history(connection, indicator.id)
            for indicator in registry.indicators
            if indicator.is_configured
        },
        "digest": digest,
        "gate": gate_results,
        # The flat per-entry status table. The digest splits entries into
        # changed/unchanged, which is the right shape for a reader but loses
        # the uniform status column the completeness trace needs.
        "entries": [
            {"id": entry["id"], "domain": entry["domain"], "status": entry["status"]}
            for entry in all_entries
        ],
    }
    return digest, bundle


def _write_bundle(bundle: dict[str, Any], output: Path | None, today: date) -> Path:
    path = output or (DEFAULT_OUTPUT_DIR / f"{today.isoformat()}-market.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(macro_store.to_json_safe(bundle), indent=2), encoding="utf-8")
    return path


def _do_refresh_step(
    registry: Registry, db_path: Path, today: date, *, only_domains, max_indicators, force: bool
) -> list[dict[str, Any]]:
    connection = macro_store.connect_read_write(db_path)
    try:
        gate_results = market_freshness_gate.compute_gate(registry, connection, today=today)
        if force:
            for result in gate_results:
                if result.get("configured"):
                    result["due"] = True
                    result["due_reason"] = result.get("due_reason") or "forced"
        return market_freshness_gate.refresh_due_indicators(
            registry, connection, gate_results, today=today, only_domains=only_domains, max_indicators=max_indicators
        )
    finally:
        connection.close()


def _do_read_step(
    registry: Registry,
    db_path: Path,
    today: date,
    *,
    domains_filter: set[str] | None,
    no_bundle: bool,
    output: Path | None,
    record_run: bool,
    run_dir: Path | None = None,
    run_id: str | None = None,
    trace_log_path: Path | None = None,
    workspace_status: str | None = None,
    skip_reason: str | None = None,
) -> tuple[dict[str, Any], Path | None]:
    try:
        connection = macro_store.connect_read_only(db_path)
    except macro_store.MarketStoreNotReady as exc:
        return {"error": str(exc)}, None

    gate_results = market_freshness_gate.compute_gate(registry, connection, today=today)
    digest, bundle = build_digest_and_bundle(registry, connection, gate_results, today=today, domains_filter=domains_filter)
    connection.close()

    bundle_path: Path | None = None
    if not no_bundle:
        bundle_path = _write_bundle(bundle, output, today)

    # Subject is the run date -- this skill is portfolio-wide, so unlike its
    # per-ticker sibling there is no security to name.
    trace = build_trace(bundle["entries"], today.isoformat())
    if workspace_status is not None:
        trace.workspace = skill_trace.WorkspaceOutcome(
            status=workspace_status, run_id=run_id, skip_reason=skip_reason
        )
    skill_trace.emit(
        trace,
        run_dir=run_dir,
        run_id=run_id,
        text_log_path=trace_log_path,
        jsonl_log_path=trace_log_path.with_suffix(".jsonl") if trace_log_path else None,
    )

    if record_run:
        # `read` is documented as read-only for the fan-out/gate reasoning,
        # but recording a run is genuinely a write. This is a brief, separate
        # write -- not the long fetch-and-persist `refresh` does -- so it
        # doesn't need the gate/refresh split's protection, only its own
        # short-lived exclusive lock.
        write_connection = macro_store.connect_read_write(db_path)
        try:
            macro_store.record_run(
                write_connection,
                run_date=today,
                registry_version=registry.version,
                status="ok",
                bundle_path=str(bundle_path) if bundle_path else None,
                indicator_count=len(registry.indicators),
                created_at=datetime.combine(today, datetime.min.time()),
            )
        finally:
            write_connection.close()

    return digest, bundle_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull, persist, and diff macro/market indicators. Skills pull; agents analyze."
    )
    parser.add_argument("--mode", choices=["gate", "refresh", "read"], help="Run a single mode; omit for refresh -> read.")
    parser.add_argument("--registry-path", type=Path, help="Override the registry YAML path (testing).")
    parser.add_argument("--db-path", type=Path, help="Override market.duckdb's path (testing). Suppresses run recording.")
    parser.add_argument("--domain", nargs="+", help="Limit read output to these domains.")
    parser.add_argument("--only", dest="only_domains", nargs="+", help="refresh only: limit refresh to these domains.")
    parser.add_argument("--max-indicators", type=int, help="refresh only: cap how many due indicators refresh in one call.")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cadence gating; refresh every configured indicator.")
    parser.add_argument("--no-bundle", action="store_true", help="Print the digest only; write no bundle file.")
    parser.add_argument("--output", type=Path, help="Bundle output path override (single-run only).")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--run-date", type=date.fromisoformat, help="Override today's date (testing).")
    parser.add_argument(
        "--run-id",
        help="Attach to this run workspace by name, creating it if it does not exist. "
             "A run is opened by default for any evidence-producing pull (--mode read or "
             "the default refresh->read sequence) even without this flag. "
             "See --no-run to opt out instead.",
    )
    parser.add_argument(
        "--no-run", action="store_true",
        help="Do not attach to or create a run workspace; log only to the shared "
             "logs/SkillTrace files, as before this became the default. The opt-out is "
             "still recorded in the trace (workspace.skip_reason), so it stays auditable "
             "rather than silent.",
    )
    parser.add_argument(
        "--trace-log-path", type=Path, default=None,
        help="Override the shared trace log path (testing). The .jsonl sidecar follows it.",
    )
    args = parser.parse_args(argv)
    if args.no_run and args.run_id:
        parser.error("--no-run and --run-id are contradictory: --run-id asks to attach to a run")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    today = args.run_date or date.today()

    try:
        registry = load_registry(args.registry_path)
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2

    db_path = args.db_path or macro_store.MARKET_DB_PATH
    is_default_db = args.db_path is None
    domains_filter = set(args.domain) if args.domain else None
    only_domains = set(args.only_domains) if args.only_domains else None

    # Attaching to a run is default-on for any pull that actually produces
    # evidence (--mode read or the default refresh->read sequence) -- see the
    # matching decision in investment-analyst-resources's `_resolve_run` for
    # why this moved from an opt-in flag into the code path itself. `gate`
    # produces nothing to register; `refresh` writes to the DB but emits no
    # bundle. Reuses `is_default_db`, the existing signal for "this is a
    # test/debug invocation" that already gates `record_run` below, so the
    # test suite does not populate `workspace/runs/`.
    produces_evidence = args.mode in (None, "read")
    run_dir = None
    run_id = None
    workspace_status = "skipped"
    skip_reason: str | None = None
    if not produces_evidence:
        skip_reason = "read-only mode" if args.mode == "gate" else "no evidence produced"
    elif args.no_run:
        skip_reason = "--no-run"
    elif not is_default_db:
        skip_reason = "non-default-db"
    else:
        # Attach to the run, creating it if it does not exist yet -- the same
        # attach-or-create used by investment-analyst-resources, via the one
        # shared implementation. Never log outside the run the caller asked
        # for: on a typo this creates a new run and says so, rather than
        # silently writing somewhere else.
        from workspace import run as run_module

        run_id, run_dir, created = run_module.ensure_run(
            args.run_id,
            mode="market_scan",
            subject_type="market",
            question=(
                f"Ad-hoc macro/market pull for {args.run_date or 'today'}; "
                "no research question was recorded."
            ),
            trigger=SKILL_NAME,
        )
        if created:
            print(f"note: created run workspace {run_id}", file=sys.stderr)
        workspace_status = "created" if created else "attached"

    if args.mode == "gate":
        try:
            connection = macro_store.connect_read_only(db_path)
        except macro_store.MarketStoreNotReady:
            connection = None
        try:
            gate_results = market_freshness_gate.compute_gate(registry, connection, today=today)
        finally:
            if connection is not None:
                connection.close()
        print(json.dumps(macro_store.to_json_safe(gate_results), indent=2 if args.pretty else None))
        return 0

    if args.mode == "refresh":
        results = _do_refresh_step(
            registry, db_path, today, only_domains=only_domains, max_indicators=args.max_indicators, force=args.force_refresh
        )
        print(json.dumps(macro_store.to_json_safe(results), indent=2 if args.pretty else None))
        return 1 if any(r["status"] != "ok" for r in results) else 0

    if args.mode == "read":
        digest, bundle_path = _do_read_step(
            registry, db_path, today, domains_filter=domains_filter, no_bundle=args.no_bundle,
            output=args.output, record_run=is_default_db,
            run_dir=run_dir, run_id=run_id, trace_log_path=args.trace_log_path,
            workspace_status=workspace_status, skip_reason=skip_reason,
        )
        return _emit_digest(digest, bundle_path, pretty=args.pretty)

    # Default: refresh -> read, one process.
    refresh_results = _do_refresh_step(
        registry, db_path, today, only_domains=only_domains, max_indicators=args.max_indicators, force=args.force_refresh
    )
    failed = [r for r in refresh_results if r["status"] != "ok"]
    if failed:
        print(json.dumps(macro_store.to_json_safe(refresh_results), indent=2 if args.pretty else None), file=sys.stderr)

    digest, bundle_path = _do_read_step(
        registry, db_path, today, domains_filter=domains_filter, no_bundle=args.no_bundle,
        output=args.output, record_run=is_default_db,
        run_dir=run_dir, run_id=run_id, trace_log_path=args.trace_log_path,
        workspace_status=workspace_status, skip_reason=skip_reason,
    )
    exit_code = _emit_digest(digest, bundle_path, pretty=args.pretty)
    return 1 if failed and exit_code == 0 else exit_code


def _emit_digest(digest: dict[str, Any], bundle_path: Path | None, *, pretty: bool) -> int:
    if "error" in digest:
        print(digest["error"], file=sys.stderr)
        return 1
    digest_text = json.dumps(macro_store.to_json_safe(digest), indent=2 if pretty else None)
    print(digest_text)
    if bundle_path:
        print(f"# bundle: {bundle_path}", file=sys.stderr)
    if len(digest_text.encode("utf-8")) > DIGEST_WARN_BYTES:
        print(f"# warning: digest exceeded the {DIGEST_WARN_BYTES}-byte soft limit", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

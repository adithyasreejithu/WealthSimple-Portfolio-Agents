"""`python src/app.py run <subcommand>` argument handling.

Deliberately does nothing an agent could mistake for reasoning: every command
here creates, reads, or validates files. No subcommand claims to invoke an
agent that does not exist -- unavailable stages are reported from
`run_metadata.available_components` instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import typing
from pathlib import Path

from . import cache as cache_module
from . import decision_validation as decision_validation_module
from . import evidence as evidence_module
from . import investment_worksheet as investment_worksheet_module
from . import policy_worksheet as policy_worksheet_module
from . import run as run_module
from . import thesis_validation as thesis_validation_module
from . import validation as validation_module
from .analysis_models import AnalysisHorizon, AnalysisMode, TriggerType
from .models import EvidenceStatus  # noqa: F401  (documents the allowed values below)
from .paths import WorkspaceError, resolve_in_run, utc_now_iso

EVIDENCE_STATUSES = ("pending", "available", "partial", "missing", "stale", "invalid")
ANALYSIS_MODES = list(typing.get_args(AnalysisMode))
ANALYSIS_HORIZONS = list(typing.get_args(AnalysisHorizon))
TRIGGER_TYPES = list(typing.get_args(TriggerType))


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _cmd_create(args: argparse.Namespace) -> int:
    run_id, directory = run_module.create_run(Path(args.request), trigger=args.trigger)
    _print({"run_id": run_id, "run_dir": str(directory), "status": "created"})
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.read_metadata(directory)
    _print(
        {
            "run_dir": str(directory),
            "metadata": metadata.model_dump(mode="json"),
            "evidence": evidence_module.read_records(directory),
        }
    )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    _print(run_module.list_runs())
    return 0


def _cmd_register_evidence(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.read_metadata(directory)

    if args.missing:
        status = args.status if args.status != "available" else "missing"
        artifact = None
    else:
        if not args.file:
            raise WorkspaceError("supply --file, or --missing to register an explicit gap")
        artifact = Path(args.file)
        status = args.status

    record = evidence_module.register(
        directory,
        run_id=metadata.run_id,
        evidence_type=args.type,
        source_name=args.source,
        status=status,
        artifact=artifact,
        source_url=args.url,
        retrieved_at=utc_now_iso() if artifact else None,
        collection_method=args.method,
        notes=list(args.note or []),
    )
    from . import audit as audit_module

    audit_module.append_event(
        directory,
        run_id=metadata.run_id,
        event="evidence_registered",
        evidence_id=record.evidence_id,
        details={"type": record.evidence_type, "source": record.source_name, "status": record.status},
    )
    _print(record.model_dump(mode="json"))
    return 0


def _cmd_build_manifest(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    path = run_module.rebuild_manifest(
        directory, target_stage=args.target_stage, prior_thesis_path=args.prior_thesis
    )
    _print({"manifest": str(path)})
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    result = validation_module.validate_run(directory)
    _print(result)
    return 0 if result["ok"] else 1


def _cmd_build_worksheet(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.read_metadata(directory)
    scope = investment_worksheet_module.build_analysis_scope(
        run_id=metadata.run_id,
        subject=args.ticker,
        asset_track=args.asset_track,
        mode=args.mode,
        trigger={
            "type": args.trigger_type,
            "occurred_at": args.trigger_occurred_at,
            "detail": args.trigger_detail,
        },
        decision_horizon=args.horizon,
    )
    worksheet, worksheet_path, worksheet_hash = investment_worksheet_module.build_worksheet_for_run(
        directory, run_id=metadata.run_id, ticker=args.ticker, scope=scope,
    )
    context_path = worksheet_path.with_name(worksheet_path.name.replace("-worksheet.json", "-analyst-context.md"))
    _print(
        {
            "worksheet_path": worksheet_path.relative_to(directory).as_posix(),
            "analyst_context_path": (
                context_path.relative_to(directory).as_posix() if context_path.is_file() else None
            ),
            "worksheet_hash": worksheet_hash,
            "evidence_health": worksheet["evidence_health"],
        }
    )
    return 0


def _cmd_check_thesis(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    payload = json.loads(resolve_in_run(directory, args.path).read_text(encoding="utf-8"))
    result = thesis_validation_module.check_thesis_draft(payload, run_dir=directory)
    _print(result)
    return 0 if result["ok"] else 1


def _cmd_save_thesis(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.read_metadata(directory)
    payload = json.loads(resolve_in_run(directory, args.path).read_text(encoding="utf-8"))
    result = thesis_validation_module.save_thesis(
        payload, run_dir=directory, run_id=metadata.run_id, ticker=args.ticker,
    )
    _print(result)
    return 0 if result["ok"] else 1


def _cmd_build_policy_context(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.read_metadata(directory)
    kwargs = {"db_path": args.db_path} if args.db_path else {}
    worksheet, worksheet_path, worksheet_hash = policy_worksheet_module.build_policy_worksheet_for_run(
        directory, run_id=metadata.run_id, ticker=args.ticker, **kwargs,
    )
    _print(
        {
            "policy_worksheet_path": worksheet_path.relative_to(directory).as_posix(),
            "policy_worksheet_hash": worksheet_hash,
            "policy_checks": worksheet["policy_checks"],
            "current_weight_pct": worksheet["current_weight_pct"],
            "subject": worksheet["subject"],
        }
    )
    return 0


def _cmd_check_decision(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    payload = json.loads(resolve_in_run(directory, args.path).read_text(encoding="utf-8"))
    result = decision_validation_module.check_decision_draft(payload, run_dir=directory)
    _print(result)
    return 0 if result["ok"] else 1


def _cmd_save_decision(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.read_metadata(directory)
    payload = json.loads(resolve_in_run(directory, args.path).read_text(encoding="utf-8"))
    result = decision_validation_module.save_decision(
        payload, run_dir=directory, run_id=metadata.run_id, ticker=args.ticker,
    )
    _print(result)
    return 0 if result["ok"] else 1


def _cmd_set_status(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.set_status(directory, args.status, note=args.note)
    _print({"run_id": metadata.run_id, "status": metadata.status})
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    destination = run_module.archive_run(directory, validate=not args.no_validate)
    _print({"archived_to": str(destination)})
    return 0


def _cmd_log_event(args: argparse.Namespace) -> int:
    directory = run_module.resolve_run(args.run_id)
    metadata = run_module.read_metadata(directory)
    details = json.loads(args.details) if args.details else None
    if details is not None and not isinstance(details, dict):
        raise WorkspaceError("--details must be a JSON object")

    from . import audit as audit_module

    record = audit_module.append_event(
        directory,
        run_id=metadata.run_id,
        event=args.event,
        actor=args.actor,
        status=args.status,
        artifact=args.artifact,
        details=details,
        error=args.error,
    )
    _print(record)
    return 0


def _cmd_gc(args: argparse.Namespace) -> int:
    results = cache_module.gc(older_than_days=args.older_than_days, dry_run=args.dry_run)
    _print({"dry_run": args.dry_run, "older_than_days": args.older_than_days, "runs": results})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.py run",
        description="Create and manage investment-research run workspaces.",
    )
    subcommands = parser.add_subparsers(dest="subcommand", required=True)

    create = subcommands.add_parser("create", help="Create a run from a request file.")
    create.add_argument("--request", required=True, help="Path to a request YAML or JSON file.")
    create.add_argument("--trigger", default="cli", help="What initiated this run.")
    create.set_defaults(func=_cmd_create)

    show = subcommands.add_parser("show", help="Print a run's metadata and evidence.")
    show.add_argument("--run-id", required=True)
    show.set_defaults(func=_cmd_show)

    listing = subcommands.add_parser("list", help="Summarize every active run.")
    listing.set_defaults(func=_cmd_list)

    register = subcommands.add_parser(
        "register-evidence", help="Record one piece of evidence, present or missing."
    )
    register.add_argument("--run-id", required=True)
    register.add_argument("--file", help="Artifact inside the run directory.")
    register.add_argument(
        "--missing", action="store_true",
        help="Register an explicit gap instead of a file. Never invent a value for it.",
    )
    register.add_argument("--type", required=True, help="Evidence type, e.g. price_quote.")
    register.add_argument("--source", required=True, help="Source name, e.g. yfinance.")
    register.add_argument("--status", default="available", choices=EVIDENCE_STATUSES)
    register.add_argument("--url", help="Source URL, when there is one.")
    register.add_argument("--method", help="How it was collected.")
    register.add_argument("--note", action="append", help="Repeatable free-text note.")
    register.set_defaults(func=_cmd_register_evidence)

    manifest = subcommands.add_parser("build-manifest", help="Regenerate the context manifest.")
    manifest.add_argument("--run-id", required=True)
    manifest.add_argument("--target-stage", help="Which stage will consume this manifest.")
    manifest.add_argument("--prior-thesis", help="Run-relative path to a prior thesis, if any.")
    manifest.set_defaults(func=_cmd_build_manifest)

    build_worksheet = subcommands.add_parser(
        "build-worksheet", help="Build the investment worksheet + analyst context for one ticker."
    )
    build_worksheet.add_argument("--run-id", required=True)
    build_worksheet.add_argument("--ticker", required=True)
    build_worksheet.add_argument("--mode", required=True, choices=ANALYSIS_MODES)
    build_worksheet.add_argument("--horizon", required=True, choices=ANALYSIS_HORIZONS)
    build_worksheet.add_argument("--asset-track", default="equity", choices=["equity", "etf"])
    build_worksheet.add_argument("--trigger-type", default="user_request", choices=TRIGGER_TYPES)
    build_worksheet.add_argument("--trigger-detail")
    build_worksheet.add_argument("--trigger-occurred-at")
    build_worksheet.set_defaults(func=_cmd_build_worksheet)

    check_thesis = subcommands.add_parser(
        "check-thesis", help="Dry-run validate a draft investment-thesis.v1 artifact. Writes nothing."
    )
    check_thesis.add_argument("--run-id", required=True)
    check_thesis.add_argument(
        "--path", required=True, help="Run-relative path to the draft JSON, e.g. tmp/PLTR-thesis-draft.json."
    )
    check_thesis.set_defaults(func=_cmd_check_thesis)

    save_thesis = subcommands.add_parser(
        "save-thesis", help="Validate a draft and, if valid, finalize it into agent_outputs/."
    )
    save_thesis.add_argument("--run-id", required=True)
    save_thesis.add_argument("--path", required=True)
    save_thesis.add_argument("--ticker", required=True)
    save_thesis.set_defaults(func=_cmd_save_thesis)

    build_policy_context = subcommands.add_parser(
        "build-policy-context",
        help="Build the portfolio-policy worksheet (exposure + single-name/group-cap checks) for one ticker.",
    )
    build_policy_context.add_argument("--run-id", required=True)
    build_policy_context.add_argument("--ticker", required=True)
    build_policy_context.add_argument(
        "--db-path", help="Override the configured pipeline database (test/debug only)."
    )
    build_policy_context.set_defaults(func=_cmd_build_policy_context)

    check_decision = subcommands.add_parser(
        "check-decision", help="Dry-run validate a draft DecisionProposal artifact. Writes nothing."
    )
    check_decision.add_argument("--run-id", required=True)
    check_decision.add_argument(
        "--path", required=True, help="Run-relative path to the draft JSON, e.g. tmp/PLTR-decision-draft.json."
    )
    check_decision.set_defaults(func=_cmd_check_decision)

    save_decision = subcommands.add_parser(
        "save-decision", help="Validate a draft and, if valid, finalize it into final/."
    )
    save_decision.add_argument("--run-id", required=True)
    save_decision.add_argument("--path", required=True)
    save_decision.add_argument("--ticker", required=True)
    save_decision.set_defaults(func=_cmd_save_decision)

    validate = subcommands.add_parser("validate", help="Check a run's files, schemas, and references.")
    validate.add_argument("--run-id", required=True)
    validate.set_defaults(func=_cmd_validate)

    status = subcommands.add_parser("set-status", help="Move a run to a new lifecycle state.")
    status.add_argument("--run-id", required=True)
    status.add_argument("--status", required=True)
    status.add_argument("--note", help="Recorded as a warning, or an error when failing the run.")
    status.set_defaults(func=_cmd_set_status)

    archive = subcommands.add_parser("archive", help="Move a run into the archive (never deletes).")
    archive.add_argument("--run-id", required=True)
    archive.add_argument(
        "--no-validate", action="store_true",
        help="Archive without validating -- for retaining an abandoned or failed run.",
    )
    archive.set_defaults(func=_cmd_archive)

    log_event = subcommands.add_parser(
        "log-event",
        help="Append a free-form audit event to a run's history -- for an orchestrating "
             "agent's own actions (preflight checks, stage dispatch, final report) that "
             "aren't already recorded by another run subcommand.",
    )
    log_event.add_argument("--run-id", required=True)
    log_event.add_argument("--event", required=True, help="Short event name, e.g. preflight_passed.")
    log_event.add_argument("--actor", required=True, help="Who performed this action, e.g. investment-orchestrator.")
    log_event.add_argument("--status", default="success", help="e.g. success, failure, skipped.")
    log_event.add_argument("--details", help="JSON object of extra structured detail.")
    log_event.add_argument("--artifact", help="Run-relative path this event refers to, if any.")
    log_event.add_argument("--error", help="Error message, typically paired with --status failure.")
    log_event.set_defaults(func=_cmd_log_event)

    gc = subcommands.add_parser(
        "gc",
        help="Purge cache/ for runs older than --older-than-days that never reached a terminal "
             "status (a crashed run's own cache is otherwise only purged by set-status/archive).",
    )
    gc.add_argument(
        "--older-than-days", type=int, default=7,
        help="Only purge runs whose created_at is at least this many days old (default: 7).",
    )
    gc.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be purged without deleting anything.",
    )
    gc.set_defaults(func=_cmd_gc)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

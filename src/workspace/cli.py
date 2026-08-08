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
from pathlib import Path

from . import evidence as evidence_module
from . import run as run_module
from . import validation as validation_module
from .models import EvidenceStatus  # noqa: F401  (documents the allowed values below)
from .paths import WorkspaceError, utc_now_iso

EVIDENCE_STATUSES = ("pending", "available", "partial", "missing", "stale", "invalid")


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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

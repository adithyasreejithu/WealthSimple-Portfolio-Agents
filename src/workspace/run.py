"""Run lifecycle: create, load, update status, archive.

Creation is the delicate part. A half-built run is worse than no run -- a
later stage would find a directory, assume it is usable, and act on missing
files. So a run is assembled under a hidden temporary name and moved into
place with a single `os.replace` only once every required file exists; any
failure removes the temporary directory and leaves the runs root untouched.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from . import audit as audit_module
from . import evidence as evidence_module
from . import manifest as manifest_module
from . import state as state_module
from .models import Request, RunMetadata
from .paths import (
    AUDIT_FILENAME,
    EVIDENCE_REGISTRY_RELPATH,
    METADATA_FILENAME,
    REQUEST_FILENAME,
    RUN_SUBDIRS,
    WorkspaceError,
    archive_dir,
    generate_run_id,
    is_valid_run_id,
    run_dir as run_dir_for,
    runs_root,
    utc_now_iso,
)

# Components this workflow will eventually call. Recorded on every run so a
# reader can tell which stages were even possible at the time -- an absent
# agent is reported as unavailable, never quietly skipped.
# Sentinel `--run-id` value meaning "generate one for me". Chosen over a
# separate flag so a caller has one knob: an ID to attach to, or `auto`.
AUTO_RUN_ID = "auto"

KNOWN_COMPONENTS: dict[str, str] = {
    "workspace_cli": "available",
    "investment_analyst_resources": "available",
    "market_analyst_resources": "available",
    "portfolio_database": "available",
    "knowledge_base": "available",
    "policy_engine": "unavailable",
    "market_researcher_agent": "deferred",
    "investment_analyst_agent": "deferred",
    "portfolio_manager_agent": "deferred",
}


class RunExistsError(WorkspaceError):
    """The target run directory is already present; runs are never overwritten."""


class RunNotFoundError(WorkspaceError):
    """No run directory for the supplied ID."""


class ArchiveCollisionError(WorkspaceError):
    """An archive slot for this run is already occupied."""


def load_request(path: Path) -> Request:
    """Parse and normalize a request file (YAML or JSON, both accepted)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise WorkspaceError(f"cannot read request {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError(f"request must be a mapping: {path}")
    try:
        return Request.model_validate(raw)
    except ValidationError as exc:
        raise WorkspaceError(f"invalid request {path}: {exc}") from exc


def _subject_label(request: Request) -> str:
    identifiers = request.subject.identifiers
    return identifiers.ticker or identifiers.name or request.subject.type


def create_run(
    request_path: Path,
    *,
    root: Path | None = None,
    trigger: str = "cli",
    now: datetime | None = None,
) -> tuple[str, Path]:
    """Create one run directory atomically from a request file."""
    return create_from_request(
        load_request(request_path), root=root, trigger=trigger, now=now
    )


def create_from_request(
    request: Request,
    *,
    root: Path | None = None,
    trigger: str = "cli",
    now: datetime | None = None,
) -> tuple[str, Path]:
    """Create one run directory atomically. Returns `(run_id, run_dir)`.

    Split from `create_run` so a caller that already holds a validated
    `Request` -- notably `ensure_run` below, which synthesizes one in memory --
    does not have to round-trip it through a temporary file just to be allowed
    to create a run.
    """
    moment = now or datetime.now(timezone.utc)
    run_id = request.run_id or generate_run_id(request.mode, _subject_label(request), now=moment)
    if not is_valid_run_id(run_id):
        raise WorkspaceError(f"invalid run_id in request: {run_id!r}")

    runs_dir = root or runs_root()
    target = runs_dir / run_id
    if target.exists():
        raise RunExistsError(f"run already exists, refusing to overwrite: {target}")

    runs_dir.mkdir(parents=True, exist_ok=True)
    staging = runs_dir / f".tmp-{run_id}"
    if staging.exists():
        shutil.rmtree(staging)

    try:
        staging.mkdir(parents=True)
        for subdir in RUN_SUBDIRS:
            (staging / subdir).mkdir(parents=True)

        # Normalize: the stored request always carries the resolved run_id and
        # creation time, whatever the submitted file did or did not specify.
        request.run_id = run_id
        request.created_at = request.created_at or moment
        (staging / REQUEST_FILENAME).write_text(
            yaml.safe_dump(
                request.model_dump(mode="json", exclude_none=False),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        stamp = utc_now_iso()
        metadata = RunMetadata(
            run_id=run_id,
            mode=request.mode,
            status=state_module.CREATED,
            trigger=trigger,
            created_at=stamp,
            updated_at=stamp,
            available_components=dict(KNOWN_COMPONENTS),
            planned_steps=list(request.required_analysis),
        )
        _write_metadata(staging, metadata)

        (staging / EVIDENCE_REGISTRY_RELPATH).touch()

        audit_module.append_event(
            staging,
            run_id=run_id,
            event="run_created",
            details={"mode": request.mode, "trigger": trigger},
        )

        manifest_module.write(
            staging,
            manifest_module.build(staging, request, run_id=run_id),
        )

        _assert_complete(staging)
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return run_id, target


def _assert_complete(directory: Path) -> None:
    """Last gate before a staged run becomes visible."""
    required = [REQUEST_FILENAME, METADATA_FILENAME, AUDIT_FILENAME, EVIDENCE_REGISTRY_RELPATH]
    missing = [name for name in required if not (directory / name).exists()]
    missing += [name for name in RUN_SUBDIRS if not (directory / name).is_dir()]
    if missing:
        raise WorkspaceError(f"run initialization incomplete, missing: {', '.join(missing)}")


def _write_metadata(directory: Path, metadata: RunMetadata) -> Path:
    path = directory / METADATA_FILENAME
    path.write_text(
        json.dumps(metadata.model_dump(mode="json", exclude_none=False), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def resolve_run(run_id: str, *, root: Path | None = None) -> Path:
    directory = run_dir_for(run_id, root=root)
    if not directory.is_dir():
        raise RunNotFoundError(f"no run found for id: {run_id}")
    return directory


def ensure_run(
    run_id: str | None,
    *,
    mode: str,
    subject_type: str = "other",
    ticker: str | None = None,
    question: str | None = None,
    trigger: str = "skill",
    root: Path | None = None,
    now: datetime | None = None,
) -> tuple[str, Path, bool]:
    """Attach to a run, creating it if it does not exist yet.

    Returns `(run_id, run_dir, created)`. This is the single implementation of
    attach-or-create: a skill that wants to deposit evidence into a run calls
    this and never reimplements the logic, so there is one definition of what
    an auto-created run looks like rather than one per producer.

    `run_id` of `"auto"` (or `None`) generates a fresh ID -- the ad-hoc case,
    "give me a run for this one pull." An explicit ID attaches to that run if
    it exists and creates it under that exact name if it does not, which is
    what lets an orchestrator hand the same ID to a fan-out of skills without
    caring which of them happens to run first.

    The trade-off of create-on-miss: a mistyped ID silently becomes a new
    empty run instead of erroring. Callers must therefore say so on stderr
    when `created` comes back True -- the run ID appearing in the output is
    the only cue a caller has that they typo'd rather than attached.
    """
    from .models import RequestBody, Subject, SubjectIdentifiers

    if run_id and run_id != AUTO_RUN_ID:
        if not is_valid_run_id(run_id):
            raise WorkspaceError(f"invalid run id: {run_id!r}")
        directory = run_dir_for(run_id, root=root)
        if directory.is_dir():
            # Attach only to a run that is actually usable. A half-built
            # directory must not be written into as though it were complete.
            _assert_complete(directory)
            return run_id, directory, False
    else:
        run_id = None

    request = Request(
        run_id=run_id,
        mode=mode,
        subject=Subject(
            type=subject_type, identifiers=SubjectIdentifiers(ticker=ticker)
        ),
        request=RequestBody(
            question=question
            or f"Auto-created by {trigger}; no research question was recorded.",
            context=(
                "This run was opened by a data-collection skill rather than "
                "from a submitted request. Evidence here is not yet tied to a "
                "stated question."
            ),
        ),
    )
    try:
        created_id, directory = create_from_request(
            request, root=root, trigger=trigger, now=now
        )
    except RunExistsError:
        # Two invocations racing on the same explicit run_id: both saw the
        # directory missing, both tried to create it, this one lost the race.
        # The winner's run is just as usable, so attach to it instead of
        # failing -- that is the whole point of fan-out sharing one run_id.
        # (Not reachable for the auto-generated-ID path: those IDs carry a
        # random suffix, so a genuine collision there is not this race.)
        if run_id is None:
            raise
        directory = run_dir_for(run_id, root=root)
        _assert_complete(directory)
        return run_id, directory, False
    return created_id, directory, True


def read_metadata(directory: Path) -> RunMetadata:
    path = directory / METADATA_FILENAME
    if not path.is_file():
        raise WorkspaceError(f"missing {METADATA_FILENAME} in {directory}")
    try:
        return RunMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise WorkspaceError(f"invalid {METADATA_FILENAME}: {exc}") from exc


def read_request(directory: Path) -> Request:
    path = directory / REQUEST_FILENAME
    if not path.is_file():
        raise WorkspaceError(f"missing {REQUEST_FILENAME} in {directory}")
    return load_request(path)


def set_status(
    directory: Path,
    target_status: str,
    *,
    actor: str = "workflow_cli",
    note: str | None = None,
) -> RunMetadata:
    """Move a run to a new state, refusing transitions the lifecycle forbids."""
    metadata = read_metadata(directory)
    previous = metadata.status
    state_module.assert_transition(previous, target_status)

    stamp = utc_now_iso()
    metadata.status = target_status
    metadata.updated_at = stamp
    if target_status == state_module.IN_PROGRESS and metadata.started_at is None:
        metadata.started_at = stamp
    if target_status in (state_module.COMPLETED, state_module.FAILED):
        metadata.completed_at = stamp
    if note:
        (metadata.errors if target_status == state_module.FAILED else metadata.warnings).append(note)

    _write_metadata(directory, metadata)
    audit_module.append_event(
        directory,
        run_id=metadata.run_id,
        event="status_changed",
        actor=actor,
        details={"from": previous, "to": target_status, **({"note": note} if note else {})},
    )
    return metadata


def record_component(directory: Path, component: str, availability: str) -> RunMetadata:
    """Mark a component available/unavailable/deferred for this run."""
    metadata = read_metadata(directory)
    metadata.available_components[component] = availability
    metadata.updated_at = utc_now_iso()
    _write_metadata(directory, metadata)
    return metadata


def complete_step(directory: Path, step: str) -> RunMetadata:
    metadata = read_metadata(directory)
    if step not in metadata.completed_steps:
        metadata.completed_steps.append(step)
    metadata.updated_at = utc_now_iso()
    _write_metadata(directory, metadata)
    return metadata


def rebuild_manifest(
    directory: Path,
    *,
    target_stage: str | None = None,
    prior_thesis_path: str | None = None,
) -> Path:
    metadata = read_metadata(directory)
    request = read_request(directory)
    built = manifest_module.build(
        directory,
        request,
        run_id=metadata.run_id,
        target_stage=target_stage,
        prior_thesis_path=prior_thesis_path,
    )
    path = manifest_module.write(directory, built)
    audit_module.append_event(
        directory,
        run_id=metadata.run_id,
        event="manifest_built",
        artifact=path.name,
        details={
            "evidence_count": len(built.evidence),
            "missing_count": len(built.missing_information),
            "validation_status": built.validation_status,
        },
    )
    return path


def archive_run(
    directory: Path,
    *,
    archive_root: Path | None = None,
    validate: bool = True,
) -> Path:
    """Move a completed run into the date-bucketed archive.

    Never deletes: the directory is moved with its structure intact, and only
    `tmp/` is discarded. A run that fails validation is still archivable with
    `validate=False`, because an abandoned run must be retained too -- but the
    default refuses, so archiving is not a way to hide a broken run.
    """
    metadata = read_metadata(directory)
    if validate:
        from . import validation as validation_module

        result = validation_module.validate_run(directory)
        if not result["ok"]:
            raise WorkspaceError(
                "run failed validation; fix it or archive with --no-validate. Errors: "
                + "; ".join(result["errors"])
            )

    created = metadata.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    destination = archive_dir(metadata.run_id, created_at=created, root=archive_root)
    if destination.exists():
        raise ArchiveCollisionError(f"archive slot already occupied: {destination}")

    # `tmp/` is the one disposable part of a run; everything else moves as-is.
    tmp_dir = directory / "tmp"
    if tmp_dir.is_dir():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(exist_ok=True)

    state_module.assert_transition(metadata.status, state_module.ARCHIVED)
    stamp = utc_now_iso()
    metadata.status = state_module.ARCHIVED
    metadata.archived = True
    metadata.archived_at = stamp
    metadata.archive_path = str(destination)
    metadata.updated_at = stamp
    _write_metadata(directory, metadata)
    audit_module.append_event(
        directory,
        run_id=metadata.run_id,
        event="run_archived",
        details={"destination": str(destination)},
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(directory), str(destination))
    return destination


def list_runs(*, root: Path | None = None) -> list[dict[str, Any]]:
    """Summarize every run in the runs root, newest first."""
    runs_dir = root or runs_root()
    if not runs_dir.is_dir():
        return []
    summaries: list[dict[str, Any]] = []
    for entry in sorted(runs_dir.iterdir(), reverse=True):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            metadata = read_metadata(entry)
        except WorkspaceError:
            summaries.append({"run_id": entry.name, "status": "unreadable"})
            continue
        summaries.append(
            {
                "run_id": metadata.run_id,
                "mode": metadata.mode,
                "status": metadata.status,
                "created_at": metadata.created_at.isoformat(),
                "evidence": len(evidence_module.read_records(entry)),
            }
        )
    return summaries
